"""Microphone and speech methods of the local interface.

Split out of `web_bridge.py`, which had grown to 64 HTTP-exposed methods in one
class. These are mixed back into `AuraWebBridge`, so every method keeps the name
the HTTP layer already calls it by; only the file it lives in changed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .speech import SpeechOutput


class VoiceBridge:
    def get_voices(self) -> dict:
        return {"ok": True, "voices": SpeechOutput.installed_voices(),
                "neural": self._neural_voice_models(),
                "neural_selected": str(self.agent.config.data["speech_model"])}

    def _neural_voice_models(self) -> list[str]:
        """Piper voices sitting in `aura-voices/`, newest naming aside.

        Aura never downloads one by itself; this only makes a model the user has
        already put there selectable without editing config.json by hand.
        """
        folder = Path(self.NEURAL_VOICE_FOLDER)
        if not folder.is_dir():
            return []
        return sorted(f"{self.NEURAL_VOICE_FOLDER}/{path.name}"
                      for path in folder.glob("*.onnx")
                      if path.with_suffix(".onnx.json").is_file())

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
            spoken, message = False, "Aura could not start the voice preview."
            try:
                spoken, message = self.speech.speak(
                    "Hello. I’m Aura. I’m here, listening, and ready to create with you.",
                    on_cues=lambda payload: self._push("speech_cues", **payload),
                )
            finally:
                # The outcome used to be discarded: a preview that failed sounded
                # exactly like one that worked but was inaudible.
                self._push("speech", active=False, preview=True,
                           spoken=spoken, message=message)
                self.speech.enabled = was_enabled

        threading.Thread(target=speak, daemon=True, name="aura-voice-preview").start()
        return {"ok": True}
