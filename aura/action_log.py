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
            self._trim_if_large()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.on_event:
            self.on_event(event)
        return event

    # The audit log only ever grows, and `recent` runs on every panel refresh,
    # so it reads the tail instead of the whole file and the file is capped.
    MAX_BYTES = 4_000_000
    KEEP_BYTES = 2_000_000
    TAIL_BYTES = 200_000

    def _tail(self) -> list[str]:
        size = self.path.stat().st_size
        with self.path.open("rb") as handle:
            if size > self.TAIL_BYTES:
                handle.seek(size - self.TAIL_BYTES)
                handle.readline()  # discard the partial first line
            data = handle.read()
        return data.decode("utf-8", errors="replace").splitlines()

    def _trim_if_large(self) -> None:
        """Cap the log, always cutting on a line boundary."""
        try:
            if self.path.stat().st_size <= self.MAX_BYTES:
                return
            with self.path.open("rb") as handle:
                handle.seek(self.path.stat().st_size - self.KEEP_BYTES)
                handle.readline()
                kept = handle.read()
            self.path.write_bytes(kept)
        except OSError:
            pass

    def recent(self, limit: int = 60) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self._tail()
        events: list[dict] = []
        for line in lines[-max(1, min(int(limit), 250)):]:
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError:
                continue
        return events
