"""Running a script the model wrote, with Aura's tools available inside it.

Ten files read one at a time cost 4,710 tokens of a 66,816-token window, and
they stay there for the rest of the conversation. A script that reads the same
ten and prints a summary costs a few hundred. That is the whole point: the
intermediate results never enter the context at all.

The design is from `execute_code` in NousResearch's hermes-agent (MIT, © 2025
Nous Research) — a child process, an RPC channel back to the agent, a generated
stub module the script imports, and only stdout returned. Three things differ
here.

**Loopback TCP, not a Unix socket.** Aura runs on Windows, where AF_UNIX is
unreliable; hermes-agent falls back to TCP there too. A TCP port on 127.0.0.1
is reachable by any process on this machine, which a filesystem socket is not,
so every call must carry a token minted for this run and thrown away after it.
Without that, any local program could drive Aura's tools.

**Tool calls go through `_execute_tool`.** Not a parallel path: the same
function the model's own tool calls use. Approvals, the workspace sandbox,
recovery snapshots, and the action log all apply exactly as they do to a direct
call, because they are the same code. That is what keeps this from being a hole
in everything else Aura enforces.

**The script body is not sandboxed, and this file will not pretend otherwise.**
It is Python running as Mat, so it can open files and sockets directly whatever
the tool whitelist says. Mat chose that deliberately, having been shown the
trade. What is done here is what can honestly be done: credentials are stripped
from the environment, the process is killable, and the limits are real.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: Substrings that mark a variable as a secret. Matched case-insensitively on
#: the name, because the value is not something to go looking through.
SECRET_MARKERS = ("key", "token", "secret", "password", "credential", "passwd",
                  "auth", "api")

#: Passed through by exact name. Anything not here and not obviously safe is
#: dropped, which is the right way round for a list nobody will maintain.
SAFE_VARIABLES = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                  "HOME", "USERPROFILE", "LANG", "LC_ALL", "TZ",
                  "PYTHONPATH", "PYTHONIOENCODING", "VIRTUAL_ENV",
                  "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")


@dataclass
class CodeResult:
    """What a finished script has to say for itself."""

    status: str                 # ok | error | timeout | no_output
    output: str = ""
    error: str = ""
    tool_calls: int = 0
    seconds: float = 0.0
    tools_used: list[str] = field(default_factory=list)


class ToolBridge:
    """The RPC listener: one thread, one token, one script's worth of calls."""

    def __init__(self, dispatch: Callable[[str, dict], dict], *,
                 allowed: frozenset[str], max_calls: int) -> None:
        self.dispatch = dispatch
        self.allowed = allowed
        self.max_calls = max_calls
        self.token = secrets.token_hex(16)
        self.calls = 0
        self.used: list[str] = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self._server.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="aura-code-rpc")

    @property
    def port(self) -> int:
        return int(self._server.getsockname()[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # A bridge built and closed without ever serving is a normal thing —
        # a script that fails to stage, or a caller checking one decision —
        # and joining an unstarted thread raises rather than doing nothing.
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self._server.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            with connection:
                try:
                    self._handle(connection)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(30)
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                return
            raw += chunk
        request = json.loads(raw.decode("utf-8"))
        connection.sendall(
            (json.dumps(self._answer(request), ensure_ascii=False) + "\n").encode("utf-8"))

    def _answer(self, request: dict) -> dict:
        # The token is the only thing standing between Aura's tools and any
        # other process on this machine, since a loopback port has no owner.
        if not secrets.compare_digest(str(request.get("token", "")), self.token):
            return {"ok": False, "error": "not authorised"}
        name = str(request.get("tool", ""))
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "arguments must be an object"}
        if name not in self.allowed:
            return {"ok": False, "error":
                    f"{name!r} cannot be called from a script. Available: "
                    + ", ".join(sorted(self.allowed))}
        if self.calls >= self.max_calls:
            return {"ok": False, "error":
                    f"this script has used its {self.max_calls} tool calls"}
        self.calls += 1
        self.used.append(name)
        try:
            return self.dispatch(name, arguments)
        except Exception as exc:      # a broken tool must not kill the bridge
            return {"ok": False, "error": str(exc)}


STUB = '''"""Aura's tools, callable from this script.

Each function sends its arguments to Aura and returns what the tool returned.
The same code runs as when Aura calls the tool herself, so approvals, the
workspace sandbox and the recovery snapshots all still apply.
"""
import json as _json
import os as _os
import socket as _socket

_PORT = int(_os.environ["AURA_RPC_PORT"])
_TOKEN = _os.environ["AURA_RPC_TOKEN"]


class ToolError(RuntimeError):
    """A tool refused. The message is what Aura would have shown the model."""


def _call(tool, arguments):
    payload = _json.dumps({"token": _TOKEN, "tool": tool, "arguments": arguments},
                          ensure_ascii=False, default=str) + "\\n"
    with _socket.create_connection(("127.0.0.1", _PORT), timeout=120) as link:
        link.sendall(payload.encode("utf-8"))
        raw = b""
        while not raw.endswith(b"\\n"):
            chunk = link.recv(65536)
            if not chunk:
                break
            raw += chunk
    answer = _json.loads(raw.decode("utf-8") or "{}")
    if not answer.get("ok"):
        raise ToolError(answer.get("error", "the tool failed"))
    return answer


{functions}
'''

FUNCTION = '''
def {name}(**arguments):
    """{doc}"""
    return _call({name!r}, arguments)
'''


def build_stub(tools: dict[str, str]) -> str:
    """The module the script imports, one function per allowed tool.

    Substituted rather than formatted: the stub's own body contains a JSON
    object literal, and `str.format` reads those braces as fields — which it
    did, failing with a bare KeyError on "token" that said nothing about where
    it came from.
    """
    rendered = "".join(
        FUNCTION.format(name=name, doc=(summary or "").replace('"', "'")[:200])
        for name, summary in sorted(tools.items()))
    return STUB.replace("{functions}", rendered)


def safe_environment() -> dict[str, str]:
    """The parent's environment with anything that looks like a secret removed."""
    clean: dict[str, str] = {}
    for name in SAFE_VARIABLES:
        value = os.environ.get(name)
        if value and not _is_secret(name):
            clean[name] = value
    return clean


def _is_secret(name: str) -> bool:
    lowered = name.casefold()
    return any(marker in lowered for marker in SECRET_MARKERS)


def run_script(script: str, *, dispatch: Callable[[str, dict], dict],
               tools: dict[str, str], workspace: Path,
               timeout: float = 120.0, max_calls: int = 50,
               max_output: int = 20_000) -> CodeResult:
    """Run one script to completion, and return only what it printed."""
    bridge = ToolBridge(dispatch, allowed=frozenset(tools), max_calls=max_calls)
    bridge.start()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="aura-code-") as staging:
            stage = Path(staging)
            (stage / "aura_tools.py").write_text(build_stub(tools), encoding="utf-8")
            (stage / "script.py").write_text(script, encoding="utf-8")
            environment = safe_environment()
            environment["AURA_RPC_PORT"] = str(bridge.port)
            environment["AURA_RPC_TOKEN"] = bridge.token
            environment["PYTHONPATH"] = str(stage)
            environment["PYTHONIOENCODING"] = "utf-8"
            # Its own process group, so a script that spawns something can be
            # killed with its children rather than leaving them behind.
            creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                finished = subprocess.run(
                    [sys.executable, str(stage / "script.py")],
                    cwd=str(workspace), env=environment, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=timeout, creationflags=creation, check=False)
            except subprocess.TimeoutExpired:
                return CodeResult("timeout", "", f"the script ran past {timeout:.0f}s "
                                  f"and was stopped", bridge.calls,
                                  time.monotonic() - started, list(bridge.used))
            output = (finished.stdout or "")[:max_output]
            truncated = len(finished.stdout or "") > max_output
            if truncated:
                output += f"\n[only the first {max_output:,} characters are shown]"
            if finished.returncode != 0:
                return CodeResult("error", output, (finished.stderr or "")[-4000:],
                                  bridge.calls, time.monotonic() - started,
                                  list(bridge.used))
            if not output.strip():
                # A script that prints nothing has told the model nothing, which
                # reads as silence and is nearly always a mistake rather than a
                # result. Saying so is more use than returning an empty string.
                return CodeResult("no_output", "", "the script printed nothing, so "
                                  "there is no result to read — print what you found",
                                  bridge.calls, time.monotonic() - started,
                                  list(bridge.used))
            return CodeResult("ok", output, "", bridge.calls,
                              time.monotonic() - started, list(bridge.used))
    finally:
        bridge.stop()
