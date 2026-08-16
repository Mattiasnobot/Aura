from __future__ import annotations

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
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .action_log import ActionLog
from .commands import CommandAgent
from .config import ConfigStore
from .memory import MemoryStore
from .provider import LMStudioProvider, MockProvider, Provider, ProviderContext, ToolCall
from .image_diff import compare_images
from .permissions import ExternalReader, ExternalWriter, PermissionStore
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


class TaskCancelled(RuntimeError):
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
        self._sweep_retention()

    MUTATING_TOOL_NAMES = {
        "create_folder", "create_file", "write_file", "write_files", "append_file",
        "replace_in_file", "apply_edits", "copy_file", "move_file", "safe_delete_file",
        "create_archive", "extract_archive", "import_file", "write_external_file",
    }

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
        self.memory.remember_message("user", message)
        self.last_recalled = []
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
            if isinstance(self.provider, MockProvider) and re.search(
                    r"(create|build|make).*(hello[ -]?world).*(python|app)", lower):
                response = self.build_hello_world(approve, set_state)
            elif lower.startswith("list files") or "what files" in lower:
                files = self.sandbox.list_files()
                response = "Workspace files:\n" + ("\n".join(f"• {f}" for f in files) if files else "(empty)")
            elif lower.startswith("read file "):
                name = message[len("read file "):].strip()
                response = f"Contents of {name}:\n\n{self.sandbox.read_file(name)}"
                self.log.record("read_file", path=name)
            elif lower.startswith("remember my name is "):
                name = message[len("remember my name is "):].strip()
                self.memory.set_name(name)
                response = f"I’ll remember that your name is {name}."
            elif lower.startswith("remember preference ") and "=" in message:
                pair = message[len("remember preference "):]
                key, value = pair.split("=", 1)
                self.memory.set_preference(key, value)
                response = f"Remembered: {key.strip()} = {value.strip()}."
            elif self._is_greeting(message):
                # A greeting must be instant and must never replay a previous
                # build task.  Substantive conversation still goes to the
                # configured provider; this tiny social acknowledgement does
                # not need a 4K-token model round-trip.
                response = self._greeting_response(message)
            elif isinstance(self.provider, LMStudioProvider):
                response = self._tool_conversation(message, approve, set_state, token)
            else:
                response = self.provider.reply(message, self._context(message))
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
        self.memory.remember_message("assistant", response)
        self.tasks.finish(task_id, status, response)
        self.current_task_id = None
        self.sandbox.active_task_id = None
        return response

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
        expected_base, expected_paths = self._extract_artifact_contract(routing_request)
        if not self._requires_mutation(routing_request):
            # Naming a file in a read-only request ("read notes.txt", "screenshot
            # index.html") is a reference, not a promise to create it. Demanding
            # it exist afterwards fails tasks that in fact succeeded — and an
            # external file can never be inside the workspace at all. The folder
            # itself stays, because it still scopes validation reporting.
            expected_paths = []
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
        verification_needed = False
        # One budget for every completion gate. Four independent counters
        # allowed up to nine extra rounds, each re-answering from scratch.
        retries_left = self.MAX_COMPLETION_RETRIES
        validation_attempted = False
        unconfirmed: list[str] = []
        successful_tools = 0
        external_written: set[str] = set()
        external_activity = False
        workspace_mutation = False
        mutation_performed = False
        validation_succeeded = False
        validation_evidence: dict | None = None
        validation_scope: str | None = None
        verified_final_paths: set[str] = set()
        pending_verifications: dict[str, str] = {}
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
        verification_tools = {"read_file", "read_many_files", "file_info", "inspect_code"}
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
            if not response.tool_calls:
                if not response.content:
                    raise RuntimeError("the model returned neither text nor a tool request")
                missing_action = action_expected and successful_tools == 0
                missing_mutation = requires_mutation and not mutation_performed
                # Work in a granted folder can never satisfy a workspace
                # artifact contract, so requiring one there nags forever.
                missing_artifacts = [] if external_activity else [
                    path for path in expected_paths
                    if not self.sandbox.path(path).is_file()
                    and posixpath.basename(self._normalize_path(path)) not in external_written]
                if missing_artifacts and retries_left > 0:
                    retries_left -= 1
                    if token:
                        token("Aura is checking the requested deliverables and continuing…\n\n")
                    messages.append({"role": "system", "content":
                        "The artifact contract is not satisfied. Create these exact missing paths now: " +
                        json.dumps(missing_artifacts) + ". Then validate and inspect them before reporting completion."})
                    continue
                if missing_artifacts:
                    # Report rather than raise: the model's work is still worth
                    # showing, but Aura must not imply it was verified.
                    unconfirmed.append(
                        "these files were requested but not found: "
                        + ", ".join(f"`{path}`" for path in missing_artifacts[:8]))
                validation_path = expected_base or self._validation_root(pending_verifications)
                # A build word alone only demands validation once the workspace
                # actually changed; an explicit "validate" always does.
                validation_required = validation_asked or (build_words and workspace_mutation)
                # ...except for work done entirely in a granted folder, where
                # validating the workspace would prove nothing about it.
                if external_activity and not workspace_mutation:
                    validation_required = False
                # Only one model attempt here: Aura can validate deterministically
                # itself, so further rounds would spend the user's time asking the
                # model for something the backend is about to do anyway.
                if (validation_required and not validation_succeeded
                        and not validation_attempted and retries_left > 0):
                    validation_attempted = True
                    retries_left -= 1
                    if token:
                        token("Aura is validating the completed project…\n\n")
                    messages.append({"role": "system", "content":
                        "The requested project has not passed validate_project at the required path. "
                        f"Run validate_project with path {validation_path!r}, fix every issue, and validate again."})
                    continue
                if validation_required and not validation_succeeded:
                    automatic = self._validate_project(validation_path)
                    self._record_automatic_validation(validation_path, automatic)
                    if not automatic["valid"]:
                        first = automatic["issues"][0] if automatic["issues"] else {"error": "unknown issue"}
                        unconfirmed.append(
                            "validation of `" + str(validation_path) + "` failed: "
                            + str(first.get("error", "unknown issue")))
                    validation_succeeded = automatic["valid"]
                    validation_evidence = dict(automatic)
                    validation_scope = validation_path
                    self._clear_verified_scope(pending_verifications, validation_path)
                    verification_needed = bool(pending_verifications)
                    if successful_tools == 0 and not requires_mutation:
                        # A local model may describe the correct action yet decline
                        # to call a harmless read-only tool. Aura can perform this
                        # deterministic validation itself and report only facts.
                        successful_tools += 1
                        missing_action = False
                        response.content = self._format_validation_report(
                            validation_path, automatic,
                            self.sandbox.list_files(validation_path),
                        )
                if (missing_action or missing_mutation) and retries_left > 0:
                    retries_left -= 1
                    if token:
                        token("Aura noticed the requested action was not completed and is correcting it…\n\n")
                    requirement = "perform the requested workspace mutation" if missing_mutation else "use the relevant tool"
                    messages.append({"role": "system", "content":
                        f"The user requested an actionable operation, but no successful tool has fulfilled it. "
                        f"Do not claim completion or inability: {requirement} now, inspect the result, and report only confirmed facts."})
                    continue
                if missing_action or missing_mutation:
                    if missing_action and not missing_mutation and not requires_mutation:
                        selected_names = {
                            item["function"]["name"] for item in selected_tools
                        }
                        if "list_files" in selected_names:
                            # Add the facts to the reply instead of replacing it.
                            # Returning the listing threw away a perfectly good
                            # answer whenever the model chose not to call a tool.
                            list_path = expected_base or "."
                            files = self.sandbox.list_files(list_path)
                            self.log.record("list_files", "ok", path=list_path, automatic=True)
                            if self.current_task_id:
                                self.tasks.record_tool(
                                    self.current_task_id, "list_files",
                                    {"path": list_path, "automatic": True},
                                    {"ok": True, "files": files},
                                )
                            successful_tools += 1
                            missing_action = False
                            if not response.content:
                                response.content = self._format_file_report(list_path, files)
                    if missing_action or missing_mutation:
                        unconfirmed.append(
                            "no tool actually performed the requested change, so this "
                            "answer describes intent rather than confirmed work")
                if verification_needed and retries_left > 0:
                    retries_left -= 1
                    if token:
                        token("Aura is verifying the files it just changed…\n\n")
                    messages.append({"role": "system", "content":
                        "A workspace mutation occurred after the last verification. Do not finish yet. "
                        "Use read_file/file_info, validate_project, or a successful validation command to verify the final state, "
                        "fix any problem, and then give the final report."})
                    continue
                if verification_needed:
                    verification_errors = self._verify_pending(pending_verifications)
                    if verification_errors:
                        unconfirmed.append(
                            "the final state could not be verified: "
                            + "; ".join(verification_errors[:3]))
                    paths = sorted(pending_verifications)
                    verified_final_paths.update(paths)
                    self.log.record("verify_final_state", paths=paths, automatic=True)
                    if self.current_task_id:
                        self.tasks.record_tool(
                            self.current_task_id, "verify_final_state", {"paths": paths},
                            {"ok": True, "automatic": True},
                        )
                    pending_verifications.clear()
                    verification_needed = False
                missing = set(missing_artifacts)
                present = [path for path in expected_paths if path not in missing]
                return self._format_completion_evidence(
                    response.content, validation_scope, validation_evidence,
                    sorted(verified_final_paths), present, unconfirmed,
                )
            if response.content and token:
                token("\n\n")
            state("working")
            for call in response.tool_calls:
                self._check_cancelled()
                result = self._execute_tool(call, approve)
                # An attached image cannot travel inside a tool result, which is
                # plain text. Lift it out and send it as a real multimodal turn.
                attachment = result.pop("content", None) if call.name == "look_at_image" else None
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
                if attachment:
                    messages.append({"role": "user", "content": [
                        {"type": "text",
                         "text": f"Here is the image {result.get('path')} you asked to look at."},
                        {"type": "image_url", "image_url": {"url": attachment}},
                    ]})
                if result.get("ok"):
                    successful_tools += 1
                    if call.name in EXTERNAL_TOOLS:
                        external_activity = True
                    if call.name in {"write_external_file", "undo_external_change"}:
                        # Counts as fulfilling the request even though the file
                        # lives outside the sandbox entirely.
                        if result.get("path"):
                            external_written.add(Path(str(result["path"])).name)
                        mutation_performed = True
                    if call.name in mutation_tools:
                        mutation_performed = True
                        workspace_mutation = True
                        pending_verifications.update(self._mutation_expectations(call, result))
                        validation_succeeded = False
                        validation_evidence = None
                        validation_scope = None
                    elif call.name in verification_tools:
                        verified_paths = []
                        if call.name == "read_many_files":
                            verified_paths = [str(item.get("path", "")) for item in result.get("files", [])
                                              if isinstance(item, dict)]
                        else:
                            verified_paths = [str(result.get("path") or call.arguments.get("path", ""))]
                        for verified_path in verified_paths:
                            normalized = self._normalize_path(verified_path)
                            if normalized:
                                verified_final_paths.add(normalized)
                            pending_verifications.pop(normalized, None)
                    if call.name == "validate_project" and result.get("valid"):
                        requested_path = self._normalize_path(str(call.arguments.get("path", ".")))
                        self._clear_verified_scope(pending_verifications, requested_path)
                        if self._validation_satisfies(requested_path, expected_base, expected_paths):
                            validation_succeeded = True
                            validation_evidence = dict(result)
                            validation_scope = requested_path
                verification_needed = bool(pending_verifications)
            state("thinking")
        raise RuntimeError("the model exceeded the tool-operation limit; ask it to continue in a new message")

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
        paths = [f"{target}/{name}" if target and "/" not in name and "\\" not in name else name
                 for name in filenames]
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
                                    unconfirmed: list[str] | None = None) -> str:
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
        return report

    @staticmethod
    def tool_definitions() -> list[dict]:
        def tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
            return {"type": "function", "function": {"name": name, "description": description,
                    "parameters": {"type": "object", "properties": properties,
                                   "required": required or [], "additionalProperties": False}}}
        path = {"type": "string", "description": "Workspace-relative path; never absolute and never use .."}
        return [
            tool("list_files", "List files recursively inside a workspace folder.",
                 {"path": {**path, "default": "."}}),
            tool("create_folder", "Create an empty workspace folder and missing parent folders. Use this instead of mkdir.",
                 {"path": path}, ["path"]),
            tool("read_file", "Read a UTF-8 text file or a focused line range from the workspace.",
                 {"path": path, "start_line": {"type": "integer", "minimum": 1, "default": 1},
                  "end_line": {"type": "integer", "minimum": 1}}, ["path"]),
            tool("read_many_files", "Read several related UTF-8 workspace files in one call with bounded output.",
                 {"paths": {"type": "array", "minItems": 1, "maxItems": 20,
                            "items": path},
                  "max_lines_each": {"type": "integer", "minimum": 20, "maximum": 1000,
                                     "default": 300}}, ["paths"]),
            tool("file_info", "Inspect a file's size, line count, and modification time.",
                 {"path": path}, ["path"]),
            tool("create_file", "Create a new UTF-8 file; fails if it already exists.",
                 {"path": path, "content": {"type": "string"}}, ["path", "content"]),
            tool("write_file", "Create or replace a UTF-8 file in the workspace.",
                 {"path": path, "content": {"type": "string"}}, ["path", "content"]),
            tool("write_files", "Create or replace up to 20 related UTF-8 files in one batch. Every file remains recoverable.",
                 {"files": {"type": "array", "minItems": 1, "maxItems": 20,
                            "items": {"type": "object", "properties": {
                                "path": path, "content": {"type": "string"}},
                                "required": ["path", "content"], "additionalProperties": False}}},
                 ["files"]),
            tool("append_file", "Append UTF-8 text to a workspace file, creating it if needed.",
                 {"path": path, "content": {"type": "string"}}, ["path", "content"]),
            tool("replace_in_file", "Precisely replace exact text in an existing UTF-8 file. Fails if the match count is unexpected.",
                 {"path": path, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                  "expected_count": {"type": "integer", "minimum": 1, "default": 1}},
                 ["path", "old_text", "new_text"]),
            tool("apply_edits", "Atomically apply several exact text replacements to one file with one recovery snapshot.",
                 {"path": path, "edits": {"type": "array", "minItems": 1, "maxItems": 50,
                  "items": {"type": "object", "properties": {
                      "old_text": {"type": "string"}, "new_text": {"type": "string"},
                      "expected_count": {"type": "integer", "minimum": 1, "default": 1}},
                      "required": ["old_text", "new_text"], "additionalProperties": False}}},
                 ["path", "edits"]),
            tool("search_files", "Search file names and UTF-8 contents in the workspace.",
                 {"query": {"type": "string"}, "path": {**path, "default": "."}}, ["query"]),
            tool("search_text", "Return matching lines with file names and line numbers.",
                 {"query": {"type": "string"}, "path": {**path, "default": "."},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}, ["query"]),
            tool("list_granted_folders",
                 "List folders outside the workspace that the user has granted Aura "
                 "permission to read. Aura cannot grant itself access; only the user "
                 "can, from the Permissions panel.", {}, []),
            tool("list_external_folder",
                 "List files inside a folder the user has already granted. Fails if "
                 "there is no active permission for that folder.",
                 {"path": {"type": "string"},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200}},
                 ["path"]),
            tool("read_external_file",
                 "Read a UTF-8 text file inside a folder the user has already granted. "
                 "Fails if there is no active permission for that file's folder.",
                 {"path": {"type": "string"}}, ["path"]),
            tool("write_external_file",
                 "Write a UTF-8 text file inside a folder the user granted for writing. "
                 "The previous version is saved first, so the change can be undone. "
                 "A read grant is not enough; writing needs its own permission.",
                 {"path": {"type": "string"}, "content": {"type": "string"}},
                 ["path", "content"]),
            tool("undo_external_change",
                 "Undo Aura's most recent write outside the workspace, restoring the "
                 "previous version or removing a file it created.", {}, []),
            tool("check_accessibility",
                 "Report accessibility problems in workspace HTML: images without alt "
                 "text, form controls without labels, empty links or buttons, a missing "
                 "lang or title, and skipped heading levels. Structural checks only — "
                 "it does not evaluate colour contrast.",
                 {"path": {**path, "default": "."}}, []),
            tool("compare_images",
                 "Measure exactly how two workspace PNG images differ: percentage of "
                 "changed pixels and the region that changed. Use it to check a render "
                 "against a reference or to detect a layout regression between two "
                 "screenshots. This is a real pixel measurement, not an impression.",
                 {"first": path, "second": path,
                  "tolerance": {"type": "integer", "minimum": 0, "maximum": 128,
                                "default": 8}},
                 ["first", "second"]),
            tool("capture_page",
                 "Render a workspace HTML page in a local headless browser and save a "
                 "PNG screenshot of it into the workspace. Use this to see how a page "
                 "actually looks, then call look_at_image on the saved screenshot. "
                 "Needs the user's approval because it launches a browser.",
                 {"path": path,
                  "width": {"type": "integer", "minimum": 320, "maximum": 2560, "default": 1200},
                  "height": {"type": "integer", "minimum": 240, "maximum": 2000, "default": 800}},
                 ["path"]),
            tool("look_at_image",
                 "Actually look at a workspace image (PNG/JPEG/GIF/WebP/BMP). The image "
                 "is attached to the conversation so you can describe or compare what it "
                 "shows. Call this whenever the user asks what an image contains or looks "
                 "like — listing or reading the file cannot answer that, because its "
                 "pixels are only visible through this tool.",
                 {"path": path}, ["path"]),
            tool("find_relevant_files",
                 "Rank workspace files by relevance to a described topic or question. "
                 "Use this when you do not know the exact wording to search for; use "
                 "search_files or search_text when you need an exact string. Matches "
                 "words, not synonyms.",
                 {"query": {"type": "string"}, "path": {**path, "default": "."},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, ["query"]),
            tool("copy_file", "Copy a workspace file.",
                 {"source": path, "destination": path}, ["source", "destination"]),
            tool("move_file", "Move or rename a workspace file.",
                 {"source": path, "destination": path}, ["source", "destination"]),
            tool("safe_delete_file", "Move a file into Aura's recoverable trash.",
                 {"path": path}, ["path"]),
            tool("undo_last_change", "Undo Aura's most recent file mutation using its protected snapshot history.", {}, []),
            tool("rollback_task", "Undo every still-active file mutation belonging to a specific Aura task ID.",
                 {"task_id": {"type": "string"}}, ["task_id"]),
            tool("change_history", "List recent recoverable workspace mutations and whether they were undone.",
                 {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}),
            tool("workspace_summary", "Summarize workspace file count, size, extensions, and largest files.", {}, []),
            tool("inspect_code", "Outline symbols, imports, and structure in a Python, JavaScript, or TypeScript file without executing it.",
                 {"path": path}, ["path"]),
            tool("compare_files", "Produce a bounded unified diff between two UTF-8 workspace files.",
                 {"left": path, "right": path,
                  "context_lines": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3}},
                 ["left", "right"]),
            tool("calculate", "Evaluate arithmetic and common math functions locally without running code.",
                 {"expression": {"type": "string"}}, ["expression"]),
            tool("system_info", "Inspect non-sensitive local runtime facts such as OS, Python, CPU count, and workspace disk space.", {}, []),
            tool("validate_project", "Safely validate every project file, including Python, JSON, TOML, HTML, CSS, JavaScript/TypeScript, XML, and UTF-8 text, without executing project code.",
                 {"path": {**path, "default": "."}}, []),
            tool("run_command", "Run an actual program, test, build, or project runtime inside the workspace. Commands use a direct argument array with no shell. Never use this for file/folder operations; create_file and write_file create parent folders. Use python for Python; unsafe commands require approval.",
                 {"command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                  "timeout": {"type": "number", "minimum": 1, "maximum": 60, "default": 15}}, ["command"]),
            tool("http_get", "Fetch a bounded HTTP(S) text response. Localhost is direct; external network access asks for approval.",
                 {"url": {"type": "string"},
                  "timeout": {"type": "number", "minimum": 1, "maximum": 20, "default": 10}}, ["url"]),
            tool("open_workspace_item", "Open a workspace file or folder in its normal desktop application after approval.",
                 {"path": path}, ["path"]),
            tool("create_archive", "Create a recoverable ZIP archive from a workspace file or folder.",
                 {"source": path, "destination": path}, ["source", "destination"]),
            tool("extract_archive", "Safely extract a workspace ZIP with traversal, link, file-count, and size protection.",
                 {"archive": path, "destination": path}, ["archive", "destination"]),
            tool("capability_summary", "List Aura's currently available tools and autonomy policy.", {}, []),
            tool("remember_name", "Remember the user's preferred name.",
                 {"name": {"type": "string"}}, ["name"]),
            tool("remember_preference", "Remember one durable user preference.",
                 {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
            tool("remember_personal_fact", "Remember one clear, non-sensitive fact the user explicitly stated about their preferences, interests, goals, projects, tools, or working style.",
                 {"category": {"type": "string", "enum": sorted(MemoryStore.PROFILE_CATEGORIES)},
                  "value": {"type": "string"}}, ["category", "value"]),
            tool("list_personal_memory", "Review the editable personal facts Aura currently remembers about the user.",
                 {"query": {"type": "string", "default": ""}}, []),
            tool("forget_personal_fact", "Forget one personal memory matching the user's description. Ambiguous matches are returned without deleting anything.",
                 {"query": {"type": "string"}}, ["query"]),
            tool("correct_personal_fact", "Correct one unambiguous personal memory and mark the corrected value as user-confirmed.",
                 {"query": {"type": "string"}, "new_value": {"type": "string"},
                  "category": {"type": "string", "enum": sorted(MemoryStore.PROFILE_CATEGORIES)}},
                 ["query", "new_value"]),
            tool("recent_tasks", "Review Aura's recent persistent task outcomes and tools used.",
                 {"limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}}),
        ]

    def _execute_tool(self, call: ToolCall, approve: Callable[[list[str]], bool] | None) -> dict:
        name, args = call.name, call.arguments
        tool_ok = True
        try:
            if name == "list_files":
                result = {"files": self.sandbox.list_files(str(args.get("path", ".")))[:1000]}
            elif name == "create_folder":
                target = self.sandbox.create_folder(str(args["path"]))
                result = {"path": target.relative_to(self.sandbox.root).as_posix()}
            elif name == "read_file":
                content = self.sandbox.read_file(str(args["path"]))
                lines = content.splitlines(keepends=True)
                start = max(1, int(args.get("start_line", 1)))
                end = min(len(lines), int(args.get("end_line", start + 399)))
                selected = "".join(lines[start - 1:end])
                result = {"path": args["path"], "content": selected,
                          "start_line": start, "end_line": end, "total_lines": len(lines),
                          "truncated": end < len(lines)}
            elif name == "read_many_files":
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
            elif name == "file_info":
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
            elif name in {"create_file", "write_file", "append_file"}:
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
            elif name == "write_files":
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
            elif name == "replace_in_file":
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
            elif name == "apply_edits":
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
            elif name == "search_files":
                result = {"matches": self.sandbox.search_files(
                    str(args["query"]), str(args.get("path", ".")))[:500]}
            elif name == "search_text":
                limit = max(1, min(int(args.get("limit", 100)), 500))
                result = {"matches": self.sandbox.search_text(
                    str(args["query"]), str(args.get("path", ".")), limit)}
            elif name == "list_granted_folders":
                result = {"folders": [
                    {"path": grant["root"], "mode": grant["mode"],
                     "project": grant.get("project")}
                    for grant in self.permissions.active()
                    if grant.get("capability") == "read_folder"]}
            elif name == "list_external_folder":
                result = {"path": str(args["path"]),
                          "files": self.external.list_files(
                              str(args["path"]), limit=int(args.get("limit", 200)))}
            elif name == "read_external_file":
                result = {"path": str(args["path"]),
                          "content": self.external.read_file(str(args["path"]))}
            elif name == "write_external_file":
                result = self.external_writer.write_file(
                    str(args["path"]), str(args["content"]),
                    task_id=self.current_task_id)
            elif name == "undo_external_change":
                result = self.external_writer.undo_last()
            elif name == "check_accessibility":
                result = check_accessibility(self.sandbox, str(args.get("path", ".")))
            elif name == "compare_images":
                result = compare_images(
                    self.sandbox.path(str(args["first"])),
                    self.sandbox.path(str(args["second"])),
                    tolerance=int(args.get("tolerance", 8)))
            elif name == "capture_page":
                result = self._capture_page(
                    str(args["path"]), approve,
                    int(args.get("width", 1200)), int(args.get("height", 800)))
                tool_ok = bool(result.get("approved"))
            elif name == "look_at_image":
                if not self.vision_enabled():
                    raise ValueError(
                        "The loaded model does not accept images. Turn vision on in "
                        "Settings if you know it does.")
                result = self._read_image_attachment(str(args["path"]))
            elif name == "find_relevant_files":
                result = {"matches": self.index.search(
                    str(args["query"]), int(args.get("limit", 10)),
                    str(args.get("path", ".")))}
            elif name == "copy_file":
                target = self.sandbox.copy_file(str(args["source"]), str(args["destination"]))
                result = {"path": target.relative_to(self.sandbox.root).as_posix()}
            elif name == "move_file":
                target = self.sandbox.move_file(str(args["source"]), str(args["destination"]))
                result = {"path": target.relative_to(self.sandbox.root).as_posix()}
            elif name == "safe_delete_file":
                target = self.sandbox.safe_delete_file(str(args["path"]))
                result = {"trashed_as": target.name, "recoverable": True}
            elif name == "undo_last_change":
                result = self.sandbox.undo_last_change()
            elif name == "rollback_task":
                result = self.sandbox.rollback_task(str(args["task_id"]))
            elif name == "change_history":
                result = {"changes": self.sandbox.change_history(int(args.get("limit", 20)))}
            elif name == "workspace_summary":
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
            elif name == "inspect_code":
                result = self._inspect_code(str(args["path"]))
            elif name == "compare_files":
                left, right = str(args["left"]), str(args["right"])
                context = max(0, min(int(args.get("context_lines", 3)), 20))
                result = self.sandbox.compare_files(left, right, context)
            elif name == "calculate":
                expression = str(args["expression"])
                result = {"expression": expression, "result": self._calculate(expression)}
            elif name == "system_info":
                disk = shutil.disk_usage(self.sandbox.root)
                result = {"os": platform.platform(), "python": platform.python_version(),
                          "architecture": platform.machine(), "cpu_count": os.cpu_count(),
                          "workspace": str(self.sandbox.root),
                          "workspace_disk": {"total": disk.total, "used": disk.used, "free": disk.free}}
            elif name == "validate_project":
                result = self._validate_project(str(args.get("path", ".")))
            elif name == "run_command":
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
                tool_ok = run.succeeded
                if not tool_ok:
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
            elif name == "http_get":
                result = self._http_get(str(args["url"]), approve,
                                        max(1.0, min(float(args.get("timeout", 10)), 20.0)))
            elif name == "open_workspace_item":
                path = str(args["path"])
                target = self.sandbox.path(path)
                if not target.exists():
                    raise FileNotFoundError(path)
                if not approve or not approve(["OPEN", path]):
                    raise PermissionError("Opening a desktop application was not approved")
                os.startfile(target)  # type: ignore[attr-defined]
                result = {"path": path, "opened": True}
            elif name == "create_archive":
                target = self.sandbox.create_archive(str(args["source"]), str(args["destination"]))
                result = {"path": target.relative_to(self.sandbox.root).as_posix(),
                          "bytes": target.stat().st_size}
            elif name == "extract_archive":
                extracted = self.sandbox.extract_archive(str(args["archive"]), str(args["destination"]))
                result = {"files": [path.relative_to(self.sandbox.root).as_posix() for path in extracted],
                          "count": len(extracted)}
            elif name == "capability_summary":
                result = {"tools": [item["function"]["name"] for item in self.tool_definitions()],
                          "tool_count": len(self.tool_definitions()),
                          "reasoning_depth": self.config.data.get("reasoning_depth"),
                          "autonomy_mode": self.config.data.get("autonomy_mode"),
                          "workspace_only": True,
                          "approval_policy": "Safe local tools are automatic; executable code, external HTTP, and desktop launches ask first."}
            elif name == "remember_name":
                self.memory.set_name(str(args["name"]))
                result = {"remembered": True}
            elif name == "remember_preference":
                self.memory.set_preference(str(args["key"]), str(args["value"]))
                result = {"remembered": True}
            elif name == "remember_personal_fact":
                item = self.memory.learn_fact(
                    str(args["category"]), str(args["value"]),
                    source="Explicitly remembered through Aura chat", confidence=1.0, explicit=True,
                )
                result = {"remembered": True, "memory": item}
            elif name == "list_personal_memory":
                query = str(args.get("query", "")).strip()
                memories = (self.memory.find_profile_memories(query) if query
                            else self.memory.profile_memories())
                result = {"memories": memories[:100], "count": len(memories)}
            elif name == "forget_personal_fact":
                query = str(args["query"])
                matches = self.memory.find_profile_memories(query)
                if not matches:
                    raise FileNotFoundError("No personal memory matches that description")
                if len(matches) != 1:
                    choices = "; ".join(str(item.get("value", "")) for item in matches[:5])
                    raise ValueError(f"Memory description is ambiguous; matching facts: {choices}")
                removed = self.memory.forget_profile_memory(str(matches[0]["id"]))
                result = {"forgotten": True, "memory": removed}
            elif name == "correct_personal_fact":
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
            elif name == "recent_tasks":
                result = {"tasks": self.tasks.recent(max(1, min(int(args.get("limit", 5)), 20)))}
            else:
                raise ValueError(f"unknown tool: {name}")
            redacted = {k: v for k, v in args.items()
                        if k not in {"content", "old_text", "new_text", "edits"}}
            if name != "run_command":
                self.log.record(name, tool_call=call.id, arguments=redacted)
            payload = {"ok": tool_ok, **result}
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

    def _http_get(self, url: str, approve: Callable[[list[str]], bool] | None,
                  timeout: float) -> dict:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        opener = build_opener(NoRedirect)
        current = url.strip()
        approved_hosts: set[str] = set()
        for _ in range(6):
            parsed = urlparse(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("URL must use http or https")
            if parsed.username or parsed.password:
                raise ValueError("URLs containing credentials are not allowed")
            host = parsed.hostname.casefold()
            is_loopback = host in {"localhost", "127.0.0.1", "::1"}
            if not is_loopback and host not in approved_hosts:
                if not approve or not approve(["HTTP GET", current]):
                    raise PermissionError("External network request was not approved")
                approved_hosts.add(host)
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
