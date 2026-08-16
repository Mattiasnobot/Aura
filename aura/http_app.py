from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import urllib.request
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .web_bridge import AuraWebBridge


DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 6_000_000
MAX_PREVIEW_ASSET_BYTES = 5_000_000
PREVIEW_PREFIX = "/workspace-preview/"
API_METHODS = {
    "get_bootstrap", "poll_events", "submit", "stop", "resolve_approval",
    "check_provider", "get_settings", "save_settings", "get_models", "get_voices",
    "complete_onboarding", "restart_onboarding",
    "start_voice", "stop_voice", "get_microphones", "calibrate_voice", "preview_voice",
    "open_workspace", "recent_tasks", "rollback_task",
    "get_mind_graph", "save_ui_state", "workspace_snapshot", "preview_workspace_file",
    "open_workspace_item", "import_files", "get_personal_memory", "add_personal_memory",
    "update_personal_memory", "forget_personal_memory", "revert_personal_memory",
    "export_personal_memory", "export_diagnostics",
    "list_permissions", "grant_folder_access", "grant_domain_access", "network_status",
    "autonomy_status", "pause_autonomy", "emergency_stop",
    "revoke_folder_access",
    "revoke_all_permissions", "external_changes", "undo_external_change", "resume_task",
    "list_sessions", "new_session", "open_session", "archive_session",
    "search_conversations", "export_conversation",
    "create_workspace_folder", "create_workspace_file", "rename_workspace_item",
    "move_workspace_item", "copy_workspace_item", "delete_workspace_item",
    "list_trash", "restore_workspace_item", "undo_workspace_change",
    "workspace_change_history", "compare_workspace_files",
    "start_preview_server", "stop_preview_server", "preview_server_status",
    "preview_server_log", "check_workspace_assets",
}


class AuraHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], bridge: AuraWebBridge, web_root: Path) -> None:
        self.bridge = bridge
        self.web_root = web_root.resolve()
        self.session_token = secrets.token_urlsafe(32)
        super().__init__(address, AuraRequestHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def allowed_origins(self) -> set[str]:
        """`localhost` is the same loopback interface, and people type it.

        Only these two names are accepted: any other host — including a domain
        that resolves to 127.0.0.1 — still sends its own Origin and is refused.
        """
        port = self.server_address[1]
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class AuraRequestHandler(BaseHTTPRequestHandler):
    server: AuraHTTPServer
    server_version = "AuraLocal/1.0"
    sys_version = ""

    def log_message(self, _format: str, *_args) -> None:
        return

    def _headers(self, status: int, content_type: str, length: int,
                 *, session_cookie: bool = False,
                 frame_policy: str | None = "DENY",
                 content_security_policy: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if frame_policy:
            self.send_header("X-Frame-Options", frame_policy)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", content_security_policy or (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ))
        self.send_header("X-Aura-Server", "html-local-v1")
        if session_cookie:
            self.send_header(
                "Set-Cookie",
                f"AuraSession={self.server.session_token}; Path=/; HttpOnly; SameSite=Strict",
            )
        self.end_headers()

    def _send_bytes(self, data: bytes, content_type: str, status: int = HTTPStatus.OK,
                    *, session_cookie: bool = False,
                    frame_policy: str | None = "DENY",
                    content_security_policy: str | None = None) -> None:
        self._headers(status, content_type, len(data), session_cookie=session_cookie,
                      frame_policy=frame_policy,
                      content_security_policy=content_security_policy)
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers may cancel an asset request while navigating; that is not a server failure.
            return

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK,
                   *, session_cookie: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status,
                         session_cookie=session_cookie)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            # The version is here so an update can be confirmed from the
            # browser, without opening a file or trusting a cached page.
            self._send_json({"app": "Aura", "interface": "html", "ready": True,
                             "version": __version__})
            return
        if path == "/favicon.ico":
            self._send_bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            return
        if path.startswith(PREVIEW_PREFIX):
            self._serve_workspace_preview(path)
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8", True),
            "/index.html": ("index.html", "text/html; charset=utf-8", True),
            "/styles.css": ("styles.css", "text/css; charset=utf-8", False),
            "/avatar-face.js": ("avatar-face.js", "text/javascript; charset=utf-8", False),
            "/app.js": ("app.js", "text/javascript; charset=utf-8", False),
        }
        asset = assets.get(path)
        if not asset:
            self._send_json({"ok": False, "error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        name, content_type, session_cookie = asset
        target = self.server.web_root / name
        try:
            data = target.read_bytes()
        except OSError:
            self._send_json({"ok": False, "error": "Aura interface asset is unavailable."},
                            HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_bytes(data, content_type, session_cookie=session_cookie)

    def _serve_workspace_preview(self, request_path: str) -> None:
        if not self._session_cookie_valid():
            self._send_json({"ok": False, "error": "Unauthorized local preview."},
                            HTTPStatus.FORBIDDEN)
            return
        relative = unquote(request_path[len(PREVIEW_PREFIX):])
        if not relative:
            self._send_json({"ok": False, "error": "Preview file is missing."},
                            HTTPStatus.NOT_FOUND)
            return
        try:
            target = self.server.bridge.agent.sandbox.path(relative)
            if not target.is_file():
                raise FileNotFoundError(relative)
            size = target.stat().st_size
            if size > MAX_PREVIEW_ASSET_BYTES:
                self._send_json({"ok": False, "error": "Preview asset exceeds the 5 MB limit."},
                                HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            data = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError, ValueError):
            self._send_json({"ok": False, "error": "Workspace preview asset was not found."},
                            HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
                "application/javascript", "application/json", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        preview_policy = (
            "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; media-src 'self'; "
            "connect-src 'none'; object-src 'none'; frame-src 'none'; "
            "base-uri 'self'; form-action 'none'; frame-ancestors 'self'"
        )
        self._send_bytes(data, content_type, frame_policy="SAMEORIGIN",
                         content_security_policy=preview_policy)

    def do_POST(self) -> None:
        if urlparse(self.path).path not in {"/api/call", "/api/shutdown"}:
            self._send_json({"ok": False, "error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        if not self._authorized():
            self._send_json({"ok": False, "error": "Unauthorized local request."},
                            HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self._send_json({"ok": False, "error": "Request is too large."},
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be an object.")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/shutdown":
            self._send_json({"ok": True, "message": "Aura is shutting down."})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        method = str(payload.get("method", ""))
        args = payload.get("args", [])
        if method not in API_METHODS or not isinstance(args, list) or len(args) > 10:
            self._send_json({"ok": False, "error": "Unsupported API operation."},
                            HTTPStatus.BAD_REQUEST)
            return
        try:
            result = getattr(self.server.bridge, method)(*args)
            self._send_json({"ok": True, "result": result})
        except (TypeError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self._send_json({"ok": False, "error": "Aura's local backend could not complete that request."},
                            HTTPStatus.INTERNAL_SERVER_ERROR)

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and origin not in self.server.allowed_origins:
            return False
        if self.headers.get("X-Aura-Client") != "html-ui-v1":
            return False
        return self._session_cookie_valid()

    def _session_cookie_valid(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        session = cookie.get("AuraSession")
        return bool(session and secrets.compare_digest(session.value, self.server.session_token))


def create_server(bridge: AuraWebBridge, port: int = DEFAULT_PORT) -> AuraHTTPServer:
    web_root = Path(__file__).resolve().parent / "web"
    for required in ("index.html", "styles.css", "avatar-face.js", "app.js"):
        if not (web_root / required).is_file():
            raise RuntimeError(f"Aura HTML asset is missing: {required}")
    return AuraHTTPServer(("127.0.0.1", port), bridge, web_root)


def existing_aura_url(port: int) -> str | None:
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=0.4) as response:
            data = json.loads(response.read().decode("utf-8"))
            if response.headers.get("X-Aura-Server") == "html-local-v1" and data.get("app") == "Aura":
                return url
    except Exception:
        return None
    return None


def open_browser(url: str) -> None:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
    elif not webbrowser.open(url, new=1):
        raise RuntimeError(f"Open Aura in a browser at {url}")


def run() -> None:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 10):
        existing = existing_aura_url(port)
        if existing:
            open_browser(existing)
            return

    bridge = AuraWebBridge()
    server: AuraHTTPServer | None = None
    try:
        for port in range(DEFAULT_PORT, DEFAULT_PORT + 10):
            try:
                server = create_server(bridge, port)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("Aura could not reserve a local HTML port (8765–8774).")
        url_file = bridge.agent.sandbox.meta / "web-url.txt"
        url_file.write_text(server.origin + "\n", encoding="utf-8")
        open_browser(server.origin)
        server.serve_forever(poll_interval=0.2)
    finally:
        if server is not None:
            server.server_close()
        bridge.shutdown()
