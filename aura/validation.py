from __future__ import annotations

import ast
import json
import posixpath
import re
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from .safety import SandboxViolation, WorkspaceSandbox


MAX_VALIDATION_BYTES = 5_000_000
TEXT_SUFFIXES = {
    ".cjs", ".css", ".csv", ".html", ".htm", ".ini", ".js", ".jsx", ".json",
    ".md", ".mjs", ".py", ".svg", ".toml", ".ts", ".tsx", ".txt",
    ".xml", ".yaml", ".yml",
}
VOID_HTML_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
OPTIONAL_HTML_END = {"colgroup", "dd", "dt", "li", "option", "p", "tbody", "td", "tfoot", "th", "thead", "tr"}


class _HTMLStructureValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        tag = tag.casefold()
        if tag in VOID_HTML_ELEMENTS:
            return
        if tag in OPTIONAL_HTML_END and self.stack and self.stack[-1] == tag:
            self.stack.pop()
        self.stack.append(tag)

    def handle_startendtag(self, _tag: str, _attrs) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in VOID_HTML_ELEMENTS:
            self.errors.append(f"void element </{tag}> must not have a closing tag")
            return
        if tag not in self.stack:
            self.errors.append(f"unexpected closing tag </{tag}>")
            return
        while self.stack and self.stack[-1] != tag:
            skipped = self.stack.pop()
            if skipped not in OPTIONAL_HTML_END:
                self.errors.append(f"<{skipped}> is not closed before </{tag}>")
        if self.stack:
            self.stack.pop()

    def finish(self) -> list[str]:
        self.close()
        for tag in reversed(self.stack):
            if tag not in OPTIONAL_HTML_END:
                self.errors.append(f"unclosed <{tag}> element")
        return self.errors


def _balanced_delimiters(content: str, *, javascript: bool = False) -> str | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    stack: list[tuple[str, int]] = []
    quote: str | None = None
    escaped = False
    block_comment = False
    line_comment = False
    previous = ""
    index = 0
    while index < len(content):
        char = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if javascript and char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if javascript and char == "/" and (not previous or previous in "=([{!?:;,<>+-*%&|^~"):
            index += 1
            regex_class = False
            regex_escaped = False
            while index < len(content):
                regex_char = content[index]
                if regex_escaped:
                    regex_escaped = False
                elif regex_char == "\\":
                    regex_escaped = True
                elif regex_char == "[":
                    regex_class = True
                elif regex_char == "]":
                    regex_class = False
                elif regex_char == "/" and not regex_class:
                    break
                elif regex_char == "\n":
                    return "unterminated regular expression"
                index += 1
            if index >= len(content):
                return "unterminated regular expression"
            index += 1
            while index < len(content) and content[index].isalpha():
                index += 1
            previous = "r"
            continue
        if char in {"'", '"'} or (javascript and char == "`"):
            quote = char
        elif char in pairs:
            stack.append((char, index))
        elif char in closing:
            if not stack or stack[-1][0] != closing[char]:
                return f"unexpected {char!r} at character {index + 1}"
            stack.pop()
        if not char.isspace():
            previous = char
        index += 1
    if quote:
        return "unterminated string"
    if block_comment:
        return "unterminated block comment"
    if stack:
        opening, position = stack[-1]
        return f"unclosed {opening!r} from character {position + 1}"
    return None


def _javascript_error(target: Path, content: str) -> str | None:
    node = shutil.which("node")
    if node and target.suffix.casefold() in {".js", ".mjs", ".cjs"}:
        try:
            completed = subprocess.run(
                [node, "--check", str(target)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20, shell=False, check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()
                return detail[-1] if detail else "JavaScript syntax check failed"
            return None
        except (OSError, subprocess.SubprocessError):
            pass
    return _balanced_delimiters(content, javascript=True)


def validate_project(sandbox: WorkspaceSandbox, relative: str = ".") -> dict:
    base = sandbox.path(relative)
    checked = {
        "python": 0, "json": 0, "toml": 0, "html": 0, "css": 0,
        "javascript": 0, "xml": 0, "text": 0, "binary": 0,
    }
    if not base.exists():
        return {"valid": False, "checked": checked,
                "issues": [{"path": relative, "error": "Project path does not exist"}],
                "files_seen": 0}
    files = sandbox.list_files(relative)
    issues: list[dict[str, str]] = []
    for name in files:
        target = sandbox.path(name)
        try:
            if target.stat().st_size > MAX_VALIDATION_BYTES:
                raise ValueError("file exceeds Aura's 5 MB validation limit")
            raw = target.read_bytes()
            suffix = Path(name).suffix.casefold()
            if suffix not in TEXT_SUFFIXES and b"\x00" in raw:
                checked["binary"] += 1
                continue
            content = raw.decode("utf-8")
            if "\x00" in content:
                raise ValueError("text file contains a NUL character")
            checked["text"] += 1
            if suffix == ".py":
                ast.parse(content, filename=name)
                checked["python"] += 1
            elif suffix == ".json":
                json.loads(content)
                checked["json"] += 1
            elif suffix == ".toml":
                tomllib.loads(content)
                checked["toml"] += 1
            elif suffix in {".html", ".htm"}:
                parser = _HTMLStructureValidator()
                parser.feed(content)
                html_errors = parser.finish()
                if html_errors:
                    raise ValueError("; ".join(html_errors[:5]))
                checked["html"] += 1
            elif suffix == ".css":
                error = _balanced_delimiters(content)
                if error:
                    raise ValueError(error)
                checked["css"] += 1
            elif suffix in {".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"}:
                error = _javascript_error(target, content)
                if error:
                    raise ValueError(error)
                checked["javascript"] += 1
            elif suffix in {".xml", ".svg"}:
                ET.fromstring(content)
                checked["xml"] += 1
        except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError,
                ET.ParseError, UnicodeDecodeError, OSError, ValueError) as exc:
            message = re.sub(r"\s+", " ", str(exc)).strip()
            issues.append({"path": name, "error": message[:1000]})
    if not files:
        issues.append({"path": relative, "error": "Project contains no files"})
    return {"valid": not issues, "checked": checked, "issues": issues,
            "files_seen": len(files)}


class _AssetReferenceCollector(HTMLParser):
    ASSET_ATTRS = {"link": "href", "script": "src", "img": "src"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_name = self.ASSET_ATTRS.get(tag.casefold())
        if not attr_name:
            return
        value = dict(attrs).get(attr_name)
        if value:
            self.references.append(value)


def _is_local_asset_reference(value: str) -> bool:
    lowered = value.strip().casefold()
    if not lowered:
        return False
    return not lowered.startswith(("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:", "#"))


def check_broken_assets(sandbox: WorkspaceSandbox, relative: str = ".") -> dict:
    """Crawl HTML files for local link/script/img references that do not resolve."""
    base = sandbox.path(relative)
    if not base.exists():
        return {"ok": False, "error": "Project path does not exist", "checked": 0, "broken": []}
    checked = 0
    broken: list[dict[str, str]] = []
    for name in sandbox.list_files(relative):
        if Path(name).suffix.casefold() not in {".html", ".htm"}:
            continue
        try:
            content = sandbox.read_file(name)
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        parser = _AssetReferenceCollector()
        parser.feed(content)
        parser.close()
        folder = posixpath.dirname(name)
        for reference in parser.references:
            cleaned = reference.split("#", 1)[0].split("?", 1)[0]
            if not _is_local_asset_reference(cleaned):
                continue
            if cleaned.startswith("/"):
                joined = posixpath.normpath(cleaned.lstrip("/"))
            else:
                joined = posixpath.normpath(posixpath.join(folder, cleaned))
            if joined == ".." or joined.startswith("../"):
                broken.append({"file": name, "reference": reference})
                continue
            try:
                target = sandbox.path(joined)
            except (SandboxViolation, ValueError):
                broken.append({"file": name, "reference": reference})
                continue
            if not target.is_file():
                broken.append({"file": name, "reference": reference})
    return {"ok": True, "checked": checked, "broken": broken}
