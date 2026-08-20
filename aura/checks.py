"""Read-only things Aura can look at on a schedule.

Each check is a named, deterministic function over state Aura already has. That
is the whole design constraint, and it is what makes a recurring check safe to
run while nobody is watching:

* **No model call.** A check is backend code, so it costs nothing, cannot
  hallucinate, and cannot decide to do something else.
* **No writes.** Nothing here mutates the workspace, memory, or permissions.
* **A fixed vocabulary.** The tool that schedules a check picks a name from this
  registry rather than passing free text. Free text would turn "a check" into
  "an arbitrary agent turn in the background", which is exactly the thing 48.5
  exists to put behind a proposal.

A check returns `None` when there is nothing worth saying. That matters more
than it looks: a daily check that reports "all fine" forever trains its reader
to ignore it, and then the one time it matters it is ignored too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


#: How much repetition makes a pattern. Three, because one is an event and two
#: is a coincidence — and a check that speaks too early teaches its reader to
#: ignore it, which is the failure mode this whole file is written against.
MIN_REPEATS = 3
MIN_STREAK = 3

#: A pattern has to be *forming*, not merely present in the history. Counting
#: over all time turns "19 failures" into a permanent complaint about things
#: that were fixed days ago, and a check nobody can silence by fixing the
#: problem is the definition of a nag.
WINDOW_HOURS = 48

@dataclass(frozen=True)
class Finding:
    """Something worth saying, and optionally something worth doing about it.

    `proposal` is a plain request, worded exactly as the user might have typed
    it. It is never executed by the check: it is stored, shown, and only runs if
    the user approves — in the foreground, through the ordinary tool loop, with
    every gate and snapshot that any other request gets.
    """

    message: str
    proposal: str = ""


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    #: agent -> a Finding worth reporting, or None to stay quiet
    run: Callable[[object], "Finding | None"]


def _validate_workspace(agent) -> Finding | None:
    result = agent._validate_project(".")
    if result.get("valid"):
        return None
    if not int(result.get("files_seen", 0)):
        # "Project contains no files" is a fair answer to an explicit validate
        # request and pure noise as a background check: an empty workspace is
        # not a problem anyone needs waking for.
        return None
    issues = result.get("issues") or []
    first = issues[0] if issues else {}
    where = first.get("path") or first.get("file") or "the workspace"
    what = first.get("error", "an unnamed problem")
    more = f" (and {len(issues) - 1} more)" if len(issues) > 1 else ""
    return Finding(f"Validation is failing: {where} — {what}{more}.",
                   f"Validate the workspace, fix every issue it reports, and validate again.")


def _broken_links(agent) -> Finding | None:
    from .validation import check_broken_assets
    result = check_broken_assets(agent.sandbox, ".")
    broken = result.get("broken") or []
    if not broken:
        return None
    first = broken[0]
    more = f" (and {len(broken) - 1} more)" if len(broken) > 1 else ""
    return Finding(
        f"{len(broken)} broken local reference"
        f"{'' if len(broken) == 1 else 's'}: "
        f"{first.get('file', '?')} points at {first.get('reference', '?')}{more}.",
        f"In {first.get('file', 'the page')}, the reference to "
        f"{first.get('reference', 'a missing file')} does not resolve. "
        "Read the file, then either correct the path or remove the reference.")


def _recent_failures(agent) -> Finding | None:
    """The pattern the diagnostics export made visible, noticed continuously.

    Only speaks when the same failure has happened more than once, because one
    failure is an event and three of the same are a pattern.
    """
    counts: dict[str, int] = {}
    for event in _within_window(agent.db.failed_actions(40)):
        reason = str(event.get("error") or event.get("action") or "")[:90]
        # Left to `model_producing_nothing`, which says something actionable
        # about them. Two checks describing one pattern is the noise this file
        # exists to avoid.
        if reason and not _says_nothing_usable(reason):
            counts[reason] = counts.get(reason, 0) + 1
    repeated = sorted(((n, reason) for reason, n in counts.items() if n >= 3), reverse=True)
    if not repeated:
        return None
    count, reason = repeated[0]
    # No proposal: a repeated failure is worth knowing about, and what to do
    # about it depends on judgement Aura does not have.
    return Finding(f"That has failed {count} times in the last two days: {reason}")


#: Outcomes that are the user's decision rather than a fault. Counting a
#: declined plan as a failure told the user their own "no" three times.
USER_DECISIONS = frozenset({"declined", "cancelled"})


def _within_window(events: list[dict], hours: int = WINDOW_HOURS) -> list[dict]:
    """Keep only what happened recently enough to still be news, and only
    things that actually went wrong — a decision is not a failure."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept = []
    for event in events:
        if str(event.get("status") or "") in USER_DECISIONS:
            continue
        stamp = str(event.get("time") or "")
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            kept.append(event)
    return kept


#: Owned by `model_producing_nothing`, which can say something useful about
#: them. Excluded from the generic repeat check so one pattern is reported once.
NOTHING_USABLE = ("neither text nor a tool", "empty response",
                  "did not perform the requested")


def _says_nothing_usable(error: str) -> bool:
    lowered = str(error).casefold()
    return any(marker in lowered for marker in NOTHING_USABLE)


def _recent_task_outcomes(agent, limit: int = 12) -> list[str]:
    """Newest first. Only finished work counts — running tasks say nothing yet."""
    outcomes: list[str] = []
    # task_events takes a number of *tasks*, and each one carries several events.
    for event in agent.db.task_events(limit):
        if str(event.get("event")) == "finished":
            outcomes.append(str(event.get("status") or ""))
    return list(reversed(outcomes))[:limit]


def _failing_streak(agent) -> Finding | None:
    """Several tasks in a row failing is a different thing from several failing.

    Counting failures over a window says "some things go wrong", which everyone
    already knows. A run says something is wrong *now* — the model unloaded, the
    workspace gone, a setting changed — and that is worth interrupting for.
    """
    outcomes = _recent_task_outcomes(agent)
    streak = 0
    for status in outcomes:
        if status == "error":
            streak += 1
        elif status == "cancelled":
            continue          # a cancellation is the user's decision, not a fault
        else:
            break
    if streak < MIN_STREAK:
        return None
    service = getattr(agent.provider, "SERVICE", "the model server")
    return Finding(
        f"The last {streak} tasks in a row failed. Something changed rather than "
        f"something being hard: worth checking that {service} still has a model "
        f"loaded before asking for more.")


def _model_producing_nothing(agent) -> Finding | None:
    """The failure that is actually the most common, named as itself.

    Measured on the real journal: "the model returned neither text nor a tool
    request" eleven times, against two for the empty-response message everyone
    was chasing. They are the same underlying problem — the model produced
    nothing usable — and counting them separately hid how big it was.
    """
    silent = sum(1 for event in _within_window(agent.db.failed_actions(60))
                 if _says_nothing_usable(event.get("error") or ""))
    if silent < MIN_REPEATS:
        return None
    return Finding(
        f"{silent} times in the last two days the model answered with nothing Aura could use "
        f"— no text and no tool call. That is usually the model rather than the "
        f"request: check it is still loaded, or try a smaller one.")


def _unkept_promises(agent) -> Finding | None:
    """Files that were promised and never appeared, when it keeps happening."""
    missing: dict[str, int] = {}
    for event in _within_window(agent.db.failed_actions(60)):
        text = str(event.get("error") or "")
        marker = "required artifacts are still missing:"
        if marker in text.casefold():
            name = text.split(":", 1)[-1].strip()[:60]
            if name:
                missing[name] = missing.get(name, 0) + 1
    repeated = sorted(((count, name) for name, count in missing.items()
                       if count >= MIN_REPEATS), reverse=True)
    if not repeated:
        return None
    count, name = repeated[0]
    return Finding(
        f"{name} was promised and never written {count} times. Either the request "
        f"names a file it does not really want, or the build keeps stopping short.",
        f"Look at why {name} is expected but never created, and tell me which of "
        f"the two it is. Do not create the file.")


REGISTRY: dict[str, Check] = {
    check.name: check for check in (
        Check("validate_workspace",
              "Validate every project file and report only when validation fails.",
              _validate_workspace),
        Check("broken_links",
              "Crawl workspace HTML for local links, scripts, and images that do not resolve.",
              _broken_links),
        Check("recent_failures",
              "Notice when the same failure has happened several times lately.",
              _recent_failures),
        Check("failing_streak",
              "Notice when several tasks in a row have failed, which usually means "
              "something changed rather than something being hard.",
              _failing_streak),
        Check("model_producing_nothing",
              "Notice when the model keeps answering with neither text nor a tool call.",
              _model_producing_nothing),
        Check("unkept_promises",
              "Notice a file that keeps being promised and never written.",
              _unkept_promises),
    )
}


#: Switched on for a new installation. Both are silent unless something is
#: actually wrong, which is the bar for anything that speaks unprompted.
#: `validate_workspace` is deliberately *not* here: a workspace mid-edit is
#: often temporarily invalid, and a check that nags during normal work is worse
#: than no check.
DEFAULT_CHECKS = ("broken_links", "recent_failures", "failing_streak",
                  "model_producing_nothing")
DEFAULT_EVERY_MINUTES = 24 * 60


def names() -> list[str]:
    return sorted(REGISTRY)


def get(name: str) -> Check | None:
    return REGISTRY.get(str(name))
