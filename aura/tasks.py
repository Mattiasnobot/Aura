from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class TaskJournal:
    """Append-only records of requests, tool work, and outcomes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def start(self, request: str) -> str:
        task_id = uuid4().hex[:12]
        self._append({"event": "started", "task_id": task_id, "time": self._now(),
                      "request": request[:4000]})
        return task_id

    def record_tool(self, task_id: str, name: str, arguments: dict, result: dict) -> None:
        safe_result = {key: value for key, value in result.items() if key != "content"}
        self._append({"event": "tool", "task_id": task_id, "time": self._now(),
                      "tool": name, "arguments": arguments, "result": safe_result})

    def finish(self, task_id: str, status: str, summary: str) -> None:
        self._append({"event": "finished", "task_id": task_id, "time": self._now(),
                      "status": status, "summary": summary[:4000]})

    def recent(self, limit: int = 10, *, only_actionable: bool = False,
               active_task_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        tasks: dict[str, dict] = {}
        order: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
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

    def _append(self, event: dict) -> None:
        with self._lock:
            self._trim_if_large()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _trim_if_large(self, max_bytes: int = 2_000_000, keep_bytes: int = 1_000_000) -> None:
        """Keep the journal from growing without bound; recent() only ever needs the tail.

        Trimming cuts on a line boundary: slicing by character count left a
        half-written JSON object as the first line, which was then silently
        discarded on every read.
        """
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
            if size <= max_bytes:
                return
            with self.path.open("rb") as handle:
                handle.seek(size - keep_bytes)
                handle.readline()  # drop the partial line
                kept = handle.read()
            self.path.write_bytes(kept)
        except OSError:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
