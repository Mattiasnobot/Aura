"""How hot to run the model, decided per turn rather than once at startup.

Aura ran everything at one temperature — 0.4, chosen when the local model was a
9B — so a conversation was sampled as conservatively as a refactor. Chatting
wants room to move; analysis and code want to stay grounded.

**What this does not do: protect tool calls.** That was the original argument
for sampling code turns cold, and it did not survive being measured. Against the
loaded gpt-oss-20b, with the real system prompt and the real routed tool set, a
request naming its own tool came back correct 120 times out of 120 — at 0.0,
0.4, 0.6, 0.9, 1.4, and at `top_p 0.02`. On an ambiguous request, where the model
had to choose, temperature moved the choice from 27/30 to 24/30 in favour of the
same tool: Fisher exact p = 0.47, which is nothing. The substantive argument —
the search string itself — was identical at both settings.

So these profiles buy variety in *wording*, not reliability in *acting*. Setting
a code turn cold on the theory that it protects the tool call would be cargo
cult. What has not been measured is whether cold sampling improves the content
of long generated files, which is the one claim in that family still standing.

The kind of turn is read from **the tools routing already selected**, not from a
second pass over the text. Routing has done that work, with Estonian stems folded
in by `language.with_english_hints`, so a profile derived from its answer speaks
every language the router does and cannot drift away from it. Offering a tool that
writes to the workspace is what makes a turn a coding turn — which is the same
definition the mutation gates already use.

**Why this lives in Aura rather than in LM Studio.** Measured against the loaded
gpt-oss-20b: the same prompt six times at `temperature 0` gave one answer, at
`temperature 2` gave five, and adding `top_p 0.01` collapsed it back to one. A
value in the request overrides whatever the model was loaded with, so LM Studio's
own sliders only govern what the request leaves out — which is `top_p` and
`top_k`, unless Mat fills those in too.

**What the profiles are actually worth.** 45 samples each of "name one European
city", through this module and the real config. The code profile answered Paris
42/45 (93%); the chat profile 29/45 (64%) and reached for four cities rather than
three. Fisher exact two-sided p = 0.0014, so the spread is real and not noise.

Two things that measurement also settled, and they temper the numbers below:

* The middle of the range is flat. 0.4 and 0.6 were indistinguishable, and so
  were 0.9 and 1.4. Nudging a profile by a tenth will do nothing observable;
  only large moves change behaviour.
* `top_p` outranks temperature. At `top_p 0.02` the model was fully
  deterministic *while temperature said 0.9* — 16 identical answers. Anyone
  setting a tight `top_p` here has switched temperature off without meaning to.

**A caveat worth keeping in view.** gpt-oss's own documentation asks for
temperature 1.0, and it is a reasoning model whose chain of thought has been
reported to degrade when sampled cold. If tool calls start arriving malformed or
the reasoning begins to loop, the fix is to raise these numbers toward 1.0 —
not to lower them further. That is why every value here is a config key.
"""

from __future__ import annotations

from typing import NamedTuple

#: Tools that change something on disk. A turn offered any of these is writing,
#: and writing is where cold sampling earns its keep.
WRITING_TOOLS = frozenset({
    "write_file", "write_files", "create_file", "append_file", "apply_edits",
    "replace_in_file", "create_folder", "copy_file", "move_file",
    "safe_delete_file", "create_archive", "extract_archive",
    "write_external_file", "run_command",
    # A script writes to the workspace through the same tools everything else
    # does, so a turn offered one is a coding turn whatever else it holds.
    "execute_code",
})

#: Tools that look, measure, or check without changing anything. A turn offered
#: only these is working — reading the workspace, validating, comparing, planning.
WORKING_TOOLS = frozenset({
    "read_file", "read_many_files", "read_external_file", "list_files",
    "list_external_folder", "search_files", "search_text", "find_relevant_files",
    "file_info", "workspace_summary", "inspect_code", "validate_project",
    "check_accessibility", "compare_files", "compare_images", "capture_page",
    "look_at_image", "search_web", "http_get", "calculate", "system_info",
    "record_plan_steps", "update_plan_step", "change_history", "recent_tasks",
    "rollback_task", "undo_last_change", "undo_external_change", "self_check",
    "how_i_have_been_running", "list_granted_folders", "open_workspace_item",
})

KINDS = ("chat", "work", "code")


class Sampling(NamedTuple):
    """What this turn should be sampled at, and why it was decided that way."""

    kind: str
    temperature: float
    max_tokens: int
    #: None means Aura stays silent and LM Studio's loaded value stands — which
    #: is what it did before these existed, so a blank field changes nothing.
    #: A number travels with the request and overrides the slider, the same way
    #: temperature was measured to.
    top_p: float | None = None
    top_k: int | None = None


#: The defaults, from the tuning guides Mat collected for gpt-oss-20b. Chat sits
#: at the top of its band because that is the one Aura is judged on by ear;
#: code sits at the top of *its* band rather than the bottom, because 0.2 on a
#: reasoning model is where the looping reports start.
DEFAULTS: dict[str, tuple[float, int]] = {
    "chat": (0.9, 2048),
    "work": (0.6, 4096),
    "code": (0.4, 6144),
}


def kind_for_tools(tool_names) -> str:
    """Which of the three jobs this turn is, from the tools it was offered."""
    offered = {str(name) for name in (tool_names or ())}
    if offered & WRITING_TOOLS:
        return "code"
    if offered & WORKING_TOOLS:
        return "work"
    return "chat"


def for_turn(tool_names, config: dict | None = None) -> Sampling:
    """The sampling for this turn, honouring anything Mat has overridden.

    Falls back to the single global `temperature`/`max_tokens` when the
    per-task profile is switched off, which is exactly the old behaviour.
    """
    settings = dict(config or {})
    kind = kind_for_tools(tool_names)
    if not settings.get("sampling_by_task", True):
        return Sampling(kind,
                        _clean_temperature(settings.get("temperature"), 0.4),
                        _clean_tokens(settings.get("max_tokens"), 4096),
                        *_tops(settings))
    fallback_temperature, fallback_tokens = DEFAULTS[kind]
    return Sampling(
        kind,
        _clean_temperature(settings.get(f"temperature_{kind}"), fallback_temperature),
        _clean_tokens(settings.get(f"max_tokens_{kind}"), fallback_tokens),
        *_tops(settings))


#: Not split per profile. The guides give one recommendation for these rather
#: than one per task, and six more numbers in the panel would buy nothing.
def _tops(settings: dict) -> tuple[float | None, int | None]:
    return _optional(settings.get("top_p"), float, 0.0, 1.0),            _optional(settings.get("top_k"), int, 0, 500)


def _optional(value, cast, low, high):
    """A blank field means "leave it to LM Studio", not zero."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return max(low, min(cast(value), high))
    except (TypeError, ValueError):
        return None


def _clean_temperature(value, fallback: float) -> float:
    try:
        return max(0.0, min(float(value), 2.0))
    except (TypeError, ValueError):
        return fallback


def _clean_tokens(value, fallback: int) -> int:
    try:
        return max(256, min(int(value), 65536))
    except (TypeError, ValueError):
        return fallback
