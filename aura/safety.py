from __future__ import annotations

from .errors import AuraError

import difflib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .store import Database


class SandboxViolation(AuraError, ValueError):
    """Raised when an operation attempts to leave the Aura workspace."""


class WorkspaceSandbox:
    def __init__(self, root: str | Path = "aura-workspace",
                 database: "Database | None" = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash = self.root / ".aura-trash"
        self.meta = self.root / ".aura"
        self.trash.mkdir(exist_ok=True)
        self.meta.mkdir(exist_ok=True)
        self.history = self.meta / "history"
        self.history.mkdir(exist_ok=True)
        self.db = database or Database(self.meta / "aura.db")
        self.active_task_id: str | None = None

    def path(self, relative: str | Path, *, allow_meta: bool = False) -> Path:
        raw = Path(relative)
        if raw.is_absolute():
            raise SandboxViolation("Absolute paths are not allowed")
        candidate = self.root / raw
        current = self.root
        for part in raw.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise SandboxViolation("Parent path traversal is not allowed")
            current = current / part
            if current.exists() and self._is_link_like(current):
                raise SandboxViolation("Symbolic links and junctions are not allowed")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise SandboxViolation("Path is outside the Aura workspace") from exc
        if not allow_meta and resolved != self.root:
            first = resolved.relative_to(self.root).parts[0]
            if first in {".aura", ".aura-trash"}:
                raise SandboxViolation("Aura metadata folders are protected")
        return resolved

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction and is_junction())

    def create_folder(self, relative: str) -> Path:
        target = self.path(relative)
        if target.exists():
            raise FileExistsError(relative)
        self._snapshot("create_folder", [target])
        target.mkdir(parents=True, exist_ok=False)
        return target

    def create_file(self, relative: str, content: str = "") -> Path:
        target = self.path(relative)
        if target.exists():
            raise FileExistsError(relative)
        self._snapshot("create_file", [target])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_file(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        self._snapshot("write_file", [target])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def import_file(self, relative: str, content: bytes) -> Path:
        """Write user-selected bytes through the same recoverable sandbox history."""
        target = self.path(relative)
        self._snapshot("import_file", [target])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def read_file(self, relative: str) -> str:
        return self.path(relative).read_text(encoding="utf-8")

    def list_files(self, relative: str = ".") -> list[str]:
        base = self.path(relative)
        if not base.exists():
            return []
        files = []
        for item in base.rglob("*"):
            if item.is_file() and not item.is_symlink():
                rel = item.relative_to(self.root)
                if rel.parts[0] not in {".aura", ".aura-trash"}:
                    files.append(rel.as_posix())
        return sorted(files)

    def search_files(self, query: str, relative: str = ".") -> list[str]:
        needle = query.casefold()
        matches: list[str] = []
        for name in self.list_files(relative):
            if needle in name.casefold():
                matches.append(name)
                continue
            try:
                if needle in self.read_file(name).casefold():
                    matches.append(name)
            except (UnicodeDecodeError, OSError):
                pass
        return matches

    def search_text(self, query: str, relative: str = ".", limit: int = 200) -> list[dict]:
        needle = query.casefold()
        matches: list[dict] = []
        for name in self.list_files(relative):
            try:
                lines = self.read_file(name).splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if needle in line.casefold():
                    matches.append({"path": name, "line": number, "text": line[:500]})
                    if len(matches) >= limit:
                        return matches
        return matches

    def copy_file(self, source: str, destination: str) -> Path:
        src, dst = self.path(source), self.path(destination)
        if not src.is_file():
            raise FileNotFoundError(source)
        self._snapshot("copy_file", [dst])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    def move_file(self, source: str, destination: str) -> Path:
        src, dst = self.path(source), self.path(destination)
        if not src.exists():
            raise FileNotFoundError(source)
        if not src.is_file():
            raise IsADirectoryError(source)
        self._snapshot("move_file", [src, dst])
        dst.parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(src), str(dst)))

    def move_folder(self, source: str, destination: str) -> Path:
        src, dst = self.path(source), self.path(destination)
        if not src.is_dir():
            raise NotADirectoryError(source)
        if dst.exists():
            raise FileExistsError(destination)
        self._ensure_no_links(src)
        src_files = [item for item in src.rglob("*") if item.is_file()]
        dst_files = [dst / item.relative_to(src) for item in src_files]
        self._snapshot("move_folder", src_files + dst_files)
        dst.parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(src), str(dst)))

    def copy_folder(self, source: str, destination: str) -> Path:
        src, dst = self.path(source), self.path(destination)
        if not src.is_dir():
            raise NotADirectoryError(source)
        if dst.exists():
            raise FileExistsError(destination)
        self._ensure_no_links(src)
        src_files = [item for item in src.rglob("*") if item.is_file()]
        dst_files = [dst / item.relative_to(src) for item in src_files]
        self._snapshot("copy_folder", dst_files)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        return dst

    def safe_delete_file(self, relative: str) -> Path:
        src = self.path(relative)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(relative)
        self._snapshot("safe_delete_file", [src])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = "__".join(src.relative_to(self.root).parts)
        dst = self.trash / f"{stamp}__{safe_name}"
        moved = Path(shutil.move(str(src), str(dst)))
        self._record_trash(moved, src.relative_to(self.root).as_posix(), "file")
        return moved

    def safe_delete_folder(self, relative: str) -> Path:
        src = self.path(relative)
        if not src.is_dir():
            raise NotADirectoryError(relative)
        self._ensure_no_links(src)
        src_files = [item for item in src.rglob("*") if item.is_file()]
        self._snapshot("safe_delete_folder", src_files)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = "__".join(src.relative_to(self.root).parts)
        dst = self.trash / f"{stamp}__dir__{safe_name}"
        moved = Path(shutil.move(str(src), str(dst)))
        self._record_trash(moved, src.relative_to(self.root).as_posix(), "folder")
        return moved

    def list_trash(self) -> list[dict]:
        index = self.db.trash_entries()
        items = []
        for child in self.trash.iterdir():
            entry = index.get(child.name)
            stat = child.stat()
            items.append({
                "trash_name": child.name,
                "original_path": entry.get("original_path") if entry else None,
                "kind": entry.get("kind") if entry else ("folder" if child.is_dir() else "file"),
                "deleted_at": entry.get("deleted_at") if entry else None,
                "size": stat.st_size if child.is_file() else None,

            })
        items.sort(key=lambda item: item["trash_name"], reverse=True)
        return items

    def restore_from_trash(self, trash_name: str) -> Path:
        name = str(trash_name).strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise SandboxViolation("Invalid trash entry name")
        trashed = self.trash / name
        if not trashed.exists():
            raise FileNotFoundError(trash_name)
        entry = self.db.trash_entries().get(name)
        original_path = entry.get("original_path") if entry else None
        if not original_path:
            raise ValueError(f"Cannot determine the original location for {trash_name}")
        target = self.path(original_path)
        if target.exists():
            raise FileExistsError(original_path)
        if trashed.is_dir():
            targets = [target / item.relative_to(trashed) for item in trashed.rglob("*") if item.is_file()]
        else:
            targets = [target]
        self._snapshot("restore_from_trash", targets)
        target.parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(trashed), str(target)))

    def compare_files(self, left: str, right: str, context_lines: int = 3) -> dict:
        context = max(0, min(int(context_lines), 20))
        difference = list(difflib.unified_diff(
            self.read_file(left).splitlines(),
            self.read_file(right).splitlines(),
            fromfile=left, tofile=right, n=context, lineterm="",
        ))
        truncated = len(difference) > 2000
        return {"left": left, "right": right,
                "diff": "\n".join(difference[:2000]),
                "different": bool(difference), "truncated": truncated}

    def _record_trash(self, trashed: Path, original_path: str, kind: str) -> None:
        self.db.add_trash({
            "trash_name": trashed.name, "original_path": original_path, "kind": kind,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })

    def _prune_empty_dirs(self, start: Path) -> None:
        """Remove now-empty ancestor directories left behind after archiving a file to trash."""
        current = start
        while current != self.root:
            try:
                if not current.is_dir() or any(current.iterdir()):
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _ensure_no_links(self, root: Path) -> None:
        if self._is_link_like(root):
            raise SandboxViolation("Symbolic links and junctions are not allowed")
        for item in root.rglob("*"):
            if self._is_link_like(item):
                raise SandboxViolation("Symbolic links and junctions are not allowed")

    def create_archive(self, source: str, destination: str) -> Path:
        """Create a recoverable ZIP from a workspace file or folder."""
        src, dst = self.path(source), self.path(destination)
        if not src.exists():
            raise FileNotFoundError(source)
        if dst.suffix.casefold() != ".zip":
            raise ValueError("Archive destination must end in .zip")
        self._snapshot("create_archive", [dst])
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if src.is_file():
                archive.write(src, src.name)
            else:
                for item in sorted(src.rglob("*")):
                    if item == dst or not item.is_file() or self._is_link_like(item):
                        continue
                    relative = item.relative_to(src.parent).as_posix()
                    archive.write(item, relative)
        return dst

    def extract_archive(self, archive_path: str, destination: str) -> list[Path]:
        """Safely extract a ZIP without links, traversal, or unbounded expansion."""
        src, dst = self.path(archive_path), self.path(destination)
        if not src.is_file():
            raise FileNotFoundError(archive_path)
        extracted: list[tuple[zipfile.ZipInfo, Path]] = []
        with zipfile.ZipFile(src) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > 1000:
                raise ValueError("Archive contains more than 1,000 files")
            if sum(info.file_size for info in infos) > 100_000_000:
                raise ValueError("Archive expands beyond Aura's 100 MB limit")
            for info in infos:
                member = Path(info.filename.replace("\\", "/"))
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"Unsafe archive path: {info.filename}")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError(f"Archive links are not allowed: {info.filename}")
                target = self.path(dst.relative_to(self.root) / member)
                extracted.append((info, target))
            self._snapshot("extract_archive", [target for _, target in extracted])
            for info, target in extracted:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return [target for _, target in extracted]

    def undo_last_change(self) -> dict:
        change = self.db.last_undoable_change()
        if not change:
            raise FileNotFoundError("There are no workspace changes to undo")
        return self._restore_change(change)

    def rollback_task(self, task_id: str) -> dict:
        changes = self.db.undoable_task_changes(task_id)
        if not changes:
            raise FileNotFoundError(f"Task {task_id} has no active workspace changes")
        restored: list[str] = []
        operations: list[str] = []
        for change in reversed(changes):
            result = self._restore_change(change)
            restored.extend(result["paths"])
            operations.append(result["undid"])
        return {"task_id": task_id, "changes_undone": len(changes),
                "operations": operations, "paths": restored}

    def rollback_session(self, task_ids: list[str]) -> dict:
        """Undo a whole conversation, newest task first.

        Order is the whole correctness argument: undoing oldest-first would
        restore an early backup and then have a later one overwrite it, leaving
        the workspace in a state that never existed.

        One task failing does not abandon the rest. A conversation is undone as
        far as it can be, and what could not be undone is named — stopping
        halfway without saying so would be the worst of both.
        """
        undone: list[dict] = []
        skipped: list[dict] = []
        paths: list[str] = []
        for task_id in task_ids:
            try:
                result = self.rollback_task(task_id)
            except FileNotFoundError:
                # Nothing left to undo for this one: already rolled back, or it
                # never touched a file. Not a failure worth alarming about.
                skipped.append({"task_id": task_id, "reason": "nothing to undo"})
                continue
            except (OSError, ValueError) as exc:
                skipped.append({"task_id": task_id, "reason": str(exc)})
                continue
            undone.append(result)
            paths.extend(result["paths"])
        return {"tasks_undone": len(undone), "tasks_skipped": len(skipped),
                "changes_undone": sum(item["changes_undone"] for item in undone),
                "paths": paths, "skipped": skipped}

    def _restore_change(self, change: dict) -> dict:
        restored: list[str] = []
        for item in reversed(change["items"]):
            target = self.path(item["path"])
            if target.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                trash_target = self.trash / f"{stamp}__undo__{'__'.join(Path(item['path']).parts)}"
                shutil.move(str(target), str(trash_target))
                self._prune_empty_dirs(target.parent)
            backup = item.get("backup")
            if backup:
                backup_path = self.history / backup
                if not backup_path.is_file():
                    raise FileNotFoundError(f"Undo backup is missing: {backup}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target)
            restored.append(item["path"])
        # Marking the change itself, rather than appending a separate undo
        # record, is what makes a change impossible to undo twice.
        self.db.mark_change_undone(change["id"])
        return {"undid": change["operation"], "paths": restored}

    def change_history(self, limit: int = 20) -> list[dict]:
        return self.db.change_history(max(1, min(limit, 100)))

    def _snapshot(self, operation: str, targets: list[Path]) -> None:
        change_id = uuid4().hex
        items = []
        for index, target in enumerate(targets):
            relative = target.relative_to(self.root).as_posix()
            backup_name = None
            if target.exists():
                if not target.is_file():
                    raise IsADirectoryError(relative)
                backup_name = f"{change_id}_{index}.bak"
                shutil.copy2(target, self.history / backup_name)
            items.append({"path": relative, "backup": backup_name})
        self.db.add_change({
            "id": change_id, "operation": operation,
            "time": datetime.now(timezone.utc).isoformat(), "items": items,
            "task_id": self.active_task_id,
        })

