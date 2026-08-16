from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    rowid_alias INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS task_events (
    rowid_alias INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event TEXT NOT NULL,
    time TEXT NOT NULL,
    request TEXT,
    tool TEXT,
    arguments TEXT,
    result TEXT,
    status TEXT,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events (task_id);

-- An undo is a column on the change, not a separate row. A tombstone row could
-- be deleted independently of its change, which would make an already-undone
-- change undoable again and overwrite good files with stale backups.
CREATE TABLE IF NOT EXISTS changes (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    time TEXT NOT NULL,
    task_id TEXT,
    undone_at TEXT
);
CREATE INDEX IF NOT EXISTS changes_task ON changes (task_id);

CREATE TABLE IF NOT EXISTS change_items (
    change_id TEXT NOT NULL REFERENCES changes (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    backup TEXT
);
CREATE INDEX IF NOT EXISTS change_items_change ON change_items (change_id);

CREATE TABLE IF NOT EXISTS trash (
    trash_name TEXT PRIMARY KEY,
    original_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_changes (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    backup TEXT,
    created INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    time TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
"""

MIGRATED_SUFFIX = ".migrated"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Database:
    """One local SQLite file behind Aura's journals and recovery records.

    Standard library only, and an embedded file rather than a server, so it
    keeps Aura dependency-free. Settings, personal memory, and permissions stay
    as JSON on purpose: they are small, hand-editable, and exportable.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        """A short-lived connection per operation.

        Holding one open connection meant the file was never released, which on
        Windows blocks deleting the workspace and leaves the handle dangling for
        the life of the process. Opening per call costs microseconds against an
        existing file and removes the lifecycle and cross-thread problems.
        """
        with self._lock:
            connection = sqlite3.connect(str(self.path), timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                yield connection
                connection.commit()
            finally:
                connection.close()

    def close(self) -> None:
        """Kept for symmetry: connections never outlive an operation."""
        return None

    def _execute(self, sql: str, parameters: tuple = ()) -> int:
        with self._connect() as connection:
            return connection.execute(sql, parameters).rowcount

    def _query(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(connection.execute(sql, parameters))

    # --------------------------------------------------------------- actions

    def add_action(self, event: dict) -> None:
        details = {key: value for key, value in event.items()
                   if key not in {"time", "action", "status"}}
        self._execute(
            "INSERT INTO actions (time, action, status, details) VALUES (?, ?, ?, ?)",
            (str(event.get("time")), str(event.get("action")), str(event.get("status")),
             json.dumps(details, ensure_ascii=False)),
        )

    def recent_actions(self, limit: int) -> list[dict]:
        rows = self._query(
            "SELECT time, action, status, details FROM actions "
            "ORDER BY rowid_alias DESC LIMIT ?", (int(limit),))
        events: list[dict] = []
        for row in reversed(rows):
            try:
                details = json.loads(row["details"])
            except (json.JSONDecodeError, TypeError):
                details = {}
            events.append({"time": row["time"], "action": row["action"],
                           "status": row["status"], **details})
        return events

    # ----------------------------------------------------------- task events

    def add_task_event(self, event: dict) -> None:
        self._execute(
            "INSERT INTO task_events "
            "(task_id, event, time, request, tool, arguments, result, status, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(event.get("task_id")), str(event.get("event")), str(event.get("time")),
             event.get("request"), event.get("tool"),
             json.dumps(event.get("arguments"), ensure_ascii=False)
             if event.get("arguments") is not None else None,
             json.dumps(event.get("result"), ensure_ascii=False)
             if event.get("result") is not None else None,
             event.get("status"), event.get("summary")),
        )

    def task_events(self, task_limit: int) -> list[dict]:
        """Every event for the most recent `task_limit` tasks, oldest first."""
        recent = self._query(
            "SELECT task_id, MAX(rowid_alias) AS last_seen FROM task_events "
            "GROUP BY task_id ORDER BY last_seen DESC LIMIT ?", (int(task_limit),))
        if not recent:
            return []
        ids = [row["task_id"] for row in recent]
        placeholders = ",".join("?" for _ in ids)
        rows = self._query(
            f"SELECT * FROM task_events WHERE task_id IN ({placeholders}) "
            "ORDER BY rowid_alias", tuple(ids))
        events: list[dict] = []
        for row in rows:
            event = {"task_id": row["task_id"], "event": row["event"], "time": row["time"]}
            for key in ("request", "tool", "status", "summary"):
                if row[key] is not None:
                    event[key] = row[key]
            for key in ("arguments", "result"):
                if row[key] is not None:
                    try:
                        event[key] = json.loads(row[key])
                    except json.JSONDecodeError:
                        event[key] = {}
            events.append(event)
        return events

    def task_events_for(self, task_id: str) -> list[dict]:
        """Every event of one task, oldest first."""
        rows = self._query(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY rowid_alias",
            (str(task_id),))
        events: list[dict] = []
        for row in rows:
            event = {"task_id": row["task_id"], "event": row["event"], "time": row["time"]}
            for key in ("request", "tool", "status", "summary"):
                if row[key] is not None:
                    event[key] = row[key]
            for key in ("arguments", "result"):
                if row[key] is not None:
                    try:
                        event[key] = json.loads(row[key])
                    except json.JSONDecodeError:
                        event[key] = {}
            events.append(event)
        return events

    # --------------------------------------------------------------- changes

    def add_change(self, change: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO changes (id, operation, time, task_id, undone_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (str(change["id"]), str(change.get("operation")), str(change.get("time")),
                 change.get("task_id")))
            connection.executemany(
                "INSERT INTO change_items (change_id, position, path, backup) "
                "VALUES (?, ?, ?, ?)",
                [(str(change["id"]), index, str(item.get("path")), item.get("backup"))
                 for index, item in enumerate(change.get("items", []))])

    def _change_row(self, row: sqlite3.Row) -> dict:
        items = self._query(
            "SELECT path, backup FROM change_items WHERE change_id = ? ORDER BY position",
            (row["id"],))
        return {"id": row["id"], "operation": row["operation"], "time": row["time"],
                "task_id": row["task_id"], "undone_at": row["undone_at"],
                "items": [{"path": item["path"], "backup": item["backup"]} for item in items]}

    def last_undoable_change(self) -> dict | None:
        rows = self._query(
            "SELECT * FROM changes WHERE undone_at IS NULL "
            "ORDER BY rowid DESC LIMIT 1")
        return self._change_row(rows[0]) if rows else None

    def undoable_task_changes(self, task_id: str) -> list[dict]:
        rows = self._query(
            "SELECT * FROM changes WHERE task_id = ? AND undone_at IS NULL ORDER BY rowid",
            (str(task_id),))
        return [self._change_row(row) for row in rows]

    def mark_change_undone(self, change_id: str) -> None:
        self._execute("UPDATE changes SET undone_at = ? WHERE id = ?", (_now(), str(change_id)))

    def change_history(self, limit: int) -> list[dict]:
        rows = self._query("SELECT * FROM changes ORDER BY rowid DESC LIMIT ?", (int(limit),))
        history = []
        for row in rows:
            change = self._change_row(row)
            history.append({
                "id": change["id"], "operation": change["operation"], "time": change["time"],
                "paths": [item["path"] for item in change["items"]],
                "undone": change["undone_at"] is not None, "task_id": change["task_id"],
            })
        return history

    # ----------------------------------------------------------------- trash

    def add_trash(self, entry: dict) -> None:
        self._execute(
            "INSERT OR REPLACE INTO trash (trash_name, original_path, kind, deleted_at) "
            "VALUES (?, ?, ?, ?)",
            (str(entry["trash_name"]), str(entry["original_path"]),
             str(entry["kind"]), str(entry["deleted_at"])))

    def trash_entries(self) -> dict[str, dict]:
        return {row["trash_name"]: {"trash_name": row["trash_name"],
                                    "original_path": row["original_path"],
                                    "kind": row["kind"], "deleted_at": row["deleted_at"]}
                for row in self._query("SELECT * FROM trash")}

    def remove_trash(self, trash_name: str) -> None:
        self._execute("DELETE FROM trash WHERE trash_name = ?", (str(trash_name),))

    # ------------------------------------------------------ external changes

    def add_external_change(self, record: dict) -> None:
        self._execute(
            "INSERT OR REPLACE INTO external_changes "
            "(id, path, backup, created, task_id, time, undone) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(record["id"]), str(record["path"]), record.get("backup"),
             1 if record.get("created") else 0, record.get("task_id"),
             str(record.get("time")), 1 if record.get("undone") else 0))

    def external_changes(self, limit: int) -> list[dict]:
        rows = self._query("SELECT * FROM external_changes ORDER BY rowid DESC LIMIT ?",
                           (int(limit),))
        return [{"id": row["id"], "path": row["path"], "backup": row["backup"],
                 "created": bool(row["created"]), "task_id": row["task_id"],
                 "time": row["time"], "undone": bool(row["undone"])}
                for row in reversed(rows)]

    def last_external_change(self) -> dict | None:
        rows = self._query(
            "SELECT * FROM external_changes WHERE undone = 0 ORDER BY rowid DESC LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        return {"id": row["id"], "path": row["path"], "backup": row["backup"],
                "created": bool(row["created"]), "task_id": row["task_id"],
                "time": row["time"], "undone": False}

    def mark_external_undone(self, change_id: str) -> None:
        self._execute("UPDATE external_changes SET undone = 1 WHERE id = ?", (str(change_id),))

    # ------------------------------------------------------------- retention

    def referenced_backups(self) -> set[str]:
        """Every backup filename still reachable from either recovery table."""
        names = {row["backup"] for row in
                 self._query("SELECT backup FROM change_items WHERE backup IS NOT NULL")}
        names |= {row["backup"] for row in
                  self._query("SELECT backup FROM external_changes WHERE backup IS NOT NULL")}
        return {str(name) for name in names if name}

    def sweep(self, history: Path, trash: Path, *, days: int = 30,
              max_changes: int = 500) -> dict:
        """Expire old recovery records, then delete what nothing references.

        A change, its items, and its backups always go together, in one
        transaction — the whole point of moving this out of flat files.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        keep = max(1, int(max_changes))
        with self._connect() as connection:
            # Two plain statements rather than one clever compound: an ORDER BY
            # inside a UNION applies to the whole compound select, not the arm.
            expired = connection.execute(
                "DELETE FROM changes WHERE time < ?", (cutoff,)).rowcount
            expired += connection.execute(
                "DELETE FROM changes WHERE id NOT IN "
                "(SELECT id FROM (SELECT id FROM changes ORDER BY rowid DESC LIMIT ?))",
                (keep,)).rowcount
            expired_external = connection.execute(
                "DELETE FROM external_changes WHERE time < ?", (cutoff,)).rowcount

        referenced = self.referenced_backups()
        freed = 0
        if history.is_dir():
            for candidate in history.iterdir():
                if candidate.is_file() and candidate.name not in referenced:
                    try:
                        candidate.unlink()
                        freed += 1
                    except OSError:
                        continue

        # Trash is swept by age only: an undo moves the displaced file here
        # without recording a row, so a reference-based sweep would delete
        # files the user can still restore.
        trashed = 0
        limit = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        if trash.is_dir():
            for child in trash.iterdir():
                try:
                    modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
                except OSError:
                    continue
                if modified >= limit:
                    continue
                try:
                    if child.is_dir():
                        import shutil
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    continue
                self.remove_trash(child.name)
                trashed += 1

        return {"changes_expired": max(0, expired),
                "external_expired": max(0, expired_external),
                "backups_freed": freed, "trash_removed": trashed}

    # ------------------------------------------------------------- migration

    def migrate_jsonl(self, meta: Path) -> dict:
        """Import the old flat journals once, keeping the originals as evidence."""
        sources = {
            "actions": meta / "action-log.jsonl",
            "tasks": meta / "tasks.jsonl",
            "changes": meta / "changes.jsonl",
            "trash": meta / "trash.jsonl",
            "external": meta / "external-changes.jsonl",
        }
        if not any(path.is_file() for path in sources.values()):
            return {}
        counts: dict[str, int] = {}

        def rows(path: Path):
            if not path.is_file():
                return
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    yield entry

        for entry in rows(sources["actions"]):
            self.add_action(entry)
            counts["actions"] = counts.get("actions", 0) + 1
        for entry in rows(sources["tasks"]):
            if entry.get("task_id"):
                self.add_task_event(entry)
                counts["task_events"] = counts.get("task_events", 0) + 1

        # Two passes: store the changes, then fold each old tombstone into the
        # undone_at column of the change it referred to.
        undo_of: list[str] = []
        for entry in rows(sources["changes"]):
            if entry.get("operation") == "undo":
                if entry.get("undo_of"):
                    undo_of.append(str(entry["undo_of"]))
                continue
            if entry.get("id"):
                self.add_change(entry)
                counts["changes"] = counts.get("changes", 0) + 1
        for change_id in undo_of:
            self.mark_change_undone(change_id)
            counts["undone"] = counts.get("undone", 0) + 1

        for entry in rows(sources["trash"]):
            if entry.get("trash_name"):
                self.add_trash(entry)
                counts["trash"] = counts.get("trash", 0) + 1
        for entry in rows(sources["external"]):
            if entry.get("id"):
                self.add_external_change(entry)
                counts["external"] = counts.get("external", 0) + 1

        for path in sources.values():
            if path.is_file():
                path.replace(path.with_suffix(path.suffix + MIGRATED_SUFFIX))
        return counts
