from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .store import Database


class ActionLog:
    """Durable audit trail of everything Aura did, stored in the local database."""

    def __init__(self, path: Path | Database,
                 on_event: Callable[[dict], None] | None = None) -> None:
        # A Path keeps the original call style working (and is what the tests
        # use); the agent passes the shared Database so one connection serves
        # every journal.
        if isinstance(path, Database):
            self.db = path
            self._owns_db = False
        else:
            self.db = Database(Path(path).parent / "aura.db")
            self._owns_db = True
        self.on_event = on_event

    def record(self, action: str, status: str = "ok", **details: object) -> dict:
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "status": status,
            **details,
        }
        self.db.add_action(event)
        if self.on_event:
            self.on_event(event)
        return event

    def recent(self, limit: int = 60) -> list[dict]:
        return self.db.recent_actions(max(1, min(int(limit), 250)))
