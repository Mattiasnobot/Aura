from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections import deque
import queue
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from .agent import AuraAgent
from .graph_model import build_mind_graph
from .permissions import PermissionRefused
from .preview_server import PreviewServer
from .provider import LMStudioProvider
from .speech import SpeechOutput
from .validation import check_broken_assets
from .voice import VoiceInput


class AuraWebBridge:
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
        self._task_approved_exact: dict[str, set[str]] = {}
        self._approval_lock = threading.Lock()
        self.preview_server = PreviewServer(self.agent.sandbox, self.agent.log)

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
            response = self.agent.handle(
                text,
                approve=self._approve_command,
                state=self._on_agent_state,
                token=on_token,
            )
            recent = self.agent.tasks.recent(1)
            recalled = [{"value": item.get("value"), "category": item.get("category"),
                         "recall_reason": item.get("recall_reason")}
                        for item in self.agent.last_recalled if item.get("recall_reason")]
            self._push("reply", text=response, streamed=bool(streamed),
                       task=recent[0] if recent else None, recalled=recalled)
            if self.agent.last_learned:
                self._push("memory_learned", memories=self.agent.last_learned)
            selected = getattr(self.agent.provider, "model", None)
            if selected:
                self._push("provider", online=True, label=selected)
            if self.speech.enabled:
                def speak() -> None:
                    self._push("speech", active=True)
                    try:
                        self.speech.speak(
                            response,
                            on_cues=lambda payload: self._push("speech_cues", **payload),
                        )
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

    def _approve_command(self, command: list[str]) -> bool:
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
        with self._approval_lock:
            pending = list(self._approvals.values())
            self._approvals.clear()
        for reply in pending:
            try:
                reply.put_nowait("deny")
            except queue.Full:
                pass

    def check_provider(self) -> dict:
        def work() -> None:
            try:
                models = self.agent.provider.available_models()
                selected = self.agent.provider.selected_model() if models else None
                self._push("provider", online=True, label=selected or "no model", count=len(models))
            except Exception as exc:
                self._push("provider", online=False, label="offline", error=str(exc))

        threading.Thread(target=work, daemon=True, name="aura-provider-check").start()
        return {"ok": True}

    def get_settings(self) -> dict:
        config = self.agent.config.data
        return {key: config[key] for key in (
            "lm_studio_url", "model", "timeout", "temperature", "max_tokens",
            "reasoning_depth", "autonomy_mode", "learn_from_conversations", "vision_mode",
            "speak_responses", "speech_engine", "speech_voice", "speech_rate", "speech_volume",
            "voice_engine", "voice_device", "voice_language", "voice_calibration_ms",
            "voice_silence_ms", "voice_max_seconds", "voice_noise_floor",
            "whisper_cpp_path", "whisper_model_path",
            "avatar_motion", "avatar_intensity", "avatar_quality",
        )}

    def save_settings(self, values: dict) -> dict:
        with self._state_lock:
            if self._busy or self._voice_active:
                return {"ok": False, "error": "Finish or stop the current task before changing models."}
        try:
            timeout = float(values.get("timeout", 180))
            temperature = float(values.get("temperature", 0.4))
            max_tokens = int(values.get("max_tokens", 4096))
            speech_rate = int(values.get("speech_rate", -1))
            speech_volume = int(values.get("speech_volume", 95))
            voice_engine = str(values.get("voice_engine", "auto"))
            voice_device = str(values.get("voice_device", "")).strip()
            voice_language = str(values.get("voice_language", "en")).strip()
            voice_calibration_ms = int(values.get("voice_calibration_ms", 500))
            voice_silence_ms = int(values.get("voice_silence_ms", 1200))
            voice_max_seconds = int(values.get("voice_max_seconds", 25))
            voice_noise_floor = float(values.get(
                "voice_noise_floor", self.agent.config.data["voice_noise_floor"]))
            whisper_cpp_path = str(values.get("whisper_cpp_path", "")).strip()
            whisper_model_path = str(values.get("whisper_model_path", "")).strip()
            avatar_motion = str(values.get("avatar_motion", "natural"))
            avatar_intensity = int(values.get("avatar_intensity", 65))
            avatar_quality = str(values.get("avatar_quality", "auto"))
            reasoning_depth = str(values.get("reasoning_depth", "deep"))
            autonomy_mode = str(values.get("autonomy_mode", "powerful"))
            vision_mode = str(values.get("vision_mode", "auto")).casefold()
            if vision_mode not in {"auto", "on", "off"}:
                vision_mode = "auto"
            learn_from_conversations = bool(values.get("learn_from_conversations", True))
            if not 5 <= timeout <= 900:
                raise ValueError("Timeout must be between 5 and 900 seconds.")
            if not 0 <= temperature <= 2:
                raise ValueError("Temperature must be between 0 and 2.")
            if not 256 <= max_tokens <= 32768:
                raise ValueError("Maximum response tokens must be between 256 and 32768.")
            if not -10 <= speech_rate <= 10:
                raise ValueError("Speech rate must be between -10 and 10.")
            if not 0 <= speech_volume <= 100:
                raise ValueError("Speech volume must be between 0 and 100.")
            if voice_engine not in {"auto", "pocketsphinx", "whisper_cpp"}:
                raise ValueError("Voice recognition must be automatic, PocketSphinx, or Whisper.cpp.")
            if voice_device and not voice_device.isdigit():
                raise ValueError("Choose a microphone from Aura’s device list.")
            if not re.fullmatch(r"[A-Za-z-]{2,12}", voice_language):
                raise ValueError("Voice language must be a short language code such as en or et.")
            if not 200 <= voice_calibration_ms <= 2000:
                raise ValueError("Microphone calibration must be between 200 and 2000 ms.")
            if not 500 <= voice_silence_ms <= 3000:
                raise ValueError("End-of-speech pause must be between 500 and 3000 ms.")
            if not 5 <= voice_max_seconds <= 60:
                raise ValueError("Maximum listening time must be between 5 and 60 seconds.")
            if not 0 <= voice_noise_floor <= 1:
                raise ValueError("Microphone noise calibration is invalid.")
            if len(whisper_cpp_path) > 500 or len(whisper_model_path) > 500:
                raise ValueError("Whisper.cpp paths are too long.")
            if avatar_motion not in {"calm", "natural", "expressive"}:
                raise ValueError("Avatar motion must be calm, natural, or expressive.")
            if not 0 <= avatar_intensity <= 100:
                raise ValueError("Avatar intensity must be between 0 and 100.")
            if avatar_quality not in {"auto", "high", "low"}:
                raise ValueError("Avatar quality must be automatic, high, or low.")
            if reasoning_depth not in {"fast", "balanced", "deep"}:
                raise ValueError("Reasoning depth must be fast, balanced, or deep.")
            if autonomy_mode not in {"careful", "balanced", "powerful"}:
                raise ValueError("Autonomy must be careful, balanced, or powerful.")
            engine = str(values.get("speech_engine", "piper"))
            if engine not in {"piper", "sapi"}:
                raise ValueError("Speech engine must be Piper or SAPI.")
            provider = LMStudioProvider(
                str(values.get("lm_studio_url", "")).strip(),
                str(values.get("model") or "").strip() or None,
                timeout, temperature, max_tokens,
            )
            enabled = bool(values.get("speak_responses", False))
            voice = str(values.get("speech_voice", "")).strip()
            self.agent.config.update(
                lm_studio_url=provider.base_url, model=provider.model, timeout=timeout,
                temperature=temperature, max_tokens=max_tokens,
                reasoning_depth=reasoning_depth, autonomy_mode=autonomy_mode,
                learn_from_conversations=learn_from_conversations, vision_mode=vision_mode,
                speak_responses=enabled, speech_engine=engine, speech_voice=voice,
                speech_rate=speech_rate, speech_volume=speech_volume,
                voice_engine=voice_engine, voice_device=voice_device,
                voice_language=voice_language, voice_calibration_ms=voice_calibration_ms,
                voice_silence_ms=voice_silence_ms, voice_max_seconds=voice_max_seconds,
                voice_noise_floor=voice_noise_floor, whisper_cpp_path=whisper_cpp_path,
                whisper_model_path=whisper_model_path,
                avatar_motion=avatar_motion, avatar_intensity=avatar_intensity,
                avatar_quality=avatar_quality,
            )
            self.agent.set_provider(provider)
            self.speech.configure(
                enabled=enabled, voice=voice, rate=speech_rate, volume=speech_volume,
                engine=engine, neural_model=str(self.agent.config.data["speech_model"]),
            )
            self.voice.configure(
                engine=voice_engine, device=voice_device, language=voice_language,
                calibration_ms=voice_calibration_ms, silence_ms=voice_silence_ms,
                max_seconds=voice_max_seconds, noise_floor=voice_noise_floor,
                whisper_path=whisper_cpp_path, whisper_model=whisper_model_path,
            )
            self._push("settings_saved")
            self.check_provider()
            return {"ok": True, "tools": len(self.agent.tool_definitions()),
                    "reasoning_depth": reasoning_depth, "autonomy_mode": autonomy_mode,
                    "avatar": {"motion": avatar_motion, "intensity": avatar_intensity,
                               "quality": avatar_quality},
                    "voice": self.voice.capabilities()}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def get_models(self, url: str) -> dict:
        try:
            provider = LMStudioProvider(str(url).strip(), timeout=10)
            return {"ok": True, "models": provider.available_models()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "models": []}

    def get_voices(self) -> dict:
        return {"ok": True, "voices": SpeechOutput.installed_voices()}

    def get_microphones(self) -> dict:
        return {
            "ok": True,
            "devices": self.voice.devices(),
            "capabilities": self.voice.capabilities(),
            "selected": str(self.agent.config.data["voice_device"]),
        }

    def start_voice(self, mode: str = "toggle") -> dict:
        capture_mode = "hold" if str(mode) == "hold" else "toggle"
        with self._state_lock:
            if self._busy or self._voice_active:
                return {"ok": False, "error": "Aura is already listening or working."}
            self._voice_active = True
        # Speaking to Aura always interrupts her current local reply first.
        self.speech.stop()
        self._push("speech", active=False)
        self._push("voice_session", phase="starting", mode=capture_mode,
                   message="Opening the selected microphone…")
        self._push("state", value="listening")

        def work() -> None:
            last_level_at = 0.0

            def on_level(level: float, rms: float) -> None:
                nonlocal last_level_at
                now = time.monotonic()
                if now - last_level_at >= 0.075:
                    last_level_at = now
                    self._push("voice_level", level=round(level, 3), rms=round(rms, 6))

            def on_partial(text: str) -> None:
                self._push("voice_partial", text=text)

            def on_status(phase: str) -> None:
                messages = {
                    "calibrating": "Listening to the room for a moment…",
                    "listening": "Listening locally — release to send.",
                    "processing": "Transcribing locally…",
                }
                self._push("voice_session", phase=phase, mode=capture_mode,
                           message=messages.get(phase, phase.title()))

            try:
                ok, text = self.voice.listen(
                    mode=capture_mode, on_level=on_level,
                    on_partial=on_partial, on_status=on_status,
                )
                if ok:
                    self._push("voice_text", text=text)
                    self._push("voice_session", phase="recognized", text=text,
                               message="Heard clearly. Sending to Aura…")
                    submitted = self.submit(text)
                    if not submitted.get("ok"):
                        self._push("voice_error", message=submitted.get("error", "Could not send speech."))
                        self._push("state", value="idle")
                elif "cancel" in text.lower():
                    self._push("voice_session", phase="cancelled", message="Voice input cancelled.")
                    self._push("state", value="idle")
                else:
                    self._push("voice_error", message=text, retry=True)
                    self._push("voice_session", phase="error", message=text)
                    self._push("state", value="idle")
            except Exception as exc:
                message = f"Voice input stopped safely: {exc}"
                self._push("voice_error", message=message, retry=True)
                self._push("voice_session", phase="error", message=message)
                self._push("state", value="idle")
            finally:
                with self._state_lock:
                    self._voice_active = False
                self._push("voice_level", level=0.0, rms=0.0)

        threading.Thread(target=work, daemon=True, name="aura-voice").start()
        return {"ok": True, "mode": capture_mode}

    def stop_voice(self, cancel: bool = False) -> dict:
        with self._state_lock:
            active = self._voice_active
        if not active:
            return {"ok": True, "active": False}
        self.voice.request_stop(cancel=bool(cancel))
        phase = "cancelled" if cancel else "processing"
        message = "Voice input cancelled." if cancel else "Finishing local transcription…"
        self._push("voice_session", phase=phase, message=message)
        if cancel:
            self._push("state", value="idle")
        return {"ok": True, "active": True}

    def calibrate_voice(self, device: str = "") -> dict:
        with self._state_lock:
            if self._busy or self._voice_active:
                return {"ok": False, "error": "Finish the current task or voice session first."}
            self._voice_active = True
        selected_device = str(device).strip()
        if selected_device and not selected_device.isdigit():
            with self._state_lock:
                self._voice_active = False
            return {"ok": False, "error": "Choose a microphone from Aura’s device list."}
        self.voice.device = selected_device
        self.agent.config.update(voice_device=selected_device)
        self.speech.stop()
        self._push("speech", active=False)
        self._push("state", value="listening")
        self._push("voice_session", phase="calibrating", message="Stay quiet for a moment…")

        def work() -> None:
            try:
                ok, result = self.voice.calibrate(
                    on_level=lambda level, rms: self._push(
                        "voice_level", level=round(level, 3), rms=round(rms, 6)))
                if ok and isinstance(result, dict):
                    self.agent.config.update(voice_noise_floor=float(result["noise_floor"]))
                    self._push("voice_calibration", **result)
                    self._push("voice_session", phase="calibrated",
                               message="Microphone calibrated for this room.")
                else:
                    self._push("voice_error", message=str(result), retry=True)
                    self._push("voice_session", phase="error", message=str(result))
            except Exception as exc:
                message = f"Calibration stopped safely: {exc}"
                self._push("voice_error", message=message, retry=True)
                self._push("voice_session", phase="error", message=message)
            finally:
                with self._state_lock:
                    self._voice_active = False
                self._push("voice_level", level=0.0, rms=0.0)
                self._push("state", value="idle")

        threading.Thread(target=work, daemon=True, name="aura-voice-calibration").start()
        return {"ok": True}

    def preview_voice(self) -> dict:
        with self._state_lock:
            if self._voice_active:
                return {"ok": False, "error": "Finish the microphone session before previewing Aura’s voice."}
        self.speech.stop()

        def speak() -> None:
            was_enabled = self.speech.enabled
            self.speech.enabled = True
            self._push("speech", active=True, preview=True)
            try:
                self.speech.speak(
                    "Hello. I’m Aura. I’m here, listening, and ready to create with you.",
                    on_cues=lambda payload: self._push("speech_cues", **payload),
                )
            finally:
                self._push("speech", active=False, preview=True)
                self.speech.enabled = was_enabled

        threading.Thread(target=speak, daemon=True, name="aura-voice-preview").start()
        return {"ok": True}

    def open_workspace(self) -> dict:
        try:
            os.startfile(self.agent.sandbox.root)  # type: ignore[attr-defined]
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def workspace_snapshot(self) -> dict:
        try:
            files = []
            for relative in self.agent.sandbox.list_files():
                target = self.agent.sandbox.path(relative)
                stat = target.stat()
                suffix = target.suffix.casefold()
                preview_kind = "text" if suffix in self.TEXT_PREVIEW_SUFFIXES else (
                    "image" if suffix in self.IMAGE_PREVIEW_SUFFIXES else "binary")
                if suffix in {".html", ".htm", ".svg"}:
                    preview_kind = "rendered"
                files.append({
                    "path": relative,
                    "name": target.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "suffix": suffix,
                    "preview_kind": preview_kind,
                })
            return {"ok": True, "files": files, "count": len(files)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "files": [], "count": 0}

    def preview_workspace_file(self, relative: str) -> dict:
        try:
            path = str(relative).strip()
            target = self.agent.sandbox.path(path)
            if not target.is_file():
                raise FileNotFoundError(path)
            size = target.stat().st_size
            suffix = target.suffix.casefold()
            if suffix in self.IMAGE_PREVIEW_SUFFIXES:
                if size > self.MAX_PREVIEW_BYTES:
                    return {"ok": True, "path": path, "size": size, "kind": "binary",
                            "message": "Image is too large for the safe preview."}
                mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix[1:]}"
                return {"ok": True, "path": path, "size": size, "kind": "image",
                        "mime": mime, "content": base64.b64encode(target.read_bytes()).decode("ascii")}
            if suffix not in self.TEXT_PREVIEW_SUFFIXES:
                return {"ok": True, "path": path, "size": size, "kind": "binary",
                        "message": "Binary preview is intentionally disabled."}
            raw = target.read_bytes()[: self.MAX_PREVIEW_BYTES + 1]
            truncated = len(raw) > self.MAX_PREVIEW_BYTES or size > self.MAX_PREVIEW_BYTES
            content = raw[: self.MAX_PREVIEW_BYTES].decode("utf-8")
            kind = "rendered" if suffix in {".html", ".htm", ".svg"} else "text"
            result = {"ok": True, "path": path, "size": size, "kind": kind,
                      "content": content, "truncated": truncated, "suffix": suffix}
            if kind == "rendered":
                # The protected GET route gives the document a real base URL, so
                # relative CSS, images, fonts, and links resolve inside the same
                # sandboxed workspace. Scripts remain disabled by the route CSP
                # and the iframe sandbox.
                result["url"] = "/workspace-preview/" + quote(path, safe="/")
                result["scripts_enabled"] = False
            return result
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def open_workspace_item(self, relative: str) -> dict:
        try:
            target = self.agent.sandbox.path(str(relative).strip())
            if not target.exists():
                raise FileNotFoundError(str(relative))
            os.startfile(target)  # type: ignore[attr-defined]
            return {"ok": True}
        except (FileNotFoundError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def import_files(self, items: list[dict], destination: str = ".") -> dict:
        try:
            if not isinstance(items, list) or not 1 <= len(items) <= 5:
                raise ValueError("Drop between 1 and 5 files at a time.")
            destination_path = str(destination or ".").strip()
            self.agent.sandbox.path(destination_path)
            decoded: list[tuple[str, bytes]] = []
            total = 0
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid dropped file.")
                original_name = str(item.get("name", "")).strip()
                name = Path(original_name).name
                if not name or name in {".", ".."}:
                    raise ValueError("A dropped file has no safe name.")
                try:
                    data = base64.b64decode(str(item.get("content", "")), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(f"{name} is not a valid file upload.") from exc
                if len(data) > self.MAX_IMPORT_FILE_BYTES:
                    raise ValueError(f"{name} exceeds the 1.5 MB local import limit.")
                total += len(data)
                if total > self.MAX_IMPORT_TOTAL_BYTES:
                    raise ValueError("Dropped files exceed the 4 MB total import limit.")
                decoded.append((name, data))
            imported = []
            for name, data in decoded:
                requested = Path(destination_path) / name
                candidate = requested.as_posix()
                stem, suffix = Path(name).stem, Path(name).suffix
                index = 2
                while self.agent.sandbox.path(candidate).exists():
                    candidate = (Path(destination_path) / f"{stem} ({index}){suffix}").as_posix()
                    index += 1
                self.agent.sandbox.import_file(candidate, data)
                imported.append(candidate)
                self.agent.log.record("import_file", "ok", path=candidate, bytes=len(data))
            return {"ok": True, "files": imported, "count": len(imported)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "files": [], "count": 0}

    def create_workspace_folder(self, path: str) -> dict:
        try:
            cleaned = str(path).strip()
            self.agent.sandbox.create_folder(cleaned)
            self.agent.log.record("create_folder", "ok", path=cleaned)
            return {"ok": True, "path": cleaned}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def create_workspace_file(self, path: str, content: str = "") -> dict:
        try:
            cleaned = str(path).strip()
            self.agent.sandbox.create_file(cleaned, str(content))
            self.agent.log.record("create_file", "ok", path=cleaned)
            return {"ok": True, "path": cleaned}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def rename_workspace_item(self, path: str, new_name: str) -> dict:
        try:
            cleaned = str(path).strip()
            source = self.agent.sandbox.path(cleaned)
            if not source.exists():
                raise FileNotFoundError(cleaned)
            name = Path(str(new_name).strip()).name
            if not name or name in {".", ".."}:
                raise ValueError("Enter a valid name.")
            new_relative = (Path(cleaned).parent / name).as_posix()
            if source.is_dir():
                self.agent.sandbox.move_folder(cleaned, new_relative)
            else:
                self.agent.sandbox.move_file(cleaned, new_relative)
            self.agent.log.record("rename_item", "ok", path=cleaned, to=new_relative)
            return {"ok": True, "path": new_relative}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def move_workspace_item(self, source: str, destination: str) -> dict:
        try:
            cleaned_source = str(source).strip()
            cleaned_destination = str(destination).strip()
            resolved = self.agent.sandbox.path(cleaned_source)
            if not resolved.exists():
                raise FileNotFoundError(cleaned_source)
            if resolved.is_dir():
                self.agent.sandbox.move_folder(cleaned_source, cleaned_destination)
                self.agent.log.record("move_folder", "ok", path=cleaned_source, to=cleaned_destination)
            else:
                self.agent.sandbox.move_file(cleaned_source, cleaned_destination)
                self.agent.log.record("move_file", "ok", path=cleaned_source, to=cleaned_destination)
            return {"ok": True, "path": cleaned_destination}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def copy_workspace_item(self, source: str, destination: str) -> dict:
        try:
            cleaned_source = str(source).strip()
            cleaned_destination = str(destination).strip()
            resolved = self.agent.sandbox.path(cleaned_source)
            if not resolved.exists():
                raise FileNotFoundError(cleaned_source)
            if resolved.is_dir():
                self.agent.sandbox.copy_folder(cleaned_source, cleaned_destination)
                self.agent.log.record("copy_folder", "ok", path=cleaned_source, to=cleaned_destination)
            else:
                self.agent.sandbox.copy_file(cleaned_source, cleaned_destination)
                self.agent.log.record("copy_file", "ok", path=cleaned_source, to=cleaned_destination)
            return {"ok": True, "path": cleaned_destination}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def delete_workspace_item(self, path: str) -> dict:
        try:
            cleaned = str(path).strip()
            resolved = self.agent.sandbox.path(cleaned)
            if not resolved.exists():
                raise FileNotFoundError(cleaned)
            if resolved.is_dir():
                trashed = self.agent.sandbox.safe_delete_folder(cleaned)
                kind = "folder"
                self.agent.log.record("safe_delete_folder", "ok", path=cleaned)
            else:
                trashed = self.agent.sandbox.safe_delete_file(cleaned)
                kind = "file"
                self.agent.log.record("safe_delete_file", "ok", path=cleaned)
            return {"ok": True, "trashed_as": trashed.name, "kind": kind}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def list_trash(self) -> dict:
        try:
            return {"ok": True, "items": self.agent.sandbox.list_trash()}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "items": []}

    def restore_workspace_item(self, trash_name: str) -> dict:
        try:
            restored = self.agent.sandbox.restore_from_trash(str(trash_name).strip())
            path = restored.relative_to(self.agent.sandbox.root).as_posix()
            self.agent.log.record("restore_from_trash", "ok", trash_name=trash_name, path=path)
            return {"ok": True, "path": path}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def undo_workspace_change(self) -> dict:
        try:
            result = self.agent.sandbox.undo_last_change()
            self.agent.log.record("undo_last_change", "ok", paths=result.get("paths", []))
            return {"ok": True, **result}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def workspace_change_history(self, limit: int = 20) -> dict:
        try:
            return {"ok": True, "changes": self.agent.sandbox.change_history(int(limit))}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "changes": []}

    def compare_workspace_files(self, left: str, right: str, context_lines: int = 3) -> dict:
        try:
            result = self.agent.sandbox.compare_files(str(left), str(right), int(context_lines))
            return {"ok": True, **result}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def start_preview_server(self, path: str = ".") -> dict:
        try:
            return self.preview_server.start(str(path))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def stop_preview_server(self) -> dict:
        try:
            return self.preview_server.stop()
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    def preview_server_status(self) -> dict:
        return self.preview_server.status()

    def preview_server_log(self, limit: int = 50) -> dict:
        return {"ok": True, "entries": self.preview_server.recent_log(limit)}

    def check_workspace_assets(self, path: str = ".") -> dict:
        try:
            return check_broken_assets(self.agent.sandbox, str(path))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

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

    def get_personal_memory(self) -> dict:
        try:
            self.agent.memory.load()
            memories = self.agent.memory.profile_memories()
            return {
                "ok": True,
                "name": self.agent.memory.data.get("name"),
                "preferences": dict(self.agent.memory.data.get("preferences", {})),
                "learning_enabled": bool(self.agent.config.data.get("learn_from_conversations", True)),
                "memories": memories,
                "count": len(memories),
                "conflicts": self.agent.memory.conflicting_pairs(),
                "categories": sorted(self.agent.memory.PROFILE_CATEGORIES),
                "privacy": (("Automatic learning is on. " if self.agent.config.data.get("learn_from_conversations", True)
                             else "Automatic learning is off. ") +
                            "Only clear, non-sensitive facts are eligible; everything stays local and editable."),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "memories": [], "count": 0}

    def add_personal_memory(self, category: str, value: str) -> dict:
        try:
            item = self.agent.memory.learn_fact(
                str(category), str(value), source="Added from What Aura knows",
                confidence=1.0, explicit=True,
            )
            self.agent.log.record("remember_personal_fact", "ok", memory_id=item["id"],
                                  category=item["category"], value=item["value"])
            self._push("memory_changed", action="added", memory=item)
            return {"ok": True, "memory": item}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def list_permissions(self) -> dict:
        store = self.agent.permissions
        return {"ok": True, "active": store.active(), "history": store.history(30),
                "note": ("Aura can only read folders you grant here. It cannot grant "
                         "itself access, and nothing outside the workspace is readable "
                         "by default.")}

    def grant_folder_access(self, path: str, mode: str = "session",
                            project: str | None = None, writable: bool = False) -> dict:
        """Grant access to one folder. Only ever called by the user.

        Write access is a separate grant rather than something a read grant
        quietly implies, so the Permissions list always shows it explicitly.
        """
        try:
            grants = [self.agent.permissions.grant("read_folder", str(path), str(mode),
                                                   project=project or None)]
            if writable:
                grants.append(self.agent.permissions.grant(
                    "write_folder", str(path), str(mode), project=project or None))
            for grant in grants:
                self.agent.log.record("grant_folder_access", "ok", path=grant["root"],
                                      capability=grant["capability"],
                                      mode=grant["mode"], grant_id=grant["id"])
                self._push("permissions_changed", action="granted", grant=grant)
            return {"ok": True, "grant": grants[0], "grants": grants}
        except (PermissionRefused, OSError, ValueError) as exc:
            self.agent.log.record("grant_folder_access", "error", path=str(path),
                                  error=str(exc))
            return {"ok": False, "error": str(exc)}

    def external_changes(self, limit: int = 20) -> dict:
        return {"ok": True, "changes": self.agent.external_writer.changes(int(limit))}

    def undo_external_change(self) -> dict:
        try:
            result = self.agent.external_writer.undo_last()
            self.agent.log.record("undo_external_change", "ok", path=result["path"],
                                  action=result["action"])
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def revoke_folder_access(self, grant_id: str) -> dict:
        try:
            grant = self.agent.permissions.revoke(str(grant_id))
            self.agent.log.record("revoke_folder_access", "ok", path=grant["root"],
                                  grant_id=grant["id"])
            self._push("permissions_changed", action="revoked", grant=grant)
            return {"ok": True, "grant": grant}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    def revoke_all_permissions(self) -> dict:
        count = self.agent.permissions.revoke_all()
        self.agent.log.record("revoke_all_permissions", "ok", revoked=count)
        self._push("permissions_changed", action="revoked_all", revoked=count)
        return {"ok": True, "revoked": count}

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
            )
            self.agent.log.record("update_personal_fact", "ok", memory_id=item["id"],
                                  category=item["category"], value=item["value"])
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
        self.agent.cancel_current()
        self.speech.stop()
        self.voice.stop()
        self.preview_server.stop_if_running()
        self._deny_pending_approvals()
