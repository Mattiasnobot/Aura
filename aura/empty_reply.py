"""Telling apart the several different failures that all look like silence.

Aura has one test for an empty answer — `not response.content` — and one remedy
for it: tell the model its reply was empty and ask again without tools. That
remedy is right for exactly one of the faults it fires on.

The log says so. Twenty-five recorded empties, and the recent ones carry two
quite different signatures:

    finish_reason 'stop',        completion_tokens 1   the model emitted the
                                                       stop token and nothing else
    finish_reason '(not given)', prompt_tokens 0       the request never reached
                                                       generation at all

The second is not the model being quiet. Nothing was generated because nothing
was asked — and Aura answers it by appending "Your last response was completely
empty" to the conversation, which is a sentence about an event the model never
took part in. It then spends a second turn on it.

The rule that separates them is taken from `empty_response_guard.py` in
NousResearch's hermes-agent (MIT, © 2025 Nous Research): usage is only evidence
when the request itself was counted, so a reply with no prompt tokens tells you
nothing about the model and must fail open. Their reasoning-token carve-out is
here too, and it matters more for Aura than it does for them — gpt-oss thinks in
a separate channel constantly, so a turn that spent its whole budget thinking has
generated plenty and simply had no room left to speak. Telling *that* model it
said nothing is both false and useless.

Their remedy is to stop retrying and fall through to a different model. Aura has
one model, so the useful half is the diagnosis rather than the failover.
"""

from __future__ import annotations

from typing import NamedTuple

#: A reply that stopped after this many tokens said nothing on purpose. One is
#: the stop token itself; the allowance covers a template that emits a wrapper
#: token alongside it.
DELIBERATE_CEILING = 2


class EmptyKind(NamedTuple):
    """What kind of silence this was, and what to do about it."""

    #: `none` when there is nothing wrong — the reply had content.
    name: str
    #: What to say to the model, or "" when addressing the model is the wrong
    #: move because it was never involved.
    instruction: str
    #: Whether asking again stands any chance of a different answer.
    worth_retrying: bool
    #: What Mat should be told when the turn ends with nothing — but only when
    #: this classification knows more than the finish-reason text already did.
    #: Empty means "the existing sentence is better than anything I can add".
    explanation: str


NOT_EMPTY = EmptyKind("none", "", False, "")


def classify(response, answer_limit: int = 0) -> EmptyKind:
    """Which silence this is, from what the reply actually reported.

    Fails open in every uncertain direction: an unrecognised shape comes back
    as the ordinary case, which is the behaviour Aura had before this existed.
    """
    if getattr(response, "content", None):
        return NOT_EMPTY

    prompt_tokens = _count(response, "prompt_tokens")
    completion_tokens = _count(response, "completion_tokens")
    finish_reason = str(getattr(response, "finish_reason", "") or "")
    thinking = len(str(getattr(response, "reasoning", "") or ""))

    # The server counted the request and counted nothing in it. That is
    # positive evidence something upstream failed — the window, the template,
    # the socket. A reply with *no* usage block says nothing at all and must
    # not land here: it is indistinguishable from an ordinary quiet turn, and
    # treating the two alike turned a retryable stumble into a dead turn.
    if (getattr(response, "usage_reported", False)
            and prompt_tokens <= 0 and completion_tokens <= 0):
        # Not worth asking again: the same request would be sent, and it was
        # not the model that declined it. Every logged episode of retrying a
        # silence with the same payload got the same silence back.
        return EmptyKind(
            "never_asked", "", False,
            "The request did not reach the model — nothing was counted on the "
            "way in. That is a connection or context-window fault rather than "
            "the model choosing silence.")

    # It generated, and spent the lot before reaching an answer. gpt-oss thinks
    # in a separate channel, so this is the common shape of a long tool turn
    # that ran out of room rather than one that had nothing to say.
    if finish_reason == "length" or (thinking and completion_tokens > DELIBERATE_CEILING):
        room = f" of the {answer_limit:,} it was given" if answer_limit else ""
        return EmptyKind(
            "thought_it_away",
            "You used your whole response budget thinking and left none for the "
            "answer. Give the user your conclusion now, in a few short sentences, "
            "before working anything else out.",
            True,
            f"The model spent all {completion_tokens:,} tokens{room} on private "
            f"reasoning and had none left to answer with. Raising the response "
            f"token limit for this kind of turn would fix it.")

    # Stopped immediately and on purpose. The same prompt will do it again, so
    # the retry has to ask differently — which is what Aura already does.
    if completion_tokens <= DELIBERATE_CEILING and finish_reason == "stop":
        return EmptyKind(
            "chose_silence",
            "Your last response was completely empty. The tools have been taken "
            "away for this turn, so answer the user directly in plain text now, "
            "using what you already found. Never reply with nothing.",
            True,
            # No explanation of its own: `_empty_response_reason_from_finish`
            # already says this one well, and two sentences for one fault is
            # how they drift apart.
            "")

    return EmptyKind(
        "unexplained",
        "Your last response was completely empty. The tools have been taken "
        "away for this turn, so answer the user directly in plain text now, "
        "using what you already found. Never reply with nothing.",
        True,
        "")


def _count(response, field: str) -> int:
    try:
        return int(getattr(response, field, 0) or 0)
    except (TypeError, ValueError):
        return 0
