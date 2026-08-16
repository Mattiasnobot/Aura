"""One thread that wakes, asks permission, and runs what is due.

Deliberately dull. The interesting decisions all live elsewhere: whether
anything may run at all is `AutonomyGuard`'s answer, and what a particular kind
of scheduled work actually does belongs to its handler. This file only decides
*when*, and makes sure the answer is honoured.

Four rules it exists to keep:

* **Never while the user is waiting.** A background run that collides with a
  request in flight would fight it for the model and the workspace.
* **A refusal does not consume the work.** If the guard says no — quiet hours,
  the daily cap, paused — the row stays due and is tried again later, rather
  than being marked run and quietly skipped.
* **A run is bounded.** The guard's per-run budget is a deadline, not a
  suggestion.
* **A crash in one task never stops the loop**, and is recorded as that task's
  outcome so it is visible instead of silent.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable


#: kind -> handler(task: dict) -> str  (the outcome to record)
Handler = Callable[[dict], str]


class Scheduler:
    """Runs due scheduled tasks, inside the envelope and never over the user."""

    #: How often the loop looks. Short enough to feel prompt, long enough to be
    #: invisible: due times are minutes apart, not seconds.
    TICK_SECONDS = 20.0

    def __init__(self, database, guard, log, *, busy: Callable[[], bool],
                 tick_seconds: float | None = None) -> None:
        self.database = database
        self.guard = guard
        self.log = log
        self.busy = busy
        self.tick_seconds = float(tick_seconds or self.TICK_SECONDS)
        self.handlers: dict[str, Handler] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, kind: str, handler: Handler) -> None:
        """Teach the scheduler one kind of work. Kinds arrive in later steps."""
        self.handlers[str(kind)] = handler

    # ------------------------------------------------------------------ loop

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="aura-scheduler")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:                      # never let the loop die
                self.log.record("scheduler", "error", error=str(exc)[:300])
            self._stop.wait(self.tick_seconds)

    # ------------------------------------------------------------------ work

    def tick(self, moment: datetime | None = None) -> list[str]:
        """Run whatever is due right now. Returns the ids actually run."""
        if self.busy():
            # The user is mid-request; their work owns the model and the
            # workspace until it finishes.
            return []
        verdict = self.guard.may_run()
        if not verdict:
            return []
        now = moment or datetime.now(timezone.utc)
        ran: list[str] = []
        for task in self.database.due_scheduled_tasks(now.isoformat()):
            if self._stop.is_set() or self.busy():
                break
            if not self.guard.may_run():
                # The allowance can run out part-way through a batch. Stop
                # rather than spend what is not there.
                break
            ran.append(str(task["id"]))
            self._run_one(task, now)
        return ran

    def _run_one(self, task: dict, now: datetime) -> None:
        handler = self.handlers.get(str(task.get("kind")))
        if handler is None:
            # An unknown kind is disabled rather than retried forever: something
            # wrote a row this build cannot serve, and silently looping on it
            # would burn the daily allowance for nothing.
            self.database.record_scheduled_run(
                task["id"], f"no handler for {task.get('kind')!r}", None)
            self.log.record("scheduled_task", "error", task_id=task["id"],
                            kind=task.get("kind"), error="unknown kind")
            return
        self.guard.note_run(f"{task.get('kind')}: {str(task.get('request', ''))[:80]}")
        try:
            outcome = str(handler(dict(task)) or "done")
            status = "ok"
        except Exception as exc:
            outcome, status = f"failed: {exc}"[:400], "error"
        self.database.record_scheduled_run(task["id"], outcome, self._next_run(task, now))
        self.log.record("scheduled_task", status, task_id=task["id"],
                        kind=task.get("kind"), outcome=outcome[:200])

    @staticmethod
    def _next_run(task: dict, now: datetime) -> str | None:
        """When this should happen again — or None, which retires a one-off."""
        every = int(task.get("every_minutes") or 0)
        if every <= 0:
            return None
        return (now + timedelta(minutes=every)).isoformat()
