from __future__ import annotations

from .errors import AuraError
from .tools_files import FilesTools
from .tools_recovery import RecoveryTools
from .tools_memory import MemoryTools
from .tools_outside import OutsideTools
from .tools_plan import PlanTools
from .tools_media import MediaTools
from .tools_system import SystemTools
from .tools_code import CodeTools

import ast
import base64
import hashlib
import json
import math
import os
import posixpath
import re
import sys
import tempfile
import time
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
from . import language
from . import context_budget, empty_reply, routing, sampling, tool_errors, tool_loops
from . import services
from . import websearch
from . import toolkit
from .toolkit import tool
from .turn import PASS, GateResult, TurnState
from .permissions import (ExternalReader, ExternalWriter, PermissionDenied,
                          PermissionRefused, PermissionStore, reject_unsafe_host)
from .preview_server import PreviewServer
from .safety import SandboxViolation, WorkspaceSandbox
from .screenshot import (ScreenshotUnavailable, browser_command_preview, capture,
                         find_browser)
from .search_index import WorkspaceIndex
from .tasks import TaskJournal
from .validation import check_broken_assets, validate_project


EXTERNAL_TOOLS = {
    "list_granted_folders", "list_external_folder", "read_external_file",
    "write_external_file", "undo_external_change",
}


class TaskCancelled(AuraError, RuntimeError):
    pass


class TurnExpired(AuraError, RuntimeError):
    """The turn ran past its time budget. Distinct from cancellation: Mat did not
    ask for this to stop, so the answer says what was achieved rather than that it
    was called off."""


class AuraAgent(FilesTools, RecoveryTools, MemoryTools, OutsideTools, MediaTools,
                SystemTools, PlanTools, CodeTools):
    MAX_TOOL_ROUNDS = 48
    MAX_WRITE_BYTES = 1_000_000
    MAX_HTTP_BYTES = 250_000
    ROUND_LIMITS = {"fast": 16, "balanced": 30, "deep": 48}
    # Recovery stays available for 30 days or 500 changes, whichever ends first.
    RETENTION_DAYS = 30
    RETENTION_CHANGES = 500
    # Total extra model rounds allowed to satisfy the completion gates.
    MAX_COMPLETION_RETRIES = 3
    #: Silence gets one retry, not three. Across every episode in the live log a
    #: second and third attempt at the same question returned the same silence,
    #: costing minutes at 22 tokens a second to learn nothing.
    MAX_EMPTY_RETRIES = 1

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
        #: Learns what this model charges per character from the token counts
        #: LM Studio returns, so the preflight check is measured against the
        #: model actually loaded rather than against a rule of thumb.
        self.budget = context_budget.TokenMeter()
        self.provider = provider or self._build_provider()
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
        #: Sticky for the conversation; cleared when a new one starts.
        self.current_project: str | None = None
        #: Called as private reasoning arrives, with how much. This model spends
        #: most of a turn producing reasoning that never reaches the browser, so
        #: without this the interface cannot tell thinking from a hang.
        self.on_thinking: Callable[[int], None] | None = None
        #: The request being handled, in the user's own words. The plan
        #: file quotes it, so a paraphrase would be worse than nothing.
        self.current_request: str = ""
        #: Told after every tool, so a caller can follow the work while it
        #: happens. Set per turn, in the same way approve/state/token are.
        self.on_tool: Callable[[str, dict, bool], None] | None = None
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
        # A new conversation is not a continuation of the last project.
        self.current_project = None
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

    def _build_provider(self):
        """Whichever model the user chose — and never a silent switch.

        A cloud provider that stepped in when the local one was unreachable
        would move the conversation, the memories, and whatever files the tools
        read off this machine without anyone asking. So there is no fallback
        here: if the chosen provider cannot be reached, that is an error to
        report, not a reason to send the same words somewhere else.
        """
        chosen = str(self.config.data.get("provider", "local")).strip()
        if chosen == "openai":
            from .cloud import OpenAIProvider
            return OpenAIProvider(
                api_key=os.getenv("OPENAI_API_KEY")
                or str(self.config.data.get("openai_api_key") or ""),
                model=str(self.config.data.get("openai_model") or ""),
                base_url=str(self.config.data.get("openai_base_url") or ""),
                timeout=float(self.config.data["timeout"]),
                max_tokens=int(self.config.data.get("cloud_max_tokens") or 0) or None,
                temperature=float(self.config.data["temperature"]),
            )
        if chosen == "claude":
            from .cloud import AnthropicProvider
            return AnthropicProvider(
                api_key=os.getenv("ANTHROPIC_API_KEY")
                or str(self.config.data.get("anthropic_api_key") or ""),
                model=str(self.config.data.get("cloud_model") or ""),
                timeout=float(self.config.data["timeout"]),
                max_tokens=int(self.config.data.get("cloud_max_tokens") or 0) or None,
            )
        return LMStudioProvider(
            base_url=os.getenv("AURA_LM_STUDIO_URL", str(self.config.data["lm_studio_url"])),
            model=os.getenv("AURA_LM_STUDIO_MODEL") or self.config.data["model"],
            timeout=float(os.getenv("AURA_LM_STUDIO_TIMEOUT", str(self.config.data["timeout"]))),
            temperature=float(self.config.data["temperature"]),
            max_tokens=int(self.config.data["max_tokens"]),
        )

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
                return self._guess_vision(model)
            cache = dict(probed) if isinstance(probed, dict) else {}
            cache[model] = supported
            self.config.update(vision_probe=cache)
            self.log.record("vision_probe", "ok", model=model, supported=supported)
            return supported
        return self._guess_vision(model)

    def _guess_vision(self, model: str) -> bool:
        """Ask the provider in use, not the one that used to be the only one.

        This named `LMStudioProvider` outright, so a cloud model was judged by
        LM Studio's list of name fragments — which no Claude or GPT model
        matches — and images were quietly withheld from providers that handle
        them well. The providers' own answers were being written and ignored.
        """
        guesser = getattr(self.provider, "model_may_support_vision", None)
        if not callable(guesser):
            return LMStudioProvider.model_may_support_vision(model)
        return bool(guesser(model))

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

    def workspace_projects(self) -> list[str]:
        """Top-level folders. A project here is a folder, not a guess at a noun."""
        root = Path(self.sandbox.root)
        if not root.is_dir():
            return []
        return sorted(item.name for item in root.iterdir()
                      if item.is_dir() and not item.name.startswith("."))

    def detect_project(self, message: str) -> str | None:
        """Which project this request is about.

        Matched against folders that actually exist, so an ordinary word can
        never invent a project. Measured before this existed: "Add a contact
        page to the promo site" detected nothing, because the only shape
        recognised was "in the promo folder".
        """
        text = str(message or "").casefold()
        for name in self.workspace_projects():
            lowered = name.casefold()
            if re.search(rf"(?<![\w-]){re.escape(lowered)}(?![\w-])", text):
                return name
        # An explicit "the X project" or "in the X folder" is the user saying
        # which project this is, and it holds whether or not the folder exists
        # yet — a project usually gets named before it gets created. The
        # existence rule above is only there to stop a bare noun like "shopping
        # list" inventing one.
        return self._extract_artifact_contract(message)[0]

    def project_for(self, message: str) -> str | None:
        """The project in play, remembered across the conversation.

        A follow-up rarely names it again — "now add a footer to it" is the
        normal second message — and forgetting it there is what made Aura recall
        another project's rules for the work in hand.
        """
        found = self.detect_project(message)
        if found:
            self.current_project = found
        return self.current_project

    def _context(self, query: str = "") -> ProviderContext:
        project = self.project_for(query) if query else self.current_project
        recalled = self.memory.relevant_memories(query, 12, project=project)
        # Remember what was recalled so the interface can explain the choice.
        self.last_recalled = recalled
        return ProviderContext(self.memory.data.get("name"), self.memory.data.get("preferences", {}),
                               self.memory.data.get("conversation", []), recalled,
                               project=project,
                               lessons=self.lessons_for(project),
                               steps=self.db.plan_steps(project) if project else [],
                               plan=self.plan_text(project))

    #: Long enough to hold a real plan, short enough that carrying it into every
    #: request in the project stays affordable.
    MAX_PLAN = 6000
    PLAN_FILE = "PLAN.md"

    def plan_path(self, project: str | None) -> str | None:
        """Where a project's plan lives: a file inside it, not a card on screen.

        A card is agreed once and then gone. A file survives the turn, can be
        corrected by hand, shows up in the workspace, and is already covered by
        undo and history — which is the whole reason to put it there.
        """
        return f"{project}/{self.PLAN_FILE}" if project else None

    def plan_text(self, project: str | None) -> str:
        path = self.plan_path(project)
        if not path:
            return ""
        try:
            target = self.sandbox.path(path)
        except Exception:
            return ""
        if not target.is_file():
            return ""
        try:
            return target.read_text(encoding="utf-8", errors="replace")[:self.MAX_PLAN].strip()
        except OSError:
            return ""


    #: Enough for a handful of real rules; past this the prompt is being used as
    #: a filing cabinet and something else has gone wrong.
    MAX_LESSONS = 8

    def lessons_for(self, project: str | None) -> list[str]:
        """Corrections Mat has given about how work is done in this project.

        These outlived the project-role feature they were once carried beside:
        a lesson is a correction Mat actually gave, which is worth repeating,
        rather than a stance invented for a folder.
        """
        if not project:
            return []
        lessons = []
        for item in self.memory.data.get("profile_memories", []):
            if str(item.get("category")) != "lesson":
                continue
            if str(item.get("project") or "").strip() != project:
                continue
            value = str(item.get("value", "")).strip()
            if value and value not in lessons:
                lessons.append(value)
        return lessons[-self.MAX_LESSONS:]


    #: Enough of the plan's opening to say what the project is for; not so much
    #: that the role becomes a second copy of the plan, which is carried anyway.
    ROLE_PLAN_CHARS = 240


    def _plan_subject(self, plan: str) -> str:
        """The part of a plan that says what the project is for.

        A plan opens with Aura's own scaffolding — "Started by Aura from what the
        first change actually did. It is a draft — correct it freely…" — which is
        true, and says nothing about the project. Taking the first 240 characters
        spent almost all of them on that preamble. The `What was asked` section is
        the substance, so that is what is read.
        """
        text = str(plan or "")
        if not text.strip():
            return ""
        lowered = text.casefold()
        marker = lowered.find("## what was asked")
        if marker >= 0:
            body = text[marker:].split("\n", 1)[-1]
            # Up to the next heading: the following sections are what Aura did,
            # not what the project is.
            body = body.split("\n#", 1)[0]
        else:
            # No section to find, so the first line that is not a heading.
            body = "\n".join(line for line in text.splitlines()
                             if line.strip() and not line.lstrip().startswith("#"))
        return " ".join(body.split())[:self.ROLE_PLAN_CHARS]

    #: How far back to look for what working on a project has meant in practice.
    ROLE_TASK_HISTORY = 40

    def tools_used_in(self, project: str) -> list[str]:
        """Which tools have actually been used on this project, most used first.

        From the task journal rather than from intent: what the work *was*, not
        what anybody said it would be.
        """
        counted: dict[str, int] = {}
        for task in self.tasks.recent(self.ROLE_TASK_HISTORY):
            request = str(task.get("request") or "")
            if project.casefold() not in request.casefold():
                continue
            for name in task.get("tools", []) or []:
                if name:
                    counted[str(name)] = counted.get(str(name), 0) + 1
        ranked = sorted(counted, key=lambda name: -counted[name])
        return ranked[:6]

    def handle(self, message: str, approve: Callable[[list[str]], bool] | None = None,
               state: Callable[[str], None] | None = None,
               token: Callable[[str], None] | None = None) -> str:
        set_state = state or (lambda _: None)
        self.cancel_event.clear()
        #: What this turn was asked for, in the user's own words. Read by the
        #: plan file, which is the user's record of why a project looks the way
        #: it does — a paraphrase there would be worse than nothing.
        self.current_request = message
        task_id = self.tasks.start(message, session_id=self.session_id)
        self.current_task_id = task_id
        self.sandbox.active_task_id = task_id
        self._remember("user", message)
        # Before learning, not after: the project decides how a fact learned in
        # this message is filed, and the first message of a conversation is
        # exactly the one that names the project.
        self.project_for(message)
        self.last_recalled = []
        self.fetched_sources = []
        self.searches_this_turn = {}
        self.last_learned = (
            self.memory.learn_from_message(message, project=self.current_project)
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
        except TurnExpired:
            # Not a failure and not a cancellation: the work simply ran out of time,
            # and what it managed is worth more than an apology.
            status = "expired"
            # The real turn, not a blank one. The clock can run out anywhere —
            # including between a tool being requested and it running — and a
            # report that says "nothing happened" when three tools already
            # succeeded is exactly the dishonesty the gates exist to prevent.
            response = self._format_out_of_time(
                getattr(self, "_turn_state", None) or TurnState(),
                float(self.config.data.get("turn_budget_seconds", 0) or 0))
            self.log.record("request", "expired", task_id=task_id)
            set_state("idle")
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
        if not requires_mutation or approve is None:
            return ""
        project = self.current_project
        # Two ways in. A build that names several files has always stopped here.
        # A build inside a project that has no plan yet now does too, because
        # the request most worth agreeing first — "look at this and improve it"
        # — names no files at all, so the old condition never saw it.
        naming_files = len(expected_paths) >= 2
        opening_a_project = bool(project) and not self.plan_text(project)
        if not naming_files and not opening_a_project:
            return ""
        state("thinking")
        asked = list(self.provider.start_messages(message, self._context(message)))
        if naming_files:
            instruction = (
                "Do not create anything yet. List only the files you would create for this "
                "request, one per line, as `path - one short line on what it holds`. Use "
                "exactly these paths: " + json.dumps(expected_paths) +
                ". No preamble, no code, no explanation after the list.")
        else:
            # No filenames were named, so asking for a file list would invite
            # invention. Steps are what there is to agree here.
            instruction = (
                "Do not create or change anything yet. Look first if you have not already, "
                "then list the steps you would take, one per line, as "
                "`- step - how it can be checked`. Base every line on what the files you "
                "read actually show; write no step you cannot check. At most eight lines, "
                "no preamble and nothing after the list.")
        # The language rule lives in `start_messages`, several messages earlier,
        # and this instruction is English and comes last — so the last thing
        # read before writing said "English" without meaning to. Both models
        # drafted the plan in Finnish, which is the drift this rule exists to
        # stop, so it has to be repeated here rather than merely stated once.
        rule = self.provider.LANGUAGE_RULE.get(language.detect(message))
        if rule:
            instruction += " " + rule
        asked.append({"role": "system", "content": instruction})
        try:
            drafted = self.provider.complete(asked, [])
        except Exception as exc:
            # A failed plan must not cost the user their request — but it must
            # not vanish either. This is a whole model call, and it used to fail
            # leaving no trace anywhere that it had been attempted.
            self.log.record("plan_draft", "error", error=str(exc))
            return ""
        lines = [line.strip(" -*\t") for line in str(drafted.content or "").splitlines()]
        if naming_files:
            listed = [line for line in lines
                      if line and any(path.split("/")[-1] in line for path in expected_paths)]
            if not listed:
                listed = [f"{path} - part of the requested build" for path in expected_paths]
        else:
            listed = [line for line in lines if line][:8]
            if not listed:
                # Nothing usable came back. Better to get on with the work than
                # to show the user an empty card and ask them to approve it.
                return ""
        plan = "\n".join(f"- {line}" for line in listed[:20])
        self.log.record("file_plan", "ok", files=len(listed))
        # Which of the two lists this is. The browser headed both "the files she
        # would create", so a list of steps was shown as filenames about to be
        # written — a card that describes the wrong action is worse than no card.
        kind = "FILES" if naming_files else "STEPS"
        if not approve(["PLAN", plan, kind]):
            self.log.record("file_plan", "declined", files=len(listed))
            return self.PLAN_DECLINED
        # Keep what was agreed. A plan shown once and discarded leaves the next
        # turn with nothing to work from and the user with nothing to correct,
        # which is the whole difference between a card and a file.
        if project and not self.plan_text(project):
            try:
                self.sandbox.write_file(self.plan_path(project),
                                        self._agreed_plan_file(plan))
                self.log.record("plan_written", "ok", path=self.plan_path(project),
                                agreed=True)
            except Exception as exc:
                # Still worth following — but a plan that was agreed and never
                # filed is exactly why "continue building it" finds nothing to
                # continue from, so the failure is recorded rather than shrugged.
                self.log.record("plan_written", "error",
                                path=self.plan_path(project), error=str(exc))
        return plan

    def _agreed_plan_file(self, plan: str) -> str:
        """The approved plan, written the way the user will meet it again."""
        return "\n".join([
            f"# {self.current_project}",
            "",
            "Agreed with you before the work started. Correct it freely — Aura reads",
            "this file back before the next piece of work and follows it rather than",
            "her own memory.",
            "",
            "## What was asked",
            "",
            str(self.current_request or "").strip() or "_Not recorded._",
            "",
            "## The plan",
            "",
            plan,
            "",
            "## Still open",
            "",
            "- _Add what should happen next._",
            "",
        ])

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
        """The one place a turn is allowed to stop, for either of the two reasons.

        Called on every streamed token, so putting the clock here is what makes the
        budget a real deadline rather than a check between rounds — a single long
        generation used to sail straight past it.
        """
        if self.cancel_event.is_set():
            raise TaskCancelled()
        deadline = getattr(self, "_turn_deadline", None)
        if deadline is not None and time.monotonic() > deadline:
            raise TurnExpired()

    def _prepare_turn(self, message: str,
                      approve: Callable[[list[str]], bool] | None,
                      state: Callable[[str], None]) -> tuple | None:
        """Decide what this turn is, before a single word is sent.

        Which tools are offered, which files the request named, whether a plan
        is already agreed, and what would count as the work actually being done.
        None of it depends on the loop, and the loop needs only what is returned.

        Returns None when Mat declined the file plan — the only way preparing a
        turn can end it.
        """
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
            # The one way preparation ends the turn: Mat looked at the file list and
            # said no. Signalled rather than returned, because the caller owns what
            # the user is told.
            return None
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
        # Every keyword test below reads this rather than the raw request:
        # they decide what Aura promised to produce and whether the turn counts
        # as finished, and in Estonian they were all silently answering "no".
        routed_words = language.with_english_hints(routing_request.casefold())
        if any(word in routed_words for word in ("build", "project", "app", "website")):
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
        validation_asked = "validate" in routed_words
        build_words = any(word in routed_words for word in
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
            routed_words,
        ))
        # The router offers tools for almost any wording, so "tools were offered"
        # is far too weak a reason to insist one must have run. Only demand action
        # when the request actually asked for work — otherwise an ordinary
        # question like "how does my project look?" spends the whole retry budget
        # proving something the user never asked about.
        asks_for_work = requires_mutation or any(
            verb in routed_words for verb in
            ("list", "read", "show", "find", "search", "open", "inspect", "check",
             "validate", "compare", "look at", "screenshot", "capture", "undo",
             "run ", "test"))
        # Caught in live use: "ja mitu rida on seal esimeses failis?" names a
        # file, asks a question, and contains no verb from the list above — so
        # nothing insisted on a tool, and Aura answered "137 rida" about a file
        # of 46 without opening it. A question about something in the workspace
        # expects a look, whether or not it happens to use a reading verb.
        asks_for_work = asks_for_work or (
            "?" in routing_request and self._question_needs_looking(routing_request))
        action_expected = (bool(selected_tools) and asks_for_work
                           and not (auto_learning_only or memory_read_question))
        # Recorded now, before any tool runs: afterwards a file that was edited and
        # one that was invented look identical on disk.
        missing_at_start = {path for path in expected_paths
                            if path and not self._file_exists(path)}
        state_of_turn = TurnState(
            missing_at_start=missing_at_start,
            edit_request=self._is_edit_request(routing_request),
            expected_paths=list(expected_paths), expected_base=expected_base,
            requires_mutation=requires_mutation, action_expected=action_expected,
            validation_asked=validation_asked, build_words=build_words,
            selected_tools=list(selected_tools),
            sampling_kind=sampling.kind_for_tools(
                item["function"]["name"] for item in selected_tools),
            # One budget for every gate. Four independent counters once allowed
            # up to nine extra rounds, each re-answering from scratch.
            retries_left=self.MAX_COMPLETION_RETRIES,
        )
        round_limit = self.ROUND_LIMITS.get(reasoning_depth, self.ROUND_LIMITS["balanced"])
        return messages, selected_tools, state_of_turn, round_limit


    def _finish_turn(self, turn: TurnState, response) -> str:
        """Compose what Mat reads, once the gates agree the turn may end.

        Three things can still change the answer here: the model went quiet after
        doing real work, it handed back a tool call written as text, or the
        evidence footer needs assembling from what was actually verified.
        """
        if turn.empty_response and turn.successful_tools:
            # The work happened; only the closing sentence did not. Raising
            # here threw away the truth — a live run removed a broken link
            # and then reported "I couldn't complete that safely", which is
            # the opposite of what occurred. Aura writes the report herself
            # from what she actually did.
            response.content = self._format_silent_completion(turn)
            turn.record_unconfirmed(
                "the model stopped responding before summarising, so this "
                "description was assembled from the recorded actions")
            turn.empty_response = False
        elif turn.empty_response:
            raise RuntimeError(self._empty_response_reason(turn))

        # Last line of defence. The gate asks the model to try again, but if it
        # spends its retry and still hands back markup, that markup must not be
        # what Mat reads — it is not an answer, and one of these named a
        # destructive tool nobody had asked for.
        if self._is_tool_markup(response.content):
            turn.emitted_tool_markup = True
            done = self._format_silent_completion(turn)
            response.content = f"{self.TOOL_MARKUP_REPLY}\n\n{done}"
            turn.record_unconfirmed(
                "the model wrote a tool call out as text rather than running it; "
                "nothing was executed from it")

        missing = set(turn.missing_artifacts)
        present = [path for path in turn.expected_paths if path not in missing]
        return self._format_completion_evidence(
            response.content, turn.validation_scope,
            turn.validation_evidence,
            sorted(turn.verified_final_paths), present,
            turn.unconfirmed, list(self.fetched_sources),
            sorted(turn.measured_paths - turn.verified_final_paths),
            # Named to be changed, and not there to change.
            sorted(path for path in turn.missing_at_start
                   if turn.edit_request and self._file_exists(path)),
        )

    def _tool_conversation(self, message: str, approve: Callable[[list[str]], bool] | None,
                           state: Callable[[str], None], token: Callable[[str], None] | None = None) -> str:
        prepared = self._prepare_turn(message, approve, state)
        if prepared is None:
            return ("I stopped before creating anything. Tell me what the file list "
                    "should be instead and I'll follow that.")
        messages, selected_tools, state_of_turn, round_limit = prepared
        heat = sampling.for_turn(
            (item["function"]["name"] for item in selected_tools), self.config.data)
        #: Read by `_gate_empty_response`, which can only say "of the 6,144 it
        #: was given" if it knows what this turn was given.
        self._turn_heat = heat
        def emit(piece: str) -> None:
            self._check_cancelled()
            if token:
                token(piece)

        def watch(count: int) -> None:
            """The reasoning heartbeat, with the clock read on the way past."""
            self._check_cancelled()
            if self.on_thinking:
                self.on_thinking(count)
        without_tools = False
        # A round limit bounds the wrong thing. What Mat spends is time, and at 22
        # tokens a second 48 rounds is not a limit at all — the only real ceiling
        # was the HTTP timeout, which arrives as a failure instead of an answer.
        budget = float(self.config.data.get("turn_budget_seconds", 0) or 0)
        deadline = (time.monotonic() + budget) if budget > 0 else None
        #: Read by `_check_cancelled` on every streamed token, which is what makes
        #: this a deadline and not a between-rounds suggestion.
        self._turn_deadline = deadline
        #: Reachable from `handle`, so a turn that expires deep inside a round can
        #: still be reported from what really ran.
        self._turn_state = state_of_turn
        for round_index in range(round_limit):
            self._check_cancelled()
            if deadline is not None and round_index and time.monotonic() > deadline:
                return self._out_of_time(state_of_turn, budget, round_index)
            if round_index:
                # Any second or later round replaces whatever was streamed
                # before it. Clearing here — rather than at each individual
                # retry — means no future retry path can reintroduce the
                # duplicated-answer bug by forgetting to signal.
                state("retry")
            # A conversation that ends with Aura's own reply is not a question, and
            # asking it anyway is a guaranteed silence: the model answers with a
            # single stop token, correctly, and the log records "empty response".
            # Found by replaying a captured payload — removing exactly that last
            # assistant message turned the silence into a working tool call.
            self._ensure_something_to_answer(messages)
            self._fit_to_context(messages, [] if without_tools else selected_tools, heat)
            try:
                response = self.provider.complete(
                    messages, [] if without_tools else selected_tools,
                    # Sampled for the job. A retry drops the tools but not the
                    # kind of turn it is, so this reads the original selection.
                    temperature=heat.temperature, max_tokens=heat.max_tokens,
                    top_p=heat.top_p, top_k=heat.top_k,
                    on_token=emit if token else None,
                    # Wired through `watch` rather than straight to the heartbeat:
                    # a model emitting only private thinking produces no content
                    # tokens, so without this the clock is never read at all during
                    # the exact turns that run long.
                    on_reasoning=watch)
            except TurnExpired:
                return self._out_of_time(state_of_turn, budget, round_index)
            # Every reply carries the true cost of what was just sent, so the
            # rate is measured against the loaded model rather than assumed.
            self.budget.observe(
                context_budget.request_characters(
                    messages, [] if without_tools else selected_tools),
                getattr(response, "prompt_tokens", 0))
            self.budget.observe_answer(getattr(response, "completion_tokens", 0))
            # One round only: the next gate decides again from what it sees.
            without_tools = False
            assistant: dict = {"role": "assistant", "content": response.content or None}
            if response.tool_calls:
                assistant["tool_calls"] = [{
                    "id": call.id, "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                } for call in response.tool_calls]
            self._carry_reasoning(messages, assistant, response)
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
            # Judged fresh each round. A note describes the reply in front of the
            # gate, and the reply changes every round — so accumulating them meant
            # a doubt raised in round one survived into the final answer after
            # round two had resolved it. Aura would tell Mat a file "was never
            # opened" about a file she had since read, in the one section whose
            # entire purpose is to be true.
            state_of_turn.unconfirmed.clear()
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
                # Make room before adding to the ask. Retrying a failed turn with
                # a bigger prompt than the one that just failed is how a quiet
                # model is kept quiet.
                trimmed = self._compact_for_retry(messages)
                if trimmed:
                    self.log.record("retry_compacted", "ok", characters=trimmed)
                without_tools = retry.drop_tools
                # A user turn, not a system one. `merge_system_messages` hoists
                # every system message to the front of the payload, so a retry
                # appended as `system` left the conversation still ending on Aura's
                # own reply — which is the guaranteed silence this whole hunt was
                # about. Measured on the captured payload: as sent, one token back;
                # the same instruction as a user turn, 354 tokens of answer.
                messages.append({"role": "user", "content": retry.instruction})
                continue
            return self._finish_turn(state_of_turn, response)
        raise RuntimeError("the model exceeded the tool-operation limit; ask it to continue in a new message")

    # ------------------------------------------------------------------ turn

    def _within_reading_budget(self, payload: str, turn: TurnState, name: str) -> str:
        """Shorten a tool result once the turn has read its fill.

        Every tool already caps itself — `read_many_files` refuses past 250,000
        characters. Nothing capped their *sum*, and because each round re-sends the
        whole conversation, a single generous read is paid for again on every round
        that follows it. The budget is spent across the turn, so early tools get
        their full result and only the ones past the line are trimmed.
        """
        budget = int(self.config.data.get("turn_tool_characters", 0) or 0)
        turn.tool_characters += len(payload)
        if budget <= 0 or turn.tool_characters <= budget:
            return payload
        # Whatever is left of the budget, never less than enough to be useful.
        room = max(600, budget - (turn.tool_characters - len(payload)))
        if len(payload) <= room:
            return payload
        self.log.record("reading_budget_reached", "ok", tool=name,
                        spent=turn.tool_characters, budget=budget, kept=room)
        return payload[:room] + (
            f"\n…[this result was shortened: the turn has already read "
            f"{turn.tool_characters:,} characters of tool output. Ask for a "
            f"specific file or range if you need more.]")

    def _run_one_tool(self, call: ToolCall, approve: Callable[[list[str]], bool] | None,
                      messages: list[dict], turn: TurnState) -> None:
        """Execute one tool call and record what it proves about the turn."""
        result = self._execute_tool(call, approve)
        if not result.get("ok"):
            # Said in the tool result rather than the system prompt, because the
            # model reads this one immediately and in context. A failure used to
            # be logged and forgotten, so the same call could be made five times
            # and read five identical errors with nothing pointing that out.
            advice = tool_loops.note_for(call.name, call.arguments,
                                         turn.tool_failures, turn.repeated_calls)
            if advice:
                result["what_to_do"] = advice
                self.log.record("tool_loop", "warn", tool=call.name,
                                failures=turn.tool_failures.get(call.name, 0))
        # An attached image cannot travel inside a tool result, which is plain
        # text. Lift it out and send it as a real multimodal turn.
        attachment = result.pop("content", None) if call.name == "look_at_image" else None
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": self._within_reading_budget(
                             json.dumps(result, ensure_ascii=False), turn, call.name)})
        if attachment:
            messages.append({"role": "user", "content": [
                {"type": "text",
                 "text": f"Here is the image {result.get('path')} you asked to look at."},
                {"type": "image_url", "image_url": {"url": attachment}},
            ]})
        if not result.get("ok"):
            return
        turn.successful_tools += 1
        turn.tools_run.append(call.name)
        if call.name == "run_command" and result.get("approved") and not result.get("timed_out"):
            # A refused command still produces a successful tool result that
            # describes the refusal, so the tool's name is not evidence that
            # anything executed. This is.
            turn.commands_executed += 1
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
                    # Measuring a file still proves a write landed — it just does
                    # not prove anything about what the file says.
                    if call.name in self.CONTENT_TOOLS:
                        turn.verified_final_paths.add(normalized)
                    else:
                        turn.measured_paths.add(normalized)
                turn.pending_verifications.pop(normalized, None)
        if call.name == "validate_project" and result.get("valid"):
            requested_path = self._normalize_path(str(call.arguments.get("path", ".")))
            self._clear_verified_scope(turn.pending_verifications, requested_path)
            if self._validation_satisfies(requested_path, turn.expected_base, turn.expected_paths):
                turn.validation_succeeded = True
                turn.validation_evidence = dict(result)
                turn.validation_scope = requested_path
        turn.verification_needed = bool(turn.pending_verifications)

    def _out_of_time(self, turn: TurnState, seconds: float, rounds: int) -> str:
        turn.ran_out_of_time = True
        self.log.record("turn_budget_reached", "ok", seconds=seconds, rounds=rounds,
                        tools_run=turn.successful_tools)
        return self._format_out_of_time(turn, seconds)

    def _format_out_of_time(self, turn: TurnState, seconds: float) -> str:
        """Stop honestly, rather than leaving Mat watching a spinner.

        Everything here is a fact Aura recorded. The turn is not claimed to be
        finished, because it is not — but what actually ran is worth more than the
        timeout that used to arrive in its place.
        """
        # Rounding 90 seconds up to "2 minutes" overstates the wait Mat actually
        # had, which is a small lie in a message whose whole job is honesty.
        shape = (f"{seconds / 60:.0f} minutes" if seconds >= 120
                 else f"{seconds:.0f} seconds")
        lines = [f"I stopped after {shape} — this turn was taking too long, "
                 f"so here is where it got to rather than nothing at all."]
        done = list(dict.fromkeys(turn.tools_run))
        if done:
            lines.append("What actually ran: "
                         + ", ".join(f"`{name}`" for name in done[:8]) + ".")
        read = sorted(turn.verified_final_paths)
        if read:
            lines.append("Files read: " + ", ".join(f"`{p}`" for p in read[:8]) + ".")
        if turn.pending_verifications:
            lines.append("Not verified: "
                         + ", ".join(f"`{p}`" for p in sorted(turn.pending_verifications)[:8]) + ".")
        lines.append("Ask me to continue and I will pick it up from here. "
                     "You can change the limit in Settings.")
        return "\n\n".join(lines)

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

    #: Tool results this many rounds back are history the model has already acted
    #: on. The most recent ones are what it is answering from, so they are left
    #: whole.
    KEEP_WHOLE_RESULTS = 2
    #: Enough to show what a result was, not enough to carry its bulk.
    TRIMMED_RESULT = 240

    #: How much of the thinking to hand back. The tail, not the head: a chain of
    #: thought ends with the decision it reached, which is the part the next
    #: round needs.
    REASONING_CARRIED = 2400

    def _carry_reasoning(self, messages: list[dict], assistant: dict, response) -> None:
        """Give the model back the thinking behind its own last turn.

        Roughly three quarters of what this model produces is `reasoning_content`,
        and all of it used to be discarded — so after calling a tool it saw its
        own previous turn as a bare function call and had to work out again why it
        had made it. Only the newest turn keeps its reasoning; carrying every
        round's would grow the prompt by three quarters each time, which is the
        problem `_compact_for_retry` exists to fight.
        """
        if not self.config.data.get("send_reasoning_back", True):
            return
        for earlier in messages:
            if earlier.get("role") == "assistant":
                earlier.pop("reasoning_content", None)
        thinking = str(getattr(response, "reasoning", "") or "").strip()
        if not thinking:
            return
        if len(thinking) > self.REASONING_CARRIED:
            thinking = "…" + thinking[-self.REASONING_CARRIED:]
        assistant["reasoning_content"] = thinking

    #: Said when the conversation has nothing outstanding in it. Short on purpose:
    #: it exists to give the model a turn to take, not to steer the answer.
    NOTHING_TO_ANSWER = ("Continue from where you left off and finish your reply to "
                         "the user's last question. Do not repeat what you already said.")

    def _ensure_something_to_answer(self, messages: list[dict]) -> bool:
        """Never ask the model to speak after Aura's own reply.

        The last cause of the silences, and the only one that was never the model's
        fault. A payload ending in an assistant turn — no tool calls, nothing
        outstanding — asks the model to respond to itself, and one stop token is the
        right answer to that. Replaying the captured payload confirmed it: drop that
        final assistant message and the same request produced a tool call.
        """
        if not messages:
            return False
        last = messages[-1]
        if last.get("role") != "assistant" or last.get("tool_calls"):
            return False
        self.log.record("nothing_to_answer", "ok",
                        trailing=len(str(last.get("content") or "")))
        # A user turn, not a system one, for two reasons that both bite: the chat
        # template refuses a system message anywhere but the front, and
        # `merge_system_messages` would hoist it there anyway — leaving the
        # conversation still ending in Aura's own reply, which is the whole problem.
        messages.append({"role": "user", "content": self.NOTHING_TO_ANSWER})
        return True

    def _fit_to_context(self, messages: list[dict], tools: list[dict], heat) -> None:
        """Shorten the conversation *before* sending, if it will not fit.

        Aura used to send and hope. When the payload was too large LM Studio
        trimmed it until the chat template had no user turn left and raised,
        which arrived looking like a model that had chosen to say nothing.
        Compaction existed but only ever ran after a turn had already failed.

        Never raises and never blocks the send: if the estimate is wrong, the
        turn should still be attempted. The worst this can do is shorten some
        old tool results that were going to be truncated by the server anyway.
        """
        try:
            window = self.provider.loaded_context()
        except Exception:
            return              # a provider that cannot say has nothing to check
        if not window:
            return
        answer = getattr(heat, "max_tokens", 0) or 0
        previous = None
        for _ in range(3):      # each pass shortens the next tier of results
            call = context_budget.verdict(self.budget, messages, tools, window, answer)
            if call["fits"]:
                return
            # A pass that barely moves the number is a pass that has run out of
            # material. Measured: the first cut saved 35,040 characters and the
            # next two saved 384 and 376 — two rounds of work for nothing.
            if previous is not None and previous - call["over_by"] < 200:
                self.log.record(
                    "context_preflight", "warn", needed=call["guarded"],
                    room=call["room"], over_by=call["over_by"],
                    note="nothing left worth shortening")
                return
            previous = call["over_by"]
            saved = self._compact_for_retry(messages)
            self.log.record(
                "context_preflight", "ok" if saved else "warn",
                needed=call["guarded"], room=call["room"],
                over_by=call["over_by"], characters_saved=saved,
                chars_per_token=call["chars_per_token"],
                calibrated=call["calibrated"])
            if not saved:
                # Nothing left to shorten. Said plainly rather than silently:
                # the turn may still work, and if it does not, this line is
                # the reason, sitting in the log before the failure.
                return

    def _note_bookkeeping_failure(self, what: str, project: str, exc: Exception) -> None:
        """Record a failure in the record-keeping itself.

        Guarded twice over: these failures are usually the database, and the
        log lives in the same database, so an unguarded write here would turn a
        lost note into a lost turn.
        """
        try:
            self.log.record(what, "error", project=project, error=str(exc))
        except Exception:
            pass

    def _compact_for_retry(self, messages: list[dict]) -> int:
        """Shorten older tool results in place; return the characters saved.

        Shortened, never removed: each `tool` message answers a `tool_call` by
        id, and dropping one leaves the conversation malformed in a way the
        server rejects. Nothing else is touched — the system prompt, the user's
        words and the assistant's own turns all stay exactly as they were.
        """
        places = [i for i, message in enumerate(messages)
                  if message.get("role") == "tool"]
        saved = 0
        for index in places[:-self.KEEP_WHOLE_RESULTS] if len(places) > self.KEEP_WHOLE_RESULTS else []:
            content = str(messages[index].get("content") or "")
            if len(content) <= self.TRIMMED_RESULT:
                continue
            dropped = len(content) - self.TRIMMED_RESULT
            messages[index] = dict(messages[index], content=(
                content[:self.TRIMMED_RESULT]
                + f"\n…[{dropped} characters of this earlier result were trimmed to "
                  f"make room; ask again if you need them]"))
            saved += dropped
        return saved

    #: Enough to find a pattern across a few days, few enough to stay small.
    SILENCES_KEPT = 10

    def _capture_silence(self) -> str:
        """Write out the prompt that produced a silence, and return its filename.

        The model is deterministic — the same payload gives the same answer every
        time — so a silence is not bad luck, it is a specific prompt that reliably
        produces one token. That makes it reproducible in principle, and it has been
        irreproducible in practice only because nothing kept the prompt.

        Never allowed to break a turn: a diagnostic that can fail the thing it is
        diagnosing is worse than no diagnostic.
        """
        payload = getattr(self.provider, "last_payload", None)
        if not payload:
            return ""
        try:
            folder = self.sandbox.meta / "silences"
            folder.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            target = folder / f"{stamp}.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            for old in sorted(folder.glob("*.json"))[:-self.SILENCES_KEPT]:
                old.unlink(missing_ok=True)
            return target.name
        except OSError:
            return ""

    def _empty_response_reason(self, turn: TurnState) -> str:
        """Explain the silence with what the server actually reported.

        Measured on two real occurrences: `finish_reason` was `stop` with a
        completion of one token, against a 32k budget and a 3.7k prompt. The
        model was loaded, fast, and answering — it simply chose to say nothing.
        The old message sent the user to check LM Studio, which is the one place
        the evidence had already cleared.

        A classification set by the gate wins, when it made one: it saw the
        reply rather than only the two fields kept from it, and it is the same
        judgement that chose the remedy — so the sentence Mat reads and the
        decision Aura took cannot disagree.
        """
        if getattr(turn, "empty_explanation", ""):
            return turn.empty_explanation
        if turn.finish_reason == "length":
            return ("the model ran out of room mid-answer. Raise the maximum "
                    "response length in Settings, or ask for less at once")
        if turn.finish_reason == "stop" and turn.completion_tokens <= 2:
            return ("the model chose not to answer that — it stopped after a "
                    "single token rather than failing. Rephrasing usually helps; "
                    "asking about something it was just told tends to produce this")
        if turn.finish_reason:
            return ("the model returned nothing usable and stopped with "
                    f"{turn.finish_reason!r}")
        service = getattr(self.provider, "SERVICE", "the model server")
        return ("the model kept returning an empty response. Check that a model "
                f"is loaded in {service}, or try a shorter request")

    #: The shapes a text-format tool call arrives in. Deliberately narrow: this
    #: must catch a reply that *is* a tool call, never a reply that mentions one.
    TOOL_MARKUP = re.compile(
        r"<\s*tool_call\s*>|<\s*function\s*=|<\|tool\u2581call\|>|\[TOOL_CALL\]",
        re.IGNORECASE)

    @classmethod
    def _is_tool_markup(cls, content: str) -> bool:
        """Is this reply a tool call the server failed to parse?

        Measured against the real case: the whole reply was four lines of
        `<tool_call><function=undo_last_change>`. A reply that merely *discusses*
        tool calls — which happens whenever Mat and Aura talk about her own code —
        has prose around it, so the markup has to dominate before this fires.
        """
        text = str(content or "").strip()
        if not text or not cls.TOOL_MARKUP.search(text):
            return False
        without = cls.TOOL_MARKUP.sub("", text)
        without = re.sub(r"[<>/=\s\w.\u2581|\[\]-]{0,80}$", "", without, count=1)
        # What is left once the markup is removed: if almost nothing, the reply was
        # the call itself rather than a message that happened to contain one.
        return len(without.strip()) < max(40, len(text) * 0.4)

    def _gate_tool_markup(self, turn: TurnState, response) -> GateResult:
        """Refuse a tool call that arrived as prose.

        Not parsed and not executed — a call that comes through as content has
        bypassed the structured path, and the one that prompted this gate was
        `undo_last_change`, which nobody asked for. Treated as no answer at all.
        """
        content = str(getattr(response, "content", "") or "")
        if not self._is_tool_markup(content):
            return PASS
        self.log.record("tool_markup", "error", emitted=content[:200],
                        captured=self._capture_silence())
        turn.emitted_tool_markup = True
        if turn.empty_retries_used >= self.MAX_EMPTY_RETRIES:
            return PASS
        turn.empty_retries_used += 1
        return GateResult(drop_tools=True, instruction=(
            "Your last reply was a tool call written as text, which does nothing. "
            "The tools have been taken away for this turn. Answer the user directly "
            "in plain words, using what you already found."))

    def _gate_empty_response(self, turn: TurnState, response) -> GateResult:
        """An empty completion is usually a stumble, not a verdict.

        It was the single most frequent failure in real use, and it used to end
        the turn outright while the shared budget sat unused beside it.
        """
        turn.empty_response = not response.content
        turn.finish_reason = getattr(response, "finish_reason", "") or ""
        turn.completion_tokens = int(getattr(response, "completion_tokens", 0) or 0)
        if response.content:
            return PASS
        # Which silence this is decides the remedy. Telling a model its reply was
        # empty is a sentence about an event it took no part in when the request
        # never reached it, and it is simply false when the model generated
        # plenty and spent it all on thinking.
        limit = getattr(getattr(self, "_turn_heat", None), "max_tokens", 0) or 0
        kind = empty_reply.classify(response, limit)
        turn.empty_kind = kind.name
        turn.empty_explanation = kind.explanation
        # Measured rather than merely reported. "It kept returning an empty
        # response" was true and told nobody anything; finish_reason separates a
        # model that ran out of budget mid-answer from one that chose silence.
        self.log.record(
            "empty_response", "error",
            kind=kind.name,
            finish_reason=getattr(response, "finish_reason", "") or "(not given)",
            prompt_tokens=getattr(response, "prompt_tokens", 0),
            completion_tokens=getattr(response, "completion_tokens", 0),
            max_tokens=getattr(self.provider, "max_tokens", 0),
            tools_run=turn.successful_tools,
            retries_left=turn.retries_left,
            # The token itself, escaped. A stop marker, a bare newline and a
            # real word are three different faults and every previous record
            # of this failure threw away the one field that separates them.
            emitted=repr(str(getattr(response, "content", "") or ""))[:120],
            empty_retries_used=turn.empty_retries_used,
            captured=self._capture_silence(),
            tools_named=list(turn.tools_run)[-6:])
        # Every episode in the log spent the full budget on the same question and
        # got the same silence back. One retry, and it asks differently.
        if turn.empty_retries_used >= self.MAX_EMPTY_RETRIES:
            return PASS
        if not kind.worth_retrying:
            # Asking again would send the identical request, and it was not the
            # model that refused it. Ending here saves a round and lets the turn
            # report the real fault instead of a second silence.
            return PASS
        turn.empty_retries_used += 1
        # Tools stay when the model was mid-work and merely ran out of room to
        # speak; taking them away there would throw the turn's progress out.
        return GateResult(drop_tools=(kind.name != "thought_it_away"),
                          instruction=kind.instruction)

    def _gate_unread_files(self, turn: TurnState, response) -> GateResult:
        """Note any file the answer discusses but never opened.

        Deliberately not a language test. Aura already knows which files she read
        and which she only measured, so the honest fact is available without
        guessing at intent from words — and without the false positives that
        hunting for "probably" in Estonian and English would bring.

        A note, never a retry: mentioning a file one has not read can be perfectly
        reasonable, and the point is that Mat can tell the difference.
        """
        content = str(getattr(response, "content", "") or "")
        if not content:
            return PASS
        unopened = sorted(turn.measured_paths - turn.verified_final_paths)
        discussed = [path for path in unopened
                     if path and PurePosixPath(path).name.lower() in content.lower()]
        if not discussed:
            return PASS
        listed = ", ".join(f"`{path}`" for path in discussed[:6])
        more = f" and {len(discussed) - 6} more" if len(discussed) > 6 else ""
        return GateResult(note=(
            f"anything said about the contents of {listed}{more}: the size and line "
            f"count were checked, but the {'files were' if len(discussed) > 1 else 'file was'} "
            f"never opened"))

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

    #: Words that assert a command actually produced something. Deliberately
    #: past-tense and result-shaped: "I could run X" and "run X to see" are
    #: proposals, and nagging about those would make the gate a nuisance rather
    #: than a guard. Both languages, because both are spoken here.
    COMMAND_CLAIMS = (
        "käivitasin", "jooksutasin", "väljastas", "väljund oli", "tagastas",
        "käsu tulemus", "käsk andis", "käsk tagastas", "output was", "it printed",
        "the command returned", "the command output", "ran the command",
        "i ran ", "command succeeded", "successfully ran",
        # Added from a reply that actually appeared rather than from
        # imagination: "The command has been executed and is awaiting user
        # approval before completion" — self-contradictory, and the first half
        # is the false part. The earlier list was written by guessing at
        # phrasings and missed this one entirely.
        "has been executed", "was executed", "command has been run",
        "on käivitatud", "sai käivitatud", "käsk täideti",
    )

    def _claims_a_command_ran(self, text: str) -> bool:
        lowered = str(text or "").casefold()
        return any(phrase in lowered for phrase in self.COMMAND_CLAIMS)

    def _gate_command_claim(self, turn: TurnState, response) -> GateResult:
        """Describing a command's output requires a command to have run.

        Only bites in the one unambiguous case: the reply asserts a result and
        nothing executed at all this turn. When a command did run, no attempt is
        made to match claims to commands — a gate that guesses would produce
        exactly the false accusations it exists to prevent.
        """
        if turn.empty_response or turn.commands_executed:
            return PASS
        if not self._claims_a_command_ran(response.content):
            return PASS
        if turn.retries_left > 0:
            return GateResult(
                notice="Aura is checking what actually ran…\n\n",
                instruction=(
                    "No command was executed in this turn — either none was requested of "
                    "the tool, or the user declined it. Your reply describes a command's "
                    "result, which did not happen. Rewrite it: say plainly that the command "
                    "was not run and why, and keep everything you did actually do. Do not "
                    "report output you did not receive from a tool."))
        return GateResult(note="the reply describes a command's result, but no command "
                               "was run in this turn")

    def _gate_plan_progress(self, turn: TurnState, response) -> GateResult:
        """Move the plan forward from evidence, not from the model remembering to.

        `update_plan_step` exists and she will not reliably call it — the same
        finding as `_gate_plan`, which stopped asking the model to write PLAN.md
        and had Aura write it from what the turn actually did.

        Never asks for another round: this is bookkeeping about work already
        finished, and the user should not wait a minute for it.
        """
        project = self.current_project
        if not project or not turn.successful_tools:
            return PASS
        try:
            steps = self.db.plan_steps(project)
        except Exception as exc:
            # Bookkeeping must never break a finished turn, but a plan that
            # cannot be read is a plan that has silently stopped existing.
            self._note_bookkeeping_failure("plan_steps_read", project, exc)
            return PASS
        if not steps:
            return PASS

        proof = self._step_evidence(turn)
        first_open = True
        for step in steps:
            if step["status"] == "done" or step["id"] in turn.plan_steps_recorded:
                continue
            # A step usually names its own artifact, and whether that file exists
            # is a fact about the workspace rather than a guess about the turn.
            # That is what keeps the record level with the files rather than with
            # what was said about them.
            named = self._paths_named_in(step["text"])
            landed = bool(named) and all(
                self._step_file_exists(project, path) for path in named)
            evidence = ""
            if landed and (proof or turn.workspace_mutation):
                evidence = ("the files it names exist: "
                            + ", ".join(f"`{p}`" for p in named)
                            + (f"; {proof}" if proof else ""))
            elif first_open and proof:
                evidence = proof
            try:
                if evidence:
                    self.db.set_step_status(step["id"], "done", evidence)
                    self.log.record("plan_step", "ok", project=project,
                                    step=step["text"], evidence=evidence, automatic=True)
                elif first_open and turn.workspace_mutation and step["status"] == "todo":
                    # Work happened but nothing proved this step finished. "Started"
                    # is true; "done" would not be.
                    self.db.set_step_status(step["id"], "doing", "")
            except Exception as exc:
                # Without this the durable plan simply stops advancing and
                # nothing says so — which is the shape of the bug that took
                # yesterday to find, arriving next time with no evidence at all.
                self._note_bookkeeping_failure("plan_step", project, exc)
            first_open = False
        return PASS

    #: Anything in a step that looks like a workspace file: a path with a folder,
    #: or a bare name with an extension. Quotes and backticks are stripped because
    #: a plan written by a model is full of both.
    STEP_PATH = re.compile(r"[`'\"]?((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6})[`'\"]?")

    def _step_file_exists(self, project: str, path: str) -> bool:
        """Does the file a step names exist, however the step spelled it?

        Steps are written from inside the project — "Update js/main.bundle.js"
        means `shop/js/main.bundle.js`. Measured on Mat's own plan, where two of
        three steps named their files project-relative and would otherwise have
        read as missing forever.
        """
        if self._file_exists(path):
            return True
        return bool(project) and self._file_exists(f"{project}/{path}")

    def _paths_named_in(self, text: str) -> list[str]:
        """Workspace files a step names, in the order it names them."""
        found: list[str] = []
        for match in self.STEP_PATH.finditer(str(text or "")):
            path = match.group(1).strip("`'\"")
            # The capture starts after the protocol, so a URL has to be caught by
            # what precedes it rather than by how it begins.
            if "://" in str(text)[max(0, match.start() - 3):match.start() + 1]:
                continue
            if path in found:
                continue
            found.append(path)
        return found

    def _step_evidence(self, turn: TurnState) -> str:
        """What this turn proved, in the words Mat will read back later.

        Only verified outcomes count. A successful write says the write succeeded
        and nothing about whether the step is done — the distinction section 8 of
        the prompt draws, applied where it decides a fact rather than a sentence.
        """
        if turn.validation_succeeded and turn.validation_scope:
            files = int((turn.validation_evidence or {}).get("files_seen", 0))
            return (f"validate_project passed on `{turn.validation_scope}`"
                    + (f", {files} files checked" if files else ""))
        confirmed = sorted(turn.verified_final_paths)
        wanted = [path for path in turn.expected_paths if path]
        if wanted and all(path in turn.verified_final_paths for path in wanted):
            return "read back after writing: " + ", ".join(f"`{p}`" for p in wanted)
        if confirmed and turn.workspace_mutation and not turn.missing_artifacts:
            return "read back after writing: " + ", ".join(f"`{p}`" for p in confirmed[:5])
        return ""

    def _gate_plan(self, turn: TurnState, response) -> GateResult:
        """A turn that changed a project leaves a written record behind.

        The system prompt has asked for a PLAN.md since long before this gate,
        and four measured runs wrote one in two of them — so asking politely
        achieves it half the time. The first version of this gate asked the
        model again, which cost a whole extra round (112–370s on this machine)
        for bookkeeping rather than correctness, and it took retries away from
        the gates that decide whether the work is actually right. An existing
        test caught that by pinning the number of model rounds.

        So Aura writes it herself, from what the turn really did. Cheaper, and
        also truer: a record built from the tools that actually ran cannot
        claim work that did not happen, which a model summarising itself can.
        The user corrects it, and the next turn reads it back.
        """
        if turn.empty_response or not turn.workspace_mutation:
            return PASS
        project = self.current_project
        path = self.plan_path(project)
        # Never overwrite. A plan the model wrote, or one the user edited, is
        # worth more than anything reconstructed here.
        if not path or self.plan_text(project):
            return PASS
        try:
            self.sandbox.write_file(path, self._describe_turn(turn))
        except Exception as exc:
            return GateResult(note=f"no plan could be written to `{path}`: {exc}")
        self.log.record("plan_written", "ok", path=path, tools=len(turn.tools_run))
        return PASS

    def _file_exists(self, relative: str) -> bool:
        try:
            return self.sandbox.path(relative).is_file()
        except Exception:
            return False

    def _describe_turn(self, turn: TurnState) -> str:
        """The plan file's first draft: what was asked, what was done, what is open.

        Only facts this turn can vouch for. Anything uncertain is left as a
        blank for the user rather than guessed at, because he reads this and an
        invented assumption here would quietly become his.
        """
        counted: dict[str, int] = {}
        for name in turn.tools_run:
            counted[name] = counted.get(name, 0) + 1
        # Only files that are really there. `expected_paths` is what the
        # request was read to mean, and the reading can be wrong — one live run
        # put `avaleht/index.html` in this list because the Estonian phrase for
        # "the X folder" had been parsed off a word that was not a folder. A
        # plan naming a file that does not exist is worse than one naming none.
        touched = sorted(name for name in
                         set(turn.expected_paths) | set(turn.verified_final_paths)
                         if self._file_exists(name))
        request = str(self.current_request or "").strip()

        lines = [f"# {self.current_project}", ""]
        lines += ["Started by Aura from what the first change to this project actually did.",
                  "It is a draft — correct it freely. She reads this file back before the",
                  "next piece of work and follows it rather than her own memory.", ""]
        lines += ["## What was asked", "", request or "_Not recorded._", ""]
        lines += ["## What was done", ""]
        lines += [f"- `{name}`" + (f" ×{count}" if count > 1 else "")
                  for name, count in counted.items()] or ["- _Nothing recorded._"]
        lines += ["", "## Files", ""]
        lines += [f"- `{name}`" for name in touched] or ["- _None named._"]
        lines += ["", "## Still open", "", "- _Add what should happen next._", ""]
        return "\n".join(lines)

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
    # Artifacts first: the files the user named this turn matter more than the
    # record of why. The plan comes before validation because a plan written
    # after a failed check would be a description of the failure.
    # The plan is written last, after every gate that decides whether the work
    # was actually right. It takes no retry of its own, so it can never spend
    # the budget those gates need.
    COMPLETION_GATES = (_gate_tool_markup, _gate_empty_response, _gate_artifacts,
                        _gate_command_claim,
                        _gate_validation, _gate_action, _gate_verification,
                        # Late, and note-only: it judges the answer Mat will actually
                        # read, after every gate that can still send the model back
                        # for another round has had its say.
                        _gate_unread_files, _gate_plan_progress, _gate_plan)

    #: Said instead of the markup when the model cannot be talked out of it.
    TOOL_MARKUP_REPLY = (
        "I tried to call one of my own tools and wrote it out as text instead of "
        "running it, which does nothing. I have not acted on it. Ask me again and "
        "I will answer directly.")

    #: Tools whose success means this turn changed something the user asked to
    #: change. Deliberately wider than MUTATING_TOOL_NAMES, which is only about
    #: recoverable *file* mutations: undoing and remembering change state too.
    STATE_CHANGING_TOOLS = {
        "create_folder", "create_file", "write_file", "write_files", "append_file",
        "replace_in_file", "apply_edits", "copy_file", "move_file", "safe_delete_file",
        "create_archive", "extract_archive", "undo_last_change", "rollback_task",
        "remember_name", "remember_preference", "remember_personal_fact",
        "forget_personal_fact", "correct_personal_fact"}
    #: Tools that read a file's contents. Only these establish what a file says,
    #: and so only these let the report claim the file was inspected.
    CONTENT_TOOLS = {"read_file", "read_many_files", "inspect_code"}
    #: Tools that measure a file without opening it. `file_info` returns bytes,
    #: line count and modification time — good evidence that a write landed, and
    #: no evidence at all about content. Counting it as inspection let Aura print
    #: "Final file state inspected" directly beneath a table of guessed contents.
    SHAPE_TOOLS = {"file_info"}
    VERIFICATION_TOOLS = CONTENT_TOOLS | SHAPE_TOOLS

    #: Named here because callers and tests reach for it here; the list itself
    #: lives beside the routing that uses it.
    FALLBACK_TOOLS = routing.FALLBACK_TOOLS

    @classmethod
    def select_tool_definitions(cls, message: str, autonomy: str = "balanced",
                                reasoning_depth: str = "balanced") -> list[dict]:
        """Which tools to offer for this request. The rules live in `routing.py`."""
        return routing.select(message, autonomy, reasoning_depth, cls.tool_definitions())

    def _question_needs_looking(self, message: str) -> bool:
        """Does this question ask for something only the workspace can answer?

        The rule lives in `routing.py`; the workspace folders come from here.
        """
        return routing.question_needs_looking(message, self.workspace_projects())

    #: Up to this many words, a bare "this" or "that" is taken to mean the last
    #: request. Beyond it, the pronoun almost always refers to something inside the
    #: sentence it appears in.
    SHORT_REFERENCE_WORDS = 8

    def _routing_request(self, message: str) -> str:
        """Add recent user intent only when the message explicitly refers back."""
        lower = message.casefold().strip()
        if self._is_greeting(message):
            return message
        continuation = bool(re.fullmatch(
            r"(?:yes|yeah|yep|sure|ok(?:ay)?|continue|proceed|go ahead|keep going|do it|finish it)[.! ]*",
            lower,
        ))
        # Phrases that can only mean the previous request, at any length.
        reference = bool(re.search(
            r"\b(?:i meant|run it|fix it|build it|make it|finish it|do it|"
            r"the same|previous|above|where you left off)\b",
            lower,
        ))
        # A lone demonstrative is different. "Do this" is a whole request pointing
        # backwards; "Use this local context only: …" points inside its own
        # sentence. Length is what separates them, and reading the second kind as a
        # follow-up made a question about a name inherit "kirjuta" from three turns
        # earlier and open a build-approval card.
        if not reference and len(lower.split()) <= self.SHORT_REFERENCE_WORDS:
            reference = bool(re.search(r"\b(?:that|this|those|them)\b", lower))
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
        return language.is_greeting(message)

    @staticmethod
    def _greeting_response(message: str) -> str:
        cleaned = re.sub(r"[^\wõäöüšž]+", " ", message.casefold(), flags=re.UNICODE).strip()
        if re.match(r"^(?:hei|tere|tsau)\b", cleaned):
            return "Hei! Olen siin ja valmis. Mida soovid teha?"
        return "Hello! I’m here and ready. What would you like to do?"

    @staticmethod
    def _strip_negative_clauses(message: str) -> str:
        return routing.strip_negative_clauses(message)

    #: Verbs that presuppose the thing already exists. "Create a file" says nothing
    #: about what is there; "change the title in it" asserts there is a title.
    EDIT_VERBS = ("edit", "change", "update", "fix", "modify", "replace", "rename",
                  "correct", "adjust", "improve", "refactor", "rewrite")
    #: Verbs that ask for something new, which make the request ambiguous rather
    #: than mistaken — so nothing is claimed when both appear.
    CREATE_VERBS = ("create", "make", "build", "generate", "add", "new")

    @classmethod
    def _is_edit_request(cls, message: str) -> bool:
        """Did the user ask to change something they believe already exists?"""
        lower = language.with_english_hints(str(message).casefold())
        edits = any(re.search(rf"\b{verb}\b", lower) for verb in cls.EDIT_VERBS)
        creates = any(re.search(rf"\b{verb}\b", lower) for verb in cls.CREATE_VERBS)
        return edits and not creates

    @staticmethod
    def _requires_mutation(message: str) -> bool:
        lower = AuraAgent._strip_negative_clauses(
            language.with_english_hints(message.casefold().replace("don’t", "don't")))
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
        lower = language.with_english_hints(str(message).casefold())
        if any(tool in lower for tool in EXTERNAL_TOOLS):
            return True
        if re.search(r"\b[a-z]:[\\/]", lower):
            return True
        return any(phrase in lower for phrase in (
            "granted folder", "granted write folder", "granted read folder",
            "outside the workspace", "outside my workspace", "external folder",
        ))

    #: Technology names that look exactly like filenames and never are. Measured
    #: on a real request listing a stack: "Next.js" and "Node.js" were read as
    #: two files Aura had been asked to create, so the completion gate would
    #: have reported a finished job unfinished for want of them.
    #:
    #: A deny-list rather than something cleverer because the only signal that
    #: separates them is what the word means. The cost is that a project with a
    #: genuine top-level `next.js` must write it as `./next.js` or put it in a
    #: folder, which is a far rarer request than naming the framework.
    NOT_FILENAMES = frozenset({
        "node.js", "next.js", "nuxt.js", "vue.js", "react.js", "express.js",
        "three.js", "d3.js", "chart.js", "alpine.js", "ember.js", "backbone.js",
        "discord.js", "socket.js", "video.js", "moment.js", "nest.js",
    })

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
            # Only bare names are ambiguous; a path was clearly meant as one.
            if "/" not in name and "\\" not in name and                     name.casefold() in AuraAgent.NOT_FILENAMES:
                continue
            if name not in filenames:
                filenames.append(name)
        target = None
        patterns = (
            r"(?i)\b(?:in|inside|under)\s+(?:a\s+|the\s+)?([\w.-]+)\s+(?:folder|directory)\b",
            r"(?i)\b(?:in|inside|under)\s+([\w.-]+)\s+with\b",
            r"(?i)\b([\w.-]+)\s+project\b",
            # Estonian puts the folder on either side of the word for it, and
            # marks the relation with a case ending rather than a preposition.
            # "kausta promo" puts the name after it; "promo kaustas" and
            # "aura_craft projektis" put it before. Matching both directions
            # with one word list made "projektis uus leht" report the folder
            # as "uus": the case ending is what says which side to read.
            r"(?i)\b(?:kausta|kataloogi)\s+([\w.-]+)",
            r"(?i)\b([\w.-]+)\s+(?:kaustas|kataloogis|projektis)\b",
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
                                    sources: list[str] | None = None,
                                    # Kept apart from `verified_paths` so the report
                                    # cannot promote a measurement into an inspection.
                                    measured_paths: list[str] | None = None,
                                    # Files the request spoke of as existing, which
                                    # did not, and were created during the turn.
                                    created_instead: list[str] | None = None) -> str:
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
        # Stated as evidence rather than as a doubt: it is confirmed, it was simply
        # left out. "The heading is correctly set" is true and hides that the file
        # was invented rather than edited.
        conjured = [path for path in (created_instead or []) if path]
        if conjured:
            display = ", ".join(f"`{path}`" for path in conjured[:8])
            suffix = f" and {len(conjured) - 8} more" if len(conjured) > 8 else ""
            evidence.append(
                f"Did not exist and was created, rather than changed: {display}{suffix}.")
        # Said separately, and said smaller, because it is a smaller claim.
        measured = [path for path in (measured_paths or [])
                    if path and path not in required and path not in verified]
        if measured:
            display = ", ".join(f"`{path}`" for path in measured[:8])
            suffix = f" and {len(measured) - 8} more" if len(measured) > 8 else ""
            evidence.append(
                f"Size and line count checked, contents not read: {display}{suffix}.")
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


















































    #: A reminder only ever shows a message, so the model may set one. It may
    #: not schedule anything else: this tool hard-codes the kind, so nothing it
    #: writes can become background work that acts.
    MAX_ACTIVE_REMINDERS = 20



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
            self._report_tool(name, args, True)
            return payload
        except Exception as exc:
            # The model is told what to do next, not what Python called it. The
            # log keeps the raw text as well, because the sentence that helps a
            # model act is not always the one that helps Mat debug.
            explained = tool_errors.explain(exc, name, args, self.sandbox.root)
            self.log.record(name, "error", tool_call=call.id, error=explained,
                            raw_error=str(exc) if explained != str(exc) else None)
            self._report_tool(name, args, False)
            payload = {"ok": False, "error": explained}
            if self.current_task_id:
                safe_args = {k: v for k, v in args.items()
                             if k not in {"content", "old_text", "new_text", "edits"}}
                self.tasks.record_tool(self.current_task_id, name, safe_args, payload)
            return payload


    def _report_tool(self, name: str, arguments: dict, succeeded: bool) -> None:
        """Tell the caller a tool finished. Never let that break the tool."""
        if self.on_tool is None:
            return
        try:
            self.on_tool(name, dict(arguments), succeeded)
        except Exception:
            pass

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
                # No allowlist any more — Mat removed it, having decided that
                # granting each site by hand cost more than it protected. What
                # stays is the address check below, which is a different thing:
                # it refuses a public name that resolves onto his own network.
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
        """Syntax, and whether the pages can actually find what they reference.

        Mat opened a finished landing page and got Times New Roman on white — no
        styling at all — while Aura reported "validation passed, 0 issues" and
        "projekt on valmis kasutamiseks". Every stylesheet link was written
        absolute (`/css/style.css`) against files that live in `shop/css/`, so the
        markup parsed perfectly and resolved to nothing.

        A page whose own stylesheet does not load is broken, whatever the parser
        says. `check_broken_assets` already knew this and was never asked.
        """
        result = validate_project(self.sandbox, relative)
        try:
            assets = check_broken_assets(self.sandbox, relative)
        except (OSError, ValueError):
            return result       # never let the extra check break the ordinary one
        broken = assets.get("broken") or []
        if not broken:
            return result
        issues = list(result.get("issues") or [])
        for item in broken[:20]:
            issues.append({
                "file": item.get("file", ""),
                "error": (f"references {item.get('reference')!r}, which does not "
                          f"exist — a leading '/' makes a path absolute, so it looks "
                          f"outside the project"),
            })
        return {**result, "valid": False, "issues": issues,
                "broken_references": len(broken)}

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
