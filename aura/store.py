from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


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

CREATE TABLE IF NOT EXISTS messages (
    rowid_alias INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages (session_id);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    started TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

-- Work Aura may do without being asked. `next_run` is UTC ISO; a row with
-- `every_minutes = 0` runs once and disables itself.
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    request TEXT NOT NULL,
    every_minutes INTEGER NOT NULL DEFAULT 0,
    next_run TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL,
    last_run TEXT,
    last_outcome TEXT,
    runs INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_tasks (enabled, next_run);

-- Work a background check would like to do but must not do on its own. A
-- proposal never runs by itself: it waits here until the user approves it, and
-- is then submitted as an ordinary foreground request.
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    finding TEXT NOT NULL,
    request TEXT NOT NULL,
    created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals (status, created);

-- The plan, as state rather than prose. `PLAN.md` stays the readable record
-- Mat can edit; this is the part a turn can resume from, because "what is left
-- to do" has to survive the conversation that decided it.
CREATE TABLE IF NOT EXISTS plan_steps (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    -- todo -> doing -> done, or blocked with a reason. Blocked is not failure:
    -- it is a step that cannot proceed until something outside it changes.
    status TEXT NOT NULL DEFAULT 'todo',
    evidence TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plan_steps_project ON plan_steps (project, position);

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
            self._migrate(connection)

    #: Ordered, append-only. Each entry is applied once, in order, to a database
    #: whose `user_version` is below its index+1, and `user_version` is then set
    #: to that number in the same transaction. `CREATE TABLE IF NOT EXISTS` gets
    #: a *new* database to the current shape but says nothing about changing one
    #: that already holds the user's data — that is what this is for.
    #:
    #: Rules: never edit a migration that has shipped, never renumber, and add
    #: only statements that are safe to run against real data.
    MIGRATIONS: tuple[tuple[str, ...], ...] = (
        # 1 — baseline. Everything up to this point was created by SCHEMA, so
        # this records the version without changing anything.
        (),
        # 2 — `scheduled_tasks` arrived. Worth being straight about what this
        # does: SCHEMA runs `CREATE TABLE IF NOT EXISTS` on every open, so a new
        # table reaches old databases without any migration at all. The entry
        # earns its keep by making the version number mean something — anything
        # that needs to know whether this database has schedules can ask, rather
        # than probing for a table. The mechanism becomes load-bearing the first
        # time a *column* changes, which no `IF NOT EXISTS` can do for us.
        (),
        # 3 — `proposals` arrived, on the same terms as 2: the table reaches an
        # existing database through SCHEMA, and the version is what lets code
        # ask whether this database understands proposals.
        (),
        # 5 — `plan_steps` arrived, reaching existing databases through SCHEMA
        # like 2 and 3 before it. The version is what lets code ask whether this
        # database can hold a resumable plan at all.
        # 4 — the first migration that earns the mechanism. `task_events` gained
        # `session_id`, and no `CREATE TABLE IF NOT EXISTS` can add a column to a
        # table that already holds the user's history. Without it there is no way
        # to say which work belongs to which conversation, which is precisely
        # what "undo this conversation" has to know.
        #
        # Deliberately not backfilled: old rows keep NULL, so a conversation
        # from before this reports that it does not know rather than matching by
        # timestamp. Guessing is the one thing an action this destructive must
        # never do.
        ("ALTER TABLE task_events ADD COLUMN session_id TEXT",),
    )

    def _migrate(self, connection: sqlite3.Connection) -> int:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        target = len(self.MIGRATIONS)
        if current >= target:
            return current
        for index in range(current, target):
            for statement in self.MIGRATIONS[index]:
                try:
                    connection.execute(statement)
                except sqlite3.OperationalError as exc:
                    # Every migration up to 3 was empty, so re-running one was
                    # free and this never came up. `ADD COLUMN` has no
                    # `IF NOT EXISTS`, so a database whose version was reset —
                    # or restored oddly — would refuse to open at all. Applying
                    # an already-applied column is a success, not a failure.
                    if "duplicate column name" not in str(exc).casefold():
                        raise
        # PRAGMA does not accept a bound parameter, and `target` is a length.
        connection.execute(f"PRAGMA user_version = {int(target)}")
        return target

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

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

    def failed_actions(self, limit: int) -> list[dict]:
        """Only what went wrong, newest first — the part worth reading first."""
        rows = self._query(
            "SELECT time, action, status, details FROM actions "
            "WHERE status <> 'ok' ORDER BY rowid_alias DESC LIMIT ?", (int(limit),))
        events: list[dict] = []
        for row in rows:
            try:
                details = json.loads(row["details"])
            except (json.JSONDecodeError, TypeError):
                details = {}
            events.append({"time": row["time"], "action": row["action"],
                           "status": row["status"], **details})
        return events

    def summary(self) -> dict:
        """Row counts and file size, for a diagnostics report.

        Counts only: nothing here reveals what was said or remembered.
        """
        counts = {}
        for table in ("actions", "task_events", "changes", "change_items", "trash",
                      "external_changes", "sessions", "messages"):
            rows = self._query(f"SELECT COUNT(*) AS total FROM {table}")
            counts[table] = int(rows[0]["total"]) if rows else 0
        size = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                size += candidate.stat().st_size
        return {"counts": counts, "bytes": size,
                "undone_changes": int(self._query(
                    "SELECT COUNT(*) AS total FROM changes WHERE undone_at IS NOT NULL"
                )[0]["total"])}

    # ----------------------------------------------------------- task events

    def add_task_event(self, event: dict) -> None:
        self._execute(
            "INSERT INTO task_events "
            "(task_id, event, time, request, tool, arguments, result, status, summary, "
            "session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(event.get("task_id")), str(event.get("event")), str(event.get("time")),
             event.get("request"), event.get("tool"),
             json.dumps(event.get("arguments"), ensure_ascii=False)
             if event.get("arguments") is not None else None,
             json.dumps(event.get("result"), ensure_ascii=False)
             if event.get("result") is not None else None,
             event.get("status"), event.get("summary"), event.get("session_id")),
        )

    def undoable_paths_for_tasks(self, task_ids: list[str]) -> list[str]:
        """Which files a rollback would touch, so it can be shown before it runs."""
        if not task_ids:
            return []
        marks = ",".join("?" for _ in task_ids)
        rows = self._query(
            "SELECT DISTINCT i.path FROM change_items i JOIN changes c ON c.id = i.change_id "
            f"WHERE c.task_id IN ({marks}) AND c.undone_at IS NULL ORDER BY i.path",
            tuple(str(item) for item in task_ids))
        return [str(row["path"]) for row in rows]

    def tasks_for_session(self, session_id: str) -> list[str]:
        """Task ids started in one conversation, newest first.

        Rows from before `session_id` existed carry NULL and are simply not
        returned — a conversation that predates the column reports nothing
        rather than claiming someone else's work.
        """
        rows = self._query(
            "SELECT DISTINCT task_id, MAX(rowid_alias) AS last_seen FROM task_events "
            "WHERE session_id = ? AND task_id IS NOT NULL "
            "GROUP BY task_id ORDER BY last_seen DESC", (str(session_id),))
        return [str(row["task_id"]) for row in rows]

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

    # -------------------------------------------------------------- sessions

    def start_session(self, session_id: str, title: str | None = None) -> None:
        self._execute(
            "INSERT OR IGNORE INTO sessions (id, title, started) VALUES (?, ?, ?)",
            (str(session_id), title, _now()))

    def add_message(self, session_id: str, role: str, text: str, time: str) -> None:
        self.start_session(session_id)
        self._execute(
            "INSERT INTO messages (session_id, role, text, time) VALUES (?, ?, ?, ?)",
            (str(session_id), str(role), str(text), str(time)))
        # The first thing said names the conversation until the user renames it.
        rows = self._query("SELECT title FROM sessions WHERE id = ?", (str(session_id),))
        if rows and not rows[0]["title"] and str(role) == "user":
            self.set_session_title(session_id, str(text).strip().splitlines()[0][:80])

    def set_session_title(self, session_id: str, title: str) -> None:
        self._execute("UPDATE sessions SET title = ? WHERE id = ?",
                      (str(title), str(session_id)))

    def session_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT role, text, time FROM messages WHERE session_id = ? "
            "ORDER BY rowid_alias DESC LIMIT ?", (str(session_id), int(limit)))
        return [{"role": row["role"], "text": row["text"], "time": row["time"]}
                for row in reversed(rows)]

    def sessions(self, limit: int = 30, include_archived: bool = False) -> list[dict]:
        """List conversations worth showing, newest activity first.

        Sessions with no messages are left out: a launch or a `New` click that
        was never used is not a conversation, and listing those empties would
        push real conversations past the limit.
        """
        rows = self._query(
            "SELECT s.id, s.title, s.started, s.archived, "
            "       COUNT(m.rowid_alias) AS messages, MAX(m.time) AS last_used "
            "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id "
            "WHERE (? OR s.archived = 0) "
            "GROUP BY s.id HAVING messages > 0 "
            "ORDER BY COALESCE(MAX(m.time), s.started) DESC LIMIT ?",
            (1 if include_archived else 0, int(limit)))
        return [{"id": row["id"], "title": row["title"], "started": row["started"],
                 "archived": bool(row["archived"]), "messages": int(row["messages"] or 0),
                 "last_used": row["last_used"]} for row in rows]

    @staticmethod
    def _like_literal(term: str) -> str:
        """Escape a user's search term so `%`, `_`, and `\\` match themselves."""
        for character in ("\\", "%", "_"):
            term = term.replace(character, "\\" + character)
        return term

    def search_messages(self, query: str, limit: int = 20,
                        include_archived: bool = False) -> list[dict]:
        """Find conversations containing every word of the query.

        Every term must appear somewhere in the same message, which is what
        people expect from a search box; results come back newest first with
        the matching lines, so a conversation is recognisable without opening it.
        """
        terms = [term for term in str(query).split() if term]
        if not terms:
            return []
        condition = " AND ".join("m.text LIKE ? ESCAPE '\\'" for _ in terms)
        rows = self._query(
            "SELECT m.session_id, m.role, m.text, m.time, s.title, s.archived "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            f"WHERE {condition} AND (? OR s.archived = 0) "
            "ORDER BY m.rowid_alias DESC LIMIT ?",
            tuple(f"%{self._like_literal(term)}%" for term in terms)
            + (1 if include_archived else 0, max(int(limit), 1) * 20))

        found: dict[str, dict] = {}
        for row in rows:
            session = found.setdefault(row["session_id"], {
                "id": row["session_id"], "title": row["title"],
                "archived": bool(row["archived"]), "hits": 0, "matches": []})
            session["hits"] += 1
            if len(session["matches"]) < 3:
                session["matches"].append({
                    "role": row["role"], "time": row["time"],
                    "snippet": self._snippet(row["text"], terms[0])})
            if len(found) >= int(limit) and row["session_id"] not in found:
                break
        return list(found.values())[:int(limit)]

    @staticmethod
    def _snippet(text: str, term: str, width: int = 160) -> str:
        """A readable window around the first match, not the first 160 characters."""
        flat = " ".join(str(text).split())
        found = flat.casefold().find(term.casefold())
        if found < 0 or len(flat) <= width:
            return flat[:width] + ("…" if len(flat) > width else "")
        start = max(0, found - width // 3)
        end = min(len(flat), start + width)
        return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


    # ------------------------------------------------------------- proposals

    def add_proposal(self, source: str, finding: str, request: str) -> dict:
        identifier = uuid4().hex[:12]
        self._execute(
            "INSERT INTO proposals (id, source, finding, request, created, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (identifier, str(source), str(finding)[:600], str(request)[:1200], _now()))
        return self.proposal(identifier)

    def proposal(self, identifier: str) -> dict | None:
        rows = self._query("SELECT * FROM proposals WHERE id = ?", (str(identifier),))
        return dict(rows[0]) if rows else None

    def proposals(self, status: str = "pending", limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT * FROM proposals WHERE (? = '' OR status = ?) "
            "ORDER BY created DESC LIMIT ?", (str(status), str(status), int(limit)))
        return [dict(row) for row in rows]

    def pending_proposal_for(self, source: str, request: str) -> dict | None:
        """So a repeating check does not stack the same proposal every hour."""
        rows = self._query(
            "SELECT * FROM proposals WHERE status = 'pending' AND source = ? AND request = ?",
            (str(source), str(request)[:1200]))
        return dict(rows[0]) if rows else None

    def decide_proposal(self, identifier: str, status: str) -> None:
        self._execute("UPDATE proposals SET status = ?, decided_at = ? WHERE id = ?",
                      (str(status), _now(), str(identifier)))

    # ------------------------------------------------------------- schedules

    #: The four states a step can be in. `blocked` is deliberately not `failed`:
    #: a step waiting on something outside itself has not gone wrong.
    STEP_STATES = ("todo", "doing", "done", "blocked")

    def plan_steps(self, project: str) -> list[dict]:
        return [dict(row) for row in self._query(
            "SELECT * FROM plan_steps WHERE project = ? ORDER BY position",
            (str(project),))]

    def set_plan_steps(self, project: str, steps: list[str]) -> list[dict]:
        """Replace a project's plan, keeping what is already finished.

        A re-planned project should not forget the work already done — that is
        exactly the amnesia this table exists to end. A step whose text matches
        one already recorded keeps its status and its evidence.
        """
        existing = {row["text"]: row for row in self.plan_steps(project)}
        self._execute("DELETE FROM plan_steps WHERE project = ?", (str(project),))
        written = []
        for position, text in enumerate(steps):
            text = str(text).strip()
            if not text:
                continue
            was = existing.get(text)
            identifier = uuid4().hex[:12]
            self._execute(
                "INSERT INTO plan_steps (id, project, position, text, status, "
                "evidence, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, str(project), position, text,
                 str(was["status"]) if was else "todo",
                 str(was["evidence"]) if was else "",
                 str(was["created"]) if was else _now(), _now()))
            written.append(identifier)
        return self.plan_steps(project)

    def set_step_status(self, identifier: str, status: str, evidence: str = "") -> dict | None:
        if str(status) not in self.STEP_STATES:
            raise ValueError(f"status must be one of {', '.join(self.STEP_STATES)}")
        self._execute(
            "UPDATE plan_steps SET status = ?, evidence = ?, updated = ? WHERE id = ?",
            (str(status), str(evidence), _now(), str(identifier)))
        rows = self._query("SELECT * FROM plan_steps WHERE id = ?", (str(identifier),))
        return dict(rows[0]) if rows else None

    def next_plan_step(self, project: str) -> dict | None:
        """The step to pick up: whatever was started, else the first not begun.

        A step left in `doing` comes back first, because a turn that stopped
        mid-step is the case this whole table exists for.
        """
        steps = self.plan_steps(project)
        return (next((s for s in steps if s["status"] == "doing"), None)
                or next((s for s in steps if s["status"] == "todo"), None))

    def add_scheduled(self, kind: str, request: str, *, every_minutes: int = 0,
                      next_run: str | None = None) -> dict:
        identifier = uuid4().hex[:12]
        self._execute(
            "INSERT INTO scheduled_tasks (id, kind, request, every_minutes, next_run, "
            "enabled, created) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (identifier, str(kind), str(request), max(0, int(every_minutes)),
             str(next_run or _now()), _now()))
        return self.scheduled_task(identifier)

    def scheduled_task(self, identifier: str) -> dict | None:
        rows = self._query("SELECT * FROM scheduled_tasks WHERE id = ?", (str(identifier),))
        return dict(rows[0]) if rows else None

    def scheduled_tasks(self, include_disabled: bool = True) -> list[dict]:
        rows = self._query(
            "SELECT * FROM scheduled_tasks WHERE (? OR enabled = 1) ORDER BY next_run",
            (1 if include_disabled else 0,))
        return [dict(row) for row in rows]

    def due_scheduled_tasks(self, moment: str | None = None, limit: int = 5) -> list[dict]:
        rows = self._query(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run <= ? "
            "ORDER BY next_run LIMIT ?", (str(moment or _now()), int(limit)))
        return [dict(row) for row in rows]

    def record_scheduled_run(self, identifier: str, outcome: str,
                             next_run: str | None) -> None:
        """Write the outcome and when it should happen again.

        `next_run = None` disables the row, which is how a one-off retires
        itself rather than staying permanently due.
        """
        self._execute(
            "UPDATE scheduled_tasks SET last_run = ?, last_outcome = ?, runs = runs + 1, "
            "next_run = COALESCE(?, next_run), enabled = ? WHERE id = ?",
            (_now(), str(outcome)[:400], next_run, 1 if next_run else 0, str(identifier)))

    def enable_scheduled_task(self, identifier: str, enabled: bool = True) -> None:
        self._execute("UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                      (1 if enabled else 0, str(identifier)))

    def delete_scheduled_task(self, identifier: str) -> None:
        self._execute("DELETE FROM scheduled_tasks WHERE id = ?", (str(identifier),))

    def archive_session(self, session_id: str, archived: bool = True) -> None:
        self._execute("UPDATE sessions SET archived = ? WHERE id = ?",
                      (1 if archived else 0, str(session_id)))

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
            # Session rows left behind by older builds, which wrote one per
            # launch. A conversation is its messages; an empty row is not one,
            # and `add_message` recreates the row the moment anything is said.
            empty_sessions = connection.execute(
                "DELETE FROM sessions WHERE id NOT IN "
                "(SELECT DISTINCT session_id FROM messages)").rowcount

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
                "backups_freed": freed, "trash_removed": trashed,
                "empty_sessions_removed": max(0, empty_sessions)}

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
