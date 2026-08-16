"""The envelope around anything Aura does when nobody asked her to.

This exists before the scheduler that will use it, deliberately. Quiet hours, a
time budget, a daily cap, and a stop control are not features to bolt on once
background work is running — they are the conditions under which background work
is allowed to exist at all, and the only honest time to write them is first.

Every refusal carries a reason in plain language, because a background run that
silently does not happen is worse than one that says why.

`AutonomyGuard` decides; it never runs anything itself. That keeps it small
enough to test exhaustively, which matters for the one piece of the system whose
job is to say no.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Verdict:
    """Whether background work may start, and why not when it may not."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class AutonomyGuard:
    """Answers one question: may something run on its own right now?"""

    #: Never allow a single unattended run to exceed this, whatever is configured.
    HARD_RUN_SECONDS = 600
    #: Nor more than this many runs in a day.
    HARD_DAILY_CAP = 200

    def __init__(self, config, log) -> None:
        self.config = config
        self.log = log

    # ------------------------------------------------------------------ time

    @staticmethod
    def _minutes(value: str, fallback: int) -> int:
        """Parse "22:30" into minutes past midnight."""
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return fallback
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return fallback
        return hour * 60 + minute

    def quiet_window(self) -> tuple[int, int]:
        return (self._minutes(self.config.data.get("quiet_hours_start"), 22 * 60),
                self._minutes(self.config.data.get("quiet_hours_end"), 8 * 60))

    def in_quiet_hours(self, moment: datetime | None = None) -> bool:
        """Is now inside the quiet window?

        The window usually crosses midnight (22:00–08:00), which is exactly the
        case a naive `start <= now < end` gets wrong, so it is handled explicitly
        rather than assumed away.
        """
        start, end = self.quiet_window()
        if start == end:
            return False                     # an empty window means never quiet
        now = moment or datetime.now()
        minutes = now.hour * 60 + now.minute
        if start < end:
            return start <= minutes < end
        return minutes >= start or minutes < end

    # --------------------------------------------------------------- budgets

    @staticmethod
    def _number(value: object, fallback: int) -> int:
        """Read a configured number without treating a deliberate 0 as absent.

        `int(value or fallback)` is the obvious spelling and it is wrong here:
        a daily cap of 0 means *no background work at all*, and that spelling
        would have quietly turned it into the default allowance.
        """
        if value is None or value == "":
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def run_seconds(self) -> int:
        configured = self._number(self.config.data.get("autonomy_run_seconds"), 120)
        return max(10, min(configured, self.HARD_RUN_SECONDS))

    def daily_cap(self) -> int:
        configured = self._number(self.config.data.get("autonomy_daily_runs"), 12)
        return max(0, min(configured, self.HARD_DAILY_CAP))

    def runs_today(self, moment: datetime | None = None) -> int:
        """Count runs already spent today, from the durable audit trail.

        Read from the log rather than a counter in memory, so a restart cannot
        hand back a fresh allowance.
        """
        now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = 0
        for event in self.log.recent(500):
            if event.get("action") != "autonomous_run":
                continue
            try:
                when = datetime.fromisoformat(str(event.get("time", "")))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= midnight:
                spent += 1
        return spent

    # ---------------------------------------------------------------- verdict

    def paused(self) -> bool:
        return bool(self.config.data.get("autonomy_paused", False))

    def may_run(self, moment: datetime | None = None) -> Verdict:
        if self.paused():
            return Verdict(False, "Background work is paused.")
        cap = self.daily_cap()
        if cap == 0:
            return Verdict(False, "The daily limit for background work is set to zero.")
        if self.in_quiet_hours(moment):
            start, end = self.quiet_window()
            return Verdict(False, "It is quiet hours "
                                  f"({start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}).")
        spent = self.runs_today()
        if spent >= cap:
            return Verdict(False, f"Today's limit of {cap} background runs is used up.")
        return Verdict(True)

    def note_run(self, what: str) -> None:
        """Record that an allowance was spent. Counted from this, so it is not optional."""
        self.log.record("autonomous_run", "ok", what=str(what)[:200])

    def pause(self, reason: str = "") -> None:
        self.config.update(autonomy_paused=True)
        self.log.record("autonomy_paused", "ok", reason=str(reason)[:200])

    def resume(self) -> None:
        self.config.update(autonomy_paused=False)
        self.log.record("autonomy_resumed", "ok")

    def next_opening(self, moment: datetime | None = None) -> datetime | None:
        """When the quiet window ends, so a caller can wait rather than poll."""
        if not self.in_quiet_hours(moment):
            return None
        now = moment or datetime.now()
        _, end = self.quiet_window()
        opening = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if opening <= now:
            opening += timedelta(days=1)
        return opening
