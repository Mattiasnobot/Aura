"""What one turn actually did, and what each completion gate makes of it.

`_tool_conversation` used to keep all of this as 70 local variables in a single
344-line scope, where every gate read and wrote whatever it liked. Three bugs
came out of that arrangement:

* the repeated answer, because `state("retry")` had to be signalled at four
  separate sites and one of them was missed;
* the artifact contract leaking in from the previous turn, because
  `expected_paths` was set at the top and read three hundred lines later;
* an empty completion ending the turn while `retries_left` sat unused next to it.

Splitting the facts (`TurnState`) from the decisions (a gate returning
`GateResult`) is not about length. It means a gate can no longer quietly change
something another gate depends on, and "did I forget to signal somewhere" stops
being a question anyone has to answer by reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """A gate's verdict on whether the turn may end.

    `instruction` asks the model for another round; `note` records something the
    reply must not claim was proven. A gate may return neither, which means it
    has nothing to say.
    """

    instruction: str = ""
    notice: str = ""          # streamed to the user while the retry happens
    note: str = ""            # added to the reply's "Not confirmed" section
    #: Ask the next round without any tools. For a model that answered with
    #: nothing, removing the option to call a tool leaves only the plain answer
    #: that was wanted — a different question, rather than a louder one.
    drop_tools: bool = False

    @property
    def wants_retry(self) -> bool:
        return bool(self.instruction)


PASS = GateResult()


@dataclass
class TurnState:
    """The facts of the current turn: what was asked, and what was really done."""

    # ---- what the request framed, decided once before the first round
    expected_paths: list[str] = field(default_factory=list)
    expected_base: str | None = None
    requires_mutation: bool = False
    action_expected: bool = False
    validation_asked: bool = False
    build_words: bool = False
    selected_tools: list[dict] = field(default_factory=list)
    #: Which of the three sampling profiles this turn ran under — "chat",
    #: "work", or "code". Recorded so a reply that came out oddly can be read
    #: back against the heat it was sampled at.
    sampling_kind: str = "chat"
    #: Which silence this turn hit, when it hit one, and the sentence that
    #: explains it. Carried together so the decision Aura took and the reason
    #: Mat reads come from the same classification rather than two guesses.
    empty_kind: str = ""
    empty_explanation: str = ""
    #: How often each tool has failed this turn, and how often the exact same
    #: call has been repeated. Only successes were ever counted before, so a
    #: model could make the same failing call five times unremarked.
    tool_failures: dict[str, int] = field(default_factory=dict)
    repeated_calls: dict[str, int] = field(default_factory=dict)

    # ---- what actually happened, updated as tools run
    successful_tools: int = 0
    #: Which tools actually succeeded, in order. The count alone could
    #: say how much happened but never what, which is the difference
    #: between a plan Aura can write from facts and one she invents.
    tools_run: list[str] = field(default_factory=list)
    #: Commands that were approved and actually finished. Counted apart
    #: from `tools_run` because a refused command still returns a
    #: successful tool result describing the refusal.
    commands_executed: int = 0
    mutation_performed: bool = False
    workspace_mutation: bool = False
    external_activity: bool = False
    external_written: set[str] = field(default_factory=set)
    #: Files whose *content* was actually read. Only these may be reported as
    #: inspected.
    verified_final_paths: set[str] = field(default_factory=set)
    #: Files that were only measured — size, line count, modification time. Enough
    #: to prove a file exists and is not empty, never enough to know what it says.
    measured_paths: set[str] = field(default_factory=set)
    pending_verifications: dict[str, str] = field(default_factory=dict)
    verification_needed: bool = False
    validation_succeeded: bool = False
    validation_attempted: bool = False
    validation_evidence: dict | None = None
    validation_scope: str | None = None

    # ---- shortfalls, recomputed before every gate pass
    empty_response: bool = False
    #: Why the model stopped, straight from the server. `stop` with a
    #: one-token completion means it chose to say nothing, which needs a
    #: different sentence from a model that fell over.
    finish_reason: str = ""
    completion_tokens: int = 0
    missing_artifacts: list[str] = field(default_factory=list)
    missing_action: bool = False
    missing_mutation: bool = False

    # ---- one budget shared by every gate
    retries_left: int = 0
    #: Silences spent separately from the shared budget. Measured across every
    #: episode in the log: a second and third attempt at the same question have
    #: never once recovered, so silence is allowed exactly one retry — the one
    #: that asks differently.
    empty_retries_used: int = 0
    #: Characters of tool output already put in front of the model this turn.
    #: Individual tools cap themselves; this is what bounds their sum.
    tool_characters: int = 0
    #: Set when the turn ran out of time. The report says so rather than
    #: pretending the answer was complete.
    ran_out_of_time: bool = False
    #: The model wrote a tool call out as text instead of calling it. Not an
    #: answer, and never shown: the one that prompted this was `undo_last_change`,
    #: which nobody had asked for.
    emitted_tool_markup: bool = False
    #: Named files the request expected to already exist, which did not. Recorded
    #: before any tool runs, because afterwards there is no way to tell a file that
    #: was edited from one that was conjured.
    missing_at_start: set[str] = field(default_factory=set)
    #: True when the request was phrased as a change to something existing.
    edit_request: bool = False
    #: Steps the model set itself this turn. Never overwritten by inference —
    #: she looked at the plan and decided, which beats anything derived.
    plan_steps_recorded: set[str] = field(default_factory=set)
    unconfirmed: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [item["function"]["name"] for item in self.selected_tools]

    def spend_retry(self) -> bool:
        if self.retries_left <= 0:
            return False
        self.retries_left -= 1
        return True

    def record_unconfirmed(self, note: str) -> None:
        if note and note not in self.unconfirmed:
            self.unconfirmed.append(note)
