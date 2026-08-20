from __future__ import annotations

import json
import threading
from pathlib import Path


DEFAULTS = {
    "lm_studio_url": "http://127.0.0.1:1234/v1",
    "model": None,
    "timeout": 180.0,
    "temperature": 0.4,
    "max_tokens": 4096,
    # Sampling per kind of turn rather than one setting for all three. The old
    # single pair above stays as the fallback, and switching this off restores
    # it exactly. See `sampling.py` for why code sits at 0.4 and not 0.2.
    "sampling_by_task": True,
    "temperature_chat": 0.9, "max_tokens_chat": 2048,
    "temperature_work": 0.6, "max_tokens_work": 4096,
    "temperature_code": 0.4, "max_tokens_code": 6144,
    # Blank means LM Studio's loaded values stand, which is what Aura did
    # before it could send these at all. One pair for every profile: the
    # guides recommend one setting for these, not one per kind of turn.
    "top_p": None, "top_k": None,
    # Hand the model back its own thinking after a tool call. On a reasoning
    # model that keeps three quarters of its work private, discarding it made
    # every round start from nothing. Switchable because it costs prompt space.
    "send_reasoning_back": True,
    # How long one turn may run before Aura stops and reports what she has.
    # Not the HTTP timeout: that ends a turn as a failure, this ends it as an
    # answer. 0 removes the limit. Measured 90th percentile is 221s.
    "turn_budget_seconds": 300,
    # How much tool output one turn may put in front of the model, across every
    # tool. Individual tools cap themselves; nothing capped the total, and every
    # later round pays for the whole of it again. 0 removes the limit.
    "turn_tool_characters": 120000,
    # Local unless deliberately changed. "claude" reaches Anthropic's API and
    # needs the optional `anthropic` package; see requirements-cloud.txt for
    # what that sends off the machine.
    "provider": "local",
    "anthropic_api_key": "",
    "cloud_model": "claude-opus-5",
    # Deliberately not `max_tokens`: that one is tuned for a local 9B, and this
    # model family spends the same budget on thinking before it answers.
    "cloud_max_tokens": 16000,
    "openai_api_key": "",
    "openai_model": "",
    # Empty means OpenAI itself. Any other OpenAI-compatible service goes here,
    # which is the whole of what it takes to reach one.
    "openai_base_url": "",
    # A role per project, by folder name. A role kept in a chat message competes
    # with the system prompt every turn and dies with the conversation; kept
    # here it applies to the project it belongs to and to nothing else.
    "reasoning_depth": "deep",
    "autonomy_mode": "powerful",
    "learn_from_conversations": True,
    "vision_mode": "auto",
    "vision_probe": {},
    "current_session": None,
    "onboarded": False,
    # The envelope around anything Aura does unasked. Off-hours by default and a
    # modest daily allowance, so background work has to be widened deliberately
    # rather than discovered later.
    "autonomy_paused": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "autonomy_daily_runs": 12,
    "autonomy_run_seconds": 120,
    # Recorded once, so a default check the user switches off never
    # comes back on the next launch.
    "default_checks_seeded": False,
    # Which defaults have been offered already, by name. A bare flag could
    # only ever answer "all of them or none", so a default added later either
    # never arrived or dragged back the ones already switched off.
    "seeded_checks": [],
    # Empty means search is off. Aura holds no search credentials and never
    # has; what this points at is a service the user started themselves.
    # Estonian speech. Empty is the shipped state and an honest one: Piper
    # publishes no Estonian voice, and Windows ships none until the language
    # is added. Aura still speaks, and says why it sounds wrong.
    "speech_voice_et": "",
    "speech_model_et": "",
    "search_endpoint": "",
    # The SearXNG checkout Aura starts alongside itself. Empty means Aura
    # starts nothing and only reads whatever the user runs themselves.
    "search_install_path": "",
    # off | docker | folder. Docker is the only route that works on Windows,
    # where SearXNG cannot run natively at all.
    "search_mode": "off",
    "speak_responses": False,
    "speech_engine": "piper",
    "speech_model": "aura-voices/en_US-lessac-medium.onnx",
    "speech_voice": "Microsoft Zira Desktop - English (United States)",
    "speech_rate": -1,
    "speech_volume": 95,
    "voice_engine": "auto",
    "voice_device": "",
    "voice_language": "en",
    "voice_calibration_ms": 500,
    "voice_silence_ms": 1200,
    "voice_max_seconds": 25,
    "voice_noise_floor": 0.0,
    "whisper_cpp_path": "",
    "whisper_model_path": "",
    "avatar_motion": "natural",
    "avatar_intensity": 65,
    "avatar_quality": "auto",
    "web_sidebar_width": 250,
    "web_log_height": 170,
    "web_log_visible": False,
}


class ConfigStore:
    """Persistent, local application settings with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in DEFAULTS:
                        if key in loaded:
                            self.data[key] = loaded[key]
            except (OSError, json.JSONDecodeError):
                pass

    def update(self, **values: object) -> None:
        with self._lock:
            for key, value in values.items():
                if key not in DEFAULTS:
                    raise KeyError(f"Unknown setting: {key}")
                self.data[key] = value
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
