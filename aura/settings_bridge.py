"""Model, settings, and first-run methods of the local interface.

Split out of `web_bridge.py`, which had grown to 64 HTTP-exposed methods in one
class. These are mixed back into `AuraWebBridge`, so every method keeps the name
the HTTP layer already calls it by; only the file it lives in changed.
"""

from __future__ import annotations

import re
import threading

from .provider import LMStudioProvider


class SettingsBridge:
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
            "speak_responses", "speech_engine", "speech_voice", "speech_model",
            "speech_rate", "speech_volume",
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
            speech_model = str(values.get(
                "speech_model", self.agent.config.data["speech_model"])).strip()
            if speech_model and speech_model not in self._neural_voice_models():
                # Only a model already present in the voices folder: this setting
                # must never become a way to point Aura at an arbitrary file.
                raise ValueError("Choose a neural voice from Aura's voices folder.")
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
                speech_model=speech_model or self.agent.config.data["speech_model"],
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

    def complete_onboarding(self, url: str = "", model: str = "") -> dict:
        """Finish the first-run guide, optionally keeping the model chosen there.

        Called with nothing when the guide is skipped: the guide stops appearing
        either way, so it can never stand between someone and their first
        message. Settings are only touched when a URL was actually confirmed.
        """
        with self._state_lock:
            if self._busy:
                return {"ok": False, "error": "Finish or stop the current task first."}
        try:
            chosen = str(url).strip()
            if chosen:
                provider = LMStudioProvider(
                    chosen, str(model or "").strip() or None,
                    float(self.agent.config.data["timeout"]),
                    float(self.agent.config.data["temperature"]),
                    int(self.agent.config.data["max_tokens"]))
                self.agent.config.update(lm_studio_url=provider.base_url,
                                         model=provider.model)
                self.agent.set_provider(provider)
            self.agent.config.update(onboarded=True)
            self.agent.log.record("complete_onboarding", "ok",
                                  connected=bool(chosen))
            if chosen:
                self.check_provider()
            return {"ok": True, "model": self.agent.config.data["model"]}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def restart_onboarding(self) -> dict:
        """Let someone open the guide again from Settings."""
        self.agent.config.update(onboarded=False)
        return {"ok": True}

    def get_models(self, url: str) -> dict:
        try:
            provider = LMStudioProvider(str(url).strip(), timeout=10)
            return {"ok": True, "models": provider.available_models()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "models": []}
