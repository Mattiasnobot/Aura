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
    "reasoning_depth": "deep",
    "autonomy_mode": "powerful",
    "learn_from_conversations": True,
    "vision_mode": "auto",
    "vision_probe": {},
    "current_session": None,
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
    "web_log_visible": True,
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
