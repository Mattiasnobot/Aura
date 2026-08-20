"""Model, settings, and first-run methods of the local interface.

Split out of `web_bridge.py`, which had grown to 64 HTTP-exposed methods in one
class. These are mixed back into `AuraWebBridge`, so every method keeps the name
the HTTP layer already calls it by; only the file it lives in changed.
"""

from __future__ import annotations

import os

from . import sampling
import re
import threading

from . import search_service
from . import websearch
from .cloud import AnthropicProvider, OpenAIProvider
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

    #: Which stored key each choice uses, so forgetting one cannot reach the other.
    KEY_FOR = {"claude": ("anthropic_api_key", "ANTHROPIC_API_KEY", "Claude"),
               "openai": ("openai_api_key", "OPENAI_API_KEY", "OpenAI")}

    def forget_cloud_key(self, service: str = "claude") -> dict:
        """Remove the stored API key, and stop using it immediately.

        A blank key field means "keep the stored key" — it has to, because the
        key is never sent to the browser and so the field always opens blank.
        That leaves no way to withdraw a credential once given, which this is.
        Falls back to the local model in the same step when the key being
        removed is the one in use, since staying there would only fail on the
        next message. Takes which service, because clearing both to remove one
        would throw away a key that was working.
        """
        chosen = str(service or "claude").strip().casefold()
        if chosen not in self.KEY_FOR:
            return {"ok": False, "error": "There is no stored key for that."}
        setting, variable, label = self.KEY_FOR[chosen]
        # Only ever the key that was asked for. An earlier version cleared both,
        # which would have thrown away a working key to remove an unused one.
        updates = {setting: ""}
        active = str(self.agent.config.data.get("provider", "local")) == chosen
        if active:
            # Staying here with no key would only fail on the next message.
            updates["provider"] = "local"
        self.agent.config.update(**updates)
        if active:
            self.agent.set_provider(self.agent._build_provider())
        self._push("settings_saved")
        note = f"The {label} key was removed."
        if active:
            note += " Aura is back on the local model."
        if os.environ.get(variable):
            note += f" {variable} is still set in the environment."
        return {"ok": True, "provider": self.agent.config.data["provider"], "note": note}

    #: Long enough for a real brief, short enough that it cannot quietly become
    #: most of every request's cost.
    MAX_ROLE = 4000

    def get_project_lessons(self, project: str = "") -> dict:
        """The rules Mat has set for a project, and which ones Aura added herself."""
        name = str(project or self.agent.current_project or "").strip()
        # The panel needs a project picker of its own now that the role block it
        # borrowed one from is gone. Sent with the lessons rather than from a
        # second call, because the two are always wanted together.
        projects = self.agent.workspace_projects()
        if name and name not in projects:
            projects.append(name)
        return {"ok": True, "project": name,
                "projects": sorted(projects),
                "current": self.agent.current_project or "",
                "lessons": [
                    {"id": str(item.get("id", "")), "text": str(item.get("value", "")),
                     "from_aura": str(item.get("source", "")) != "typed by the user"}
                    for item in self.agent.memory.data.get("profile_memories", [])
                    if str(item.get("category")) == "lesson"
                    and str(item.get("project") or "").strip() == name]}

    def add_project_lesson(self, project: str, lesson: str) -> dict:
        """Set a rule for a project by hand.

        Exists because `remember_lesson` depends on Aura noticing she is being
        corrected, and that is the same judgement that fails in the first place.
        Typed here, it needs no judgement at all.
        """
        name = str(project or "").strip()
        text = str(lesson or "").strip()
        if not name:
            return {"ok": False, "error": "Choose a project first."}
        if not 3 <= len(text) <= 300:
            return {"ok": False, "error": "A rule must be between 3 and 300 characters."}
        item = self.agent.memory.learn_fact(
            "lesson", text, source="typed by the user", confidence=1.0,
            explicit=True, project=name)
        if not item:
            return {"ok": False, "error": "That rule is already set for this project."}
        self.agent.log.record("project_lesson", "ok", project=name, lesson=text)
        self._push("settings_saved")
        return {"ok": True, "project": name, "lesson": item["value"]}

    def forget_project_lesson(self, memory_id: str) -> dict:
        item = self.agent.memory.forget_profile_memory(str(memory_id))
        self.agent.log.record("project_lesson", "forgotten", memory_id=str(memory_id))
        self._push("settings_saved")
        return {"ok": True, "forgotten": str(item.get("value", ""))}



    def get_settings(self) -> dict:
        config = self.agent.config.data
        return {key: config[key] for key in (
            "lm_studio_url", "model", "timeout", "temperature", "max_tokens",
            "sampling_by_task",
            "temperature_chat", "max_tokens_chat",
            "temperature_work", "max_tokens_work",
            "temperature_code", "max_tokens_code", "top_p", "top_k",
            "turn_budget_seconds",
            "reasoning_depth", "autonomy_mode", "learn_from_conversations", "vision_mode",
            "speak_responses", "speech_engine", "speech_voice", "speech_model",
            "speech_rate", "speech_volume", "speech_voice_et", "speech_model_et",
            "voice_engine", "voice_device", "voice_language", "voice_calibration_ms",
            "voice_silence_ms", "voice_max_seconds", "voice_noise_floor",
            "whisper_cpp_path", "whisper_model_path",
            "avatar_motion", "avatar_intensity", "avatar_quality", "search_endpoint",
            "search_install_path", "search_mode",
            "provider", "cloud_model", "openai_model", "openai_base_url",
        )} | {
            # The key itself is never sent to the browser, so the field always
            # opens blank and a blank field has to mean "leave it alone". This
            # flag is what lets the interface say whether one is stored at all.
            "anthropic_key_set": bool(str(config.get("anthropic_api_key") or "").strip()
                                      or os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            "anthropic_key_from_env": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            "openai_key_set": bool(str(config.get("openai_api_key") or "").strip()
                                   or os.environ.get("OPENAI_API_KEY", "").strip()),
            "openai_key_from_env": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        }

    def save_settings(self, values: dict) -> dict:
        with self._state_lock:
            if self._busy or self._voice_active:
                return {"ok": False, "error": "Finish or stop the current task before changing models."}
        try:
            timeout = float(values.get("timeout", 180))
            # The panel stopped offering these when the per-task profiles became
            # the only way to set sampling, so an absent field means "leave it
            # alone" rather than "reset to the default". Falling back to 0.4
            # here would quietly undo Mat's stored value on every save.
            stored = self.agent.config.data
            temperature = float(values.get("temperature", stored.get("temperature", 0.4)))
            max_tokens = int(values.get("max_tokens", stored.get("max_tokens", 4096)))
            turn_budget = int(values.get("turn_budget_seconds", 300))
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
            search_endpoint = str(values.get("search_endpoint", "")).strip().rstrip("/")
            search_install_path = str(values.get("search_install_path", "")).strip()
            speech_voice_et = str(values.get("speech_voice_et", "")).strip()
            speech_model_et = str(values.get("speech_model_et", "")).strip()
            search_mode = str(values.get("search_mode", "off")).strip().casefold()
            reasoning_depth = str(values.get("reasoning_depth", "deep"))
            autonomy_mode = str(values.get("autonomy_mode", "powerful"))
            vision_mode = str(values.get("vision_mode", "auto")).casefold()
            if vision_mode not in {"auto", "on", "off"}:
                vision_mode = "auto"
            learn_from_conversations = bool(values.get("learn_from_conversations", True))
            if not 5 <= timeout <= 1800:
                # Turns measured at 100–436s on this machine, against a default
                # of 180 — and a real "LM Studio timed out" in the log. A ceiling
                # under the observed range is a ceiling that cuts off real work.
                raise ValueError("Timeout must be between 5 and 1800 seconds.")
            if not 0 <= temperature <= 2:
                raise ValueError("Temperature must be between 0 and 2.")
            if not 256 <= max_tokens <= 65536:
                raise ValueError("Maximum response tokens must be between 256 and 65536.")
            # The three profiles answer to the same bounds as the single pair
            # they replace — a value Aura would refuse globally is not one it
            # should accept because the field has a different label.
            sampling_by_task = bool(values.get(
                "sampling_by_task", self.agent.config.data.get("sampling_by_task", True)))
            profiles: dict[str, object] = {"sampling_by_task": sampling_by_task}
            for kind in sampling.KINDS:
                stored = self.agent.config.data
                heat = float(values.get(f"temperature_{kind}",
                                        stored.get(f"temperature_{kind}",
                                                   sampling.DEFAULTS[kind][0])))
                limit = int(values.get(f"max_tokens_{kind}",
                                       stored.get(f"max_tokens_{kind}",
                                                  sampling.DEFAULTS[kind][1])))
                if not 0 <= heat <= 2:
                    raise ValueError(f"{kind.title()} temperature must be between 0 and 2.")
                if not 256 <= limit <= 65536:
                    raise ValueError(
                        f"{kind.title()} response tokens must be between 256 and 65536.")
                profiles[f"temperature_{kind}"] = heat
                profiles[f"max_tokens_{kind}"] = limit
            # Blank is a real answer here: it means Aura sends nothing and the
            # value LM Studio was loaded with stands.
            for name, low, high, cast in (("top_p", 0.0, 1.0, float),
                                          ("top_k", 0, 500, int)):
                raw = values.get(name, self.agent.config.data.get(name))
                if raw is None or str(raw).strip() == "":
                    profiles[name] = None
                    continue
                try:
                    number = cast(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"{name} must be a number, or blank to let "
                                     f"LM Studio decide.") from None
                if not low <= number <= high:
                    raise ValueError(f"{name} must be between {low} and {high}, "
                                     f"or blank to let LM Studio decide.")
                profiles[name] = number
            if turn_budget != 0 and not 30 <= turn_budget <= 3600:
                raise ValueError(
                    "Turn time limit must be 0 (no limit) or between 30 and 3600 seconds.")
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
            if search_endpoint:
                # Empty is a real setting: it means search is off. Anything else
                # has to be an address Aura could actually use, checked here so
                # the mistake surfaces on save rather than mid-question.
                try:
                    search_endpoint = websearch.endpoint_of(search_endpoint)
                except websearch.SearchUnavailable as exc:
                    raise ValueError(str(exc)) from exc
            if search_mode not in {"off", "docker", "folder"}:
                raise ValueError("Search must be off, Docker, or a folder.")
            if search_mode == "folder" and not search_install_path:
                raise ValueError("Choose the SearXNG folder, or pick a different search mode.")
            if search_install_path:
                # Checked on save so a wrong folder is a message here rather
                # than a service that quietly never comes up next launch.
                try:
                    search_service.find_install(search_install_path)
                except search_service.SearchServiceError as exc:
                    raise ValueError(str(exc)) from exc
            if len(search_install_path) > 500:
                raise ValueError("That folder path is too long.")
            if reasoning_depth not in {"fast", "balanced", "deep"}:
                raise ValueError("Reasoning depth must be fast, balanced, or deep.")
            if autonomy_mode not in {"careful", "balanced", "powerful"}:
                raise ValueError("Autonomy must be careful, balanced, or powerful.")
            model_host = str(values.get("provider", "local")).strip().casefold()
            if model_host not in {"local", "claude", "openai"}:
                raise ValueError("The model runs on this computer, through Claude, or "
                                 "through OpenAI.")
            openai_model = str(values.get("openai_model", "")).strip()
            if len(openai_model) > 100:
                raise ValueError("That model name is too long.")
            openai_base_url = str(values.get("openai_base_url", "")).strip().rstrip("/")
            if openai_base_url:
                # Checked on save so a wrong address is a message here rather
                # than a failure on the next thing the user says.
                OpenAIProvider(api_key="checked-later", base_url=openai_base_url)
            cloud_model = (str(values.get("cloud_model", "")).strip()
                           or AnthropicProvider.DEFAULT_MODEL)
            if cloud_model not in AnthropicProvider.MODELS:
                raise ValueError("Choose one of the listed Claude models.")
            # A blank key field means "keep what is stored", never "erase it":
            # the field is deliberately never filled in from the server, so
            # blank is its normal state rather than an instruction.
            sent_key = str(values.get("anthropic_api_key", "")).strip()
            api_key = sent_key or str(self.agent.config.data.get("anthropic_api_key") or "")
            if model_host == "claude" and not (api_key or os.environ.get("ANTHROPIC_API_KEY")):
                raise ValueError("Claude needs an API key. Paste one here, or set "
                                 "ANTHROPIC_API_KEY before starting Aura.")
            sent_openai = str(values.get("openai_api_key", "")).strip()
            openai_key = sent_openai or str(self.agent.config.data.get("openai_api_key") or "")
            if model_host == "openai" and not (openai_key or os.environ.get("OPENAI_API_KEY")):
                where = openai_base_url or "OpenAI"
                raise ValueError(f"{where} needs an API key. Paste one here, or set "
                                 "OPENAI_API_KEY before starting Aura.")
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
            active = provider
            if model_host == "openai":
                active = OpenAIProvider(
                    api_key=openai_key, model=openai_model, base_url=openai_base_url,
                    timeout=timeout, temperature=temperature,
                    max_tokens=int(self.agent.config.data.get("cloud_max_tokens") or 0) or None)
            elif model_host == "claude":
                active = AnthropicProvider(
                    api_key=api_key, model=cloud_model, timeout=timeout,
                    max_tokens=int(self.agent.config.data.get("cloud_max_tokens") or 0) or None)
            enabled = bool(values.get("speak_responses", False))
            voice = str(values.get("speech_voice", "")).strip()
            self.agent.config.update(
                lm_studio_url=provider.base_url, model=provider.model, timeout=timeout,
                temperature=temperature, max_tokens=max_tokens, **profiles,
                reasoning_depth=reasoning_depth, autonomy_mode=autonomy_mode,
                learn_from_conversations=learn_from_conversations, vision_mode=vision_mode,
                speak_responses=enabled, speech_engine=engine, speech_voice=voice,
                speech_model=speech_model or self.agent.config.data["speech_model"],
                speech_rate=speech_rate, speech_volume=speech_volume,
                speech_voice_et=speech_voice_et, speech_model_et=speech_model_et,
                voice_engine=voice_engine, voice_device=voice_device,
                voice_language=voice_language, voice_calibration_ms=voice_calibration_ms,
                voice_silence_ms=voice_silence_ms, voice_max_seconds=voice_max_seconds,
                voice_noise_floor=voice_noise_floor, whisper_cpp_path=whisper_cpp_path,
                whisper_model_path=whisper_model_path,
                avatar_motion=avatar_motion, avatar_intensity=avatar_intensity,
                avatar_quality=avatar_quality, search_endpoint=search_endpoint,
                search_install_path=search_install_path,
                search_mode=search_mode,
                provider=model_host, cloud_model=cloud_model, anthropic_api_key=api_key,
                openai_model=openai_model, openai_api_key=openai_key,
                openai_base_url=openai_base_url,
                turn_budget_seconds=turn_budget,
            )
            self.agent.set_provider(active)
            self.speech.configure(
                enabled=enabled, voice=voice, rate=speech_rate, volume=speech_volume,
                engine=engine, neural_model=str(self.agent.config.data["speech_model"]),
                voice_et=speech_voice_et, neural_model_et=speech_model_et,
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

    def get_openai_models(self, api_key: str = "") -> dict:
        """Ask the key which models it can actually use.

        Better than a list of names kept in this file: the catalogue changes
        faster than the file does, and a name that is wrong or retired fails at
        the worst possible moment instead of at the moment of choosing.
        """
        key = str(api_key or "").strip() or str(
            self.agent.config.data.get("openai_api_key") or "")
        url = str(self.agent.config.data.get("openai_base_url") or "")
        try:
            return {"ok": True, "models": OpenAIProvider(
                api_key=key, base_url=url, timeout=15).available_models()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "models": []}
