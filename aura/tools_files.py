"""Workspace files: reading, writing, moving, and looking at shape.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from .toolkit import tool
from .validation import check_accessibility
import difflib
import os


class FilesTools:
    """Workspace files: reading, writing, moving, and looking at shape."""

    @tool('list_files', 'List files recursively inside a workspace folder.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_list_files(self, name, args, approve, call):
        result = {"files": self.sandbox.list_files(str(args.get("path", ".")))[:1000]}
        return result

    @tool('read_file', 'Read a UTF-8 text file or a focused line range from the workspace.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'start_line': {'type': 'integer', 'minimum': 1, 'default': 1}, 'end_line': {'type': 'integer', 'minimum': 1}}, ['path'])
    def _tool_read_file(self, name, args, approve, call):
        content = self.sandbox.read_file(str(args["path"]))
        lines = content.splitlines(keepends=True)
        start = max(1, int(args.get("start_line", 1)))
        end = min(len(lines), int(args.get("end_line", start + 399)))
        selected = "".join(lines[start - 1:end])
        result = {"path": args["path"], "content": selected,
                  "start_line": start, "end_line": end, "total_lines": len(lines),
                  "truncated": end < len(lines)}
        return result

    @tool('read_many_files', 'Read several related UTF-8 workspace files in one call with bounded output.',
          {'paths': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, 'max_lines_each': {'type': 'integer', 'minimum': 20, 'maximum': 1000, 'default': 300}}, ['paths'])
    def _tool_read_many_files(self, name, args, approve, call):
        paths = args.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
            raise ValueError("paths must contain between 1 and 20 files")
        max_lines = max(20, min(int(args.get("max_lines_each", 300)), 1000))
        files = []
        output_chars = 0
        for raw_path in paths:
            path = str(raw_path)
            content = self.sandbox.read_file(path)
            lines = content.splitlines(keepends=True)
            selected = "".join(lines[:max_lines])
            output_chars += len(selected)
            if output_chars > 250_000:
                raise ValueError("combined read exceeds Aura's 250,000 character context limit")
            files.append({"path": path, "content": selected, "total_lines": len(lines),
                          "truncated": len(lines) > max_lines})
        result = {"files": files, "count": len(files)}
        return result

    @tool('file_info', "Inspect a file's size, line count, and modification time.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_file_info(self, name, args, approve, call):
        target = self.sandbox.path(str(args["path"]))
        if not target.is_file():
            raise FileNotFoundError(str(args["path"]))
        stat = target.stat()
        try:
            line_count = len(target.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            line_count = None
        result = {"path": args["path"], "bytes": stat.st_size,
                  "lines": line_count, "modified": stat.st_mtime}
        return result

    @tool('create_file', 'Create a new UTF-8 file; fails if it already exists.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    @tool('write_file', 'Create or replace a UTF-8 file in the workspace.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    @tool('append_file', 'Append UTF-8 text to a workspace file, creating it if needed.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    def _tool_create_file(self, name, args, approve, call):
        content = str(args["content"])
        if len(content.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("file content exceeds Aura's 1 MB tool limit")
        if name == "create_file":
            target = self.sandbox.create_file(str(args["path"]), content)
        elif name == "append_file":
            path = str(args["path"])
            existing = self.sandbox.read_file(path) if self.sandbox.path(path).exists() else ""
            target = self.sandbox.write_file(path, existing + content)
        else:
            target = self.sandbox.write_file(str(args["path"]), content)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "bytes": len(content.encode("utf-8"))}
        return result

    @tool('create_folder', 'Create an empty workspace folder and missing parent folders. Use this instead of mkdir.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'], mutating=True)
    def _tool_create_folder(self, name, args, approve, call):
        target = self.sandbox.create_folder(str(args["path"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('write_files', 'Create or replace up to 20 related UTF-8 files in one batch. Every file remains recoverable.',
          {'files': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, 'required': ['path', 'content'], 'additionalProperties': False}}}, ['files'], mutating=True)
    def _tool_write_files(self, name, args, approve, call):
        items = args.get("files")
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("files must contain between 1 and 20 items")
        prepared: list[tuple[str, str, int]] = []
        total_bytes = 0
        for item in items:
            if not isinstance(item, dict) or "path" not in item or "content" not in item:
                raise ValueError("each file requires path and content")
            path, content = str(item["path"]), str(item["content"])
            size = len(content.encode("utf-8"))
            if size > self.MAX_WRITE_BYTES:
                raise ValueError(f"{path} exceeds Aura's 1 MB per-file tool limit")
            self.sandbox.path(path)
            prepared.append((path, content, size))
            total_bytes += size
        if total_bytes > 4_000_000:
            raise ValueError("combined batch write exceeds Aura's 4 MB limit")
        written = []
        for path, content, size in prepared:
            target = self.sandbox.write_file(path, content)
            written.append({"path": target.relative_to(self.sandbox.root).as_posix(),
                            "bytes": size})
        result = {"files": written, "count": len(written), "bytes": total_bytes}
        return result

    #: How many matches to show before the message becomes its own problem.
    SHOWN_MATCHES = 5

    @staticmethod
    def _describe_mismatch(content: str, old: str, expected: int, actual: int) -> str:
        """Say what is actually in the file, not merely that the count was wrong.

        Written from nineteen failed edits in the log. Every one refused correctly
        and left the model no better informed than before, so the next attempt was
        another guess at the same number.
        """
        lines = content.splitlines()
        if actual == 0:
            # Her text is not there. The useful question is how near she got.
            opening = old.strip().splitlines()[0][:40] if old.strip() else ""
            near = [f"line {n}: {line.strip()[:90]}"
                    for n, line in enumerate(lines, 1)
                    if opening[:12] and opening[:12] in line][:5]
            if not near and opening:
                # The substring probe only recognises an anchor that *starts*
                # correctly. One wrong in its first few characters — a `<div>`
                # the model wrote as `<section>`, a renamed class — finds
                # nothing and falls through to the least useful sentence this
                # tool can say. Ask what the file does contain instead.
                close = difflib.get_close_matches(
                    old.strip().splitlines()[0],
                    [line.strip() for line in lines], n=3, cutoff=0.6)
                near = [f"line {n}: {line.strip()[:90]}"
                        for n, line in enumerate(lines, 1)
                        if line.strip() in close][:3]
            if near:
                return ("that exact text is not in the file, though these lines are "
                        "close — check whitespace and read the file again:\n"
                        + "\n".join(near))
            return ("that exact text is not in the file at all. Read the file first; "
                    "the content may differ from what you expect.")
        # Too many. Show each one so a longer, unique anchor can be chosen.
        first = old.strip().splitlines()[0][:40] if old.strip() else old[:40]
        where = [f"line {n}: {line.strip()[:90]}"
                 for n, line in enumerate(lines, 1) if first and first in line]
        shown = where[:5]
        more = f"\n…and {len(where) - len(shown)} more" if len(where) > len(shown) else ""
        return (f"found {actual} matches, not {expected}. Include more surrounding text "
                f"so the anchor is unique, or set expected_count to {actual} if every "
                f"match should change. Matches:\n" + "\n".join(shown) + more)

    @tool('replace_in_file', 'Precisely replace exact text in an existing UTF-8 file. Fails if the match count is unexpected.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'expected_count': {'type': 'integer', 'minimum': 1, 'default': 1}}, ['path', 'old_text', 'new_text'], mutating=True)
    def _tool_replace_in_file(self, name, args, approve, call):
        path = str(args["path"])
        old, new = str(args["old_text"]), str(args["new_text"])
        if not old:
            raise ValueError("old_text cannot be empty")
        content = self.sandbox.read_file(path)
        expected = int(args.get("expected_count", 1))
        actual = content.count(old)
        if actual != expected:
            raise ValueError(self._describe_mismatch(content, old, expected, actual))
        updated = content.replace(old, new)
        if len(updated.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("updated file exceeds Aura's 1 MB tool limit")
        target = self.sandbox.write_file(path, updated)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "replacements": actual}
        return result

    @tool('apply_edits', 'Atomically apply several exact text replacements to one file with one recovery snapshot.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'edits': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': {'type': 'object', 'properties': {'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'expected_count': {'type': 'integer', 'minimum': 1, 'default': 1}}, 'required': ['old_text', 'new_text'], 'additionalProperties': False}}}, ['path', 'edits'], mutating=True)
    def _tool_apply_edits(self, name, args, approve, call):
        path = str(args["path"])
        edits = args["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= 50:
            raise ValueError("edits must contain between 1 and 50 replacements")
        updated = self.sandbox.read_file(path)
        applied = 0
        for edit in edits:
            old, new = str(edit["old_text"]), str(edit["new_text"])
            if not old:
                raise ValueError("old_text cannot be empty")
            expected = int(edit.get("expected_count", 1))
            actual = updated.count(old)
            if actual != expected:
                raise ValueError(f"edit {applied + 1}: "
                                 + self._describe_mismatch(updated, old, expected, actual))
            updated = updated.replace(old, new)
            applied += actual
        if len(updated.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("updated file exceeds Aura's 1 MB tool limit")
        target = self.sandbox.write_file(path, updated)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "edits": len(edits), "replacements": applied}
        return result

    @tool('copy_file', 'Copy a workspace file.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_copy_file(self, name, args, approve, call):
        target = self.sandbox.copy_file(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('move_file', 'Move or rename a workspace file.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_move_file(self, name, args, approve, call):
        target = self.sandbox.move_file(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('safe_delete_file', "Move a file into Aura's recoverable trash.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'], mutating=True)
    def _tool_safe_delete_file(self, name, args, approve, call):
        target = self.sandbox.safe_delete_file(str(args["path"]))
        result = {"trashed_as": target.name, "recoverable": True}
        return result

    @tool('create_archive', 'Create a recoverable ZIP archive from a workspace file or folder.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_create_archive(self, name, args, approve, call):
        target = self.sandbox.create_archive(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "bytes": target.stat().st_size}
        return result

    @tool('extract_archive', 'Safely extract a workspace ZIP with traversal, link, file-count, and size protection.',
          {'archive': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['archive', 'destination'], mutating=True)
    def _tool_extract_archive(self, name, args, approve, call):
        extracted = self.sandbox.extract_archive(str(args["archive"]), str(args["destination"]))
        result = {"files": [path.relative_to(self.sandbox.root).as_posix() for path in extracted],
                  "count": len(extracted)}
        return result

    @tool('search_files', 'Search file names and UTF-8 contents in the workspace.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, ['query'])
    def _tool_search_files(self, name, args, approve, call):
        result = {"matches": self.sandbox.search_files(
            str(args["query"]), str(args.get("path", ".")))[:500]}
        return result

    @tool('search_text', 'Return matching lines with file names and line numbers.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}}, ['query'])
    def _tool_search_text(self, name, args, approve, call):
        limit = max(1, min(int(args.get("limit", 100)), 500))
        result = {"matches": self.sandbox.search_text(
            str(args["query"]), str(args.get("path", ".")), limit)}
        return result

    @tool('compare_files', 'Produce a bounded unified diff between two UTF-8 workspace files.',
          {'left': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'right': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'context_lines': {'type': 'integer', 'minimum': 0, 'maximum': 20, 'default': 3}}, ['left', 'right'])
    def _tool_compare_files(self, name, args, approve, call):
        left, right = str(args["left"]), str(args["right"])
        context = max(0, min(int(args.get("context_lines", 3)), 20))
        result = self.sandbox.compare_files(left, right, context)
        return result

    @tool('find_relevant_files', 'Rank workspace files by relevance to a described topic or question. Use this when you do not know the exact wording to search for; use search_files or search_text when you need an exact string. Matches words, not synonyms.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10}}, ['query'])
    def _tool_find_relevant_files(self, name, args, approve, call):
        result = {"matches": self.index.search(
            str(args["query"]), int(args.get("limit", 10)),
            str(args.get("path", ".")))}
        return result

    @tool('open_workspace_item', 'Open a workspace file or folder in its normal desktop application after approval.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_open_workspace_item(self, name, args, approve, call):
        path = str(args["path"])
        target = self.sandbox.path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if not approve or not approve(["OPEN", path]):
            raise PermissionError("Opening a desktop application was not approved")
        os.startfile(target)  # type: ignore[attr-defined]
        result = {"path": path, "opened": True}
        return result

    @tool('workspace_summary', 'Summarize workspace file count, size, extensions, and largest files.',
          {}, [])
    def _tool_workspace_summary(self, name, args, approve, call):
        files = self.sandbox.list_files()
        sizes = []
        extensions: dict[str, int] = {}
        for relative in files:
            target = self.sandbox.path(relative)
            size = target.stat().st_size
            sizes.append((relative, size))
            extension = target.suffix.casefold() or "(none)"
            extensions[extension] = extensions.get(extension, 0) + 1
        result = {"file_count": len(files), "total_bytes": sum(size for _, size in sizes),
                  "extensions": dict(sorted(extensions.items())),
                  "largest_files": [{"path": path, "bytes": size}
                                    for path, size in sorted(sizes, key=lambda item: item[1], reverse=True)[:10]]}
        return result

    @tool('inspect_code', 'Outline symbols, imports, and structure in a Python, JavaScript, or TypeScript file without executing it.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_inspect_code(self, name, args, approve, call):
        result = self._inspect_code(str(args["path"]))
        return result

    @tool('validate_project', 'Safely validate every project file, including Python, JSON, TOML, HTML, CSS, JavaScript/TypeScript, XML, and UTF-8 text, without executing project code.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_validate_project(self, name, args, approve, call):
        result = self._validate_project(str(args.get("path", ".")))
        return result

    @tool('check_accessibility', 'Report accessibility problems in workspace HTML: images without alt text, form controls without labels, empty links or buttons, a missing lang or title, and skipped heading levels. Structural checks only — it does not evaluate colour contrast.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_check_accessibility(self, name, args, approve, call):
        result = check_accessibility(self.sandbox, str(args.get("path", ".")))
        return result
