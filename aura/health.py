"""One answer to "is anything broken?".

Aura has grown a lot of moving parts — a local model, speech out, voice in,
a sandboxed workspace, a database, a search engine in a container — and each one
already knew how to report itself somewhere. What was missing was a single place
that asks all of them and says plainly which is wrong, so "something is broken"
stops being a diagnosis the user has to make themselves.

Three rules hold for everything in here:

* **Nothing here changes anything.** The one exception is the workspace check,
  which writes a probe file and removes it — because "can Aura write here?" has
  no honest answer that does not involve trying.
* **Nothing here can hang.** Every check that touches the outside world is given
  a timeout, since a self-check that freezes is worse than no self-check.
* **"I cannot tell" is a real answer.** Vision cannot be tested without a model
  loaded, and reporting *unknown* is honest where reporting *ok* or *failed*
  would both be guesses.
"""

from __future__ import annotations

from . import context_budget, sampling

import shutil
import sqlite3
from contextlib import closing
import time
from dataclasses import dataclass
from pathlib import Path

OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"


@dataclass
class Result:
    name: str
    label: str
    status: str
    detail: str
    #: What the user could do about it. Empty when there is nothing to do.
    remedy: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "status": self.status,
                "detail": self.detail, "remedy": self.remedy}


def _timed(function, *args, **kwargs):
    started = time.monotonic()
    try:
        return function(*args, **kwargs), None, time.monotonic() - started
    except Exception as exc:                      # every check reports, none raises
        return None, exc, time.monotonic() - started


# --------------------------------------------------------------------- checks

def _check_model(agent) -> list[Result]:
    """Two questions, not one: is the server there, and is a model loaded.

    They fail separately and need different answers, and rolling them together
    produced the unhelpful "LM Studio is not working".
    """
    lister = getattr(agent.provider, "available_models", None)
    if lister is None:
        # Not every provider is LM Studio, and saying so beats inventing a
        # verdict about a server that was never involved.
        return [Result("provider", "Model server", UNKNOWN,
                       f"{type(agent.provider).__name__} does not report a server."),
                Result("model", "Model", UNKNOWN, "Nothing to ask.")]
    # Named from the provider in use. These sentences said "LM Studio" outright,
    # so a cloud model that was merely out of credit sent the user off to start
    # a local program that had nothing to do with it.
    service = str(getattr(agent.provider, "SERVICE", "") or "Model server")
    remote = bool(getattr(agent.provider, "is_remote", lambda: False)())
    fix = ("Check the key and the address in Settings." if remote
           else "Start LM Studio and open its local server.")
    models, error, seconds = _timed(lister)
    if error is not None:
        return [Result("provider", service, FAIL,
                       f"No answer from {getattr(agent.provider, 'base_url', service)}: {error}",
                       fix),
                Result("model", "Model", UNKNOWN,
                       "Cannot tell while the server is unreachable.")]
    reachable = Result("provider", service, OK,
                       f"Answered in {seconds:.1f}s with {len(models or [])} model(s).")
    if not models:
        return [reachable, Result("model", "Model", FAIL,
                                  "The server is running but no model is loaded.",
                                  fix if remote else "Load a model in LM Studio.")]
    selected, model_error, _ = _timed(agent.provider.selected_model)
    if model_error is not None:
        return [reachable, Result("model", "Model", WARN,
                                  f"Could not tell which model is selected: {model_error}", fix)]
    return [reachable, Result("model", "Model", OK, f"{selected} is selected."),
            _check_context(agent)]


#: Below this, an ordinary Aura conversation is truncated within a few turns —
#: the system prompt alone is around 4,000 characters before anything is said.
COMFORTABLE_CONTEXT = 16384


def _check_context(agent) -> Result:
    """How much context the loaded model actually has.

    Every silence Aura ever recorded was a conversation larger than this number,
    and nothing displayed it. The server truncates a conversation that will not
    fit, and when the truncation removes the last thing the user said, the chat
    template refuses the request outright — which arrived looking exactly like a
    model that had chosen to say nothing.
    """
    reader = getattr(agent.provider, "loaded_context", None)
    if reader is None:
        return Result("context", "Context window", UNKNOWN,
                      "This provider does not report one.")
    size, error, _ = _timed(reader)
    if error is not None or not size:
        return Result("context", "Context window", UNKNOWN,
                      "The server did not report the loaded context length.")
    if size < COMFORTABLE_CONTEXT:
        return Result("context", "Context window", WARN,
                      f"The model is loaded with only {size:,} tokens of context. "
                      f"Longer conversations are silently truncated, which shows up "
                      f"as Aura going quiet mid-task.",
                      "Raise the context length in LM Studio and load the model again.")
    # Not the answer *limit*. That is a ceiling the server does not hold back
    # against the prompt — measured: an 18,880-token prompt succeeded with the
    # limit at 64,000 on a 66,816-token window. Subtracting it here said a
    # perfectly workable setup had 2,816 tokens left, and the preflight
    # believed it and started cutting conversations that fitted.
    limit = max((sampling.for_turn([kind_tools], agent.config.data).max_tokens
                 for kind_tools in ("write_file", "read_file", "remember_name")),
                default=int(agent.config.data.get("max_tokens", 0) or 0))
    reserved = context_budget.answer_reserve(limit, size, getattr(agent, "budget", None))
    room = size - reserved
    if limit > size:
        # Harmless but confused: an answer can never reach a cap larger than the
        # window it is written into, so the number promises something it cannot
        # do. Worth saying once rather than leaving as a puzzle.
        return Result("context", "Context window", WARN,
                      f"{size:,} tokens, and the response limit is set to {limit:,} — "
                      f"larger than the whole window, so an answer can never reach it. "
                      f"Aura keeps {reserved:,} clear for the reply, leaving "
                      f"{room:,} for the conversation.",
                      "Set the response limits in Settings below the context length, "
                      "or raise the context length in LM Studio.")
    return Result("context", "Context window", OK,
                  f"{size:,} tokens, with {room:,} left for the conversation after "
                  f"the {reserved:,} kept clear for a reply.")


def _check_vision(agent) -> Result:
    mode = str(agent.config.data.get("vision_mode", "auto")).casefold()
    if mode == "off":
        return Result("vision", "Images", OK, "Turned off in Settings.")
    enabled, error, _ = _timed(agent.vision_enabled)
    if error is not None:
        return Result("vision", "Images", UNKNOWN, f"Could not be checked: {error}")
    if enabled:
        return Result("vision", "Images", OK, "The model accepts images.")
    return Result("vision", "Images", WARN,
                  "This model would not accept an image.",
                  "Load a model that reads images, or set Images to Always in Settings.")


def _check_speech(speech) -> Result:
    if speech is None:
        # Not the same as unsupported, and saying "Windows only" on Windows is
        # the kind of confident wrong answer a self-check must never give.
        return Result("speech", "Speech out", UNKNOWN, "No speech engine is configured.")
    if not getattr(speech, "available", lambda: False)():
        return Result("speech", "Speech out", WARN,
                      "Local speech output is supported on Windows only.")
    if not getattr(speech, "enabled", False):
        return Result("speech", "Speech out", OK, "Turned off in Settings.")
    if getattr(speech, "engine", "") == "piper" and speech.neural_available():
        return Result("speech", "Speech out", OK,
                      f"Piper is ready ({Path(speech.neural_model).name}).")
    voices = speech.installed_voices()
    if not voices:
        return Result("speech", "Speech out", FAIL, "No voice is installed at all.",
                      "Add a Windows voice, or put a Piper model in aura-voices.")
    return Result("speech", "Speech out", OK, f"Windows speech, {len(voices)} voice(s).")


def _check_voice_input(voice) -> Result:
    if voice is None:
        return Result("voice", "Voice in", UNKNOWN, "No recognizer is configured.")
    capabilities, error, _ = _timed(voice.capabilities)
    if error is not None:
        return Result("voice", "Voice in", UNKNOWN, f"Could not be checked: {error}")
    active = str((capabilities or {}).get("active_engine", "unavailable"))
    if active == "unavailable":
        return Result("voice", "Voice in", WARN, "No offline recognizer is installed.",
                      "Install the packages in requirements-voice.txt to talk to Aura.")
    if not (capabilities or {}).get("streaming"):
        return Result("voice", "Voice in", WARN,
                      f"{active} is installed but no microphone stream is available.",
                      "Check that a microphone is connected and permitted.")
    return Result("voice", "Voice in", OK, f"{active}, microphone available.")


def _check_workspace(agent) -> Result:
    """Writability has no honest answer that does not involve trying."""
    root = Path(agent.sandbox.root)
    if not root.is_dir():
        return Result("workspace", "Workspace", FAIL, f"{root} is not there.",
                      "Recreate the folder, or point Aura at another one.")
    probe = root / ".aura-write-probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Result("workspace", "Workspace", FAIL, f"Cannot write to {root}: {exc}",
                      "Check the folder's permissions, or that the disk is not full.")
    free = shutil.disk_usage(root).free / 1_073_741_824
    if free < 1:
        return Result("workspace", "Workspace", WARN,
                      f"Writable, but only {free:.1f} GB free on the disk.",
                      "Free some space before building anything large.")
    return Result("workspace", "Workspace", OK,
                  f"Writable, {free:.0f} GB free.")


def _check_database(agent) -> Result:
    path = Path(getattr(agent.db, "path", ""))
    if not path.is_file():
        return Result("database", "Storage", UNKNOWN, "No database file yet.")
    try:
        # `with sqlite3.connect(...)` manages the transaction, not the
        # connection — it does not close it. Every self-check would have leaked
        # one, and on Windows the open handle keeps the WAL files locked.
        with closing(sqlite3.connect(path)) as connection:
            verdict = connection.execute("PRAGMA quick_check").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        return Result("database", "Storage", FAIL, f"Could not be read: {exc}",
                      "Aura keeps the previous JSONL files; ask before deleting anything.")
    if str(verdict).casefold() != "ok":
        return Result("database", "Storage", FAIL, f"Integrity check said: {verdict}",
                      "Stop using Aura and take a copy of aura.db before anything else.")
    size = path.stat().st_size / 1_048_576
    return Result("database", "Storage", OK,
                  f"Healthy, schema v{version}, {size:.1f} MB.")


def _check_search(agent, search_service=None) -> Result:
    mode = str(agent.config.data.get("search_mode", "off"))
    endpoint = str(agent.config.data.get("search_endpoint") or "")
    if mode == "off" and not endpoint:
        return Result("search", "Web search", OK, "Turned off in Settings.")
    if search_service is not None and getattr(search_service, "error", ""):
        return Result("search", "Web search", FAIL, search_service.error,
                      "See Settings → Search.")
    if search_service is not None and search_service.status().get("running"):
        return Result("search", "Web search", OK,
                      f"Answering on {search_service.status()['endpoint']}.")
    if not endpoint:
        return Result("search", "Web search", WARN, "Switched on but no address is set.",
                      "Set the SearXNG address in Settings.")
    return Result("search", "Web search", WARN, f"Nothing is answering at {endpoint}.",
                  "Start the search engine, or switch search off in Settings.")


def run(agent, speech=None, voice=None, search_service=None) -> dict:
    """Every check, in the order a person would want to read them."""
    results: list[Result] = []
    results.extend(_check_model(agent))
    for check in (lambda: _check_vision(agent),
                  lambda: _check_workspace(agent),
                  lambda: _check_database(agent),
                  lambda: _check_speech(speech),
                  lambda: _check_voice_input(voice),
                  lambda: _check_search(agent, search_service)):
        outcome, error, _ = _timed(check)
        # A check that breaks must report itself rather than take the report
        # down with it: the whole point is to work when things are wrong.
        results.append(outcome if error is None else Result(
            "unknown", "Check failed", UNKNOWN, f"This check could not run: {error}"))
    worst = FAIL if any(r.status == FAIL for r in results) else (
        WARN if any(r.status == WARN for r in results) else OK)
    return {"status": worst,
            "checks": [result.as_dict() for result in results],
            "failed": sum(1 for r in results if r.status == FAIL),
            "warned": sum(1 for r in results if r.status == WARN)}
