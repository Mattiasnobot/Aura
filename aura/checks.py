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
from typing import Callable


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
    for event in agent.db.failed_actions(40):
        reason = str(event.get("error") or event.get("action") or "")[:90]
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    repeated = sorted(((n, reason) for reason, n in counts.items() if n >= 3), reverse=True)
    if not repeated:
        return None
    count, reason = repeated[0]
    # No proposal: a repeated failure is worth knowing about, and what to do
    # about it depends on judgement Aura does not have.
    return Finding(f"That has failed {count} times recently: {reason}")


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
    )
}


#: Switched on for a new installation. Both are silent unless something is
#: actually wrong, which is the bar for anything that speaks unprompted.
#: `validate_workspace` is deliberately *not* here: a workspace mid-edit is
#: often temporarily invalid, and a check that nags during normal work is worse
#: than no check.
DEFAULT_CHECKS = ("broken_links", "recent_failures")
DEFAULT_EVERY_MINUTES = 24 * 60


def names() -> list[str]:
    return sorted(REGISTRY)


def get(name: str) -> Check | None:
    return REGISTRY.get(str(name))
