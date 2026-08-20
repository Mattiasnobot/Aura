"""Noticing when a turn is going round in circles, and saying so usefully.

Aura counts successful tools. A failure is logged and then forgotten, so a model
that calls the same tool with the same arguments five times gets five identical
errors and no indication that repeating itself is the problem. Nothing in the
turn ever says "you have tried this".

Measured: asked to check the shop project, gpt-oss reached past the offered
`validate_project` for `python -m validate_project`. Routing now declines to
offer a shell beside a tool that answers the same question, which prevents that
particular case — but the general shape stays, and it is the shape that costs
whole turns.

The idea is from `tool_guardrails.py` in NousResearch's hermes-agent (MIT,
© 2025 Nous Research): track per-turn tool observations, keyed on the tool and
its canonicalised arguments, and let the repeat count decide what to say. Two
things are taken directly.

**Identical arguments are the strongest signal there is.** A tool that refused
one set of arguments will refuse the same set again — the model does not need
encouragement, it needs to be told the input is what failed.

**Do not let it fall back to prose.** Their guidance says outright: keep using
tools, but diagnose before retrying. A model that hits three failures tends to
stop calling anything and write an apology instead, which ends the turn with
nothing done — and Aura's own gates then read that as a reply worth keeping.
"""

from __future__ import annotations

import json

#: Said on the second identical call. Short, because the model has already read
#: one error and is about to read another.
REPEAT_NOTE = (
    "You have already called {tool} with exactly these arguments this turn, and it "
    "failed the same way. The arguments are the problem, not the attempt — change "
    "them, or use a different tool."
)

#: Said once a tool has failed several times however it was called. Longer,
#: because by this point the useful move is to stop and look.
STALL_NOTE = (
    "{tool} has failed {count} times this turn. Keep using tools rather than "
    "answering in text, but diagnose before trying again: read the error, check "
    "the path or name against what is actually there with list_files, and then "
    "try different arguments or a different tool. If something outside the "
    "workspace is blocking it, say so plainly instead of repeating the attempt."
)

#: Below this a repeat is ordinary work — reading a file twice is not a loop.
STALL_AFTER = 3


def signature(tool: str, arguments) -> str:
    """One string identifying this exact call.

    Sorted and compact so that the same arguments in a different order are the
    same call, which is how they arrive when a model rebuilds a request.
    """
    try:
        rendered = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        rendered = str(arguments)
    return f"{tool}:{rendered}"


def note_for(tool: str, arguments, failures: dict, repeats: dict) -> str:
    """What to add to a failed tool result, or "" when nothing is worth saying.

    Both counters are updated here, so a caller cannot record a failure without
    also asking what it means.
    """
    key = signature(tool, arguments)
    repeats[key] = repeats.get(key, 0) + 1
    failures[tool] = failures.get(tool, 0) + 1
    if repeats[key] >= 2:
        return REPEAT_NOTE.format(tool=tool)
    if failures[tool] >= STALL_AFTER:
        return STALL_NOTE.format(tool=tool, count=failures[tool])
    return ""
