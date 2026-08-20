from __future__ import annotations

import threading
import time
from collections import deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .action_log import ActionLog
from .safety import WorkspaceSandbox

PORT_RANGE = range(8790, 8800)
PROTECTED_SEGMENTS = {".aura", ".aura-trash"}


class _PreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler_class) -> None:
        super().__init__(address, handler_class)
        self.access_log: deque[dict[str, Any]] = deque(maxlen=200)

    def record_access(self, method: str, path: str, status: object) -> None:
        self.access_log.append({"time": time.time(), "method": method, "path": path, "status": status})


class _PreviewRequestHandler(SimpleHTTPRequestHandler):
    server: _PreviewHTTPServer
    server_version = "AuraPreview/1.0"
    sys_version = ""

    def log_message(self, _format: str, *_args) -> None:
        return

    def log_request(self, code: object = "-", size: object = "-") -> None:
        self.server.record_access(self.command, self.path, code)

    def _blocked(self) -> bool:
        cleaned = self.path.split("?", 1)[0].split("#", 1)[0]
        segments = [segment for segment in cleaned.split("/") if segment]
        return bool(segments) and segments[0] in PROTECTED_SEGMENTS

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming convention
        if self._blocked():
            self.send_error(403, "Aura metadata is protected")
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming convention
        if self._blocked():
            self.send_error(403, "Aura metadata is protected")
            return
        super().do_HEAD()


class PreviewServer:
    """A locally-bound, user-initiated live preview of one workspace folder.

    Unlike the sandboxed /workspace-preview/ route, served pages execute
    scripts normally, so starting one is always an explicit user action.
    """

    def __init__(self, sandbox: WorkspaceSandbox, log: ActionLog) -> None:
        self.sandbox = sandbox
        self.log = log
        self._lock = threading.Lock()
        self._server: _PreviewHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._relative: str | None = None
        self._started_at: float | None = None

    def start(self, relative: str = ".") -> dict:
        with self._lock:
            cleaned = str(relative).strip() or "."
            target = self.sandbox.path(cleaned)
            if not target.is_dir():
                raise NotADirectoryError(cleaned)
            if self._server is not None:
                self._stop_locked()
            handler = partial(_PreviewRequestHandler, directory=str(target))
            server: _PreviewHTTPServer | None = None
            last_error: OSError | None = None
            for port in PORT_RANGE:
                try:
                    server = _PreviewHTTPServer(("127.0.0.1", port), handler)
                    break
                except OSError as exc:
                    last_error = exc
            if server is None:
                raise OSError(
                    f"No free preview port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}"
                ) from last_error
            self._server = server
            self._relative = cleaned
            self._started_at = time.time()
            self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="aura-preview")
            self._thread.start()
            port = server.server_address[1]
            self.log.record("start_preview_server", "ok", path=cleaned, port=port)
            return self._status_locked()

    def stop(self) -> dict:
        with self._lock:
            if self._server is None:
                raise RuntimeError("No preview server is running")
            served_path = self._relative
            self._stop_locked()
            self.log.record("stop_preview_server", "ok", path=served_path)
            return {"ok": True, "path": served_path}

    def stop_if_running(self) -> None:
        with self._lock:
            if self._server is not None:
                self._stop_locked()

    def _stop_locked(self) -> None:
        server = self._server
        thread = self._thread
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._relative = None
        self._started_at = None

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict:
        if self._server is None:
            return {"ok": True, "running": False}
        port = self._server.server_address[1]
        return {
            "ok": True, "running": True, "path": self._relative,
            "url": f"http://127.0.0.1:{port}/", "started_at": self._started_at,
        }

    def recent_log(self, limit: int = 50) -> list[dict]:
        with self._lock:
            if self._server is None:
                return []
            bounded = max(1, min(int(limit), 200))
            return list(self._server.access_log)[-bounded:]
