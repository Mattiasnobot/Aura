from __future__ import annotations

from .errors import AuraError

import ast
import base64
import hashlib
import json
import math
import os
import platform
import posixpath
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .action_log import ActionLog
from .autonomy import AutonomyGuard
from .commands import CommandAgent
from .config import ConfigStore
from .memory import MemoryStore
from .provider import LMStudioProvider, MockProvider, Provider, ProviderContext, ToolCall
from . import checks
from . import services
from . import websearch
from . import toolkit
from .toolkit import tool
from .turn import PASS, GateResult, TurnState
from .image_diff import compare_images
from .permissions import (ExternalReader, ExternalWriter, PermissionRefused,
                          PermissionStore, reject_unsafe_host)
from .preview_server import PreviewServer
from .safety import SandboxViolation, WorkspaceSandbox
from .screenshot import (ScreenshotUnavailable, browser_command_preview, capture,
                         find_browser)
from .search_index import WorkspaceIndex
from .tasks import TaskJournal
from .validation import check_accessibility, validate_project


EXTERNAL_TOOLS = {
    "list_granted_folders", "list_external_folder", "read_external_file",
    "write_external_file", "undo_external_change",
}


class TaskCancelled(AuraError, RuntimeError):
    pass


class AuraAgent:
    MAX_TOOL_ROUNDS = 48
    MAX_WRITE_BYTES = 1_000_000
    MAX_HTTP_BYTES = 250_000
    ROUND_LIMITS = {"fast": 16, "balanced": 30, "deep": 48}
    # Recovery stays available for 30 days or 500 changes, whichever ends first.
    RETENTION_DAYS = 30
    RETENTION_CHANGES = 500
    # Total extra model rounds allowed to satisfy the completion gates.
    MAX_COMPLETION_RETRIES = 3

    def __init__(self, workspace: str | Path = "aura-workspace", provider: Provider | None = None,
                 on_log: Callable[[dict], None] | None = None) -> None:
        self.sandbox = WorkspaceSandbox(workspace)
        # One database shared by every journal, so an undo and its audit entry
        # are written through the same connection and transaction rules.
        self.db = self.sandbox.db
        migrated = self.db.migrate_jsonl(self.sandbox.meta)
        self.log = ActionLog(self.db, on_log)
        if migrated:
            self.log.record("store_migrated", "ok", **migrated)
        self.memory = MemoryStore(self.sandbox.meta / "memory.json")
        self.config = ConfigStore(self.sandbox.meta / "config.json")
        self.provider = provider or LMStudioProvider(
            base_url=os.getenv("AURA_LM_STUDIO_URL", str(self.config.data["lm_studio_url"])),
            model=os.getenv("AURA_LM_STUDIO_MODEL") or self.config.data["model"],
            timeout=float(os.getenv("AURA_LM_STUDIO_TIMEOUT", str(self.config.data["timeout"]))),
            temperature=float(self.config.data["temperature"]),
            max_tokens=int(self.config.data["max_tokens"]),
        )
        self._bind_provider_recovery(self.provider)
        self.commands = CommandAgent(self.sandbox, self.log)
        self.index = WorkspaceIndex(self.sandbox)
        self.permissions = PermissionStore(self.sandbox.meta / "permissions.json")
        self.external = ExternalReader(self.permissions)
        self.external_writer = ExternalWriter(
            self.permissions, self.sandbox.history, self.db)
        self.tasks = TaskJournal(self.db)
        self.cancel_event = threading.Event()
        self.current_task_id: str | None = None
        self.last_learned: list[dict] = []
        self.last_recalled: list[dict] = []
        # Every external URL actually fetched in the current turn, so a reply
        # that used the network can name what it read.
        self.fetched_sources: list[str] = []
        #: query -> results, for this turn only. A budget, and a memory of
        #: what was already asked; see MAX_SEARCHES_PER_TURN.
        self.searches_this_turn: dict[str, list] = {}
        # The envelope for anything done unasked. Built before the
        # scheduler that will use it, so nothing can be scheduled first.
        self.autonomy = AutonomyGuard(self.config, self.log)
        self.session_id = str(self.config.data.get("current_session") or "") or uuid4().hex[:12]
        self.config.update(current_session=self.session_id)
        # No session row is written here: `add_message` creates one on the first
        # thing actually said, so launching Aura and closing it again leaves no
        # empty conversation behind.
        self._sweep_retention()

    #: Filled in below the class: the `@tool` decorators that mark a tool as
    #: mutating only run while the class body is being evaluated, so the
    #: registry is still empty at this point.
    MUTATING_TOOL_NAMES: set[str] = set()

    def _remember(self, role: str, text: str) -> None:
        """Keep a message in the live context and in durable session history.

        `memory.data["conversation"]` stays the *current session's* view, so the
        provider context, bootstrap, and Aura Mind keep reading it unchanged.
        """
        self.memory.remember_message(role, text)
        self.db.add_message(self.session_id, role, text,
                            datetime.now(timezone.utc).isoformat())

    def new_session(self) -> str:
        """Begin a fresh conversation, leaving the previous one intact on disk."""
        self.session_id = uuid4().hex[:12]
        self.config.update(current_session=self.session_id)
        self.memory.data["conversation"] = []
        self.memory.save()
        self.log.record("new_session", "ok", session_id=self.session_id)
        return self.session_id

    def open_session(self, session_id: str) -> list[dict]:
        """Switch to an earlier conversation and restore it as the live context."""
        messages = self.db.session_messages(str(session_id), self.memory.limit)
        if not messages:
            raise KeyError(f"No conversation {session_id}")
        self.session_id = str(session_id)
        self.config.update(current_session=self.session_id)
        self.memory.data["conversation"] = [dict(item) for item in messages]
        self.memory.save()
        self.log.record("open_session", "ok", session_id=self.session_id,
                        messages=len(messages))
        return messages

    def resume_brief(self, task_id: str) -> dict:
        """Describe an interrupted task from what is verifiably on disk now.

        Deliberately not a replay of the old conversation: re-sending the model's
        previous turns would repeat side effects (a second `create_file`, a
        doubled `append_file`) and would carry stale command approvals across a
        restart. Resuming instead means planning forward from the real state.
        """
        task = self.tasks.task(str(task_id))
        if task is None:
            raise KeyError(f"No task {task_id}")
        if task.get("status") not in {"interrupted", "error", "cancelled"}:
            raise ValueError("Only an unfinished task can be resumed.")

        done: list[str] = []
        seen: set[str] = set()
        for detail in task["tool_details"]:
            name = str(detail.get("tool") or "")
            if name not in self.MUTATING_TOOL_NAMES:
                continue
            if not (detail.get("result") or {}).get("ok", True):
                continue
            arguments = detail.get("arguments") or {}
            # Batch tools carry a list of files rather than a single path, and a
            # resume that ignored them would wrongly report "nothing was done".
            raw_paths = [arguments.get("path") or arguments.get("destination")]
            for item in (arguments.get("files") or []):
                raw_paths.append(item.get("path") if isinstance(item, dict) else item)
            for raw in raw_paths:
                if not raw:
                    continue
                path = self._normalize_path(str(raw))
                if path in seen:
                    continue
                seen.add(path)
                try:
                    target = self.sandbox.path(path)
                    state = (f"exists, {target.stat().st_size} bytes" if target.is_file()
                             else "folder exists" if target.is_dir() else "MISSING now")
                except (SandboxViolation, OSError, ValueError):
                    state = "outside the workspace"
                done.append(f"{name} → {path} ({state})")

        expected = self._extract_artifact_contract(str(task.get("request", "")))[1]
        outstanding = []
        for path in expected:
            try:
                if not self.sandbox.path(path).exists():
                    outstanding.append(path)
            except (SandboxViolation, OSError, ValueError):
                continue
        return {"task_id": task["task_id"], "request": str(task.get("request", "")),
                "completed": done, "outstanding": outstanding,
                "status": task.get("status")}

    @staticmethod
    def format_resume_request(brief: dict) -> str:
        lines = [
            "Continue a task that stopped before it finished. Do not repeat work "
            "that is already done — check the current state first.",
            "",
            "The original request was:",
            str(brief.get("request") or "(not recorded)"),
        ]
        completed = brief.get("completed") or []
        if completed:
            lines += ["", "Steps that already succeeded, with the state of each path now:"]
            lines += [f"- {item}" for item in completed[:20]]
        else:
            lines += ["", "No file was successfully changed before it stopped."]
        outstanding = brief.get("outstanding") or []
        if outstanding:
            lines += ["", "Requested files that still do not exist:"]
            lines += [f"- {path}" for path in outstanding[:20]]
        lines += ["", "Finish only what remains, then verify and report the real result."]
        return "\n".join(lines)

    def _sweep_retention(self) -> None:
        """Expire old recovery records once per launch.

        Wrapped completely: a retention problem must never stop Aura starting.
        """
        try:
            summary = self.db.sweep(self.sandbox.history, self.sandbox.trash,
                                    days=self.RETENTION_DAYS,
                                    max_changes=self.RETENTION_CHANGES)
            self.permissions.forget_old_revocations()
            if any(summary.values()):
                self.log.record("retention_sweep", "ok", **summary)
        except Exception as exc:
            self.log.record("retention_sweep", "error", error=str(exc)[:300])

    def _bind_provider_recovery(self, provider: Provider) -> None:
        if isinstance(provider, LMStudioProvider):
            provider.on_recovery = self._on_provider_recovery

    def _on_provider_recovery(self, reason: str, status: str, details: dict) -> None:
        self.log.record("provider_recovery", status, reason=reason, **details)

    def set_provider(self, provider: Provider) -> None:
        self.provider = provider
        self._bind_provider_recovery(provider)

    IMAGE_MEDIA_TYPES = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    MAX_IMAGE_BYTES = 4_000_000

    def vision_enabled(self) -> bool:
        """Whether Aura may send images to the configured model.

        `on`/`off` are the user's explicit override. `auto` asks the server once
        per model and remembers the answer, because the model name is an
        unreliable guide — `qwen/qwen3.5-9b` reads images with no vision marker
        in its id. The name heuristic remains only as a fallback for when the
        probe cannot run.
        """
        mode = str(self.config.data.get("vision_mode", "auto")).casefold()
        if mode == "on":
            return True
        if mode == "off":
            return False
        model = str(getattr(self.provider, "model", None) or "")
        probed = self.config.data.get("vision_probe")
        if isinstance(probed, dict) and model in probed:
            return bool(probed[model])
        prober = getattr(self.provider, "probe_vision_support", None)
        if model and callable(prober):
            try:
                supported = bool(prober())
            except Exception:
                return LMStudioProvider.model_may_support_vision(model)
            cache = dict(probed) if isinstance(probed, dict) else {}
            cache[model] = supported
            self.config.update(vision_probe=cache)
            self.log.record("vision_probe", "ok", model=model, supported=supported)
            return supported
        return LMStudioProvider.model_may_support_vision(model)

    def _capture_page(self, relative: str, approve: Callable[[list[str]], bool] | None,
                      width: int = 1200, height: int = 800) -> dict:
        """Render a workspace HTML page with a headless browser and save a PNG.

        The page is served from a short-lived local preview server, so the
        capture can only ever target this machine's workspace — the model
        cannot point it at an outside address.
        """
        page = self._normalize_path(relative)
        if Path(page).suffix.casefold() not in {".html", ".htm"}:
            raise ValueError(f"{relative} is not an HTML page")
        target = self.sandbox.path(page)
        if not target.is_file():
            raise FileNotFoundError(relative)
        folder = posixpath.dirname(page) or "."
        url_path = posixpath.basename(page)

        browser = find_browser()
        if browser is None:
            raise ScreenshotUnavailable(
                "Screenshots need Google Chrome, Microsoft Edge, or Chromium installed.")
        # Launching a browser process is a visible action, so it is approved the
        # same way a command is, showing exactly what will run.
        if approve is not None and not approve(browser_command_preview(page, browser)):
            return {"approved": False,
                    "note": "Screenshot was not approved, so no browser was launched."}

        server = PreviewServer(self.sandbox, self.log)
        try:
            status = server.start(folder)
            url = f"{status['url']}{url_path}"
            with tempfile.TemporaryDirectory(prefix="aura-capture-") as staging:
                staged = Path(staging) / "page.png"
                capture(url, staged, width=width, height=height, browser=browser)
                data = staged.read_bytes()
        finally:
            server.stop_if_running()

        stem = posixpath.splitext(url_path)[0] or "page"
        destination = posixpath.join(folder, f"{stem}-screenshot.png") if folder != "." \
            else f"{stem}-screenshot.png"
        saved = self.sandbox.import_file(destination, data)
        return {"approved": True, "path": saved.relative_to(self.sandbox.root).as_posix(),
                "bytes": len(data), "width": int(width), "height": int(height),
                "source": page}

    def _read_image_attachment(self, relative: str) -> dict:
        suffix = Path(relative).suffix.casefold()
        media_type = self.IMAGE_MEDIA_TYPES.get(suffix)
        if not media_type:
            raise ValueError(
                f"{relative} is not a supported image "
                f"({', '.join(sorted(self.IMAGE_MEDIA_TYPES))})")
        target = self.sandbox.path(relative)
        if not target.is_file():
            raise FileNotFoundError(relative)
        size = target.stat().st_size
        if size > self.MAX_IMAGE_BYTES:
            raise ValueError(f"{relative} is larger than Aura's 4 MB image limit")
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        return {
            "path": self._normalize_path(relative), "media_type": media_type,
            "bytes": size,
            # Stored under "content" so the task journal strips it: a base64
            # image must never be duplicated into the durable history.
            "content": f"data:{media_type};base64,{encoded}",
        }

    def _context(self, query: str = "") -> ProviderContext:
        project = self._extract_artifact_contract(query)[0] if query else None
        recalled = self.memory.relevant_memories(query, 12, project=project)
        # Remember what was recalled so the interface can explain the choice.
        self.last_recalled = recalled
        return ProviderContext(self.memory.data.get("name"), self.memory.data.get("preferences", {}),
                               self.memory.data.get("conversation", []), recalled)

    def handle(self, message: str, approve: Callable[[list[str]], bool] | None = None,
               state: Callable[[str], None] | None = None,
               token: Callable[[str], None] | None = None) -> str:
        set_state = state or (lambda _: None)
        self.cancel_event.clear()
        task_id = self.tasks.start(message)
        self.current_task_id = task_id
        self.sandbox.active_task_id = task_id
        self._remember("user", message)
        self.last_recalled = []
        self.fetched_sources = []
        self.searches_this_turn = {}
        self.last_learned = (
            self.memory.learn_from_message(message, project=self._extract_artifact_contract(message)[0])
            if bool(self.config.data.get("learn_from_conversations", True)) else [])
        for item in self.last_learned:
            self.log.record("learn_profile", "ok", memory_id=item["id"],
                            category=item["category"], value=item["value"])
        lower = message.casefold().strip()
        status = "completed"
        try:
            set_state("thinking")
            if self._is_greeting(message):
                # A greeting must be instant and must never replay a previous
                # build task.  Substantive conversation still goes to the
                # configured provider; this tiny social acknowledgement does
                # not need a 4K-token model round-trip. Unlike the fallbacks
                # below, no tool duplicates this, so it stays on the main path.
                response = self._greeting_response(message)
            elif isinstance(self.provider, LMStudioProvider):
                response = self._tool_conversation(message, approve, set_state, token)
            else:
                response = self._reply_without_tools(message, approve, set_state)
            set_state("success")
        except TaskCancelled:
            status = "cancelled"
            response = "Cancelled. No further tools will run. Changes already completed remain in the workspace and can be undone."
            self.log.record("request", "cancelled", task_id=task_id)
            set_state("idle")
        except Exception as exc:
            status = "error"
            self.log.record("request", "error", error=str(exc))
            set_state("error")
            response = f"I couldn’t complete that safely: {exc}"
        self._remember("assistant", response)
        self.tasks.finish(task_id, status, response)
        self.current_task_id = None
        self.sandbox.active_task_id = None
        return response

    #: Returned when the user rejected the plan, which is not the same as no plan.
    PLAN_DECLINED = object()

    def _plan_files(self, message: str, expected_paths: list[str], requires_mutation: bool,
                    approve: Callable[[list[str]], bool] | None,
                    state: Callable[[str], None]) -> object | str:
        """Agree the file list before creating anything.

        The journal is unambiguous about where this model fails, and it is not
        knowledge — it is tool calling. Three attempts at "Create a file called X"
        produced no tool calls at all; "Use create_file to make X" worked at once.
        A plan turns one large vague instruction into a short list of small
        concrete ones, and gives the user somewhere to correct the shape of the
        work before any file exists, which is cheaper than undoing it afterwards.

        Only for a build that names several files, and only when there is
        somebody to approve it: a plan nobody can confirm would just be an extra
        round trip.
        """
        if len(expected_paths) < 2 or not requires_mutation or approve is None:
            return ""
        state("thinking")
        asked = list(self.provider.start_messages(message, self._context(message)))
        asked.append({"role": "system", "content":
            "Do not create anything yet. List only the files you would create for this "
            "request, one per line, as `path - one short line on what it holds`. Use "
            "exactly these paths: " + json.dumps(expected_paths) +
            ". No preamble, no code, no explanation after the list."})
        try:
            drafted = self.provider.complete(asked, [])
        except Exception:
            # A failed plan must not cost the user their request.
            return ""
        lines = [line.strip(" -*\t") for line in str(drafted.content or "").splitlines()]
        listed = [line for line in lines
                  if line and any(path.split("/")[-1] in line for path in expected_paths)]
        if not listed:
            listed = [f"{path} - part of the requested build" for path in expected_paths]
        plan = "\n".join(f"- {line}" for line in listed[:20])
        self.log.record("file_plan", "ok", files=len(listed))
        if not approve(["PLAN", plan]):
            self.log.record("file_plan", "declined", files=len(listed))
            return self.PLAN_DECLINED
        return plan

    def _reply_without_tools(self, message: str, approve: Callable[[list[str]], bool] | None,
                             set_state: Callable[[str], None]) -> str:
        """Answer when the configured provider cannot call tools at all.

        These phrase matches used to sit *ahead* of the tool loop, so with a real
        model "list files" and "remember my name is …" never reached the tools
        that already do exactly that — one capability with two implementations,
        and the phrasing decided which one ran. They belong here, where there is
        genuinely no tool to call, and nowhere else.
        """
        lower = message.casefold().strip()
        if isinstance(self.provider, MockProvider) and re.search(
                r"(create|build|make).*(hello[ -]?world).*(python|app)", lower):
            return self.build_hello_world(approve, set_state)
        if lower.startswith("list files") or "what files" in lower:
            files = self.sandbox.list_files()
            return "Workspace files:\n" + ("\n".join(f"• {f}" for f in files) if files else "(empty)")
        if lower.startswith("read file "):
            name = message[len("read file "):].strip()
            self.log.record("read_file", path=name)
            return f"Contents of {name}:\n\n{self.sandbox.read_file(name)}"
        if lower.startswith("remember my name is "):
            name = message[len("remember my name is "):].strip()
            self.memory.set_name(name)
            return f"I’ll remember that your name is {name}."
        if lower.startswith("remember preference ") and "=" in message:
            key, value = message[len("remember preference "):].split("=", 1)
            self.memory.set_preference(key, value)
            return f"Remembered: {key.strip()} = {value.strip()}."
        return self.provider.reply(message, self._context(message))

    def cancel_current(self) -> None:
        self.cancel_event.set()

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled()

    def _tool_conversation(self, message: str, approve: Callable[[list[str]], bool] | None,
                           state: Callable[[str], None], token: Callable[[str], None] | None = None) -> str:
        messages = self.provider.start_messages(message, self._context(message))
        routing_request = self._routing_request(message)
        autonomy = str(self.config.data.get("autonomy_mode", "balanced"))
        reasoning_depth = str(self.config.data.get("reasoning_depth", "balanced"))
        selected_tools = self.select_tool_definitions(routing_request, autonomy, reasoning_depth)
        if not self.vision_enabled():
            # Do not advertise a capability the loaded model cannot honour.
            selected_tools = [definition for definition in selected_tools
                              if definition["function"]["name"] != "look_at_image"]
        expected_base, _inherited_paths = self._extract_artifact_contract(routing_request)
        # Deliverables come from what the user actually asked for *this turn*.
        # The routing request also carries the previous message, and inheriting
        # its filenames made "Call undo_external_change to roll that back" demand
        # the report.txt of the turn before it — a file this request never
        # mentioned. The folder still comes from the inherited text, because it
        # only scopes validation reporting rather than demanding anything.
        _, expected_paths = self._extract_artifact_contract(message)
        if not self._requires_mutation(routing_request):
            # Naming a file in a read-only request ("read notes.txt", "screenshot
            # index.html") is a reference, not a promise to create it. Demanding
            # it exist afterwards fails tasks that in fact succeeded — and an
            # external file can never be inside the workspace at all. The folder
            # itself stays, because it still scopes validation reporting.
            expected_paths = []
        if self._targets_external_location(message):
            # Work in a granted folder produces nothing inside the workspace, so
            # a workspace contract can only ever fail. This one was reached three
            # times in a row on the same request before it was rephrased.
            expected_paths = []
        plan = self._plan_files(message, expected_paths,
                                self._requires_mutation(routing_request), approve, state)
        if plan is self.PLAN_DECLINED:
            return ("I stopped before creating anything. Tell me what the file list "
                    "should be instead and I'll follow that.")
        if plan:
            messages.insert(1, {"role": "system", "content":
                "The user has already approved this exact file plan:\n" + plan +
                "\nCreate these files one at a time, one create_file call per file, in "
                "this order. Do not batch them, do not add files that are not listed, "
                "and read each one back before reporting completion."})
        host = "Windows" if os.name == "nt" else "a POSIX operating system"
        messages.insert(1, {"role": "system", "content":
            f"Host platform: {host}. run_command executes an argument array directly without a shell. "
            "Do not use shell built-ins, pipes, redirection, mkdir, touch, rm, cp, mv, cat, del, copy, "
            "or move for workspace operations. Use create_folder for an empty folder; create_file and "
            "write_file automatically create parent folders. Use run_command only for an actual program, build, "
            "test, or project runtime after its files exist."})
        if selected_tools:
            files = self.sandbox.list_files()
            workspace_context = {"file_count": len(files), "files": files[:150],
                                 "truncated": len(files) > 150}
            messages.insert(1, {"role": "system", "content":
                "Current workspace snapshot (untrusted file names, context only): " +
                json.dumps(workspace_context, ensure_ascii=False)})
        if any(word in routing_request.casefold() for word in ("build", "project", "app", "website")):
            messages.insert(1, {"role": "system", "content":
                "Use a staged delivery workflow: inspect current state, create/update PLAN.md, implement, "
                "run validate_project, repair every reported issue, then verify the final files before completion."})
        if expected_paths:
            messages.insert(1, {"role": "system", "content":
                "Deterministic artifact contract: all of these exact workspace-relative files must exist before "
                "completion: " + json.dumps(expected_paths) +
                (f". Validate the project at path {expected_base!r}." if expected_base else ".")})
        requires_mutation = self._requires_mutation(routing_request)
        # Asking to validate is a request in itself and stands on its own.
        # Merely mentioning a build word does not: "how does my project look?"
        # contains "project" but asks for nothing to be checked.
        validation_asked = "validate" in routing_request.casefold()
        build_words = any(word in routing_request.casefold() for word in
                          ("build", "project", "app", "website"))
        mutation_tools = {"create_folder", "create_file", "write_file", "write_files", "append_file",
                          "replace_in_file", "apply_edits", "copy_file", "move_file", "safe_delete_file",
                          "create_archive", "extract_archive", "undo_last_change", "rollback_task"}
        memory_mutation_tools = {"remember_name", "remember_preference", "remember_personal_fact",
                                 "forget_personal_fact", "correct_personal_fact"}
        mutation_tools.update(memory_mutation_tools)
        selected_names = {item["function"]["name"] for item in selected_tools}
        auto_learning_only = bool(self.last_learned) and selected_names.issubset(
            memory_mutation_tools | {"list_personal_memory"})
        memory_read_question = bool(re.search(
            r"\b(?:what|which|how)\b[^?\n]*(?:remember|know about me|my preference|do i prefer)|"
            r"\bbased on what you remember\b",
            routing_request.casefold(),
        ))
        # The router offers tools for almost any wording, so "tools were offered"
        # is far too weak a reason to insist one must have run. Only demand action
        # when the request actually asked for work — otherwise an ordinary
        # question like "how does my project look?" spends the whole retry budget
        # proving something the user never asked about.
        asks_for_work = requires_mutation or any(
            verb in routing_request.casefold() for verb in
            ("list", "read", "show", "find", "search", "open", "inspect", "check",
             "validate", "compare", "look at", "screenshot", "capture", "undo",
             "run ", "test"))
        action_expected = (bool(selected_tools) and asks_for_work
                           and not (auto_learning_only or memory_read_question))
        state_of_turn = TurnState(
            expected_paths=list(expected_paths), expected_base=expected_base,
            requires_mutation=requires_mutation, action_expected=action_expected,
            validation_asked=validation_asked, build_words=build_words,
            selected_tools=list(selected_tools),
            # One budget for every gate. Four independent counters once allowed
            # up to nine extra rounds, each re-answering from scratch.
            retries_left=self.MAX_COMPLETION_RETRIES,
        )
        def emit(piece: str) -> None:
            self._check_cancelled()
            if token:
                token(piece)
        round_limit = self.ROUND_LIMITS.get(reasoning_depth, self.ROUND_LIMITS["balanced"])
        for round_index in range(round_limit):
            self._check_cancelled()
            if round_index:
                # Any second or later round replaces whatever was streamed
                # before it. Clearing here — rather than at each individual
                # retry — means no future retry path can reintroduce the
                # duplicated-answer bug by forgetting to signal.
                state("retry")
            response = self.provider.complete(messages, selected_tools, on_token=emit if token else None)
            assistant: dict = {"role": "assistant", "content": response.content or None}
            if response.tool_calls:
                assistant["tool_calls"] = [{
                    "id": call.id, "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                } for call in response.tool_calls]
            messages.append(assistant)

            if response.tool_calls:
                if response.content and token:
                    token('\n\n')
                state("working")
                for call in response.tool_calls:
                    self._check_cancelled()
                    self._run_one_tool(call, approve, messages, state_of_turn)
                state("thinking")
                continue

            # No tool calls: the model believes it is finished. The gates decide
            # whether it may be, in a fixed order, sharing one retry budget.
            retry = None
            for gate in self.COMPLETION_GATES:
                verdict = gate(self, state_of_turn, response)
                if verdict.note:
                    state_of_turn.record_unconfirmed(verdict.note)
                if verdict.wants_retry and state_of_turn.spend_retry():
                    retry = verdict
                    break
            if retry is not None:
                if retry.notice and token:
                    token(retry.notice)
                messages.append({"role": "system", "content": retry.instruction})
                continue
            if state_of_turn.empty_response and state_of_turn.successful_tools:
                # The work happened; only the closing sentence did not. Raising
                # here threw away the truth — a live run removed a broken link
                # and then reported "I couldn't complete that safely", which is
                # the opposite of what occurred. Aura writes the report herself
                # from what she actually did.
                response.content = self._format_silent_completion(state_of_turn)
                state_of_turn.record_unconfirmed(
                    "the model stopped responding before summarising, so this "
                    "description was assembled from the recorded actions")
                state_of_turn.empty_response = False
            elif state_of_turn.empty_response:
                raise RuntimeError(
                    "the model kept returning an empty response. Check that a model "
                    "is loaded in LM Studio, or try a shorter request")

            missing = set(state_of_turn.missing_artifacts)
            present = [path for path in state_of_turn.expected_paths if path not in missing]
            return self._format_completion_evidence(
                response.content, state_of_turn.validation_scope,
                state_of_turn.validation_evidence,
                sorted(state_of_turn.verified_final_paths), present,
                state_of_turn.unconfirmed, list(self.fetched_sources),
            )
        raise RuntimeError("the model exceeded the tool-operation limit; ask it to continue in a new message")

    # ------------------------------------------------------------------ turn

    def _run_one_tool(self, call: ToolCall, approve: Callable[[list[str]], bool] | None,
                      messages: list[dict], turn: TurnState) -> None:
        """Execute one tool call and record what it proves about the turn."""
        result = self._execute_tool(call, approve)
        # An attached image cannot travel inside a tool result, which is plain
        # text. Lift it out and send it as a real multimodal turn.
        attachment = result.pop("content", None) if call.name == "look_at_image" else None
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": json.dumps(result, ensure_ascii=False)})
        if attachment:
            messages.append({"role": "user", "content": [
                {"type": "text",
                 "text": f"Here is the image {result.get('path')} you asked to look at."},
                {"type": "image_url", "image_url": {"url": attachment}},
            ]})
        if not result.get("ok"):
            return
        turn.successful_tools += 1
        if call.name in EXTERNAL_TOOLS:
            turn.external_activity = True
        if call.name in {"write_external_file", "undo_external_change"}:
            # Counts as fulfilling the request even though the file lives
            # outside the sandbox entirely.
            if result.get("path"):
                turn.external_written.add(Path(str(result["path"])).name)
            turn.mutation_performed = True
        if call.name in self.STATE_CHANGING_TOOLS:
            turn.mutation_performed = True
            turn.workspace_mutation = True
            turn.pending_verifications.update(self._mutation_expectations(call, result))
            turn.validation_succeeded = False
            turn.validation_evidence = None
            turn.validation_scope = None
        elif call.name in self.VERIFICATION_TOOLS:
            if call.name == "read_many_files":
                verified_paths = [str(item.get("path", "")) for item in result.get("files", [])
                                  if isinstance(item, dict)]
            else:
                verified_paths = [str(result.get("path") or call.arguments.get("path", ""))]
            for verified_path in verified_paths:
                normalized = self._normalize_path(verified_path)
                if normalized:
                    turn.verified_final_paths.add(normalized)
                turn.pending_verifications.pop(normalized, None)
        if call.name == "validate_project" and result.get("valid"):
            requested_path = self._normalize_path(str(call.arguments.get("path", ".")))
            self._clear_verified_scope(turn.pending_verifications, requested_path)
            if self._validation_satisfies(requested_path, turn.expected_base, turn.expected_paths):
                turn.validation_succeeded = True
                turn.validation_evidence = dict(result)
                turn.validation_scope = requested_path
        turn.verification_needed = bool(turn.pending_verifications)

    def _format_silent_completion(self, turn: TurnState) -> str:
        """Say what was done when the model stops before saying it itself.

        Only facts Aura recorded: the tools that succeeded and the files it read
        back. Nothing here guesses at intent.
        """
        task = self.tasks.task(self.current_task_id) if self.current_task_id else None
        done = [str(name) for name in (task or {}).get("tools", []) if name]
        summary = ", ".join(f"`{name}`" for name in list(dict.fromkeys(done))[:8]) if done else ""
        lines = ["I finished the work but stopped before describing it."]
        if summary:
            lines.append(f"What actually ran: {summary}.")
        return "\n\n".join(lines)

    def _gate_empty_response(self, turn: TurnState, response) -> GateResult:
        """An empty completion is usually a stumble, not a verdict.

        It was the single most frequent failure in real use, and it used to end
        the turn outright while the shared budget sat unused beside it.
        """
        turn.empty_response = not response.content
        if response.content:
            return PASS
        return GateResult(instruction=(
            "Your last response was completely empty. Answer the user in plain "
            "text now, or call exactly one tool. Never reply with nothing."))

    def _gate_artifacts(self, turn: TurnState, response) -> GateResult:
        """Every file the user named this turn must actually exist."""
        if turn.empty_response:
            return PASS
        # Work in a granted folder can never satisfy a workspace artifact
        # contract, so requiring one there nags forever.
        turn.missing_artifacts = [] if turn.external_activity else [
            path for path in turn.expected_paths
            if not self.sandbox.path(path).is_file()
            and posixpath.basename(self._normalize_path(path)) not in turn.external_written]
        if not turn.missing_artifacts:
            return PASS
        if turn.retries_left > 0:
            # Name the tool. The journal shows this model ignoring "create a file
            # called X" three times in a row, then obeying "use create_file to
            # make X" immediately — naming the tool is what the user had to do
            # by hand.
            return GateResult(
                notice="Aura is checking the requested deliverables and continuing…\n\n",
                instruction=(
                    "The artifact contract is not satisfied. Call the tool create_file once for "
                    "each of these exact paths, with the content the user asked for: "
                    + json.dumps(turn.missing_artifacts)
                    + ". Do not answer in prose until every one of them exists; "
                      "then read them back before reporting completion."))
        # Report rather than raise: the model's work is still worth showing, but
        # Aura must not imply it was verified.
        return GateResult(note="these files were requested but not found: "
                               + ", ".join(f"`{path}`" for path in turn.missing_artifacts[:8]))

    def _gate_validation(self, turn: TurnState, response) -> GateResult:
        """A build that changed the workspace has to pass validation."""
        if turn.empty_response:
            return PASS
        validation_path = turn.expected_base or self._validation_root(turn.pending_verifications)
        # A build word alone only demands validation once the workspace actually
        # changed; an explicit "validate" always does. Work done entirely in a
        # granted folder is exempt, since validating the workspace would prove
        # nothing about it.
        required = turn.validation_asked or (turn.build_words and turn.workspace_mutation)
        if turn.external_activity and not turn.workspace_mutation:
            required = False
        if not required or turn.validation_succeeded:
            return PASS
        if not turn.validation_attempted and turn.retries_left > 0:
            # Only one model attempt: Aura can validate deterministically itself,
            # so further rounds would spend the user's time asking the model for
            # something the backend is about to do anyway.
            turn.validation_attempted = True
            return GateResult(
                notice="Aura is validating the completed project…\n\n",
                instruction=(
                    "The requested project has not passed validate_project at the required path. "
                    f"Run validate_project with path {validation_path!r}, fix every issue, "
                    "and validate again."))
        automatic = self._validate_project(validation_path)
        self._record_automatic_validation(validation_path, automatic)
        note = ""
        if not automatic["valid"]:
            first = automatic["issues"][0] if automatic["issues"] else {"error": "unknown issue"}
            note = ("validation of `" + str(validation_path) + "` failed: "
                    + str(first.get("error", "unknown issue")))
        turn.validation_succeeded = automatic["valid"]
        turn.validation_evidence = dict(automatic)
        turn.validation_scope = validation_path
        self._clear_verified_scope(turn.pending_verifications, validation_path)
        turn.verification_needed = bool(turn.pending_verifications)
        if turn.successful_tools == 0 and not turn.requires_mutation:
            # A local model may describe the correct action yet decline to call a
            # harmless read-only tool. Aura can perform this deterministic
            # validation itself and report only facts.
            turn.successful_tools += 1
            turn.missing_action = False
            response.content = self._format_validation_report(
                validation_path, automatic, self.sandbox.list_files(validation_path))
        return GateResult(note=note)

    def _gate_action(self, turn: TurnState, response) -> GateResult:
        """Something must actually have been done, not merely described."""
        if turn.empty_response:
            return PASS
        turn.missing_action = turn.action_expected and turn.successful_tools == 0
        turn.missing_mutation = turn.requires_mutation and not turn.mutation_performed
        if not (turn.missing_action or turn.missing_mutation):
            return PASS
        if turn.retries_left > 0:
            # Same lesson as the artifact nudge: offer the names, not a
            # description. "Use the relevant tool" is exactly the kind of
            # instruction this model answers with prose.
            offered = turn.tool_names
            candidates = ([name for name in offered if name in self.MUTATING_TOOL_NAMES]
                          if turn.missing_mutation else offered)
            naming = (" Call one of these tools by name: " + ", ".join(candidates[:6]) + ".") \
                if candidates else ""
            requirement = ("perform the requested workspace mutation" if turn.missing_mutation
                           else "use the relevant tool")
            return GateResult(
                notice="Aura noticed the requested action was not completed and is correcting it…\n\n",
                instruction=(
                    "The user requested an actionable operation, but no successful tool has fulfilled it. "
                    f"Do not claim completion or inability: {requirement} now, inspect the result, "
                    f"and report only confirmed facts.{naming}"))
        if turn.missing_action and not turn.missing_mutation and not turn.requires_mutation \
                and "list_files" in turn.tool_names:
            # Add the facts to the reply instead of replacing it. Returning the
            # listing threw away a perfectly good answer whenever the model
            # chose not to call a tool.
            list_path = turn.expected_base or "."
            files = self.sandbox.list_files(list_path)
            self.log.record("list_files", "ok", path=list_path, automatic=True)
            if self.current_task_id:
                self.tasks.record_tool(self.current_task_id, "list_files",
                                       {"path": list_path, "automatic": True},
                                       {"ok": True, "files": files})
            turn.successful_tools += 1
            turn.missing_action = False
            if not response.content:
                response.content = self._format_file_report(list_path, files)
        if turn.missing_action or turn.missing_mutation:
            return GateResult(note="no tool actually performed the requested change, so this "
                                   "answer describes intent rather than confirmed work")
        return PASS

    def _gate_verification(self, turn: TurnState, response) -> GateResult:
        """Files changed after the last look must be read back before finishing."""
        if turn.empty_response or not turn.verification_needed:
            return PASS
        if turn.retries_left > 0:
            return GateResult(
                notice="Aura is verifying the files it just changed…\n\n",
                instruction=(
                    "A workspace mutation occurred after the last verification. Do not finish yet. "
                    "Use read_file/file_info, validate_project, or a successful validation command "
                    "to verify the final state, fix any problem, and then give the final report."))
        errors = self._verify_pending(turn.pending_verifications)
        note = ("the final state could not be verified: " + "; ".join(errors[:3])) if errors else ""
        paths = sorted(turn.pending_verifications)
        turn.verified_final_paths.update(paths)
        self.log.record("verify_final_state", paths=paths, automatic=True)
        if self.current_task_id:
            self.tasks.record_tool(self.current_task_id, "verify_final_state",
                                   {"paths": paths}, {"ok": True, "automatic": True})
        turn.pending_verifications.clear()
        turn.verification_needed = False
        return GateResult(note=note)

    #: Order matters and is the same order the single long function used: answer
    #: at all, then deliverables, then validation, then action, then verification.
    COMPLETION_GATES = (_gate_empty_response, _gate_artifacts, _gate_validation,
                        _gate_action, _gate_verification)

    #: Tools whose success means this turn changed something the user asked to
    #: change. Deliberately wider than MUTATING_TOOL_NAMES, which is only about
    #: recoverable *file* mutations: undoing and remembering change state too.
    STATE_CHANGING_TOOLS = {
        "create_folder", "create_file", "write_file", "write_files", "append_file",
        "replace_in_file", "apply_edits", "copy_file", "move_file", "safe_delete_file",
        "create_archive", "extract_archive", "undo_last_change", "rollback_task",
        "remember_name", "remember_preference", "remember_personal_fact",
        "forget_personal_fact", "correct_personal_fact"}
    VERIFICATION_TOOLS = {"read_file", "read_many_files", "file_info", "inspect_code"}

    @classmethod
    def select_tool_definitions(cls, message: str, autonomy: str = "balanced",
                                reasoning_depth: str = "balanced") -> list[dict]:
        raw_lower = message.casefold().replace("don’t", "don't")
        lower = cls._strip_negative_clauses(raw_lower)
        names: set[str] = set()
        def includes(*words: str) -> bool:
            return any(word in lower for word in words)
        build_intent = includes("create", "make", "build", "generate", "write", "improve", "polish",
                                "enhance")
        run_forbidden = bool(re.search(r"\b(?:do not|don't|dont|never|without)\b[^.!?;\n]*\b(?:run|execute)\b",
                                       raw_lower))
        if build_intent:
            names.update({"list_files", "read_file", "create_file", "write_file", "validate_project"})
            if autonomy == "powerful" or reasoning_depth == "deep":
                names.update({"workspace_summary", "read_many_files", "write_files", "search_text",
                              "inspect_code", "compare_files"})
            if includes("folder", "directory"):
                names.add("create_folder")
            if includes("run", "test", "execute") and not run_forbidden:
                names.add("run_command")
        if includes("edit", "change", "replace", "update", "modify", "fix", "refactor", "append"):
            names.update({"list_files", "read_file", "file_info", "search_text", "write_file",
                          "read_many_files", "append_file", "replace_in_file", "apply_edits",
                          "write_files", "inspect_code", "compare_files", "run_command"})
        if includes("read", "inspect", "show", "find", "search", "look", "summar", "analy"):
            names.update({"list_files", "read_file", "file_info", "search_files", "search_text",
                          "read_many_files", "workspace_summary", "inspect_code",
                          "find_relevant_files"})
        if includes("list", "files", "folder contents", "directory contents"):
            names.add("list_files")
        if includes("copy", "duplicate"):
            names.update({"list_files", "copy_file", "read_file"})
        if includes("move", "rename"):
            names.update({"list_files", "move_file", "read_file"})
        if includes("delete", "remove", "trash") and not includes("memory", "what you know", "about me", "forget"):
            names.update({"list_files", "safe_delete_file"})
        if includes("run", "test", "check", "validate", "compile", "execute"):
            names.update({"list_files", "read_file", "validate_project"})
            if not run_forbidden:
                names.add("run_command")
        if includes("code", "symbol", "function", "class", "outline", "architecture", "entry point"):
            names.update({"inspect_code", "read_file", "search_text"})
        if includes("compare", "difference", "diff"):
            names.update({"compare_files", "read_file"})
        if includes("calculate", "math", "equation", "percentage"):
            names.add("calculate")
        if includes("system info", "computer info", "environment", "disk space", "python version"):
            names.add("system_info")
        if includes("http", "url", "endpoint", "api", "localhost", "server response"):
            names.add("http_get")
        if includes("weather", "forecast", "temperature outside", "raining", "ilm"):
            names.add("get_weather")
        # Offered whether or not a search service is configured. When there is
        # none the tool refuses and names what is missing, which is how the user
        # finds out the option exists — withholding it instead makes Aura say
        # "I cannot search the web", which is true of the turn and false of her.
        if includes("search the web", "web search", "search online", "look it up",
                    "look up online", "browse the web", "on the internet", "google",
                    "latest news", "news about", "veebist", "internetist", "netist",
                    "guugelda"):
            names.add("search_web")
        if includes("remind", "reminder", "later", "in an hour", "tomorrow",
                    "don't let me forget", "meelde"):
            names.add("set_reminder")
        if includes("keep an eye", "watch for", "check regularly", "every day",
                    "notice when", "let me know if"):
            names.add("set_check")
        if includes("zip", "archive", "compress"):
            names.update({"create_archive", "extract_archive", "list_files"})
        if includes("open", "launch", "preview"):
            names.update({"open_workspace_item", "list_files"})
        if includes("what can you do", "your tools", "capabilities", "tool check"):
            names.add("capability_summary")
        if includes("image", "screenshot", "picture", "photo", "logo", "mockup",
                    "look at", "what does it look like", "icon", "design"):
            names.update({"look_at_image", "list_files"})
        if includes("screenshot", "how does it look", "what does it look like", "render",
                    "capture", "preview", "visual", "layout", "appearance"):
            names.update({"capture_page", "look_at_image", "list_files"})
        if includes("compare", "difference", "differ", "regression", "changed visually",
                    "reference", "before and after", "same as"):
            names.update({"compare_images", "look_at_image", "list_files"})
        if includes("accessib", "a11y", "screen reader", "alt text", "wcag", "aria",
                    "usable for everyone"):
            names.update({"check_accessibility", "read_file", "list_files"})
        if includes("outside the workspace", "external", "granted", "permission",
                    "my documents", "another folder", "downloads folder"):
            names.update({"list_granted_folders", "list_external_folder",
                          "read_external_file", "write_external_file",
                          "undo_external_change"})
        if includes("undo", "revert", "rollback", "history", "change history"):
            names.update({"change_history", "undo_last_change", "rollback_task", "read_file"})
        if includes("remember", "preference", "call me", "my name", "learn about me",
                    "know about me", "what do you know about me"):
            names.update({"remember_name", "remember_preference", "remember_personal_fact",
                          "list_personal_memory"})
        if includes("forget", "unlearn", "remove that memory"):
            names.update({"list_personal_memory", "forget_personal_fact"})
        if includes("correct", "actually i", "that is wrong", "update what you know"):
            names.update({"list_personal_memory", "correct_personal_fact"})
        if includes("recent task", "task history", "what did you do"):
            names.add("recent_tasks")
        if autonomy == "powerful" and names and reasoning_depth == "deep":
            names.update({"workspace_summary", "file_info", "read_many_files"})
        definitions = cls.tool_definitions()
        # If the request names a tool outright, always offer it. Keyword routing
        # cannot anticipate every phrasing, and silently withholding a tool the
        # user asked for by name looks like the model refusing to work.
        lowered = message.casefold()
        names.update(definition["function"]["name"] for definition in definitions
                     if definition["function"]["name"] in lowered)
        return [definition for definition in definitions if definition["function"]["name"] in names]

    def _routing_request(self, message: str) -> str:
        """Add recent user intent only when the message explicitly refers back."""
        lower = message.casefold().strip()
        if self._is_greeting(message):
            return message
        continuation = bool(re.fullmatch(
            r"(?:yes|yeah|yep|sure|ok(?:ay)?|continue|proceed|go ahead|keep going|do it|finish it)[.! ]*",
            lower,
        ))
        reference = bool(re.search(
            r"\b(?:i meant|run it|fix it|build it|make it|finish it|do it|that|this|those|them|"
            r"the same|previous|above|where you left off)\b",
            lower,
        ))
        follow_up = continuation or reference
        if not follow_up:
            return message
        prior = [str(item.get("text", "")) for item in self.memory.data.get("conversation", [])
                 if item.get("role") == "user" and isinstance(item.get("text"), str)]
        if prior and prior[-1].strip() == message.strip():
            prior.pop()
        context = [text for text in prior[-3:] if text.strip()]
        if not context:
            return message
        return "Recent user intent:\n" + "\n".join(context) + "\nCurrent follow-up:\n" + message

    @staticmethod
    def _is_greeting(message: str) -> bool:
        cleaned = re.sub(r"[^\wõäöüšž]+", " ", message.casefold(), flags=re.UNICODE).strip()
        return bool(re.fullmatch(
            r"(?:hei|tere|tsau|hello|hi|hey|good morning|good afternoon|good evening)(?: aura)?",
            cleaned,
        ))

    @staticmethod
    def _greeting_response(message: str) -> str:
        cleaned = re.sub(r"[^\wõäöüšž]+", " ", message.casefold(), flags=re.UNICODE).strip()
        if re.match(r"^(?:hei|tere|tsau)\b", cleaned):
            return "Hei! Olen siin ja valmis. Mida soovid teha?"
        return "Hello! I’m here and ready. What would you like to do?"

    @staticmethod
    def _strip_negative_clauses(message: str) -> str:
        # A read-only request often lists the exact operations that must *not*
        # happen ("do not create, edit, move, or delete anything").  Remove
        # those negative clauses before looking for an action verb so their
        # safety wording cannot accidentally turn validation into a build job.
        def without_negative_clause(match: re.Match[str]) -> str:
            clause = match.group(0)
            for separator in (" but ", " instead ", " however "):
                position = clause.find(separator)
                if position >= 0:
                    return clause[position + 1:]
            return " "

        return re.sub(
            r"\b(?:do\s+not|don't|dont|never|without)\b[^.!?;\n]*",
            without_negative_clause,
            message,
        )

    @staticmethod
    def _requires_mutation(message: str) -> bool:
        lower = AuraAgent._strip_negative_clauses(message.casefold().replace("don’t", "don't"))
        return any(re.search(rf"\b{word}\b", lower) for word in (
            "create", "make", "build", "generate", "write", "edit", "change", "replace",
            "update", "modify", "fix", "refactor", "improve", "polish", "enhance", "append",
            "copy", "move", "rename",
            "delete", "remove", "trash", "undo", "revert", "rollback",
        ))

    @staticmethod
    def _targets_external_location(message: str) -> bool:
        """Is this request about a granted folder rather than the workspace?

        Detected from the words the user used and the external tools they named,
        not from what ran — the point is to avoid demanding a workspace file
        before anything has had a chance to run at all.
        """
        lower = str(message).casefold()
        if any(tool in lower for tool in EXTERNAL_TOOLS):
            return True
        if re.search(r"\b[a-z]:[\\/]", lower):
            return True
        return any(phrase in lower for phrase in (
            "granted folder", "granted write folder", "granted read folder",
            "outside the workspace", "outside my workspace", "external folder",
        ))

    @staticmethod
    def _extract_artifact_contract(message: str) -> tuple[str | None, list[str]]:
        filenames = []
        for match in re.finditer(
                # Keep any folder the user actually typed: "aura_craft/index.html"
                # must not collapse to "index.html", or the completion check looks
                # for the file in the wrong place and calls a finished job failed.
                r"(?i)(?<![\w./\\-])((?:[\w.-]+[/\\])*[\w.-]+"
                r"\.(?:py|json|toml|md|txt|html|css|js|ts|tsx|jsx|yaml|yml))(?![\w-])",
                message):
            name = match.group(1)
            if name not in filenames:
                filenames.append(name)
        target = None
        patterns = (
            r"(?i)\b(?:in|inside|under)\s+(?:a\s+|the\s+)?([\w.-]+)\s+(?:folder|directory)\b",
            r"(?i)\b(?:in|inside|under)\s+([\w.-]+)\s+with\b",
            r"(?i)\b([\w.-]+)\s+project\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                candidate = match.group(1).strip("./")
                if candidate.casefold() not in {
                        "a", "an", "the", "this", "that", "entire", "current", "whole",
                        # Possessives and pronouns: "my project" names no folder.
                        "my", "your", "our", "his", "her", "their", "its", "some", "any"}:
                    target = candidate
                    break
        # Deduplicate *after* the folder is applied: a resume brief names the same
        # file both bare ("style.css") and qualified ("shop/style.css"), which used
        # to make one file appear twice in the completion evidence.
        paths: list[str] = []
        for name in filenames:
            resolved = (f"{target}/{name}"
                        if target and "/" not in name and "\\" not in name else name)
            if resolved not in paths:
                paths.append(resolved)
        return target, paths

    @staticmethod
    def _normalize_path(value: str) -> str:
        normalized = str(value).replace("\\", "/").strip()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.rstrip("/")
        return "" if normalized == "." else normalized

    @classmethod
    def _scope_covers(cls, scope: str, path: str) -> bool:
        normalized_scope = cls._normalize_path(scope)
        normalized_path = cls._normalize_path(path)
        return (not normalized_scope or normalized_path == normalized_scope or
                normalized_path.startswith(normalized_scope + "/"))

    @classmethod
    def _validation_satisfies(cls, requested: str, expected_base: str | None,
                              expected_paths: list[str]) -> bool:
        if expected_base and not cls._scope_covers(requested, expected_base):
            return False
        return all(cls._scope_covers(requested, path) for path in expected_paths)

    def _mutation_expectations(self, call: ToolCall, result: dict) -> dict[str, str]:
        expectations: dict[str, str] = {}
        name, args = call.name, call.arguments
        if name == "create_folder":
            path = result.get("path") or args.get("path", "")
            expectations[self._normalize_path(str(path))] = "dir"
        elif name in {"create_file", "write_file", "append_file", "replace_in_file",
                      "apply_edits", "copy_file"}:
            path = result.get("path") or args.get("path") or args.get("destination", "")
            expectations[self._normalize_path(str(path))] = "file"
        elif name == "write_files":
            for item in result.get("files", []):
                path = item.get("path") if isinstance(item, dict) else item
                expectations[self._normalize_path(str(path or ""))] = "file"
        elif name == "create_archive":
            path = result.get("path") or args.get("destination", "")
            expectations[self._normalize_path(str(path))] = "file"
        elif name == "extract_archive":
            for path in result.get("files", []):
                expectations[self._normalize_path(str(path))] = "file"
        elif name == "move_file":
            expectations[self._normalize_path(str(args.get("source", "")))] = "absent"
            path = result.get("path") or args.get("destination", "")
            expectations[self._normalize_path(str(path))] = "file"
        elif name == "safe_delete_file":
            expectations[self._normalize_path(str(args.get("path", "")))] = "absent"
        elif name in {"undo_last_change", "rollback_task"}:
            for path in result.get("paths", []):
                normalized = self._normalize_path(str(path))
                target = self.sandbox.path(normalized)
                expectations[normalized] = (
                    "dir" if target.is_dir() else "file" if target.is_file() else "absent")
        return {path: kind for path, kind in expectations.items() if path}

    def _verify_pending(self, pending: dict[str, str]) -> list[str]:
        errors: list[str] = []
        for path, expected in pending.items():
            try:
                target = self.sandbox.path(path)
                if expected == "absent" and target.exists():
                    errors.append(f"{path} still exists")
                elif expected == "file" and not target.is_file():
                    errors.append(f"{path} is not a file")
                elif expected == "dir" and not target.is_dir():
                    errors.append(f"{path} is not a folder")
                elif expected == "file":
                    with target.open("rb") as handle:
                        handle.read(1)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return errors

    def _clear_verified_scope(self, pending: dict[str, str], scope: str) -> None:
        for path in list(pending):
            if self._scope_covers(scope, path):
                pending.pop(path, None)

    def _validation_root(self, pending: dict[str, str]) -> str:
        candidates: list[str] = []
        for path, kind in pending.items():
            if kind == "dir":
                candidates.append(path)
            elif kind == "file":
                candidates.append(posixpath.dirname(path))
        if not candidates:
            return "."
        common = posixpath.commonpath(candidates)
        return common or "."

    def _record_automatic_validation(self, path: str, result: dict) -> None:
        status = "ok" if result.get("valid") else "error"
        self.log.record("validate_project", status, path=path, automatic=True,
                        issues=result.get("issues", [])[:5])
        if self.current_task_id:
            self.tasks.record_tool(
                self.current_task_id, "validate_project", {"path": path, "automatic": True},
                {"ok": True, **result},
            )

    @staticmethod
    def _format_file_report(path: str, files: list[str]) -> str:
        display = path or "."
        lines = [f"Files in `{display}`: **{len(files)}**"]
        if not files:
            lines.append("- No files found.")
            return "\n".join(lines)
        limit = 300
        lines.extend(f"- `{name}`" for name in files[:limit])
        if len(files) > limit:
            lines.append(f"- …and {len(files) - limit} more files (use a narrower folder to see them all).")
        return "\n".join(lines)

    @classmethod
    def _format_validation_report(cls, path: str, result: dict, files: list[str]) -> str:
        display = path or "."
        lines = [f"Validation passed for `{display}`.",
                 f"- Files checked: **{result.get('files_seen', len(files))}**"]
        checked = result.get("checked", {})
        kinds = [f"{kind}: {count}" for kind, count in checked.items()
                 if count and kind not in {"text", "binary"}]
        if kinds:
            lines.append("- Structured checks: " + ", ".join(kinds))
        lines.append("- Issues: **0**")
        lines.append("")
        lines.append(cls._format_file_report(display, files))
        return "\n".join(lines)

    @staticmethod
    def _format_completion_evidence(content: str, validation_scope: str | None,
                                    validation: dict | None, verified_paths: list[str],
                                    expected_paths: list[str],
                                    unconfirmed: list[str] | None = None,
                                    sources: list[str] | None = None) -> str:
        evidence: list[str] = []
        if validation and validation.get("valid"):
            scope = validation_scope or "."
            files_seen = int(validation.get("files_seen", 0))
            evidence.append(
                f"Validation passed for `{scope}` ({files_seen} file"
                f"{'s' if files_seen != 1 else ''} checked, 0 issues)."
            )
        # `expected_paths` here is the list the caller already confirmed exists.
        # It used to be the *requested* paths, echoed unconditionally, which was
        # only harmless because a missing file raised before reaching this line.
        required = [path for path in (expected_paths or []) if path]
        if required:
            display = ", ".join(f"`{path}`" for path in required[:8])
            suffix = f" and {len(required) - 8} more" if len(required) > 8 else ""
            evidence.append(f"Required deliverables present: {display}{suffix}.")
        verified = [path for path in verified_paths if path and path not in required]
        if verified:
            display = ", ".join(f"`{path}`" for path in verified[:8])
            suffix = f" and {len(verified) - 8} more" if len(verified) > 8 else ""
            evidence.append(f"Final file state inspected: {display}{suffix}.")
        report = content.rstrip()
        if evidence:
            report += "\n\nConfirmed evidence:\n" + "\n".join(f"- {item}" for item in evidence)
        if unconfirmed:
            # Stated plainly, so an answer is never mistaken for verified work.
            report += "\n\nNot confirmed:\n" + "\n".join(f"- {item}" for item in unconfirmed)
        if sources:
            # An answer that left the machine says exactly where it went, so the
            # user can judge the source rather than take Aura's word for it.
            listed = list(dict.fromkeys(sources))
            report += "\n\nRead from the network:\n" + "\n".join(
                f"- {item}" for item in listed[:8])
            if len(listed) > 8:
                report += f"\n- …and {len(listed) - 8} more addresses."
        return report

    @staticmethod
    def tool_definitions() -> list[dict]:
        """Every tool the model may see, generated from where each is implemented.

        Declaration and dispatch used to live in two places that could drift
        apart with nothing to catch it; both now come from one registration.
        """
        return toolkit.definitions() + [service.tool_definition()
                                        for service in services.services()]

    @tool('list_files', 'List files recursively inside a workspace folder.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_list_files(self, name, args, approve, call):
        result = {"files": self.sandbox.list_files(str(args.get("path", ".")))[:1000]}
        return result

    @tool('create_folder', 'Create an empty workspace folder and missing parent folders. Use this instead of mkdir.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'], mutating=True)
    def _tool_create_folder(self, name, args, approve, call):
        target = self.sandbox.create_folder(str(args["path"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('read_file', 'Read a UTF-8 text file or a focused line range from the workspace.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'start_line': {'type': 'integer', 'minimum': 1, 'default': 1}, 'end_line': {'type': 'integer', 'minimum': 1}}, ['path'])
    def _tool_read_file(self, name, args, approve, call):
        content = self.sandbox.read_file(str(args["path"]))
        lines = content.splitlines(keepends=True)
        start = max(1, int(args.get("start_line", 1)))
        end = min(len(lines), int(args.get("end_line", start + 399)))
        selected = "".join(lines[start - 1:end])
        result = {"path": args["path"], "content": selected,
                  "start_line": start, "end_line": end, "total_lines": len(lines),
                  "truncated": end < len(lines)}
        return result

    @tool('read_many_files', 'Read several related UTF-8 workspace files in one call with bounded output.',
          {'paths': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, 'max_lines_each': {'type': 'integer', 'minimum': 20, 'maximum': 1000, 'default': 300}}, ['paths'])
    def _tool_read_many_files(self, name, args, approve, call):
        paths = args.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
            raise ValueError("paths must contain between 1 and 20 files")
        max_lines = max(20, min(int(args.get("max_lines_each", 300)), 1000))
        files = []
        output_chars = 0
        for raw_path in paths:
            path = str(raw_path)
            content = self.sandbox.read_file(path)
            lines = content.splitlines(keepends=True)
            selected = "".join(lines[:max_lines])
            output_chars += len(selected)
            if output_chars > 250_000:
                raise ValueError("combined read exceeds Aura's 250,000 character context limit")
            files.append({"path": path, "content": selected, "total_lines": len(lines),
                          "truncated": len(lines) > max_lines})
        result = {"files": files, "count": len(files)}
        return result

    @tool('file_info', "Inspect a file's size, line count, and modification time.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_file_info(self, name, args, approve, call):
        target = self.sandbox.path(str(args["path"]))
        if not target.is_file():
            raise FileNotFoundError(str(args["path"]))
        stat = target.stat()
        try:
            line_count = len(target.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            line_count = None
        result = {"path": args["path"], "bytes": stat.st_size,
                  "lines": line_count, "modified": stat.st_mtime}
        return result

    @tool('create_file', 'Create a new UTF-8 file; fails if it already exists.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    @tool('write_file', 'Create or replace a UTF-8 file in the workspace.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    @tool('append_file', 'Append UTF-8 text to a workspace file, creating it if needed.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    def _tool_create_file(self, name, args, approve, call):
        content = str(args["content"])
        if len(content.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("file content exceeds Aura's 1 MB tool limit")
        if name == "create_file":
            target = self.sandbox.create_file(str(args["path"]), content)
        elif name == "append_file":
            path = str(args["path"])
            existing = self.sandbox.read_file(path) if self.sandbox.path(path).exists() else ""
            target = self.sandbox.write_file(path, existing + content)
        else:
            target = self.sandbox.write_file(str(args["path"]), content)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "bytes": len(content.encode("utf-8"))}
        return result

    @tool('write_files', 'Create or replace up to 20 related UTF-8 files in one batch. Every file remains recoverable.',
          {'files': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'content': {'type': 'string'}}, 'required': ['path', 'content'], 'additionalProperties': False}}}, ['files'], mutating=True)
    def _tool_write_files(self, name, args, approve, call):
        items = args.get("files")
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("files must contain between 1 and 20 items")
        prepared: list[tuple[str, str, int]] = []
        total_bytes = 0
        for item in items:
            if not isinstance(item, dict) or "path" not in item or "content" not in item:
                raise ValueError("each file requires path and content")
            path, content = str(item["path"]), str(item["content"])
            size = len(content.encode("utf-8"))
            if size > self.MAX_WRITE_BYTES:
                raise ValueError(f"{path} exceeds Aura's 1 MB per-file tool limit")
            self.sandbox.path(path)
            prepared.append((path, content, size))
            total_bytes += size
        if total_bytes > 4_000_000:
            raise ValueError("combined batch write exceeds Aura's 4 MB limit")
        written = []
        for path, content, size in prepared:
            target = self.sandbox.write_file(path, content)
            written.append({"path": target.relative_to(self.sandbox.root).as_posix(),
                            "bytes": size})
        result = {"files": written, "count": len(written), "bytes": total_bytes}
        return result

    @tool('replace_in_file', 'Precisely replace exact text in an existing UTF-8 file. Fails if the match count is unexpected.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'expected_count': {'type': 'integer', 'minimum': 1, 'default': 1}}, ['path', 'old_text', 'new_text'], mutating=True)
    def _tool_replace_in_file(self, name, args, approve, call):
        path = str(args["path"])
        old, new = str(args["old_text"]), str(args["new_text"])
        if not old:
            raise ValueError("old_text cannot be empty")
        content = self.sandbox.read_file(path)
        expected = int(args.get("expected_count", 1))
        actual = content.count(old)
        if actual != expected:
            raise ValueError(f"expected {expected} exact matches but found {actual}")
        updated = content.replace(old, new)
        if len(updated.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("updated file exceeds Aura's 1 MB tool limit")
        target = self.sandbox.write_file(path, updated)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "replacements": actual}
        return result

    @tool('apply_edits', 'Atomically apply several exact text replacements to one file with one recovery snapshot.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'edits': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': {'type': 'object', 'properties': {'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'expected_count': {'type': 'integer', 'minimum': 1, 'default': 1}}, 'required': ['old_text', 'new_text'], 'additionalProperties': False}}}, ['path', 'edits'], mutating=True)
    def _tool_apply_edits(self, name, args, approve, call):
        path = str(args["path"])
        edits = args["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= 50:
            raise ValueError("edits must contain between 1 and 50 replacements")
        updated = self.sandbox.read_file(path)
        applied = 0
        for edit in edits:
            old, new = str(edit["old_text"]), str(edit["new_text"])
            if not old:
                raise ValueError("old_text cannot be empty")
            expected = int(edit.get("expected_count", 1))
            actual = updated.count(old)
            if actual != expected:
                raise ValueError(f"expected {expected} matches but found {actual} for edit {applied + 1}")
            updated = updated.replace(old, new)
            applied += actual
        if len(updated.encode("utf-8")) > self.MAX_WRITE_BYTES:
            raise ValueError("updated file exceeds Aura's 1 MB tool limit")
        target = self.sandbox.write_file(path, updated)
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "edits": len(edits), "replacements": applied}
        return result

    @tool('search_files', 'Search file names and UTF-8 contents in the workspace.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, ['query'])
    def _tool_search_files(self, name, args, approve, call):
        result = {"matches": self.sandbox.search_files(
            str(args["query"]), str(args.get("path", ".")))[:500]}
        return result

    @tool('search_text', 'Return matching lines with file names and line numbers.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}}, ['query'])
    def _tool_search_text(self, name, args, approve, call):
        limit = max(1, min(int(args.get("limit", 100)), 500))
        result = {"matches": self.sandbox.search_text(
            str(args["query"]), str(args.get("path", ".")), limit)}
        return result

    @tool('list_granted_folders', 'List folders outside the workspace that the user has granted Aura permission to read. Aura cannot grant itself access; only the user can, from the Permissions panel.',
          {}, [])
    def _tool_list_granted_folders(self, name, args, approve, call):
        result = {"folders": [
            {"path": grant["root"], "mode": grant["mode"],
             "project": grant.get("project")}
            for grant in self.permissions.active()
            if grant.get("capability") == "read_folder"]}
        return result

    @tool('list_external_folder', 'List files inside a folder the user has already granted. Fails if there is no active permission for that folder.',
          {'path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 200}}, ['path'])
    def _tool_list_external_folder(self, name, args, approve, call):
        result = {"path": str(args["path"]),
                  "files": self.external.list_files(
                      str(args["path"]), limit=int(args.get("limit", 200)))}
        return result

    @tool('read_external_file', "Read a UTF-8 text file inside a folder the user has already granted. Fails if there is no active permission for that file's folder.",
          {'path': {'type': 'string'}}, ['path'])
    def _tool_read_external_file(self, name, args, approve, call):
        result = {"path": str(args["path"]),
                  "content": self.external.read_file(str(args["path"]))}
        return result

    @tool('write_external_file', 'Write a UTF-8 text file inside a folder the user granted for writing. The previous version is saved first, so the change can be undone. A read grant is not enough; writing needs its own permission.',
          {'path': {'type': 'string'}, 'content': {'type': 'string'}}, ['path', 'content'], mutating=True)
    def _tool_write_external_file(self, name, args, approve, call):
        result = self.external_writer.write_file(
            str(args["path"]), str(args["content"]),
            task_id=self.current_task_id)
        return result

    @tool('undo_external_change', "Undo Aura's most recent write outside the workspace, restoring the previous version or removing a file it created.",
          {}, [])
    def _tool_undo_external_change(self, name, args, approve, call):
        result = self.external_writer.undo_last()
        return result

    @tool('check_accessibility', 'Report accessibility problems in workspace HTML: images without alt text, form controls without labels, empty links or buttons, a missing lang or title, and skipped heading levels. Structural checks only — it does not evaluate colour contrast.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_check_accessibility(self, name, args, approve, call):
        result = check_accessibility(self.sandbox, str(args.get("path", ".")))
        return result

    @tool('compare_images', 'Measure exactly how two workspace PNG images differ: percentage of changed pixels and the region that changed. Use it to check a render against a reference or to detect a layout regression between two screenshots. This is a real pixel measurement, not an impression.',
          {'first': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'second': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'tolerance': {'type': 'integer', 'minimum': 0, 'maximum': 128, 'default': 8}}, ['first', 'second'])
    def _tool_compare_images(self, name, args, approve, call):
        result = compare_images(
            self.sandbox.path(str(args["first"])),
            self.sandbox.path(str(args["second"])),
            tolerance=int(args.get("tolerance", 8)))
        return result

    @tool('capture_page', "Render a workspace HTML page in a local headless browser and save a PNG screenshot of it into the workspace. Use this to see how a page actually looks, then call look_at_image on the saved screenshot. Needs the user's approval because it launches a browser.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'width': {'type': 'integer', 'minimum': 320, 'maximum': 2560, 'default': 1200}, 'height': {'type': 'integer', 'minimum': 240, 'maximum': 2000, 'default': 800}}, ['path'])
    def _tool_capture_page(self, name, args, approve, call):
        result = self._capture_page(
            str(args["path"]), approve,
            int(args.get("width", 1200)), int(args.get("height", 800)))
        # The wrapper reads "ok" straight from the result now.
        result["ok"] = bool(result.get("approved"))
        return result

    @tool('look_at_image', 'Actually look at a workspace image (PNG/JPEG/GIF/WebP/BMP). The image is attached to the conversation so you can describe or compare what it shows. Call this whenever the user asks what an image contains or looks like — listing or reading the file cannot answer that, because its pixels are only visible through this tool.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_look_at_image(self, name, args, approve, call):
        if not self.vision_enabled():
            raise ValueError(
                "The loaded model does not accept images. Turn vision on in "
                "Settings if you know it does.")
        result = self._read_image_attachment(str(args["path"]))
        return result

    @tool('find_relevant_files', 'Rank workspace files by relevance to a described topic or question. Use this when you do not know the exact wording to search for; use search_files or search_text when you need an exact string. Matches words, not synonyms.',
          {'query': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10}}, ['query'])
    def _tool_find_relevant_files(self, name, args, approve, call):
        result = {"matches": self.index.search(
            str(args["query"]), int(args.get("limit", 10)),
            str(args.get("path", ".")))}
        return result

    @tool('copy_file', 'Copy a workspace file.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_copy_file(self, name, args, approve, call):
        target = self.sandbox.copy_file(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('move_file', 'Move or rename a workspace file.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_move_file(self, name, args, approve, call):
        target = self.sandbox.move_file(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix()}
        return result

    @tool('safe_delete_file', "Move a file into Aura's recoverable trash.",
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'], mutating=True)
    def _tool_safe_delete_file(self, name, args, approve, call):
        target = self.sandbox.safe_delete_file(str(args["path"]))
        result = {"trashed_as": target.name, "recoverable": True}
        return result

    @tool('undo_last_change', "Undo Aura's most recent file mutation using its protected snapshot history.",
          {}, [])
    def _tool_undo_last_change(self, name, args, approve, call):
        result = self.sandbox.undo_last_change()
        return result

    @tool('rollback_task', 'Undo every still-active file mutation belonging to a specific Aura task ID.',
          {'task_id': {'type': 'string'}}, ['task_id'])
    def _tool_rollback_task(self, name, args, approve, call):
        result = self.sandbox.rollback_task(str(args["task_id"]))
        return result

    @tool('change_history', 'List recent recoverable workspace mutations and whether they were undone.',
          {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}}, [])
    def _tool_change_history(self, name, args, approve, call):
        result = {"changes": self.sandbox.change_history(int(args.get("limit", 20)))}
        return result

    @tool('workspace_summary', 'Summarize workspace file count, size, extensions, and largest files.',
          {}, [])
    def _tool_workspace_summary(self, name, args, approve, call):
        files = self.sandbox.list_files()
        sizes = []
        extensions: dict[str, int] = {}
        for relative in files:
            target = self.sandbox.path(relative)
            size = target.stat().st_size
            sizes.append((relative, size))
            extension = target.suffix.casefold() or "(none)"
            extensions[extension] = extensions.get(extension, 0) + 1
        result = {"file_count": len(files), "total_bytes": sum(size for _, size in sizes),
                  "extensions": dict(sorted(extensions.items())),
                  "largest_files": [{"path": path, "bytes": size}
                                    for path, size in sorted(sizes, key=lambda item: item[1], reverse=True)[:10]]}
        return result

    @tool('inspect_code', 'Outline symbols, imports, and structure in a Python, JavaScript, or TypeScript file without executing it.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_inspect_code(self, name, args, approve, call):
        result = self._inspect_code(str(args["path"]))
        return result

    @tool('compare_files', 'Produce a bounded unified diff between two UTF-8 workspace files.',
          {'left': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'right': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'context_lines': {'type': 'integer', 'minimum': 0, 'maximum': 20, 'default': 3}}, ['left', 'right'])
    def _tool_compare_files(self, name, args, approve, call):
        left, right = str(args["left"]), str(args["right"])
        context = max(0, min(int(args.get("context_lines", 3)), 20))
        result = self.sandbox.compare_files(left, right, context)
        return result

    @tool('calculate', 'Evaluate arithmetic and common math functions locally without running code.',
          {'expression': {'type': 'string'}}, ['expression'])
    def _tool_calculate(self, name, args, approve, call):
        expression = str(args["expression"])
        result = {"expression": expression, "result": self._calculate(expression)}
        return result

    @tool('system_info', 'Inspect non-sensitive local runtime facts such as OS, Python, CPU count, and workspace disk space.',
          {}, [])
    def _tool_system_info(self, name, args, approve, call):
        disk = shutil.disk_usage(self.sandbox.root)
        result = {"os": platform.platform(), "python": platform.python_version(),
                  "architecture": platform.machine(), "cpu_count": os.cpu_count(),
                  "workspace": str(self.sandbox.root),
                  "workspace_disk": {"total": disk.total, "used": disk.used, "free": disk.free}}
        return result

    @tool('validate_project', 'Safely validate every project file, including Python, JSON, TOML, HTML, CSS, JavaScript/TypeScript, XML, and UTF-8 text, without executing project code.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..', 'default': '.'}}, [])
    def _tool_validate_project(self, name, args, approve, call):
        result = self._validate_project(str(args.get("path", ".")))
        return result

    @tool('run_command', 'Run an actual program, test, build, or project runtime inside the workspace. Commands use a direct argument array with no shell. Never use this for file/folder operations; create_file and write_file create parent folders. Use python for Python; unsafe commands require approval.',
          {'command': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1}, 'timeout': {'type': 'number', 'minimum': 1, 'maximum': 60, 'default': 15}}, ['command'])
    def _tool_run_command(self, name, args, approve, call):
        command = args["command"]
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise ValueError("command must be an array of strings")
        timeout = max(1.0, min(float(args.get("timeout", 15)), 60.0))
        run = self.commands.run(
            command, approve=approve, timeout=timeout,
            autonomy=str(self.config.data.get("autonomy_mode", "balanced")),
        )
        result = {"approved": run.approved, "returncode": run.returncode,
                  "stdout": run.stdout[-20_000:], "stderr": run.stderr[-20_000:],
                  "timed_out": run.timed_out, "blocked": run.blocked}
        result["ok"] = run.succeeded
        if not run.succeeded:
            if run.blocked:
                reason = run.stderr or "Command is blocked by Aura's workspace policy."
            elif not run.approved:
                reason = "Command was not approved."
            elif run.timed_out:
                reason = "Command timed out."
            elif run.returncode is None:
                reason = run.stderr or "Command could not be started."
            else:
                reason = run.stderr.strip() or f"Command exited with code {run.returncode}."
            result["error"] = reason
        return result

    @tool('http_get', 'Fetch a bounded HTTP(S) text response. Localhost is direct; any other domain must already be granted by the user under Permissions, and cannot be requested from here.',
          {'url': {'type': 'string'}, 'timeout': {'type': 'number', 'minimum': 1, 'maximum': 20, 'default': 10}}, ['url'])
    def _tool_http_get(self, name, args, approve, call):
        result = self._http_get(str(args["url"]),
                                max(1.0, min(float(args.get("timeout", 10)), 20.0)))
        return result

    @tool('search_web',
          'Search the web through the SearXNG instance the user runs on this machine. '
          'Returns titles, links, and the snippet the engine already produced. It returns '
          'snippets only and never opens the result pages, so never describe what a linked '
          'page says as if you had read it — say the snippet said it. Refuses, with a reason, '
          'when the user has no search service configured or running.',
          {'query': {'type': 'string'},
           'count': {'type': 'integer', 'minimum': 1, 'maximum': 8, 'default': 5}}, ['query'])
    def _tool_search_web(self, name, args, approve, call):
        return self._search_web(str(args["query"]), int(args.get("count", 5)))

    @tool('open_workspace_item', 'Open a workspace file or folder in its normal desktop application after approval.',
          {'path': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['path'])
    def _tool_open_workspace_item(self, name, args, approve, call):
        path = str(args["path"])
        target = self.sandbox.path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if not approve or not approve(["OPEN", path]):
            raise PermissionError("Opening a desktop application was not approved")
        os.startfile(target)  # type: ignore[attr-defined]
        result = {"path": path, "opened": True}
        return result

    @tool('create_archive', 'Create a recoverable ZIP archive from a workspace file or folder.',
          {'source': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['source', 'destination'], mutating=True)
    def _tool_create_archive(self, name, args, approve, call):
        target = self.sandbox.create_archive(str(args["source"]), str(args["destination"]))
        result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                  "bytes": target.stat().st_size}
        return result

    @tool('extract_archive', 'Safely extract a workspace ZIP with traversal, link, file-count, and size protection.',
          {'archive': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}, 'destination': {'type': 'string', 'description': 'Workspace-relative path; never absolute and never use ..'}}, ['archive', 'destination'], mutating=True)
    def _tool_extract_archive(self, name, args, approve, call):
        extracted = self.sandbox.extract_archive(str(args["archive"]), str(args["destination"]))
        result = {"files": [path.relative_to(self.sandbox.root).as_posix() for path in extracted],
                  "count": len(extracted)}
        return result

    @tool('capability_summary', "List Aura's currently available tools and autonomy policy.",
          {}, [])
    def _tool_capability_summary(self, name, args, approve, call):
        result = {"tools": [item["function"]["name"] for item in self.tool_definitions()],
                  "tool_count": len(self.tool_definitions()),
                  "reasoning_depth": self.config.data.get("reasoning_depth"),
                  "autonomy_mode": self.config.data.get("autonomy_mode"),
                  "workspace_only": True,
                  "approval_policy": "Safe local tools are automatic; executable code, external HTTP, and desktop launches ask first."}
        return result

    @tool('remember_name', "Remember the user's preferred name.",
          {'name': {'type': 'string'}}, ['name'])
    def _tool_remember_name(self, name, args, approve, call):
        self.memory.set_name(str(args["name"]))
        result = {"remembered": True}
        return result

    @tool('remember_preference', 'Remember one durable user preference.',
          {'key': {'type': 'string'}, 'value': {'type': 'string'}}, ['key', 'value'])
    def _tool_remember_preference(self, name, args, approve, call):
        self.memory.set_preference(str(args["key"]), str(args["value"]))
        result = {"remembered": True}
        return result

    @tool('remember_personal_fact', 'Remember one clear, non-sensitive fact the user explicitly stated about their preferences, interests, goals, projects, tools, or working style.',
          {'category': {'type': 'string', 'enum': ['goal', 'interest', 'personal', 'preference', 'project', 'tool', 'work_style']}, 'value': {'type': 'string'}}, ['category', 'value'])
    def _tool_remember_personal_fact(self, name, args, approve, call):
        item = self.memory.learn_fact(
            str(args["category"]), str(args["value"]),
            source="Explicitly remembered through Aura chat", confidence=1.0, explicit=True,
        )
        result = {"remembered": True, "memory": item}
        return result

    @tool('list_personal_memory', 'Review the editable personal facts Aura currently remembers about the user.',
          {'query': {'type': 'string', 'default': ''}}, [])
    def _tool_list_personal_memory(self, name, args, approve, call):
        query = str(args.get("query", "")).strip()
        memories = (self.memory.find_profile_memories(query) if query
                    else self.memory.profile_memories())
        result = {"memories": memories[:100], "count": len(memories)}
        return result

    @tool('forget_personal_fact', "Forget one personal memory matching the user's description. Ambiguous matches are returned without deleting anything.",
          {'query': {'type': 'string'}}, ['query'])
    def _tool_forget_personal_fact(self, name, args, approve, call):
        query = str(args["query"])
        matches = self.memory.find_profile_memories(query)
        if not matches:
            raise FileNotFoundError("No personal memory matches that description")
        if len(matches) != 1:
            choices = "; ".join(str(item.get("value", "")) for item in matches[:5])
            raise ValueError(f"Memory description is ambiguous; matching facts: {choices}")
        removed = self.memory.forget_profile_memory(str(matches[0]["id"]))
        result = {"forgotten": True, "memory": removed}
        return result

    @tool('correct_personal_fact', 'Correct one unambiguous personal memory and mark the corrected value as user-confirmed.',
          {'query': {'type': 'string'}, 'new_value': {'type': 'string'}, 'category': {'type': 'string', 'enum': ['goal', 'interest', 'personal', 'preference', 'project', 'tool', 'work_style']}}, ['query', 'new_value'])
    def _tool_correct_personal_fact(self, name, args, approve, call):
        query = str(args["query"])
        matches = self.memory.find_profile_memories(query)
        if not matches:
            raise FileNotFoundError("No personal memory matches that description")
        if len(matches) != 1:
            choices = "; ".join(str(item.get("value", "")) for item in matches[:5])
            raise ValueError(f"Memory description is ambiguous; matching facts: {choices}")
        updated = self.memory.update_profile_memory(
            str(matches[0]["id"]), value=str(args["new_value"]),
            category=str(args.get("category") or matches[0].get("category", "personal")),
        )
        result = {"corrected": True, "memory": updated}
        return result

    @tool('recent_tasks', "Review Aura's recent persistent task outcomes and tools used.",
          {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 5}}, [])
    def _tool_recent_tasks(self, name, args, approve, call):
        result = {"tasks": self.tasks.recent(max(1, min(int(args.get("limit", 5)), 20)))}
        return result

    @tool('set_check',
          "Watch something in the workspace on a schedule and speak only when there is "
          "something worth saying. Read-only: a check never changes anything.",
          {'check': {'type': 'string', 'enum': checks.names(),
                     'description': 'Which check to run'},
           'every_minutes': {'type': 'integer', 'minimum': 15, 'maximum': 20160,
                             'description': 'How often, at least every 15 minutes'}},
          ['check', 'every_minutes'])
    def _tool_set_check(self, name, args, approve, call):
        wanted = str(args.get("check", "")).strip()
        if checks.get(wanted) is None:
            raise ValueError(f"unknown check {wanted!r}; choose one of {', '.join(checks.names())}")
        every = max(15, min(int(args.get("every_minutes", 1440)), 20160))
        existing = [task for task in self.db.scheduled_tasks(include_disabled=False)
                    if task.get("kind") == "check" and task.get("request") == wanted]
        if existing:
            return {"check": wanted, "already_scheduled": True,
                    "next_run": existing[0]["next_run"]}
        due = datetime.now(timezone.utc) + timedelta(minutes=every)
        task = self.db.add_scheduled("check", wanted, every_minutes=every,
                                     next_run=due.isoformat())
        return {"check": wanted, "every_minutes": every, "next_run": task["next_run"]}

    #: A reminder only ever shows a message, so the model may set one. It may
    #: not schedule anything else: this tool hard-codes the kind, so nothing it
    #: writes can become background work that acts.
    MAX_ACTIVE_REMINDERS = 20

    @tool('set_reminder',
          "Remind the user about something later. Give the delay in minutes from now — "
          "convert 'tomorrow morning' or 'in an hour' yourself. A reminder only shows a "
          "message; it cannot change anything, and it waits for quiet hours.",
          {'text': {'type': 'string', 'description': 'What to remind the user about'},
           'in_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 20160,
                          'description': 'Delay from now, up to two weeks'},
           'repeat_minutes': {'type': 'integer', 'minimum': 5, 'maximum': 20160,
                              'description': 'Optional: repeat every N minutes'}},
          ['text', 'in_minutes'])
    def _tool_set_reminder(self, name, args, approve, call):
        text = str(args.get("text", "")).strip()
        if not text:
            raise ValueError("a reminder needs something to say")
        active = [task for task in self.db.scheduled_tasks(include_disabled=False)
                  if task.get("kind") == "reminder"]
        if len(active) >= self.MAX_ACTIVE_REMINDERS:
            raise ValueError(
                f"there are already {len(active)} reminders waiting; cancel one first")
        delay = max(1, min(int(args.get("in_minutes", 60)), 20160))
        repeat = int(args.get("repeat_minutes") or 0)
        due = datetime.now(timezone.utc) + timedelta(minutes=delay)
        task = self.db.add_scheduled("reminder", text[:400], every_minutes=repeat,
                                     next_run=due.isoformat())
        return {"reminder": text[:400], "due": task["next_run"],
                "in_minutes": delay, "repeats_every_minutes": repeat or None}


    def _execute_tool(self, call: ToolCall, approve: Callable[[list[str]], bool] | None) -> dict:
        """Run one tool, with the audit trail every tool shares.

        Logging, task recording, and error handling stay here rather than in the
        handlers, so a newly added tool cannot quietly skip them.
        """
        name, args = call.name, call.arguments
        try:
            service = services.get(name)
            spec = toolkit.get(name)
            if service is not None:
                # The service is handed a fetch that already enforces the domain
                # grant, so it cannot reach anywhere the user has not allowed.
                result = service.handler(
                    lambda url, timeout=10.0: self._http_get(url, timeout), dict(args))
            elif spec is not None:
                result = spec.handler(self, name, args, approve, call)
            else:
                raise ValueError(f"unknown tool: {name}")
            redacted = {k: v for k, v in args.items()
                        if k not in {"content", "old_text", "new_text", "edits"}}
            if name != "run_command":
                self.log.record(name, tool_call=call.id, arguments=redacted)
            payload = {"ok": True, **result}
            if self.current_task_id:
                self.tasks.record_tool(self.current_task_id, name, redacted, payload)
            return payload
        except Exception as exc:
            self.log.record(name, "error", tool_call=call.id, error=str(exc))
            payload = {"ok": False, "error": str(exc)}
            if self.current_task_id:
                safe_args = {k: v for k, v in args.items()
                             if k not in {"content", "old_text", "new_text", "edits"}}
                self.tasks.record_tool(self.current_task_id, name, safe_args, payload)
            return payload


    def _inspect_code(self, relative: str) -> dict:
        target = self.sandbox.path(relative)
        if not target.is_file():
            raise FileNotFoundError(relative)
        content = target.read_text(encoding="utf-8")
        if len(content) > 1_000_000:
            raise ValueError("code file exceeds Aura's 1 MB inspection limit")
        suffix = target.suffix.casefold()
        result: dict = {
            "path": relative,
            "language": suffix.lstrip(".") or "text",
            "lines": len(content.splitlines()),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "imports": [],
            "symbols": [],
        }
        if suffix == ".py":
            tree = ast.parse(content, filename=relative)
            imports: list[str] = []
            symbols: list[dict] = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(("." * node.level) + (node.module or ""))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else (
                        "async function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                    entry = {"name": node.name, "kind": kind, "line": node.lineno}
                    if isinstance(node, ast.ClassDef):
                        entry["methods"] = [child.name for child in node.body
                                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))][:80]
                    symbols.append(entry)
            result.update({"language": "python", "imports": imports[:100], "symbols": symbols[:200]})
            return result
        if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            imports = re.findall(
                r"(?m)^\s*(?:import\s+.*?\s+from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]",
                content,
            )
            symbols = []
            pattern = re.compile(
                r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(?:(class|function)\s+([A-Za-z_$][\w$]*)|"
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^\n]*\)|[A-Za-z_$][\w$]*)\s*=>)"
            )
            for match in pattern.finditer(content):
                name = match.group(2) or match.group(3)
                kind = match.group(1) or "function"
                symbols.append({"name": name, "kind": kind,
                                "line": content.count("\n", 0, match.start()) + 1})
            result.update({"language": "typescript" if "ts" in suffix else "javascript",
                           "imports": imports[:100], "symbols": symbols[:200]})
        return result

    @staticmethod
    def _calculate(expression: str) -> int | float:
        if not expression.strip() or len(expression) > 240:
            raise ValueError("expression must contain between 1 and 240 characters")
        functions = {
            "abs": abs, "round": round, "sqrt": math.sqrt, "sin": math.sin,
            "cos": math.cos, "tan": math.tan, "log": math.log, "log10": math.log10,
            "floor": math.floor, "ceil": math.ceil,
        }
        constants = {"pi": math.pi, "e": math.e, "tau": math.tau}
        binary = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
                  ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
                  ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
                  ast.Pow: lambda a, b: a ** b}
        unary = {ast.UAdd: lambda value: value, ast.USub: lambda value: -value}

        def evaluate(node: ast.AST, depth: int = 0) -> int | float:
            if depth > 30:
                raise ValueError("expression is too deeply nested")
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
            if isinstance(node, ast.Name) and node.id in constants:
                return constants[node.id]
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                left, right = evaluate(node.left, depth + 1), evaluate(node.right, depth + 1)
                if isinstance(node.op, ast.Pow) and abs(right) > 100:
                    raise ValueError("exponent is too large")
                return binary[type(node.op)](left, right)
            if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
                return unary[type(node.op)](evaluate(node.operand, depth + 1))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
                if node.keywords or len(node.args) > 3:
                    raise ValueError("unsupported function arguments")
                return functions[node.func.id](*(evaluate(arg, depth + 1) for arg in node.args))
            raise ValueError("only arithmetic and approved math functions are allowed")

        value = evaluate(ast.parse(expression, mode="eval").body)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("result is not finite")
        if abs(value) > 1e100:
            raise ValueError("result is too large")
        return value

    #: A read-only tool with no budget will be called until the turn runs out.
    #: Live, the model made twelve near-identical searches for one question
    #: before anything stopped it.
    MAX_SEARCHES_PER_TURN = 5

    def _search_web(self, query: str, count: int) -> dict:
        """Read the user's own search service. Snippets, never the pages behind them.

        There is nothing to enforce here beyond what already exists: a result
        URL is a domain the user has not granted, so `_http_get` refuses it. The
        restriction is a property of the permission model rather than a rule
        this method has to remember.
        """
        endpoint = websearch.endpoint_of(self.config.data.get("search_endpoint"))
        url = websearch.build_url(endpoint, query, count)
        asked = " ".join(str(query).split()).casefold()
        if asked in self.searches_this_turn:
            # Rephrasing the same question does not produce different results,
            # and saying so is more useful than answering it again identically.
            return {"query": query.strip(), "searched": endpoint, "repeat": True,
                    "count": len(self.searches_this_turn[asked]),
                    "results": self.searches_this_turn[asked],
                    "note": "You already searched for this. " + websearch.NOT_READ}
        if len(self.searches_this_turn) >= self.MAX_SEARCHES_PER_TURN:
            raise websearch.SearchUnavailable(
                f"That is {self.MAX_SEARCHES_PER_TURN} searches for one question, which "
                "is enough. Answer from what the snippets already say, and say plainly "
                "if they do not cover it.")
        try:
            response = self._http_get(url, 15.0)
        except (RuntimeError, OSError) as exc:
            raise websearch.unreachable(endpoint, str(exc)) from exc
        results = websearch.parse(response, count)
        # A loopback fetch is not recorded as a source, but this one is what the
        # reply actually rests on, so it is cited like any other reading.
        self.fetched_sources.append(url)
        self.searches_this_turn[asked] = results
        return {"query": query.strip(), "searched": endpoint, "count": len(results),
                "results": results, "note": websearch.NOT_READ}

    def _http_get(self, url: str, timeout: float) -> dict:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        opener = build_opener(NoRedirect)
        current = url.strip()
        for _ in range(6):
            parsed = urlparse(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("URL must use http or https")
            if parsed.username or parsed.password:
                raise ValueError("URLs containing credentials are not allowed")
            host = parsed.hostname.casefold()
            is_loopback = host in {"localhost", "127.0.0.1", "::1"}
            if not is_loopback:
                # A domain grant, never a dialog: asking mid-task is how a model
                # talks its way outward, and the folder capabilities already
                # settled that permission is something the user gives up front.
                self.permissions.check("reach_domain", current, consume=False)
                try:
                    # Re-resolved on every hop, because a name that was public
                    # when granted can point at the local network later.
                    reject_unsafe_host(host)
                except PermissionRefused as exc:
                    raise PermissionError(str(exc)) from exc
                self.fetched_sources.append(current)
            request = Request(current, headers={
                "User-Agent": "Aura-Local/1.0", "Accept": "text/*, application/json, application/xml",
            })
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as exc:
                if 300 <= exc.code < 400 and exc.headers.get("Location"):
                    current = urljoin(current, exc.headers["Location"])
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
            except (URLError, TimeoutError) as exc:
                raise RuntimeError(f"HTTP request failed: {exc}") from exc
            with response:
                raw = response.read(self.MAX_HTTP_BYTES + 1)
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset() or "utf-8"
                text_types = content_type.startswith("text/") or content_type in {
                    "application/json", "application/xml", "application/javascript",
                }
                payload = raw[:self.MAX_HTTP_BYTES].decode(charset, errors="replace") if text_types else ""
                return {"url": response.geturl(), "status": response.status,
                        "content_type": content_type, "bytes": len(raw),
                        "content": payload,
                        "truncated": len(raw) > self.MAX_HTTP_BYTES,
                        "sha256": hashlib.sha256(raw[:self.MAX_HTTP_BYTES]).hexdigest()}
        raise RuntimeError("HTTP request exceeded five redirects")

    def _validate_project(self, relative: str = ".") -> dict:
        return validate_project(self.sandbox, relative)

    def build_hello_world(self, approve: Callable[[list[str]], bool] | None,
                          state: Callable[[str], None]) -> str:
        state("working")
        project = "hello-world"
        plan = """# Hello World app\n\n1. Create a small Python entry point.\n2. Validate its syntax.\n3. Run it after approval.\n"""
        source = 'def main():\n    print("Hello from Aura!")\n\n\nif __name__ == "__main__":\n    main()\n'
        readme = "# Hello World\n\nRun with `python hello.py`.\n"
        for name, content in ((f"{project}/PLAN.md", plan), (f"{project}/hello.py", source),
                              (f"{project}/README.md", readme)):
            self.sandbox.write_file(name, content)
            self.log.record("write_file", path=name, bytes=len(content.encode()))
        validation = self.commands.run([sys.executable, "-m", "compileall", "-q", project])
        if validation.returncode != 0:
            raise RuntimeError(f"validation failed: {validation.stderr.strip()}")
        run = self.commands.run([sys.executable, f"{project}/hello.py"], approve=approve)
        if not run.approved:
            return ("I created and validated `hello-world`. Running the app still needs your approval. "
                    "Files: PLAN.md, README.md, hello.py.")
        if run.returncode != 0:
            raise RuntimeError(f"run failed: {run.stderr.strip()}")
        output = run.stdout.strip()
        return ("Built and validated `hello-world` successfully.\n"
                f"Run output: {output}\nFiles: PLAN.md, README.md, hello.py.")


# One source of truth for what counts as a workspace mutation: the tools that
# declare themselves mutating, plus `import_file`, which the user performs
# through the UI rather than the model through a tool.
AuraAgent.MUTATING_TOOL_NAMES = toolkit.mutating_names() | {"import_file"}
