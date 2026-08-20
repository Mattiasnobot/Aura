"""What a request will cost, worked out *before* it is sent.

Aura has been sending and hoping. `prompt_tokens` is read off the reply, which
is a number that arrives too late to act on, and compaction only ever ran as a
reaction to a turn that had already failed. When the payload did not fit, LM
Studio trimmed the conversation until the chat template had no user turn left
and raised — which arrived looking exactly like a model that had chosen to say
nothing, and cost a week of looking in the wrong place.

The idea of checking first is taken from NousResearch's hermes-agent (MIT,
© 2025 Nous Research), along with a finding worth stealing outright: counting
*messages* is not a proxy for size, because a handful of very large ones never
trips a count threshold and the turn hits a hard overflow anyway. Aura's version
of that mistake was worse — the trigger was a failure that had already happened.

**Their estimator is not used, because it was measured and it does not fit.**
`estimate_tokens_rough` assumes ~4 characters per token. Against the loaded
gpt-oss-20b that is wrong in both directions at once: a two-word prompt came
back 68 tokens larger than any character count can explain, and 12KB of code
came back 44% *smaller* than the rule predicts. Measured on this machine:

    content              chars/token
    code                    3.7 – 4.7
    English prose           5.3
    JSON tool schemas       5.8
    fixed template overhead ~68 tokens regardless of input

One ratio cannot describe that. But Aura has something hermes-agent cannot
assume: it talks to one model at a time, and LM Studio returns the true
`prompt_tokens` with every single reply. So the ratio is not guessed, it is
*learned* from the last few turns against the model actually loaded — and it
recalibrates by itself when Mat loads a different one.

The bias is deliberate. Overestimating costs a little context; underestimating
costs the turn. Everything here rounds against the request.
"""

from __future__ import annotations

import json

#: Until the loaded model has answered a few times, assume the densest ratio
#: seen on this machine rather than an average one. Being wrong in the safe
#: direction on the first turn after a model swap is worth more than precision.
DEFAULT_CHARS_PER_TOKEN = 3.6

#: The chat template's own preamble, which no character count of the payload
#: can see. Measured at ~68 for gpt-oss's harmony format; carried high because
#: a template Aura has never met may say more.
DEFAULT_OVERHEAD = 128

#: How far off the estimate is allowed to be before the answer stops being
#: trustworthy. Applied to the request, never to the room.
SAFETY_MARGIN = 0.12

#: Ratios outside this are not a different model, they are a bug in the
#: measurement — a truncated request, or a reply whose usage was missing.
MIN_RATIO, MAX_RATIO = 2.0, 12.0


def request_characters(messages, tools=None) -> int:
    """Every character that will go on the wire, tool schemas included.

    The schemas are easy to forget and are not small — Aura offers up to a
    dozen at a time, each with a description and a parameter block, and they
    measured as the densest content in the payload.
    """
    total = 0
    for message in messages or ():
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        # The role and the delimiters around it are real characters too.
        total += len(str(message.get("role", ""))) + 8
        total += _length(message.get("content"))
        total += _length(message.get("reasoning_content"))
        calls = message.get("tool_calls")
        if calls:
            total += _length(calls)
    if tools:
        total += _length(tools)
    return total


def _length(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value))


class TokenMeter:
    """Converts characters to tokens, and learns the rate as it goes.

    Deliberately small: a handful of recent observations, a ratio, and no
    persistence. A meter that survives a model swap would carry the old
    model's rate into the new one's window, which is the failure it exists to
    prevent.
    """

    #: Enough to smooth one odd turn, few enough to follow a model change
    #: within a conversation rather than a day.
    WINDOW = 12

    def __init__(self, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
                 overhead: int = DEFAULT_OVERHEAD) -> None:
        self._default = float(chars_per_token)
        self.overhead = int(overhead)
        self._seen: list[tuple[int, int]] = []
        #: What replies have actually cost, which decides how much of the window
        #: to hold back. Empty until this model has answered.
        self._answers: list[int] = []

    @property
    def typical_answer(self) -> int:
        """The largest reply seen recently, or 0 before there is one.

        The largest rather than the average: this decides how much room to keep
        clear, and being wrong low is what truncates an answer mid-sentence.
        """
        return max(self._answers, default=0)

    def observe_answer(self, completion_tokens: int) -> None:
        try:
            tokens = int(completion_tokens)
        except (TypeError, ValueError):
            return
        if tokens <= 0:
            return
        self._answers.append(tokens)
        del self._answers[:-self.WINDOW]

    def observe(self, characters: int, prompt_tokens: int) -> None:
        """Record what a real request actually cost.

        Small requests are ignored: at 35 characters the template overhead is
        most of the bill, so they measure the preamble rather than the rate.
        """
        try:
            characters, prompt_tokens = int(characters), int(prompt_tokens)
        except (TypeError, ValueError):
            return
        if characters < 2000 or prompt_tokens <= self.overhead:
            return
        ratio = characters / (prompt_tokens - self.overhead)
        if not MIN_RATIO <= ratio <= MAX_RATIO:
            return
        self._seen.append((characters, prompt_tokens))
        del self._seen[:-self.WINDOW]

    @property
    def chars_per_token(self) -> float:
        """The rate to bill at: the densest recently seen, not the average.

        The densest observation is the one that produces the largest estimate,
        and this number exists to stop a request being sent that will not fit.
        An average would be right more often and wrong in the direction that
        loses the turn.
        """
        if not self._seen:
            return self._default
        return min(chars / max(1, tokens - self.overhead) for chars, tokens in self._seen)

    @property
    def calibrated(self) -> bool:
        return bool(self._seen)

    def tokens_for(self, characters: int) -> int:
        return self.overhead + int(max(0, characters) / max(0.5, self.chars_per_token))

    def estimate(self, messages, tools=None) -> int:
        return self.tokens_for(request_characters(messages, tools))


#: Set aside for an answer before anything is known about this model's, and
#: the floor under every later estimate. Generous enough for a long reply with
#: reasoning; small enough that it never squeezes a conversation on its own.
DEFAULT_ANSWER_RESERVE = 4096

#: However large the cap, no more of the window than this is held back for the
#: answer. A reservation bigger than the conversation defeats the purpose of
#: having a window at all.
MOST_OF_WINDOW_RESERVED = 3


def answer_reserve(answer_limit: int, loaded_context: int,
                   meter: "TokenMeter | None" = None) -> int:
    """How much of the window to keep clear for the reply being written.

    Not `answer_limit`. That is a ceiling the server does not enforce against
    the prompt — measured: an 18,880-token prompt succeeded with the limit at
    64,000 on a 66,816-token window. Treating the cap as a reservation made
    Aura compact conversations that fitted, which is how raising a setting to
    64,000 shortened a turn instead of lengthening it.
    """
    seen = meter.typical_answer if meter is not None else 0
    wanted = max(DEFAULT_ANSWER_RESERVE, int(seen * 1.5))
    if answer_limit > 0:
        wanted = min(wanted, int(answer_limit))
    if loaded_context > 0:
        wanted = min(wanted, int(loaded_context) // MOST_OF_WINDOW_RESERVED)
    return max(0, wanted)


def room_for_request(loaded_context: int, answer_tokens: int,
                     meter: "TokenMeter | None" = None) -> int:
    """How much of the window the request may occupy."""
    if loaded_context <= 0:
        return 0            # nothing reported, so nothing can be claimed
    return max(0, int(loaded_context)
               - answer_reserve(answer_tokens, loaded_context, meter))


def verdict(meter: TokenMeter, messages, tools, loaded_context: int,
            answer_tokens: int) -> dict:
    """Whether this request fits, and by how much it misses.

    Returns a plain dict rather than raising. A budget check that can break a
    turn is worse than one that reports honestly and stands aside; `known` is
    False when the server never reported a window, and nothing in that case is
    a reason to change what Aura does.
    """
    characters = request_characters(messages, tools)
    needed = meter.tokens_for(characters)
    guarded = int(needed * (1 + SAFETY_MARGIN))
    room = room_for_request(loaded_context, answer_tokens, meter)
    known = bool(loaded_context)
    return {"known": known, "characters": characters, "needed": needed,
            "guarded": guarded, "room": room,
            "reserved": answer_reserve(answer_tokens, loaded_context, meter),
            "chars_per_token": round(meter.chars_per_token, 2),
            "calibrated": meter.calibrated,
            "fits": (not known) or guarded <= room,
            "over_by": max(0, guarded - room) if known else 0}
