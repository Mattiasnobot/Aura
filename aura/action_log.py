from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class ActionLog:
    def __init__(self, path: Path, on_event: Callable[[dict], None] | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event
        self._lock = threading.Lock()

    def record(self, action: str, status: str = "ok", **details: object) -> dict:
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "status": status,
            **details,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.on_event:
            self.on_event(event)
        return event

    def recent(self, limit: int = 60) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        events: list[dict] = []
        for line in lines[-max(1, min(int(limit), 250)):]:
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError:
                continue
        return events
