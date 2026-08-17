from __future__ import annotations

import json
import platform
import sqlite3
from collections import deque
import queue
from pathlib import Path
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from . import __version__
from . import checks
from . import health
from . import language
from .agent import AuraAgent
from .graph_model import build_mind_graph
from .preview_server import PreviewServer
from .scheduler import Scheduler
from .search_service import SearchService, SearchServiceError
from .speech import SpeechOutput
from .voice import VoiceInput
from .memory_bridge import MemoryBridge
from .settings_bridge import SettingsBridge
from .voice_bridge import VoiceBridge
from .workspace_bridge import WorkspaceBridge


class AuraWebBridge(SettingsBridge, VoiceBridge, WorkspaceBridge, MemoryBridge):
    """Narrow, thread-safe interface exposed to Aura's local HTML window."""

    TEXT_PREVIEW_SUFFIXES = {
        ".cjs", ".css", ".csv", ".html", ".htm", ".ini", ".js", ".jsx", ".json",
        ".md", ".mjs", ".py", ".svg", ".toml", ".ts", ".tsx", ".txt", ".xml",
        ".yaml", ".yml",
    }
    IMAGE_PREVIEW_SUFFIXES = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp"}
    MAX_PREVIEW_BYTES = 600_000
    MAX_IMPORT_FILE_BYTES = 1_500_000
    MAX_IMPORT_TOTAL_BYTES = 4_000_000

    def __init__(self, agent: AuraAgent | None = None,
                 speech: SpeechOutput | None = None,
                 voice: VoiceInput | None = None) -> None:
        self._session_started = datetime.now(timezone.utc)
        # Bounded broadcast history: every browser tab advances its own cursor,
        # so one open tab cannot consume another tab's updates.
        self.events: deque[dict[str, Any]] = deque(maxlen=1000)
        self._event_lock = threading.Lock()
        self._event_sequence = 0
        self._legacy_event_cursor = 0
        # Every field `_push` touches must exist before the agent is built: the
        # agent logs during construction (migration and retention), and those
        # events arrive through `_on_log` before __init__ has finished.
        self._closing = False
        self.agent = agent or AuraAgent(on_log=self._on_log)
        self.agent.log.on_event = self._on_log
        config = self.agent.config.data
        self.speech = speech or SpeechOutput(
            bool(config["speak_responses"]), str(config["speech_voice"]),
            int(config["speech_rate"]), int(config["speech_volume"]),
            str(config["speech_engine"]), str(config["speech_model"]),
        )
        self.voice = voice or VoiceInput(
            engine=str(config["voice_engine"]), device=str(config["voice_device"]),
            language=str(config["voice_language"]),
            calibration_ms=int(config["voice_calibration_ms"]),
            silence_ms=int(config["voice_silence_ms"]),
            max_seconds=int(config["voice_max_seconds"]),
            noise_floor=float(config["voice_noise_floor"]),
            whisper_path=str(config["whisper_cpp_path"]),
            whisper_model=str(config["whisper_model_path"]),
        )
        self._state_lock = threading.Lock()
        self._busy = False
        self._voice_active = False
        self._approvals: dict[str, queue.Queue[str]] = {}
        #: The approved file plan for the running turn, ticked off as the
        #: files are actually written rather than as they are intended.
        self._plan_steps: list[dict] = []
        self._task_approved_exact: dict[str, set[str]] = {}
        self._approval_lock = threading.Lock()
        self.preview_server = PreviewServer(self.agent.sandbox, self.agent.log)
        self.scheduler = Scheduler(self.agent.db, self.agent.autonomy,
                                   self.agent.log, busy=self._is_busy)
        self.scheduler.register("reminder", self._deliver_reminder)
        self.scheduler.register("check", self._run_check)
        self._seed_default_checks()
        self.scheduler.start()
        self.search_service = SearchService(self.agent.log)
        self._start_search_service()

    def _start_search_service(self) -> None:
        """Bring search up with Aura, without ever letting it hold up the start.

        A search engine that fails to launch is a reason to say so, never a
        reason for Aura not to open. Every failure here is recorded and shown in
        Settings, and Aura carries on with search switched off.
        """
        config = self.agent.config.data
        mode = str(config.get("search_mode", "off"))
        if mode not in {"docker", "folder"}:
            return

        def bring_up() -> None:
            try:
                if mode == "docker":
                    status = self.search_service.start_docker(
                        Path(self.agent.sandbox.root) / ".aura" / "searxng")
                else:
                    status = self.search_service.start_native(
                        config.get("search_install_path"))
            except SearchServiceError as exc:
                # Not fatal, and not silent: the setting the user changed is
                # exactly where the reason belongs.
                self._push("search_service", running=False, error=str(exc))
                return
            if not str(config.get("search_endpoint") or "").strip():
                # Starting the engine and leaving search switched off would be a
                # running process nothing uses.
                self.agent.config.update(search_endpoint=status["endpoint"])
            self._push("search_service", running=True, endpoint=status["endpoint"])

        # Off the startup path: SearXNG takes tens of seconds to answer the
        # first time, and the interface must not wait for it.
        threading.Thread(target=bring_up, daemon=True, name="aura-search-start").start()

    def undo_session(self, session_id: str, confirm: bool = False) -> dict:
        """Undo everything one conversation changed in the workspace.

        Only the user reaches this. `rollback_task` is a tool the model may
        call for the task it is running; a whole conversation is a different
        size of action, and nothing the model can say should reach for it.
        """
        wanted = str(session_id or "").strip()
        if not wanted:
            return {"ok": False, "error": "Name the conversation to undo."}
        task_ids = self.agent.db.tasks_for_session(wanted)
        if not task_ids:
            # Said plainly rather than reported as success. A conversation from
            # before session ids were recorded genuinely cannot be undone this
            # way, and matching it by timestamp would be a guess.
            return {"ok": False, "error": (
                "Nothing in this conversation can be undone. Either it changed "
                "no files, or it happened before Aura recorded which "
                "conversation each task belonged to.")}
        preview = self.agent.db.undoable_paths_for_tasks(task_ids)
        if not confirm:
            return {"ok": True, "confirm_needed": True, "tasks": len(task_ids),
                    "paths": preview}
        result = self.agent.sandbox.rollback_session(task_ids)
        self.agent.log.record("undo_session", "ok", session_id=wanted,
                              tasks_undone=result["tasks_undone"],
                              tasks_skipped=result["tasks_skipped"],
                              changes_undone=result["changes_undone"])
        self._push("memory_changed", action="undone")
        return {"ok": True, **result}

    def self_check(self) -> dict:
        """One answer to "is anything broken?", for the user and for Aura.

        Read-only apart from a workspace write probe, so it is safe to run at
        any moment, including while something else is going on.
        """
        report = health.run(self.agent, speech=self.speech, voice=self.voice,
                            search_service=self.search_service)
        # `verdict`, not `status`: record() already takes status positionally, and
        # passing both made the call fail only when it was actually used.
        self.agent.log.record("self_check", "ok", verdict=report["status"],
                              failed=report["failed"], warned=report["warned"])
        return {"ok": True, **report}

    def search_service_status(self) -> dict:
        return {"ok": True, **self.search_service.status()}

    def _deliver_reminder(self, task: dict) -> str:
        """Say the reminder, in the conversation, as Aura.

        It lands in the durable session history rather than only on screen, so
        a reminder that arrives while the window is closed is still there when
        it is opened — and it reads as something she said, because it is.
        """
        text = str(task.get("request", "")).strip() or "You asked me to remind you about something."
        spoken = f"Reminder: {text}"
        self.agent._remember("assistant", spoken)
        self._push("reply", text=spoken, streamed=False, reminder=True)
        self._push("state", value="idle")
        if self.speech.enabled:
            threading.Thread(target=lambda: self.speech.speak(spoken),
                             daemon=True, name="aura-reminder-voice").start()
        return f"delivered: {text[:80]}"

    def _run_check(self, task: dict) -> str:
        """Run one read-only check and speak only if it found something.

        Silence is the normal outcome and is recorded as such. A recurring check
        that says "all fine" every day teaches its reader to skip it, and then
        the once it matters it gets skipped too.
        """
        wanted = str(task.get("request", ""))
        check = checks.get(wanted)
        if check is None:
            raise ValueError(f"unknown check {wanted!r}")
        finding = check.run(self.agent)
        if not finding:
            return f"{wanted}: nothing to report"
        spoken = f"While you were away — {finding.message}"
        proposal = None
        if finding.proposal:
            # Stored, not done. The whole phase rests on this: an approval has
            # to be given for a situation the user has actually seen, and a
            # background run has not been seen by anyone.
            proposal = (self.agent.db.pending_proposal_for(wanted, finding.proposal)
                        or self.agent.db.add_proposal(wanted, finding.message, finding.proposal))
            spoken += '\n\nI can fix that if you want: ' + finding.proposal
        self.agent._remember("assistant", spoken)
        self._push("reply", text=spoken, streamed=False, check=wanted,
                   proposal=proposal)
        return f"{wanted}: {finding.message[:120]}"

    def list_proposals(self) -> dict:
        return {"ok": True, "proposals": self.agent.db.proposals("pending")}

    def approve_proposal(self, proposal_id: str) -> dict:
        """Run a proposal now, in the foreground, as if the user had typed it.

        Deliberately not a special execution path: it goes through `submit`, so
        every completion gate, approval dialog, and recoverable snapshot applies
        exactly as it would to any other request.
        """
        proposal = self.agent.db.proposal(str(proposal_id))
        if not proposal or proposal.get("status") != "pending":
            return {"ok": False, "error": "That proposal is no longer waiting."}
        started = self.submit(str(proposal["request"]))
        if not started.get("ok"):
            return started
        self.agent.db.decide_proposal(str(proposal_id), "approved")
        self.agent.log.record("approve_proposal", "ok", proposal_id=str(proposal_id),
                              source=proposal.get("source"))
        self._push("proposals_changed", pending=len(self.agent.db.proposals("pending")))
        return {"ok": True, "request": proposal["request"]}

    def dismiss_proposal(self, proposal_id: str) -> dict:
        proposal = self.agent.db.proposal(str(proposal_id))
        if not proposal or proposal.get("status") != "pending":
            return {"ok": False, "error": "That proposal is no longer waiting."}
        self.agent.db.decide_proposal(str(proposal_id), "dismissed")
        self.agent.log.record("dismiss_proposal", "ok", proposal_id=str(proposal_id))
        self._push("proposals_changed", pending=len(self.agent.db.proposals("pending")))
        return {"ok": True}

    def _seed_default_checks(self) -> None:
        """Switch on a small, quiet default set — once, and only once.

        Everything in phase 48 was built and nothing was ever scheduled, so Aura
        could watch and never did. The flag is what makes this safe: a default
        the user switches off must not reappear on the next launch, which is the
        classic way a helpful default becomes an annoyance.
        """
        seeded = list(self.agent.config.data.get("seeded_checks") or [])
        if not seeded and self.agent.config.data.get("default_checks_seeded"):
            # An install from before this was tracked by name. The two it was
            # given then are recorded so they are not offered a second time.
            seeded = ["broken_links", "recent_failures"]
        fresh = [name for name in checks.DEFAULT_CHECKS
                 if name not in seeded and checks.get(name) is not None]
        if not fresh:
            return
        first = datetime.now(timezone.utc) + timedelta(hours=2)
        for name in fresh:
            self.agent.db.add_scheduled("check", name,
                                        every_minutes=checks.DEFAULT_EVERY_MINUTES,
                                        next_run=first.isoformat())
        self.agent.config.update(default_checks_seeded=True,
                                 seeded_checks=sorted(set(seeded) | set(fresh)))
        self.agent.log.record("seed_default_checks", "ok", checks=fresh)

    def set_check_enabled(self, name: str, enabled: bool = True) -> dict:
        """Turn one check on or off. Only the user calls this."""
        wanted = str(name)
        if checks.get(wanted) is None:
            return {"ok": False, "error": f"There is no check called {wanted!r}."}
        existing = [task for task in self.agent.db.scheduled_tasks(include_disabled=False)
                    if task.get("kind") == "check" and task.get("request") == wanted]
        if enabled and not existing:
            due = datetime.now(timezone.utc) + timedelta(minutes=5)
            self.agent.db.add_scheduled("check", wanted,
                                        every_minutes=checks.DEFAULT_EVERY_MINUTES,
                                        next_run=due.isoformat())
        elif not enabled:
            for task in existing:
                self.agent.db.delete_scheduled_task(task["id"])
        self.agent.log.record("set_check_enabled", "ok", check=wanted, enabled=bool(enabled))
        return self.list_scheduled()

    def list_scheduled(self) -> dict:
        """Everything waiting to happen on its own, reminders and checks alike."""
        tasks = self.agent.db.scheduled_tasks(include_disabled=False)
        watching = {task["request"] for task in tasks if task.get("kind") == "check"}
        return {"ok": True, "scheduled": tasks,
                "reminders": [task for task in tasks if task.get("kind") == "reminder"],
                "available_checks": [{"name": name,
                                      "description": checks.get(name).description,
                                      "enabled": name in watching,
                                      "next_run": next((task["next_run"] for task in tasks
                                                        if task.get("request") == name), None)}
                                     for name in checks.names()]}

    def cancel_scheduled(self, task_id: str) -> dict:
        task = self.agent.db.scheduled_task(str(task_id))
        if not task:
            return {"ok": False, "error": "That is no longer scheduled."}
        self.agent.db.delete_scheduled_task(str(task_id))
        self.agent.log.record("cancel_scheduled", "ok", task_id=str(task_id),
                              kind=task.get("kind"))
        return {"ok": True}

    def list_reminders(self) -> dict:
        reminders = [task for task in self.agent.db.scheduled_tasks(include_disabled=False)
                     if task.get("kind") == "reminder"]
        return {"ok": True, "reminders": reminders}

    def cancel_reminder(self, reminder_id: str) -> dict:
        task = self.agent.db.scheduled_task(str(reminder_id))
        if not task or task.get("kind") != "reminder":
            return {"ok": False, "error": "That reminder no longer exists."}
        self.agent.db.delete_scheduled_task(str(reminder_id))
        self.agent.log.record("cancel_reminder", "ok", reminder_id=str(reminder_id))
        return {"ok": True}

    def _is_busy(self) -> bool:
        """Is a user request in flight? Background work waits for it."""
        with self._state_lock:
            return self._busy or self._voice_active

    def _push(self, event_type: str, **payload: Any) -> None:
        if not self._closing:
            with self._event_lock:
                self._event_sequence += 1
                self.events.append({"type": event_type, "_seq": self._event_sequence, **payload})

    def _on_log(self, event: dict) -> None:
        self._push("log", event=event)

    def _session_actions(self, limit: int = 60) -> list[dict]:
        """Return only events produced by this Aura process.

        The JSONL log remains durable for diagnostics, but the everyday activity
        panel should not reopen with failures from an unrelated older session.
        """
        actions: list[dict] = []
        for event in self.agent.log.recent(250):
            try:
                occurred = datetime.fromisoformat(str(event.get("time", "")))
                if occurred.tzinfo is None:
                    occurred = occurred.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if occurred >= self._session_started:
                actions.append(event)
        return actions[-max(1, min(int(limit), 100)):]

    def get_bootstrap(self) -> dict:
        config = self.agent.config.data
        provider = self.agent.provider
        conversation = list(self.agent.memory.data.get("conversation", []))[-16:]
        with self._event_lock:
            event_cursor = self._event_sequence
        return {
            "app": "Aura",
            "version": __version__,
            "onboarded": bool(config["onboarded"]),
            "network": self.network_status()["network"],
            "autonomy": self.autonomy_status()["autonomy"],
            "session_id": self.agent.session_id,
            "workspace": str(self.agent.sandbox.root),
            "conversation": conversation,
            "actions": self._session_actions(60),
            "event_cursor": event_cursor,
            "provider": {
                "label": getattr(provider, "model", None) or "auto model",
                "online": None,
            },
            "speech_enabled": self.speech.enabled,
            "voice": {
                **self.voice.capabilities(),
                "device": str(config["voice_device"]),
                "calibrated": float(config["voice_noise_floor"]) > 0,
            },
            "avatar": {
                "motion": str(config["avatar_motion"]),
                "intensity": int(config["avatar_intensity"]),
                "quality": str(config["avatar_quality"]),
            },
            "capabilities": {
                "tools": len(self.agent.tool_definitions()),
                "reasoning_depth": config["reasoning_depth"],
                "autonomy_mode": config["autonomy_mode"],
                "personal_memories": len(self.agent.memory.profile_memories()),
            },
            "ui": {
                "sidebar_width": int(config["web_sidebar_width"]),
                "log_height": int(config["web_log_height"]),
                "log_visible": bool(config["web_log_visible"]),
            },
        }

    def poll_events(self, after: int | None = None, limit: int | None = None) -> list[dict]:
        # Backward compatibility: the original HTML client supplied one
        # argument meaning `limit`. The cursor-aware client supplies two.
        legacy = limit is None
        if legacy:
            requested_limit = 100 if after is None else int(after)
            cursor = self._legacy_event_cursor
        else:
            requested_limit = int(limit)
            cursor = max(0, int(after or 0))
        count = max(1, min(requested_limit, 250))
        with self._event_lock:
            items = [dict(event) for event in self.events
                     if int(event.get("_seq", 0)) > cursor][:count]
            if legacy and items:
                self._legacy_event_cursor = int(items[-1]["_seq"])
        return items

    def submit(self, message: str) -> dict:
        text = str(message).strip()
        if not text:
            return {"ok": False, "error": "Write a message first."}
        if len(text) > 12_000:
            return {"ok": False, "error": "Messages are limited to 12,000 characters."}
        with self._state_lock:
            if self._busy:
                return {"ok": False, "error": "Aura is already working. Stop the current task first."}
            self._busy = True
        self._push("user_message", text=text)
        self._push("busy", value=True)
        threading.Thread(target=self._work, args=(text,), daemon=True, name="aura-agent").start()
        return {"ok": True}

    def list_sessions(self, limit: int = 30, include_archived: bool = False) -> dict:
        return {"ok": True, "current": self.agent.session_id,
                "sessions": self.agent.db.sessions(int(limit), bool(include_archived))}

    def new_session(self) -> dict:
        with self._state_lock:
            if self._busy:
                return {"ok": False, "error": "Finish or stop the current task first."}
        session_id = self.agent.new_session()
        self._push("session_changed", session_id=session_id, conversation=[])
        return {"ok": True, "session_id": session_id, "conversation": []}

    def open_session(self, session_id: str) -> dict:
        with self._state_lock:
            if self._busy:
                return {"ok": False, "error": "Finish or stop the current task first."}
        try:
            messages = self.agent.open_session(str(session_id))
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        self._push("session_changed", session_id=self.agent.session_id,
                   conversation=messages)
        return {"ok": True, "session_id": self.agent.session_id, "conversation": messages}

    def search_conversations(self, query: str, include_archived: bool = False) -> dict:
        return {"ok": True, "current": self.agent.session_id, "query": str(query),
                "sessions": self.agent.db.search_messages(
                    str(query), 20, bool(include_archived))}

    def export_conversation(self, session_id: str) -> dict:
        """Write one conversation into the workspace as readable Markdown."""
        try:
            messages = self.agent.db.session_messages(str(session_id), 10000)
            if not messages:
                return {"ok": False, "error": "That conversation has no messages."}
            listed = {item["id"]: item
                      for item in self.agent.db.sessions(200, include_archived=True)}
            title = (listed.get(str(session_id)) or {}).get("title") or "Conversation"
            lines = [f"# {title}", "",
                     f"Exported {datetime.now().strftime('%Y-%m-%d %H:%M')} "
                     f"· {len(messages)} messages · local only", ""]
            for item in messages:
                who = "You" if item["role"] == "user" else "Aura"
                lines.append(f"**{who}** · {item['time']}")
                lines.append("")
                lines.append(str(item["text"]).rstrip())
                lines.append("")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.agent.sandbox.write_file(
                f"aura-conversation-{stamp}.md", "\n".join(lines))
            path = target.relative_to(self.agent.sandbox.root).as_posix()
            self.agent.log.record("export_conversation", "ok", path=path,
                                  session_id=str(session_id), messages=len(messages))
            return {"ok": True, "path": path, "messages": len(messages)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def archive_session(self, session_id: str, archived: bool = True) -> dict:
        """Hide a conversation from the list. Nothing is deleted; it comes back
        with `Show archived`."""
        if archived and str(session_id) == self.agent.session_id:
            return {"ok": False,
                    "error": "Start a new conversation before archiving this one."}
        self.agent.db.archive_session(str(session_id), bool(archived))
        return {"ok": True}

    def resume_task(self, task_id: str) -> dict:
        """Continue an unfinished task as a fresh task grounded in real state.

        Approvals are deliberately not carried over: the new task asks again for
        anything that needs permission, exactly as a new request would.
        """
        try:
            brief = self.agent.resume_brief(str(task_id))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        request = self.agent.format_resume_request(brief)
        started = self.submit(request)
        if started.get("ok"):
            self.agent.log.record("resume_task", "ok", task_id=brief["task_id"],
                                  completed_steps=len(brief["completed"]),
                                  outstanding=len(brief["outstanding"]))
        return {**started, "resumed": brief}

    def _on_agent_state(self, name: str) -> None:
        """Forward agent states, translating the retry signal for the interface.

        "retry" is not a mood: it means the reply streamed so far is being
        thrown away, so the browser must clear it rather than append the next
        attempt underneath.
        """
        if name == "retry":
            self._push("stream_reset")
            self._push("state", value="working")
            return
        self._push("state", value=name)

    def _work(self, text: str) -> None:
        streamed: list[str] = []

        def on_token(piece: str) -> None:
            streamed.append(piece)
            self._push("stream_token", text=piece)

        try:
            self._plan_steps = []
            self.agent.on_tool = self._on_tool
            try:
                response = self.agent.handle(
                    text,
                    approve=self._approve_command,
                    state=self._on_agent_state,
                    token=on_token,
                )
            finally:
                self.agent.on_tool = None
                if self._plan_steps:
                    self._push("plan_finished",
                               done=sum(1 for step in self._plan_steps if step["done"]),
                               total=len(self._plan_steps))
                self._plan_steps = []
            recent = self.agent.tasks.recent(1)
            recalled = [{"value": item.get("value"), "category": item.get("category"),
                         "recall_reason": item.get("recall_reason")}
                        for item in self.agent.last_recalled if item.get("recall_reason")]
            self._push("reply", text=response, streamed=bool(streamed),
                       task=recent[0] if recent else None, recalled=recalled)
            if language.looks_finnish(response) and language.detect(text) == "et":
                # The model drifts to Finnish when it is asked in Estonian, and a
                # reply in the wrong language reads as Aura being confused rather
                # than as the model slipping. Recorded so the pattern is visible
                # in diagnostics, and said once so it is not just endured.
                self.agent.log.record("wrong_language", "error", expected="et",
                                      looked_like="fi")
                self._push("wrong_language", expected="Estonian", looked_like="Finnish")
            if self.agent.last_learned:
                self._push("memory_learned", memories=self.agent.last_learned)
            selected = getattr(self.agent.provider, "model", None)
            if selected:
                self._push("provider", online=True, label=selected)
            if self.speech.enabled:
                # Aura answers in the language she was asked in, so the
                # request settles what a short reply is: "Eesti pealinn on
                # Tallinn" has no Estonian letter and no giveaway word.
                asked_in = language.detect(text)
                spoken_language = language.detect(response, default=asked_in)

                def speak() -> None:
                    self._push("speech", active=True)
                    try:
                        self.speech.speak(
                            response,
                            on_cues=lambda payload: self._push("speech_cues", **payload),
                            language=spoken_language,
                        )
                        if not self.speech.last_language_covered:
                            # Said once per reply rather than swallowed: an
                            # English voice reading Estonian is not a subtle
                            # defect, and the user should know it is the missing
                            # voice rather than Aura being broken.
                            self._push("speech_language", language=spoken_language,
                                       covered=False)
                    finally:
                        self._push("speech", active=False)
                threading.Thread(target=speak, daemon=True, name="aura-speech").start()
        except Exception as exc:  # Defensive boundary around the worker itself.
            self._push("reply", text=f"I couldn’t complete that safely: {exc}", streamed=False)
            self._push("state", value="error")
        finally:
            with self._approval_lock:
                self._task_approved_exact.clear()
            with self._state_lock:
                self._busy = False
            self._push("busy", value=False)

    def stop(self) -> dict:
        self.agent.cancel_current()
        self.speech.stop()
        self.voice.stop()
        self._deny_pending_approvals()
        self._push("speech", active=False)
        self._push("voice_session", phase="cancelled", message="Stopped.")
        self._push("state", value="working")
        return {"ok": True}

    #: Tools whose success means a planned file now exists. Anything else can
    #: mention a path without producing it, and a plan that ticks on intention
    #: rather than on result is worse than no plan at all.
    PLAN_TOOLS = frozenset({"create_file", "write_file", "write_files", "append_file"})

    def _start_plan(self, plan: str) -> None:
        """Remember an approved plan so the interface can follow it.

        The plan used to be shown once, in the dialog that asked for it, and
        then vanish — leaving a spinner and a log to answer "how far along is
        this?".
        """
        steps = []
        for line in str(plan or "").splitlines():
            text = line.strip(" -*\t")
            if not text:
                continue
            path = text.split(" - ")[0].strip()
            steps.append({"path": path, "text": text, "done": False})
        self._plan_steps = steps[:20]
        if self._plan_steps:
            self._push("plan_started", steps=self._plan_steps)

    def _on_tool(self, name: str, arguments: dict, succeeded: bool) -> None:
        if not succeeded or not getattr(self, "_plan_steps", None):
            return
        if name not in self.PLAN_TOOLS:
            return
        written = []
        if isinstance(arguments.get("files"), list):
            written = [str(item.get("path", "")) for item in arguments["files"]
                       if isinstance(item, dict)]
        elif arguments.get("path"):
            written = [str(arguments["path"])]
        changed = False
        for step in self._plan_steps:
            if step["done"]:
                continue
            if any(path and (path == step["path"] or path.endswith(step["path"])
                             or step["path"].endswith(path)) for path in written):
                step["done"] = True
                changed = True
        if changed:
            self._push("plan_progress", steps=self._plan_steps,
                       done=sum(1 for step in self._plan_steps if step["done"]),
                       total=len(self._plan_steps))

    def _approve_command(self, command: list[str]) -> bool:
        """Ask, and if a plan was approved, start following it."""
        approved = self._ask_approval(command)
        if approved and len(command) > 1 and command[0] == "PLAN":
            self._start_plan(command[1])
        return approved

    def _ask_approval(self, command: list[str]) -> bool:
        task_id = self.agent.current_task_id or "active"
        exact_key = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        with self._approval_lock:
            if exact_key in self._task_approved_exact.get(task_id, set()):
                return True
        approval_id = uuid4().hex
        reply: queue.Queue[str] = queue.Queue(maxsize=1)
        with self._approval_lock:
            self._approvals[approval_id] = reply
        self._push("approval", approval_id=approval_id, command=command, task_id=task_id)
        while not self._closing:
            try:
                decision = reply.get(timeout=0.25)
                if decision == "exact_task":
                    with self._approval_lock:
                        self._task_approved_exact.setdefault(task_id, set()).add(exact_key)
                    return True
                return decision == "once"
            except queue.Empty:
                if self.agent.cancel_event.is_set():
                    break
        with self._approval_lock:
            self._approvals.pop(approval_id, None)
        return False

    def resolve_approval(self, approval_id: str, allowed: bool, scope: str = "once") -> dict:
        with self._approval_lock:
            reply = self._approvals.pop(str(approval_id), None)
        if reply is None:
            return {"ok": False, "error": "That approval is no longer active."}
        decision = "deny" if not allowed else ("exact_task" if str(scope) == "exact_task" else "once")
        reply.put(decision)
        return {"ok": True}

    def _deny_pending_approvals(self) -> None:
        """Refuse everything waiting, and tell the browser so.

        Unblocking the waiting thread is only half of it: without the event the
        card stayed on screen with nothing behind it, and because the client
        still believed an approval was live, Escape went to answering that
        instead of closing anything. Pressing Stop left a dead dialog.
        """
        with self._approval_lock:
            pending = list(self._approvals.items())
            self._approvals.clear()
        for approval_id, reply in pending:
            try:
                reply.put_nowait("deny")
            except queue.Full:
                pass
            self._push("approval_closed", approval_id=approval_id)


    NEURAL_VOICE_FOLDER = "aura-voices"



    def recent_tasks(self, limit: int = 10) -> dict:
        tasks = self.agent.tasks.recent(max(1, min(int(limit), 20)), only_actionable=True,
                                        active_task_id=self.agent.current_task_id)
        return {"ok": True, "tasks": tasks}

    def rollback_task(self, task_id: str) -> dict:
        with self._state_lock:
            if self._busy:
                return {"ok": False, "error": "Stop the current task before rolling back another one."}
            self._busy = True
        cleaned = str(task_id).strip()
        control_id = self.agent.tasks.start(f"Rollback task {cleaned}")
        try:
            result = self.agent.sandbox.rollback_task(cleaned)
            summary = (f"Rolled back {result['changes_undone']} change(s) from task {cleaned}. "
                       f"Affected paths: {', '.join(result['paths'])}")
            self.agent.tasks.finish(control_id, "completed", summary)
            self.agent.log.record("rollback_task", task_id=cleaned,
                                  changes=result["changes_undone"])
            return {"ok": True, "summary": summary, **result}
        except Exception as exc:
            self.agent.tasks.finish(control_id, "error", str(exc))
            return {"ok": False, "error": str(exc)}
        finally:
            with self._state_lock:
                self._busy = False

    def get_mind_graph(self) -> dict:
        try:
            self.agent.memory.load()
            nodes, edges = build_mind_graph(
                self.agent.memory.data,
                self.agent.tasks.recent(10),
                self.agent.sandbox.list_files(),
            )
            return {
                "ok": True,
                "nodes": [asdict(node) for node in nodes],
                "edges": [asdict(edge) for edge in edges],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "nodes": [], "edges": []}


    DIAGNOSTIC_SETTINGS = (
        "lm_studio_url", "model", "timeout", "temperature", "max_tokens",
        "reasoning_depth", "autonomy_mode", "learn_from_conversations",
        "vision_mode", "speech_engine", "voice_engine", "avatar_quality",
    )

    def export_diagnostics(self) -> dict:
        """Write one readable file describing how this installation is behaving.

        It reports settings, storage, permissions, and what recently failed —
        never conversation text, memory content, or file contents, so it can be
        shared without handing over anything private. Nothing is uploaded; the
        file is written into the workspace and stays there.
        """
        try:
            database = self.agent.db.summary()
            settings = {key: self.agent.config.data.get(key)
                        for key in self.DIAGNOSTIC_SETTINGS}
            failures = self.agent.db.failed_actions(20)
            tasks = self.agent.tasks.recent(10)
            grants = self.agent.permissions.active()
            sweeps = [event for event in self.agent.db.recent_actions(400)
                      if event.get("action") in {"retention_sweep", "store_migrated"}]

            lines = ["# Aura diagnostics", "",
                     f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · local only",
                     "", "## This machine", "",
                     f"- Aura: {__version__}",
                     f"- Platform: {platform.platform()}",
                     f"- Python: {platform.python_version()}",
                     f"- SQLite: {sqlite3.sqlite_version}",
                     f"- Workspace: `{self.agent.sandbox.root}`",
                     "", "## Settings", ""]
            for key in self.DIAGNOSTIC_SETTINGS:
                lines.append(f"- {key}: `{settings[key]}`")

            lines += ["", "## Storage", "",
                      f"- Database: {database['bytes'] / 1024:.1f} KB",
                      f"- Undone changes: {database['undone_changes']}"]
            for table, total in database["counts"].items():
                lines.append(f"- {table}: {total} rows")

            lines += ["", "## Retention", ""]
            lines += ([f"- {event['time']} · {event['action']} · "
                       + ", ".join(f"{key}={value}" for key, value in event.items()
                                   if key not in {"time", "action", "status"})
                       for event in sweeps[-5:]] or ["- Nothing swept yet."])

            lines += ["", "## Folder permissions", ""]
            lines += ([f"- `{grant.get('root')}` · {grant.get('capability')} · "
                       f"{grant.get('mode')}" for grant in grants]
                      or ["- None. Aura can reach only its own workspace."])

            lines += ["", "## Recent tasks", ""]
            lines += ([f"- {task.get('status')} · {task.get('task_id')} · "
                       f"{len(task.get('tools') or [])} tools" for task in tasks]
                      or ["- No tasks yet."])

            lines += ["", "## What recently failed", ""]
            lines += ([f"- {event['time']} · {event['action']} · {event['status']}"
                       + (f" · {event.get('error')}" if event.get("error") else "")
                       for event in failures] or ["- Nothing has failed."])

            lines += ["", "## Not included", "",
                      "Conversation text, personal memories, and file contents are "
                      "deliberately left out of this report.", ""]

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.agent.sandbox.write_file(
                f"aura-diagnostics-{stamp}.md", "\n".join(lines))
            path = target.relative_to(self.agent.sandbox.root).as_posix()
            self.agent.log.record("export_diagnostics", "ok", path=path,
                                  failures=len(failures))
            return {"ok": True, "path": path, "failures": len(failures)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def export_personal_memory(self) -> dict:
        """Write every stored memory into the workspace as readable JSON."""
        try:
            memories = self.agent.memory.profile_memories()
            payload = {
                "exported": datetime.now(timezone.utc).isoformat(),
                "name": self.agent.memory.data.get("name"),
                "count": len(memories),
                "memories": memories,
            }
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.agent.sandbox.write_file(
                f"aura-memory-export-{stamp}.json",
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            path = target.relative_to(self.agent.sandbox.root).as_posix()
            self.agent.log.record("export_personal_memory", "ok",
                                  path=path, count=len(memories))
            return {"ok": True, "path": path, "count": len(memories)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def revert_personal_memory(self, memory_id: str) -> dict:
        try:
            item = self.agent.memory.revert_profile_memory(str(memory_id))
            self.agent.log.record("revert_personal_fact", "ok", memory_id=item["id"],
                                  category=item["category"], value=item["value"])
            self._push("memory_changed", action="reverted", memory=item)
            return {"ok": True, "memory": item}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def update_personal_memory(self, memory_id: str, values: dict) -> dict:
        try:
            if not isinstance(values, dict):
                raise ValueError("Memory update must be an object")
            item = self.agent.memory.update_profile_memory(
                str(memory_id),
                value=str(values["value"]) if "value" in values else None,
                category=str(values["category"]) if "category" in values else None,
                pinned=bool(values["pinned"]) if "pinned" in values else None,
                project=str(values["project"]) if "project" in values else None,
            )
            self.agent.log.record("update_personal_fact", "ok", memory_id=item["id"],
                                  category=item["category"], value=item["value"],
                                  project=item.get("project"))
            self._push("memory_changed", action="updated", memory=item)
            return {"ok": True, "memory": item}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def forget_personal_memory(self, memory_id: str) -> dict:
        try:
            item = self.agent.memory.forget_profile_memory(str(memory_id))
            self.agent.log.record("forget_personal_fact", "ok", memory_id=item["id"],
                                  category=item.get("category"), value=item.get("value"))
            self._push("memory_changed", action="forgotten", memory_id=item["id"])
            return {"ok": True, "memory": item}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def save_ui_state(self, values: dict) -> dict:
        try:
            updates: dict[str, object] = {}
            if "sidebar_width" in values:
                updates["web_sidebar_width"] = max(190, min(int(values["sidebar_width"]), 420))
            if "log_height" in values:
                updates["web_log_height"] = max(90, min(int(values["log_height"]), 420))
            if "log_visible" in values:
                updates["web_log_visible"] = bool(values["log_visible"])
            if updates:
                self.agent.config.update(**updates)
            return {"ok": True}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def shutdown(self) -> None:
        self._closing = True
        self.scheduler.stop()
        self.agent.cancel_current()
        self.speech.stop()
        self.voice.stop()
        self.preview_server.stop_if_running()
        self.search_service.stop()
        self._deny_pending_approvals()
