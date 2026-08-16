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

    # ---- what actually happened, updated as tools run
    successful_tools: int = 0
    mutation_performed: bool = False
    workspace_mutation: bool = False
    external_activity: bool = False
    external_written: set[str] = field(default_factory=set)
    verified_final_paths: set[str] = field(default_factory=set)
    pending_verifications: dict[str, str] = field(default_factory=dict)
    verification_needed: bool = False
    validation_succeeded: bool = False
    validation_attempted: bool = False
    validation_evidence: dict | None = None
    validation_scope: str | None = None

    # ---- shortfalls, recomputed before every gate pass
    empty_response: bool = False
    missing_artifacts: list[str] = field(default_factory=list)
    missing_action: bool = False
    missing_mutation: bool = False

    # ---- one budget shared by every gate
    retries_left: int = 0
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
