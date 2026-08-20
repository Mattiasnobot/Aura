from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .store import Database


class TaskJournal:
    """Records of requests, tool work, and outcomes, stored in the local database."""

    def __init__(self, path: Path | Database) -> None:
        if isinstance(path, Database):
            self.db = path
            self._owns_db = False
        else:
            self.db = Database(Path(path).parent / "aura.db")
            self._owns_db = True

    def start(self, request: str, session_id: str = "") -> str:
        task_id = uuid4().hex[:12]
        self.db.add_task_event({"event": "started", "task_id": task_id, "time": self._now(),
                                "request": request[:4000],
                                "session_id": str(session_id) or None})
        return task_id

    def record_tool(self, task_id: str, name: str, arguments: dict, result: dict) -> None:
        safe_result = {key: value for key, value in result.items() if key != "content"}
        self.db.add_task_event({"event": "tool", "task_id": task_id, "time": self._now(),
                                "tool": name, "arguments": arguments, "result": safe_result})

    def finish(self, task_id: str, status: str, summary: str) -> None:
        self.db.add_task_event({"event": "finished", "task_id": task_id, "time": self._now(),
                                "status": status, "summary": summary[:4000]})

    def recent(self, limit: int = 10, *, only_actionable: bool = False,
               active_task_id: str | None = None) -> list[dict]:
        tasks: dict[str, dict] = {}
        order: list[str] = []
        for event in self.db.task_events(max(1, int(limit))):
            task_id = event.get("task_id")
            if not task_id:
                continue
            if task_id not in tasks:
                tasks[task_id] = {"task_id": task_id, "tools": [], "tool_details": [],
                                  "status": "running"}
                order.append(task_id)
            task = tasks[task_id]
            if event.get("event") == "started":
                task.update({"request": event.get("request", ""), "started": event.get("time")})
            elif event.get("event") == "tool":
                task["tools"].append(event.get("tool"))
                result = event.get("result", {}) if isinstance(event.get("result"), dict) else {}
                task["tool_details"].append({
                    "tool": event.get("tool"),
                    "time": event.get("time"),
                    "arguments": event.get("arguments", {}),
                    "result": {key: result[key] for key in (
                        "ok", "valid", "path", "files_seen", "error", "succeeded", "returncode",
                    ) if key in result},
                })
            elif event.get("event") == "finished":
                task.update({"status": event.get("status"), "summary": event.get("summary", ""),
                             "finished": event.get("time")})
        selected = [tasks[task_id] for task_id in reversed(order[-limit:])]
        for task in selected:
            # A "running" task with no active process behind it did not finish
            # normally — the server was restarted or crashed mid-request. Say so
            # instead of implying work is still happening.
            if task["status"] == "running" and task["task_id"] != active_task_id:
                task["status"] = "interrupted"
                if not task.get("summary"):
                    task["summary"] = ("Aura stopped or restarted before this task finished. "
                                       "No further changes were made automatically.")
            task["project"] = self._infer_project(task["tool_details"])
        if only_actionable:
            selected = [task for task in selected if task["tools"]]
        return selected

    def task(self, task_id: str) -> dict | None:
        """One task by id, in the same shape `recent()` produces."""
        events = self.db.task_events_for(str(task_id))
        if not events:
            return None
        task: dict = {"task_id": str(task_id), "tools": [], "tool_details": [],
                      "status": "running"}
        for event in events:
            if event.get("event") == "started":
                task.update({"request": event.get("request", ""), "started": event.get("time")})
            elif event.get("event") == "tool":
                task["tools"].append(event.get("tool"))
                result = event.get("result", {}) if isinstance(event.get("result"), dict) else {}
                task["tool_details"].append({
                    "tool": event.get("tool"), "time": event.get("time"),
                    "arguments": event.get("arguments", {}), "result": result,
                })
            elif event.get("event") == "finished":
                task.update({"status": event.get("status"), "summary": event.get("summary", ""),
                             "finished": event.get("time")})
        if task["status"] == "running":
            task["status"] = "interrupted"
        task["project"] = self._infer_project(task["tool_details"])
        return task

    @staticmethod
    def _infer_project(tool_details: list[dict]) -> str | None:
        """Group a task by the top-level workspace folder its first mutated path lands in."""
        for detail in tool_details:
            arguments = detail.get("arguments") or {}
            path = arguments.get("path") or arguments.get("destination")
            if not path:
                continue
            normalized = str(path).replace("\\", "/").strip("/")
            if "/" in normalized:
                return normalized.split("/", 1)[0]
        return None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
