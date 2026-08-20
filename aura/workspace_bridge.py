"""Workspace file, preview, and import methods of the local interface.

Split out of `web_bridge.py`, which had grown to 64 HTTP-exposed methods in one
class. These are mixed back into `AuraWebBridge`, so every method keeps the name
the HTTP layer already calls it by; only the file it lives in changed.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from urllib.parse import quote

from .validation import check_broken_assets


class WorkspaceBridge:
    def open_workspace(self) -> dict:
        try:
            os.startfile(self.agent.sandbox.root)  # type: ignore[attr-defined]
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def workspace_snapshot(self) -> dict:
        try:
            files = []
            for relative in self.agent.sandbox.list_files():
                target = self.agent.sandbox.path(relative)
                stat = target.stat()
                suffix = target.suffix.casefold()
                preview_kind = "text" if suffix in self.TEXT_PREVIEW_SUFFIXES else (
                    "image" if suffix in self.IMAGE_PREVIEW_SUFFIXES else "binary")
                if suffix in {".html", ".htm", ".svg"}:
                    preview_kind = "rendered"
                files.append({
                    "path": relative,
                    "name": target.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "suffix": suffix,
                    "preview_kind": preview_kind,
                })
            return {"ok": True, "files": files, "count": len(files)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "files": [], "count": 0}

    def preview_workspace_file(self, relative: str) -> dict:
        try:
            path = str(relative).strip()
            target = self.agent.sandbox.path(path)
            if not target.is_file():
                raise FileNotFoundError(path)
            size = target.stat().st_size
            suffix = target.suffix.casefold()
            if suffix in self.IMAGE_PREVIEW_SUFFIXES:
                if size > self.MAX_PREVIEW_BYTES:
                    return {"ok": True, "path": path, "size": size, "kind": "binary",
                            "message": "Image is too large for the safe preview."}
                mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix[1:]}"
                return {"ok": True, "path": path, "size": size, "kind": "image",
                        "mime": mime, "content": base64.b64encode(target.read_bytes()).decode("ascii")}
            if suffix not in self.TEXT_PREVIEW_SUFFIXES:
                return {"ok": True, "path": path, "size": size, "kind": "binary",
                        "message": "Binary preview is intentionally disabled."}
            raw = target.read_bytes()[: self.MAX_PREVIEW_BYTES + 1]
            truncated = len(raw) > self.MAX_PREVIEW_BYTES or size > self.MAX_PREVIEW_BYTES
            content = raw[: self.MAX_PREVIEW_BYTES].decode("utf-8")
            kind = "rendered" if suffix in {".html", ".htm", ".svg"} else "text"
            result = {"ok": True, "path": path, "size": size, "kind": kind,
                      "content": content, "truncated": truncated, "suffix": suffix}
            if kind == "rendered":
                # The protected GET route gives the document a real base URL, so
                # relative CSS, images, fonts, and links resolve inside the same
                # sandboxed workspace. Scripts remain disabled by the route CSP
                # and the iframe sandbox.
                result["url"] = "/workspace-preview/" + quote(path, safe="/")
                result["scripts_enabled"] = False
                # Whether the page *needs* scripts decides how loudly the panel
                # says they are off. A blank preview of a page that builds its
                # own content is indistinguishable from a broken page, and Mat
                # read one as the other: the shop rendered only its heading here
                # while the real site rendered every product.
                result["scripts_present"] = "<script" in content.casefold()
            return result
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def open_workspace_item(self, relative: str) -> dict:
        try:
            target = self.agent.sandbox.path(str(relative).strip())
            if not target.exists():
                raise FileNotFoundError(str(relative))
            os.startfile(target)  # type: ignore[attr-defined]
            return {"ok": True}
        except (FileNotFoundError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def import_files(self, items: list[dict], destination: str = ".") -> dict:
        try:
            if not isinstance(items, list) or not 1 <= len(items) <= 5:
                raise ValueError("Drop between 1 and 5 files at a time.")
            destination_path = str(destination or ".").strip()
            self.agent.sandbox.path(destination_path)
            decoded: list[tuple[str, bytes]] = []
            total = 0
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid dropped file.")
                original_name = str(item.get("name", "")).strip()
                name = Path(original_name).name
                if not name or name in {".", ".."}:
                    raise ValueError("A dropped file has no safe name.")
                try:
                    data = base64.b64decode(str(item.get("content", "")), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(f"{name} is not a valid file upload.") from exc
                if len(data) > self.MAX_IMPORT_FILE_BYTES:
                    raise ValueError(f"{name} exceeds the 1.5 MB local import limit.")
                total += len(data)
                if total > self.MAX_IMPORT_TOTAL_BYTES:
                    raise ValueError("Dropped files exceed the 4 MB total import limit.")
                decoded.append((name, data))
            imported = []
            for name, data in decoded:
                requested = Path(destination_path) / name
                candidate = requested.as_posix()
                stem, suffix = Path(name).stem, Path(name).suffix
                index = 2
                while self.agent.sandbox.path(candidate).exists():
                    candidate = (Path(destination_path) / f"{stem} ({index}){suffix}").as_posix()
                    index += 1
                self.agent.sandbox.import_file(candidate, data)
                imported.append(candidate)
                self.agent.log.record("import_file", "ok", path=candidate, bytes=len(data))
            return {"ok": True, "files": imported, "count": len(imported)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "files": [], "count": 0}

    def create_workspace_folder(self, path: str) -> dict:
        try:
            cleaned = str(path).strip()
            self.agent.sandbox.create_folder(cleaned)
            self.agent.log.record("create_folder", "ok", path=cleaned)
            return {"ok": True, "path": cleaned}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def create_workspace_file(self, path: str, content: str = "") -> dict:
        try:
            cleaned = str(path).strip()
            self.agent.sandbox.create_file(cleaned, str(content))
            self.agent.log.record("create_file", "ok", path=cleaned)
            return {"ok": True, "path": cleaned}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def rename_workspace_item(self, path: str, new_name: str) -> dict:
        try:
            cleaned = str(path).strip()
            source = self.agent.sandbox.path(cleaned)
            if not source.exists():
                raise FileNotFoundError(cleaned)
            name = Path(str(new_name).strip()).name
            if not name or name in {".", ".."}:
                raise ValueError("Enter a valid name.")
            new_relative = (Path(cleaned).parent / name).as_posix()
            if source.is_dir():
                self.agent.sandbox.move_folder(cleaned, new_relative)
            else:
                self.agent.sandbox.move_file(cleaned, new_relative)
            self.agent.log.record("rename_item", "ok", path=cleaned, to=new_relative)
            return {"ok": True, "path": new_relative}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def move_workspace_item(self, source: str, destination: str) -> dict:
        try:
            cleaned_source = str(source).strip()
            cleaned_destination = str(destination).strip()
            resolved = self.agent.sandbox.path(cleaned_source)
            if not resolved.exists():
                raise FileNotFoundError(cleaned_source)
            if resolved.is_dir():
                self.agent.sandbox.move_folder(cleaned_source, cleaned_destination)
                self.agent.log.record("move_folder", "ok", path=cleaned_source, to=cleaned_destination)
            else:
                self.agent.sandbox.move_file(cleaned_source, cleaned_destination)
                self.agent.log.record("move_file", "ok", path=cleaned_source, to=cleaned_destination)
            return {"ok": True, "path": cleaned_destination}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def copy_workspace_item(self, source: str, destination: str) -> dict:
        try:
            cleaned_source = str(source).strip()
            cleaned_destination = str(destination).strip()
            resolved = self.agent.sandbox.path(cleaned_source)
            if not resolved.exists():
                raise FileNotFoundError(cleaned_source)
            if resolved.is_dir():
                self.agent.sandbox.copy_folder(cleaned_source, cleaned_destination)
                self.agent.log.record("copy_folder", "ok", path=cleaned_source, to=cleaned_destination)
            else:
                self.agent.sandbox.copy_file(cleaned_source, cleaned_destination)
                self.agent.log.record("copy_file", "ok", path=cleaned_source, to=cleaned_destination)
            return {"ok": True, "path": cleaned_destination}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def delete_workspace_item(self, path: str) -> dict:
        try:
            cleaned = str(path).strip()
            resolved = self.agent.sandbox.path(cleaned)
            if not resolved.exists():
                raise FileNotFoundError(cleaned)
            if resolved.is_dir():
                trashed = self.agent.sandbox.safe_delete_folder(cleaned)
                kind = "folder"
                self.agent.log.record("safe_delete_folder", "ok", path=cleaned)
            else:
                trashed = self.agent.sandbox.safe_delete_file(cleaned)
                kind = "file"
                self.agent.log.record("safe_delete_file", "ok", path=cleaned)
            return {"ok": True, "trashed_as": trashed.name, "kind": kind}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def list_trash(self) -> dict:
        try:
            return {"ok": True, "items": self.agent.sandbox.list_trash()}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "items": []}

    def restore_workspace_item(self, trash_name: str) -> dict:
        try:
            restored = self.agent.sandbox.restore_from_trash(str(trash_name).strip())
            path = restored.relative_to(self.agent.sandbox.root).as_posix()
            self.agent.log.record("restore_from_trash", "ok", trash_name=trash_name, path=path)
            return {"ok": True, "path": path}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def undo_workspace_change(self) -> dict:
        try:
            result = self.agent.sandbox.undo_last_change()
            self.agent.log.record("undo_last_change", "ok", paths=result.get("paths", []))
            return {"ok": True, **result}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def workspace_change_history(self, limit: int = 20) -> dict:
        try:
            return {"ok": True, "changes": self.agent.sandbox.change_history(int(limit))}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "changes": []}

    def compare_workspace_files(self, left: str, right: str, context_lines: int = 3) -> dict:
        try:
            result = self.agent.sandbox.compare_files(str(left), str(right), int(context_lines))
            return {"ok": True, **result}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def start_preview_server(self, path: str = ".") -> dict:
        try:
            return self.preview_server.start(str(path))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def stop_preview_server(self) -> dict:
        try:
            return self.preview_server.stop()
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    def preview_server_status(self) -> dict:
        return self.preview_server.status()

    def preview_server_log(self, limit: int = 50) -> dict:
        return {"ok": True, "entries": self.preview_server.recent_log(limit)}

    def check_workspace_assets(self, path: str = ".") -> dict:
        try:
            return check_broken_assets(self.agent.sandbox, str(path))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
