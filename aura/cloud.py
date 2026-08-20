"""Reach Claude over the API, for machines that cannot run a capable model locally.

Aura is local-first and stays that way: this provider is off unless it is chosen,
and the `anthropic` package it needs is an optional extra rather than a core
dependency, exactly like the voice engines. With nothing installed and nothing
configured, Aura behaves as before.

What this costs is worth stating plainly rather than burying: with a local model
nothing leaves the machine, and with this one the conversation, the recalled
memories, and the contents of any file a tool reads are all sent to Anthropic.
That is the trade, and it is the user's to make — which is why the choice is
explicit, why the interface says where thinking is happening, and why memories
are withheld unless separately allowed.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Callable

from urllib.parse import urlparse

from .provider import (ChatProvider, OpenAICompatibleProvider, ProviderContext,
                       ProviderError, ProviderReply, ToolCall)

#: The service Aura talks to. Named here so the permission layer can grant it
#: like any other domain instead of it being an exception that bypasses the one
#: place the user can see who Aura talks to.
API_HOST = "api.anthropic.com"

MISSING_PACKAGE = (
    "The cloud provider needs the anthropic package, which Aura does not install "
    "by itself. Run: pip install -r requirements-cloud.txt"
)
MISSING_KEY = (
    "No API key found. Set the ANTHROPIC_API_KEY environment variable, or paste a "
    "key in Settings. Aura never asks for a key anywhere else."
)

_DATA_URL = re.compile(r"^data:(?P<media>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$", re.S)


class AnthropicProvider(ChatProvider):
    """Talk to Claude, translating Aura's OpenAI-shaped conversation on the way.

    Aura's whole interior — `agent.py`, the tools, the gates — speaks the shape
    LM Studio speaks. Rather than teach all of it a second dialect, the
    translation lives here, at the edge, where it is a pair of pure functions
    that can be tested without a network or a key.
    """

    #: Claude's own naming, newest first. Held as a list rather than fetched
    #: because asking costs a request and the answer changes a few times a year.
    MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")
    DEFAULT_MODEL = "claude-opus-5"

    #: Thinking is on by default on this model family and is charged against the
    #: same budget as the answer, so a limit tuned for a local 9B would cut
    #: replies off mid-sentence. Deliberately not the local `max_tokens`.
    DEFAULT_MAX_TOKENS = 16000

    #: Anthropic's reasons, in the words the rest of Aura already understands.
    #: `length` in particular is load-bearing: the empty-response instrument
    #: uses it to tell a model that ran out of room from one that chose to stop.
    STOP_REASONS = {
        "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
        "tool_use": "tool_calls", "refusal": "refusal", "pause_turn": "pause",
    }

    #: A safety classifier can decline a request outright. Asking for a second
    #: model to answer it turns a dead end into an answer, and "default" lets
    #: Anthropic route by the reason rather than Aura guessing a substitute.
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = 240.0, max_tokens: int | None = None) -> None:
        self.api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        self.model = (model or self.DEFAULT_MODEL).strip()
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens or self.DEFAULT_MAX_TOKENS)
        self._cached_client = None
        #: Set when the server rejected the fallback request shape, so the
        #: reason a request went out plainer than intended is visible rather
        #: than being silently absorbed on every later call.
        self.fallback_available = True

    # ------------------------------------------------------------------ setup

    SERVICE = "Claude"

    def describe_location(self) -> str:
        """Where the thinking happens, for the status line.

        The interface said "Local" unconditionally before this provider existed,
        and that sentence becomes untrue the moment this one is switched on.
        """
        return f"Claude • sent to {API_HOST}"

    def is_remote(self) -> bool:
        return True

    def available_models(self) -> list[str]:
        return list(self.MODELS)

    def selected_model(self) -> str:
        return self.model or self.DEFAULT_MODEL

    def _client(self):
        if self._cached_client is not None:
            return self._cached_client
        try:
            import anthropic
        except ImportError as exc:  # optional extra, not a core dependency
            raise ProviderError(MISSING_PACKAGE) from exc
        if not self.api_key:
            raise ProviderError(MISSING_KEY)
        self._cached_client = anthropic.Anthropic(api_key=self.api_key,
                                                  timeout=self.timeout)
        return self._cached_client

    # ------------------------------------------------------- translation, in

    @staticmethod
    def _content_blocks(content: object) -> list[dict]:
        """Turn one OpenAI-shaped message body into Anthropic content blocks."""
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content.strip() else []
        blocks: list[dict] = []
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and str(part.get("text", "")).strip():
                blocks.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url", ""))
                match = _DATA_URL.match(url)
                if match:
                    blocks.append({"type": "image", "source": {
                        "type": "base64", "media_type": match.group("media"),
                        "data": match.group("data")}})
        return blocks

    @classmethod
    def to_anthropic(cls, messages: list[dict]) -> tuple[str, list[dict]]:
        """Split Aura's message list into a system prompt and a message list.

        Three shapes differ from what Aura holds internally: system messages are
        a separate top-level field rather than a role, a tool result is a block
        inside a *user* message rather than a role of its own, and an assistant
        turn that calls tools carries those calls as content blocks. Results for
        one assistant turn are gathered into a single user message, which is how
        the API expects to see several tools answered at once.
        """
        system_parts: list[str] = []
        out: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    system_parts.append(content.strip())
            elif role == "tool":
                block = {"type": "tool_result",
                         "tool_use_id": str(message.get("tool_call_id", "")),
                         "content": str(content or "")}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant":
                blocks = cls._content_blocks(content)
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        arguments = {}
                    blocks.append({"type": "tool_use", "id": str(call.get("id", "")),
                                   "name": str(function.get("name", "")),
                                   "input": arguments if isinstance(arguments, dict) else {}})
                # An assistant turn with neither text nor calls has no valid
                # representation here, and sending an empty one is an error.
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif role == "user":
                blocks = cls._content_blocks(content)
                if blocks:
                    out.append({"role": "user", "content": blocks})
        return "\n\n".join(system_parts), out

    @staticmethod
    def to_anthropic_tools(tools: list[dict] | None) -> list[dict]:
        """Same tools, the other spelling: the schema moves up a level."""
        converted = []
        for tool in tools or []:
            function = tool.get("function") or {}
            converted.append({
                "name": str(function.get("name", "")),
                "description": str(function.get("description", "")),
                "input_schema": function.get("parameters")
                or {"type": "object", "properties": {}},
            })
        return converted

    # ------------------------------------------------------ translation, out

    @classmethod
    def from_anthropic(cls, message: object) -> ProviderReply:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in getattr(message, "content", None) or []:
            kind = getattr(block, "type", "")
            if kind == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif kind == "tool_use":
                arguments = getattr(block, "input", None)
                calls.append(ToolCall(id=str(getattr(block, "id", "")),
                                      name=str(getattr(block, "name", "")),
                                      arguments=dict(arguments or {})))
        stop = str(getattr(message, "stop_reason", "") or "")
        usage = getattr(message, "usage", None)
        return ProviderReply(
            content="".join(text_parts).strip(), tool_calls=calls,
            finish_reason=cls.STOP_REASONS.get(stop, stop),
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0))

    # ------------------------------------------------------------- the call

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 on_token: Callable[[str], None] | None = None) -> ProviderReply:
        system, converted = self.to_anthropic(messages)
        if not converted:
            raise ProviderError("Nothing to send: the conversation had no user message.")
        request: dict = {"model": self.selected_model(), "max_tokens": self.max_tokens,
                         "messages": converted}
        if system:
            request["system"] = system
        if tools:
            request["tools"] = self.to_anthropic_tools(tools)
        return self.from_anthropic(self._send(request, on_token))

    def _send(self, request: dict, on_token: Callable[[str], None] | None):
        """Always stream, whether or not anyone is watching the tokens.

        A long answer on a non-streaming request can outlast the connection, and
        the local provider already showed what that failure looks like from the
        outside: an empty reply with nothing to say about why.
        """
        client = self._client()

        def run(beta: bool):
            if beta:
                return client.beta.messages.stream(
                    betas=[self.FALLBACK_BETA], fallbacks="default", **request)
            return client.messages.stream(**request)

        try:
            try:
                stream = run(self.fallback_available)
            except TypeError:
                # An older anthropic package that has never heard of the
                # parameter. Worth saying once rather than retrying forever.
                self.fallback_available = False
                stream = run(False)
            with stream as active:
                if on_token:
                    for text in active.text_stream:
                        on_token(text)
                return active.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            if self.fallback_available and self._is_unknown_parameter(exc):
                self.fallback_available = False
                return self._send(request, on_token)
            raise ProviderError(self._explain(exc)) from exc

    @staticmethod
    def _is_unknown_parameter(error: Exception) -> bool:
        text = (str(error) + " " + str(getattr(error, "message", ""))).casefold()
        return "fallback" in text and ("unexpected" in text or "unknown" in text
                                       or "not supported" in text)

    @staticmethod
    def _sentence(error: Exception) -> str:
        """The one readable sentence out of an SDK error.

        `.message` carries the whole raw JSON body — status code, error type,
        request id and all — which is accurate and unreadable. The sentence
        worth showing is nested inside it.
        """
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict) and str(inner.get("message", "")).strip():
                return str(inner["message"]).strip()
        text = str(getattr(error, "message", "") or "").strip()
        match = re.search(r"'message':\s*'([^']+)'", text)
        return match.group(1) if match else text

    @staticmethod
    def _explain(error: Exception) -> str:
        """Say what actually went wrong, without echoing the key.

        The first version of this reported "could not reach api.anthropic.com"
        for a plain 400, and the first real request proved how misleading that
        is: it had reached Anthropic perfectly well, which had replied that the
        account was out of credit. A server that says something specific and
        actionable should be repeated, not replaced with a guess.
        """
        status = getattr(error, "status_code", None)
        detail = AnthropicProvider._sentence(error)
        reasons = {
            401: "The API key was rejected. Check the key in Settings or in ANTHROPIC_API_KEY.",
            403: "That key is not allowed to use this model.",
            404: "That model name does not exist. Pick another one in Settings.",
            429: "Anthropic is rate limiting this key. Wait a moment and try again.",
        }
        if status in reasons:
            return reasons[status]
        if isinstance(status, int) and status >= 500:
            return "Anthropic's service is having trouble. Try again shortly."
        if detail:
            return f"Anthropic refused the request: {detail}"
        return f"Could not reach {API_HOST}: {type(error).__name__}"

    # ------------------------------------------------------------- interface

    def model_may_support_vision(self, model: str | None = None) -> bool:
        return True

    def probe_vision_support(self) -> bool:
        """No probe needed, and none worth paying for.

        The local provider probes because LM Studio will happily load a model
        that cannot see. Every model offered here can.
        """
        return True

    def reply(self, message: str, context: ProviderContext) -> str:
        response = self.complete(self.start_messages(message, context))
        if not response.content:
            if response.finish_reason == "refusal":
                raise ProviderError("Claude declined to answer that.")
            raise ProviderError("Claude returned an empty message.")
        return response.content


class OpenAIProvider(OpenAICompatibleProvider):
    """Reach OpenAI, which is the protocol Aura already speaks.

    This one needed no new dependency and almost no new code: LM Studio serves
    the OpenAI chat-completions API, so Aura's existing client works unchanged
    once it is pointed at the real address and given a real key. What is here is
    only what genuinely differs — where it lives, how it authenticates, which of
    the many models on offer can hold a conversation, and the two request fields
    the reasoning models spell differently.

    The same trade as the Claude provider applies: the conversation, any
    memories Aura is allowed to send, and the contents of files her tools read
    all leave this machine.
    """

    SERVICE = "OpenAI"
    DEFAULT_URL = "https://api.openai.com/v1"
    API_HOST = "api.openai.com"
    UNREACHABLE_HINT = "Check this computer's internet connection"

    #: OpenAI's chat API is spoken by a good many other services, so the address
    #: is a setting rather than a constant. That is the whole of what it takes
    #: to reach them: same protocol, same client, different host and key. It
    #: also covers other model servers on this machine, which the status line
    #: will then correctly stop calling remote.
    ELSEWHERE_HINT = "Check the address in Settings"

    #: Deliberately an exclusion list rather than a list of model names. The
    #: catalogue changes faster than this file does, and asking the key what it
    #: can actually use beats hardcoding names that may be wrong or retired.
    NOT_CONVERSATIONAL = (
        "embedding", "embed", "tts", "whisper", "dall-e", "moderation",
        "image", "audio", "realtime", "transcribe", "search", "codex-mini",
        "computer-use", "sora", "babbage", "davinci",
    )

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = 240.0, max_tokens: int | None = None,
                 temperature: float = 0.4, base_url: str | None = None) -> None:
        super().__init__(base_url=(base_url or "").strip() or self.DEFAULT_URL,
                         model=(model or "").strip() or None,
                         timeout=timeout, temperature=temperature,
                         max_tokens=int(max_tokens or 16000))
        host = (urlparse(self.base_url).hostname or "").casefold()
        if host != self.API_HOST:
            # Naming OpenAI in an error raised by somebody else's server would
            # send the user to the wrong place to fix it.
            self.SERVICE = host or "That server"
            self.UNREACHABLE_HINT = self.ELSEWHERE_HINT
        # Shadows the class attribute the shared transport puts in the header.
        self.AUTH_TOKEN = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        #: Learned from the server rather than assumed, once per session, so a
        #: reasoning model does not fail twice for the same reason.
        self.rename_token_limit = False
        self.drop_temperature = False

    def describe_location(self) -> str:
        if not self.is_remote():
            return "Local • private"
        if self.SERVICE == "OpenAI":
            return f"GPT • sent to {self.API_HOST}"
        return super().describe_location()

    def available_models(self) -> list[str]:
        if not self.AUTH_TOKEN:
            raise ProviderError(
                "No API key found. Set the OPENAI_API_KEY environment variable, or paste "
                "a key in Settings.")
        models = super().available_models()
        chat = [name for name in models
                if not any(marker in name.casefold() for marker in self.NOT_CONVERSATIONAL)]
        return sorted(chat or models)

    def model_may_support_vision(self, model: str | None = None) -> bool:
        """OpenAI's conversational models have taken images since gpt-4o.

        Guessing from the name is what the local provider has to do, because LM
        Studio reports nothing but an id. Here the guess would be wrong more
        often than right, and Settings already has an explicit override.
        """
        return True

    def probe_vision_support(self) -> bool:
        return True

    def _tune_payload(self, payload: dict) -> dict:
        """Apply whatever this model turned out to want.

        The reasoning models reject `temperature` and spell the output limit
        `max_completion_tokens`. Which models those are changes over time, so
        the flags are set from what the server actually complained about rather
        than from a list of names kept in this file.
        """
        if self.rename_token_limit and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if self.drop_temperature:
            payload.pop("temperature", None)
        return payload

    def _learn_from_refusal(self, message: str) -> bool:
        """Read a rejection for the one thing it can teach, once."""
        text = message.casefold()
        learned = False
        if "max_completion_tokens" in text and not self.rename_token_limit:
            self.rename_token_limit = True
            learned = True
        if "temperature" in text and "unsupported" in text and not self.drop_temperature:
            self.drop_temperature = True
            learned = True
        return learned

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 on_token: Callable[[str], None] | None = None) -> ProviderReply:
        if not self.AUTH_TOKEN:
            raise ProviderError(
                "No API key found. Set the OPENAI_API_KEY environment variable, or paste "
                "a key in Settings. Aura never asks for a key anywhere else.")
        try:
            return super().complete(messages, tools, on_token)
        except ProviderError as exc:
            # One retry, and only when the server named something specific that
            # can be fixed. Anything else is a real error and stays one.
            if not self._learn_from_refusal(str(exc)):
                raise
            return super().complete(messages, tools, on_token)
