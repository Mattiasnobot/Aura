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


def _html_files(sandbox: WorkspaceSandbox, relative: str) -> list[str]:
    """HTML pages under `relative`, which may name a folder or a single page.

    Without the single-file case, pointing a check at one page silently scans
    nothing and an empty issue list reads as a clean result.
    """
    base = sandbox.path(relative)
    if base.is_file():
        name = base.relative_to(sandbox.root).as_posix()
        return [name] if Path(name).suffix.casefold() in {".html", ".htm"} else []
    return [name for name in sandbox.list_files(relative)
            if Path(name).suffix.casefold() in {".html", ".htm"}]


def check_broken_assets(sandbox: WorkspaceSandbox, relative: str = ".") -> dict:
    """Crawl HTML files for local link/script/img references that do not resolve."""
    base = sandbox.path(relative)
    if not base.exists():
        return {"ok": False, "error": "Project path does not exist", "checked": 0, "broken": []}
    checked = 0
    broken: list[dict[str, str]] = []
    for name in _html_files(sandbox, relative):
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
    for name in _script_files(sandbox, relative):
        try:
            content = sandbox.read_file(name)
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        for reference in _absolute_paths_in_script(content):
            joined = posixpath.normpath(reference.lstrip("/"))
            try:
                target = sandbox.path(joined)
            except (SandboxViolation, ValueError):
                broken.append({"file": name, "reference": reference})
                continue
            if not target.is_file():
                broken.append({"file": name, "reference": reference})
    for name in _data_files(sandbox, relative):
        try:
            content = sandbox.read_file(name)
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        folder = posixpath.dirname(name)
        for reference in _asset_paths_in_data(content):
            # Two readings, and only a path that fails both is reported. The
            # browser resolves it against the page; whoever wrote the data file
            # was probably thinking of the folder it sits in. Guessing wrong
            # would flag working code, and a checker that cries wolf is one
            # nobody reads.
            if not _resolves_either_way(sandbox, folder, relative, reference):
                broken.append({"file": name, "reference": reference})
    return {"ok": True, "checked": checked, "broken": broken}


#: Files that name assets without being able to link them: product lists,
#: content indexes, anything a page fetches and renders. `img.src = p.image`
#: is invisible to the script scan because the path never appears in the code.
def _data_files(sandbox: WorkspaceSandbox, relative: str) -> list[str]:
    return [name for name in sandbox.list_files(relative)
            if name.lower().endswith(".json")]


#: A quoted string that looks like a local asset: an image, font, media, or
#: document file. Deliberately narrow — a JSON value with a dot in it is
#: usually not a path, and only these extensions are worth a claim.
_DATA_ASSET = re.compile(
    r'"((?:\.{1,2}/|/)?(?:[\w.-]+/)*[\w.-]+'
    r'\.(?:png|jpe?g|gif|webp|avif|svg|ico|bmp|mp4|webm|ogg|mp3|wav|woff2?|ttf|otf|pdf))"',
    re.IGNORECASE)


def _asset_paths_in_data(content: str) -> list[str]:
    seen: list[str] = []
    for match in _DATA_ASSET.finditer(content):
        reference = match.group(1)
        if not _is_local_asset_reference(reference) or reference in seen:
            continue
        seen.append(reference)
    return seen


def _resolves_either_way(sandbox: WorkspaceSandbox, folder: str,
                         project: str, reference: str) -> bool:
    """Does this path find a file from the data file, or from the project root?"""
    root = posixpath.normpath(str(project).strip("/") or ".")
    bases = [folder, "" if root == "." else root]
    for base in bases:
        if reference.startswith("/"):
            joined = posixpath.normpath(reference.lstrip("/"))
        else:
            joined = posixpath.normpath(posixpath.join(base, reference))
        if joined == ".." or joined.startswith("../"):
            continue
        try:
            if sandbox.path(joined).is_file():
                return True
        except (SandboxViolation, ValueError):
            continue
    return False


#: Root-absolute paths written as string literals in JavaScript. Anchored on the
#: quote so a path inside a longer URL ("https://x/data/y") cannot match, and
#: requiring an extension so a route ("/shop") is not mistaken for a file.
_SCRIPT_PATH = re.compile(r"""['"](/(?:[\w.-]+/)*[\w-]+\.[A-Za-z0-9]{1,6})['"]""")


def _script_files(sandbox: WorkspaceSandbox, relative: str) -> list[str]:
    return [name for name in sandbox.list_files(relative)
            if name.lower().endswith((".js", ".mjs", ".ts"))]


def _absolute_paths_in_script(content: str) -> list[str]:
    """Root-absolute asset paths a browser cannot resolve from a local file.

    Only absolute ones. A relative path in a script may be joined from variables
    or resolved against a base this cannot see, and a checker that cries wolf on
    working code is one nobody reads.
    """
    seen: list[str] = []
    for match in _SCRIPT_PATH.finditer(content):
        reference = match.group(1)
        if reference.startswith("//"):
            continue        # protocol-relative URL, not a local path
        if reference not in seen:
            seen.append(reference)
    return seen


class _AccessibilityCollector(HTMLParser):
    """Collect accessibility problems that are decidable from markup alone.

    Deliberately limited: it never guesses at colour contrast, because that
    needs the resolved CSS cascade and a wrong answer there would be worse
    than no answer.
    """

    LABELLABLE = {"input", "select", "textarea"}
    NO_LABEL_NEEDED = {"submit", "reset", "button", "hidden", "image"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: list[dict[str, str]] = []
        self.has_html_lang = False
        self.saw_html = False
        self.title_text = ""
        self._in_title = False
        self._label_targets: set[str] = set()
        self._pending_controls: list[dict] = []
        self._open_labels = 0
        self._last_heading = 0
        # Elements whose accessible name comes from their own text.
        self._named_stack: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.casefold()
        values = {key.casefold(): (value or "") for key, value in attrs}
        line = self.getpos()[0]

        if name == "html":
            self.saw_html = True
            self.has_html_lang = bool(values.get("lang", "").strip())
        elif name == "title":
            self._in_title = True
        elif name == "img":
            if "alt" not in values:
                self.issues.append({
                    "rule": "img-alt", "line": line,
                    "detail": f"<img src=\"{values.get('src', '')[:60]}\"> has no alt attribute",
                })
        elif name == "label":
            self._open_labels += 1
            target = values.get("for", "").strip()
            if target:
                self._label_targets.add(target)
        elif name in self.LABELLABLE:
            if values.get("type", "").casefold() not in self.NO_LABEL_NEEDED:
                self._pending_controls.append({
                    "line": line, "tag": name,
                    "id": values.get("id", "").strip(),
                    "wrapped": self._open_labels > 0,
                    "aria": bool(values.get("aria-label", "").strip()
                                 or values.get("aria-labelledby", "").strip()),
                })
        elif name in {"a", "button"}:
            self._named_stack.append({
                "tag": name, "line": line, "text": "",
                "aria": bool(values.get("aria-label", "").strip()
                             or values.get("aria-labelledby", "").strip()),
                "has_img": False,
            })
        elif re.fullmatch(r"h[1-6]", name):
            level = int(name[1])
            if self._last_heading and level > self._last_heading + 1:
                self.issues.append({
                    "rule": "heading-order", "line": line,
                    "detail": f"<{name}> follows <h{self._last_heading}>, skipping a level",
                })
            self._last_heading = level

        if name == "img" and self._named_stack:
            self._named_stack[-1]["has_img"] = True

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "title":
            self._in_title = False
        elif name == "label":
            self._open_labels = max(0, self._open_labels - 1)
        elif name in {"a", "button"} and self._named_stack:
            entry = self._named_stack.pop()
            if not entry["text"].strip() and not entry["aria"] and not entry["has_img"]:
                label = "link" if entry["tag"] == "a" else "button"
                self.issues.append({
                    "rule": f"empty-{label}", "line": entry["line"],
                    "detail": f"<{entry['tag']}> has no text or aria-label",
                })

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data
        for entry in self._named_stack:
            entry["text"] += data

    def finish(self) -> list[dict]:
        for control in self._pending_controls:
            labelled = (control["wrapped"] or control["aria"]
                        or (control["id"] and control["id"] in self._label_targets))
            if not labelled:
                self.issues.append({
                    "rule": "control-label", "line": control["line"],
                    "detail": f"<{control['tag']}> has no label, aria-label, or wrapping <label>",
                })
        if self.saw_html and not self.has_html_lang:
            self.issues.append({"rule": "html-lang", "line": 1,
                                "detail": "<html> has no lang attribute"})
        if not self.title_text.strip():
            self.issues.append({"rule": "document-title", "line": 1,
                                "detail": "the page has no non-empty <title>"})
        return sorted(self.issues, key=lambda item: (item["line"], item["rule"]))


def check_accessibility(sandbox: WorkspaceSandbox, relative: str = ".") -> dict:
    """Report markup-level accessibility problems in workspace HTML pages.

    Structural only. Colour contrast is intentionally not evaluated: deciding
    it correctly needs the resolved CSS cascade, and a confident wrong answer
    would be more harmful than reporting nothing.
    """
    base = sandbox.path(relative)
    if not base.exists():
        return {"ok": False, "error": "Path does not exist", "checked": 0, "issues": []}
    checked = 0
    issues: list[dict] = []
    for name in _html_files(sandbox, relative):
        try:
            content = sandbox.read_file(name)
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        parser = _AccessibilityCollector()
        parser.feed(content)
        parser.close()
        for issue in parser.finish():
            issues.append({"file": name, **issue})
    note = ("Structural checks only - colour contrast is not evaluated, "
            "because it cannot be decided from markup alone.")
    if not checked:
        # An empty issue list must never be readable as a clean result when
        # nothing was actually examined.
        return {"ok": False, "checked": 0, "issues": [], "contrast_checked": False,
                "error": f"No HTML page was found at '{relative}', so nothing was checked.",
                "note": note}
    return {"ok": True, "checked": checked, "issues": issues,
            "contrast_checked": False,
            "summary": (f"{len(issues)} issue(s) across {checked} page(s)."
                        if issues else f"No structural issues in {checked} page(s)."),
            "note": note}
