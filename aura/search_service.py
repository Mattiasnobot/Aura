"""Starting and stopping the user's SearXNG alongside Aura.

SearXNG cannot live *inside* Aura. It is a Flask application with a large
dependency tree, and Aura's core is deliberately standard library only. What
Aura can own is its **lifecycle**: start it on launch, wait until it actually
answers, stop it on quit, and say plainly when it is not installed. The engine
stays an optional component the user installed, in the same way the voice
packages already are.

Owning the lifecycle also removes the two things most likely to go wrong:

* **JSON is on.** SearXNG serves HTML only by default, which is the first wall
  anyone hits. Aura writes the settings file itself, so the format is not
  something the user has to remember.
* **It is never exposed.** `bind_address` is forced to a loopback address. A
  search engine that answers the local network is not what anyone asked for by
  installing one for themselves.

Nothing here is reachable by the model. There is no tool that sets the install
path, starts the process, or stops it — only the user, through Settings. A
component that can launch a program is exactly the kind the model must not be
able to point somewhere new.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
import pathlib
from pathlib import Path

from .errors import UserFacingError

#: How the documented non-Docker install is run. A fixed shape, never a command
#: line from configuration: the user chooses *where* SearXNG is, not *what* runs.
MODULE = "searx.webapp"

DEFAULT_PORT = 8888
#: Generous, because the first start compiles templates and loads every engine.
START_TIMEOUT_SECONDS = 90.0
STOP_TIMEOUT_SECONDS = 10.0

#: SearXNG colours its own log output, and those escapes are meaningless in a
#: settings panel.
_ANSI = re.compile(chr(27) + r"\[[0-9;]*[A-Za-z]")   # a literal ESC here would be invisible


def explain(detail: str) -> str:
    """Turn a startup traceback into something the user can act on.

    The one that will actually happen on Windows deserves naming: SearXNG's
    `searx/valkeydb.py` imports `pwd`, a Unix-only module, so the application
    does not import at all. Reporting the raw ModuleNotFoundError would send
    someone hunting for a missing package that cannot exist here.
    """
    clean = _ANSI.sub("", detail).strip()
    if "No module named 'pwd'" in clean or 'No module named "pwd"' in clean:
        return ("SearXNG does not run natively on Windows: it imports the Unix-only "
                "'pwd' module. Run it under Docker Desktop or WSL and Aura will use "
                "it — if it is already listening on the port, Aura picks it up on its "
                "own.")
    return clean


class SearchServiceError(UserFacingError):
    """Something about the install or the process the user needs to know."""


@dataclass(frozen=True)
class Install:
    """A SearXNG checkout with its own interpreter."""

    root: Path
    python: Path

    @property
    def settings_path(self) -> Path:
        return self.root / "aura-settings.yml"


def find_install(configured: object) -> Install:
    """Locate SearXNG, or explain exactly what is missing.

    Every failure here is something the user can act on, so each one names the
    thing it could not find rather than reporting "not installed".
    """
    raw = str(configured or "").strip().strip('"')
    if not raw:
        raise SearchServiceError(
            "No SearXNG folder is set. Install it once, then give Aura the folder "
            "under Settings — or leave it empty to keep search off.")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise SearchServiceError(f"There is no folder at {root}.")
    if not (root / "searx" / "webapp.py").is_file():
        raise SearchServiceError(
            f"{root} does not look like a SearXNG checkout: it has no "
            f"searx/webapp.py. Point Aura at the folder you cloned into.")
    for candidate in (root / "venv" / "Scripts" / "python.exe",
                      root / "venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe",
                      root / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return Install(root=root, python=candidate)
    raise SearchServiceError(
        f"{root} has no virtual environment. SearXNG needs its own packages; "
        f"create one in {root / 'venv'} and install SearXNG into it.")


def write_settings(install: Install, port: int, secret: str) -> Path:
    """Write the settings file Aura runs SearXNG with.

    Deliberately Aura's own file rather than an edit of the user's: it is
    regenerated on every launch, so nothing here quietly rots, and their own
    settings.yml is left alone.
    """
    text = f"""# Written by Aura on every launch. Edit Aura's Settings, not this file.
use_default_settings: true

general:
  debug: false
  instance_name: "Aura search"

server:
  # Loopback only. A search engine started for one person should not answer
  # anyone else on the network.
  bind_address: "127.0.0.1"
  port: {port}
  secret_key: "{secret}"
  limiter: false
  public_instance: false

search:
  # The reason Aura writes this file at all: without json here, SearXNG answers
  # every request with a web page and search silently does not work.
  formats:
    - html
    - json

ui:
  static_use_hash: true
"""
    install.settings_path.write_text(text, encoding="utf-8")
    return install.settings_path


#: The official image. Fixed, because "which image to run" is not a question a
#: settings field should be able to answer.
DOCKER_IMAGE = "searxng/searxng:latest"
CONTAINER_NAME = "aura-searxng"


def find_docker() -> str:
    """Locate the docker command.

    Docker Desktop installs per-user and does not always put its bin directory
    on PATH, so `docker` missing from PATH says nothing about whether Docker is
    installed. Both were true on the machine this was written on.
    """
    found = shutil.which("docker")
    if found:
        return found
    candidates = [
        pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop"
        / "resources" / "bin" / "docker.exe",
        pathlib.Path(os.environ.get("ProgramFiles", "")) / "Docker" / "Docker"
        / "resources" / "bin" / "docker.exe",
        pathlib.Path("/usr/local/bin/docker"),
        pathlib.Path("/usr/bin/docker"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise SearchServiceError(
        "Docker was not found. Install Docker Desktop, start it, and Aura will "
        "run the search engine in a container.")


def write_container_settings(directory: Path, secret: str) -> Path:
    """The settings mounted into the container.

    The container carries its own settings.yml, so the JSON format still has to
    be turned on from outside: the same trap as a native install, in a different
    place. `bind_address` is deliberately absent here — the container binds
    inside its own network namespace, and what keeps search off the network is
    publishing the port to 127.0.0.1 only.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "settings.yml"
    target.write_text(
        "# Written by Aura on every launch, mounted read-only into the container.\n"
        "use_default_settings: true\n"
        "\n"
        "general:\n"
        '  debug: false\n'
        '  instance_name: "Aura search"\n'
        "\n"
        "server:\n"
        '  secret_key: "' + secret + '"\n'
        "  limiter: false\n"
        "  public_instance: false\n"
        "\n"
        "search:\n"
        "  # Without json here SearXNG answers every request with a web page and\n"
        "  # search silently does not work.\n"
        "  formats:\n"
        "    - html\n"
        "    - json\n",
        encoding="utf-8")
    return target


def port_answers(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


class SearchService:
    """Supervises one SearXNG process for as long as Aura is running."""

    def __init__(self, log=None) -> None:
        self.log = log
        self.process: subprocess.Popen | None = None
        self.port = DEFAULT_PORT
        self.error = ""
        self._lock = threading.Lock()
        #: True when the port was already busy at start: someone else owns that
        #: process, so Aura must read it and must not stop it on the way out.
        self.adopted = False
        #: Set when Aura started a container rather than a bare process.
        self.container = False
        self.docker = ""

    # ------------------------------------------------------------------ state

    @property
    def running(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def status(self) -> dict:
        alive = self.running or ((self.adopted or self.container) and port_answers(self.port))
        return {"running": alive, "container": self.container,
                "adopted": self.adopted, "port": self.port,
                "endpoint": f"http://127.0.0.1:{self.port}",
                "error": self.error}

    def _record(self, action: str, status: str = "ok", **details) -> None:
        if self.log is not None:
            try:
                self.log.record(action, status, **details)
            except Exception:            # logging must never break startup
                pass

    # ------------------------------------------------------------------ start

    def start_native(self, configured_path: object, port: int = DEFAULT_PORT) -> dict:
        """Start SearXNG and wait until it actually answers.

        Returning before it answers would hand the user a working-looking
        setting and a first search that fails, which is the same dishonesty as
        reporting a task complete before it is.
        """
        self.error = ""
        self.port = int(port or DEFAULT_PORT)
        if self.running:
            return self.status()
        if port_answers(self.port):
            # Something is already there — very likely a SearXNG the user runs
            # themselves. Read it, do not fight it, and do not kill it later.
            self.adopted = True
            self._record("search_service", "ok", adopted=True, port=self.port)
            return self.status()

        install = find_install(configured_path)
        settings = write_settings(install, self.port, secret=os.urandom(16).hex())
        environment = dict(os.environ)
        environment["SEARXNG_SETTINGS_PATH"] = str(settings)
        environment["SEARXNG_BASE_URL"] = f"http://127.0.0.1:{self.port}/"
        environment["SEARXNG_PORT"] = str(self.port)
        environment["SEARXNG_BIND_ADDRESS"] = "127.0.0.1"
        environment["SEARXNG_DEBUG"] = "0"
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [str(install.python), "-m", MODULE],
                cwd=str(install.root), env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=creation)
        except OSError as exc:
            self.error = f"SearXNG could not be started: {exc}"
            self._record("search_service", "error", error=self.error)
            raise SearchServiceError(self.error) from exc

        with self._lock:
            self.process = process
        self.adopted = False

        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = ""
                if process.stderr is not None:
                    detail = process.stderr.read(2000).decode("utf-8", "replace").strip()
                last = explain(detail).splitlines()[-1] if detail else ""
                self.error = last or "SearXNG stopped immediately without saying why."
                if detail and "pwd" not in detail:
                    self.error = "SearXNG stopped immediately. " + self.error
                if process.stderr is not None:
                    process.stderr.close()
                with self._lock:
                    self.process = None
                self._record("search_service", "error", error=self.error)
                raise SearchServiceError(self.error)
            if port_answers(self.port):
                self._record("search_service", "ok", port=self.port, adopted=False)
                return self.status()
            time.sleep(0.4)

        self.stop()
        self.error = (f"SearXNG did not answer on port {self.port} within "
                      f"{int(START_TIMEOUT_SECONDS)} seconds.")
        self._record("search_service", "error", error=self.error)
        raise SearchServiceError(self.error)

    def start_docker(self, settings_dir, port: int = DEFAULT_PORT) -> dict:
        """Run SearXNG in a container, and stop it when Aura stops.

        This is the route that works on Windows, where SearXNG cannot run
        natively at all. The command shape is fixed: the user chooses whether
        Docker is used, never what gets run.
        """
        self.error = ""
        self.port = int(port or DEFAULT_PORT)
        if port_answers(self.port):
            self.adopted = True
            self._record("search_service", "ok", adopted=True, port=self.port, docker=True)
            return self.status()

        docker = find_docker()
        if not self._image_present(docker):
            # Deliberately not pulled automatically: several hundred megabytes
            # fetched without being asked is not something Aura should do on its
            # own, and the user already chose to install things themselves.
            self.error = ("The search engine image is not downloaded yet. Run "
                          "`docker pull " + DOCKER_IMAGE + "` once, then restart Aura.")
            self._record("search_service", "error", error=self.error)
            raise SearchServiceError(self.error)

        settings = write_container_settings(Path(settings_dir), os.urandom(16).hex())
        self._run_docker(docker, ["rm", "-f", CONTAINER_NAME])
        started = self._run_docker(docker, [
            "run", "-d", "--name", CONTAINER_NAME,
            # 127.0.0.1 rather than a bare port: published to this machine only.
            "-p", "127.0.0.1:" + str(self.port) + ":8080",
            "-v", str(settings.parent) + ":/etc/searxng:ro",
            "-e", "SEARXNG_BASE_URL=http://localhost:" + str(self.port) + "/",
            "-e", "SEARXNG_SECRET=" + os.urandom(16).hex(),
            DOCKER_IMAGE,
        ])
        if started.returncode != 0:
            detail = explain(started.stderr or "").strip()
            self.error = ("The search container did not start. "
                          + (detail.splitlines()[-1] if detail else "Docker gave no reason."))
            self._record("search_service", "error", error=self.error)
            raise SearchServiceError(self.error)

        self.container = True
        self.docker = docker
        self.adopted = False
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if port_answers(self.port):
                self._record("search_service", "ok", port=self.port, docker=True)
                return self.status()
            time.sleep(0.5)

        logs = self._run_docker(docker, ["logs", "--tail", "20", CONTAINER_NAME])
        detail = explain((logs.stderr or "") + (logs.stdout or "")).strip()
        self.stop()
        self.error = ("The search container did not answer on port " + str(self.port)
                      + " within " + str(int(START_TIMEOUT_SECONDS)) + " seconds. "
                      + (detail.splitlines()[-1][:200] if detail else ""))
        self._record("search_service", "error", error=self.error)
        raise SearchServiceError(self.error)

    def _image_present(self, docker: str) -> bool:
        return self._run_docker(docker, ["image", "inspect", DOCKER_IMAGE]).returncode == 0

    def _run_docker(self, docker: str, arguments: list) -> subprocess.CompletedProcess:
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            return subprocess.run([docker, *arguments], capture_output=True, text=True,
                                  timeout=180, creationflags=creation)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SearchServiceError("Docker did not respond: " + str(exc)) from exc

    # ------------------------------------------------------------------- stop

    def stop(self) -> None:
        """Stop what Aura started. Anything it adopted is left running."""
        if self.container and self.docker:
            self.container = False
            self._run_docker(self.docker, ["rm", "-f", CONTAINER_NAME])
            self._record("search_service", "ok", stopped=True, docker=True)
            return
        with self._lock:
            process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        if process.stderr is not None:
            process.stderr.close()
        self._record("search_service", "ok", stopped=True)
