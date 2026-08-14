from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import unicodedata
import wave
from array import array
from math import sqrt
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

try:
    import winsound
except ImportError:  # pragma: no cover - Windows app
    winsound = None


class SpeechOutput:
    """Local Piper neural speech with a paced Windows SAPI fallback."""

    def __init__(self, enabled: bool = False, voice: str = "Microsoft Zira Desktop - English (United States)",
                 rate: int = -1, volume: int = 95, engine: str = "sapi",
                 neural_model: str | Path = "aura-voices/en_US-lessac-medium.onnx") -> None:
        self.enabled = enabled
        self.voice = voice
        self.rate = max(-10, min(int(rate), 10))
        self.volume = max(0, min(int(volume), 100))
        self.engine = engine if engine in {"piper", "sapi"} else "sapi"
        self.neural_model = Path(neural_model).resolve()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._neural_voice = None
        self._generation = 0

    @staticmethod
    def available() -> bool:
        return os.name == "nt"

    def configure(self, *, enabled: bool, voice: str, rate: int, volume: int,
                  engine: str = "piper", neural_model: str | Path | None = None) -> None:
        self.enabled = enabled
        self.voice = voice
        self.rate = max(-10, min(int(rate), 10))
        self.volume = max(0, min(int(volume), 100))
        self.engine = engine if engine in {"piper", "sapi"} else "sapi"
        if neural_model is not None:
            resolved = Path(neural_model).resolve()
            if resolved != self.neural_model:
                self._neural_voice = None
            self.neural_model = resolved
        if not enabled:
            self.stop()

    @staticmethod
    def installed_voices() -> list[str]:
        if os.name != "nt":
            return []
        script = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$v=New-Object -ComObject SAPI.SpVoice; "
            "for ($i=0; $i -lt $v.GetVoices().Count; $i++) "
            "{ $v.GetVoices().Item($i).GetDescription() }"
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=15, shell=False, creationflags=creation_flags, check=False,
            )
            if completed.returncode != 0:
                return []
            return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            return []

    def neural_available(self) -> bool:
        if winsound is None or not self.neural_model.is_file():
            return False
        try:
            import piper  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def prepare_plain_text(text: str) -> str:
        plain = re.sub(r"```.*?```", " A code block is ready in the workspace. ", text, flags=re.DOTALL)
        plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", plain)
        plain = re.sub(r"https?://\S+", " link ", plain)
        plain = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", plain)
        plain = re.sub(r"[`*_#>|]", "", plain)
        plain = "".join(character for character in plain
                        if unicodedata.category(character) not in {"So", "Cs", "Cc"} or character in "\n\t")
        paragraphs = [re.sub(r"[ \t]+", " ", part).strip() for part in re.split(r"\n\s*\n", plain)]
        return "\n\n".join(part for part in paragraphs if part)

    @staticmethod
    def prepare_sapi_xml(text: str) -> str:
        plain = SpeechOutput.prepare_plain_text(text)
        paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", plain)]
        spoken_parts: list[str] = []
        for paragraph_index, paragraph in enumerate(part for part in paragraphs if part):
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence_index, sentence in enumerate(sentence for sentence in sentences if sentence):
                spoken_parts.append(escape(sentence))
                if sentence_index < len(sentences) - 1:
                    spoken_parts.append('<silence msec="170"/>')
            if paragraph_index < len(paragraphs) - 1:
                spoken_parts.append('<silence msec="320"/>')
        return "<sapi>" + " ".join(spoken_parts) + "</sapi>"

    @staticmethod
    def _pcm_samples(raw: bytes, sample_width: int) -> list[float]:
        """Decode little-endian PCM into normalized samples without optional packages."""
        if sample_width == 1:
            return [(value - 128) / 128.0 for value in raw]
        if sample_width == 2:
            values = array("h")
            values.frombytes(raw[:len(raw) - (len(raw) % 2)])
            if os.sys.byteorder != "little":
                values.byteswap()
            return [value / 32768.0 for value in values]
        if sample_width in {3, 4}:
            scale = float(1 << (sample_width * 8 - 1))
            usable = len(raw) - (len(raw) % sample_width)
            return [
                int.from_bytes(raw[index:index + sample_width], "little", signed=True) / scale
                for index in range(0, usable, sample_width)
            ]
        return []

    @staticmethod
    def speech_cues_from_wave(path: str | Path, interval_ms: int = 55) -> dict:
        """Build compact mouth cues from the amplitude envelope of a local PCM WAV."""
        cues: list[dict] = []
        with wave.open(str(path), "rb") as audio:
            frame_rate = max(1, audio.getframerate())
            frame_count = audio.getnframes()
            sample_width = audio.getsampwidth()
            channels = max(1, audio.getnchannels())
            frames_per_cue = max(1, round(frame_rate * max(25, interval_ms) / 1000))
            smoothed = 0.0
            frame_index = 0
            while frame_index < frame_count:
                raw = audio.readframes(min(frames_per_cue, frame_count - frame_index))
                samples = SpeechOutput._pcm_samples(raw, sample_width)
                if channels > 1 and samples:
                    samples = [max(abs(value) for value in samples[index:index + channels])
                               for index in range(0, len(samples), channels)]
                if samples:
                    rms = sqrt(sum(value * value for value in samples) / len(samples))
                    peak = max(abs(value) for value in samples)
                    measured = min(1.0, max(0.0, (rms - 0.008) * 4.2, peak * 1.35))
                else:
                    measured = 0.0
                # Fast attack and a softer release keep the jaw responsive without chatter.
                smoothed = measured if measured >= smoothed else smoothed * 0.58 + measured * 0.42
                level = 0.0 if smoothed < 0.025 else min(1.0, smoothed)
                cues.append({"at_ms": round(frame_index * 1000 / frame_rate),
                             "open": round(level, 3), "shape": "audio"})
                frame_index += frames_per_cue
        return {
            "source": "audio-envelope",
            "duration_ms": round(frame_count * 1000 / frame_rate),
            "cues": cues,
        }

    def speech_cues_from_text(self, text: str) -> dict:
        """Estimate visemes when the Windows fallback cannot expose audio samples."""
        plain = re.sub(r"\s+", " ", self.prepare_plain_text(text)).strip()
        if not plain:
            return {"source": "phoneme-timing", "duration_ms": 0, "cues": []}
        step = max(45, min(95, 72 - self.rate * 2))
        cues: list[dict] = []
        index = 0
        for character in plain:
            lower = character.lower()
            if character.isspace() or character in ".,!?;:":
                level, shape = (0.02, "closed")
            elif lower in "oquw":
                level, shape = (0.58, "round")
            elif lower in "aehiy":
                level, shape = (0.72, "wide")
            elif lower in "mbp":
                level, shape = (0.06, "closed")
            else:
                level, shape = (0.38, "neutral")
            cues.append({"at_ms": index * step, "open": level, "shape": shape})
            index += 1
        return {"source": "phoneme-timing", "duration_ms": index * step, "cues": cues}

    @staticmethod
    def _emit_cues(callback: Callable[[dict], None] | None, payload: dict) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            # Speech must remain available even if a UI disconnects mid-sentence.
            pass

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            process = self._process
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            self._process = None
            if winsound is not None:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except RuntimeError:
                    pass

    def speak(self, text: str, on_cues: Callable[[dict], None] | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Speech output is disabled."
        if not self.available():
            return False, "Local speech output is currently supported on Windows."
        if self.engine == "piper" and self.neural_available():
            neural_result = self._speak_piper(text, on_cues) if on_cues else self._speak_piper(text)
            if neural_result[0]:
                return neural_result
        return self._speak_sapi(text, on_cues) if on_cues else self._speak_sapi(text)

    def _speak_piper(self, text: str, on_cues: Callable[[dict], None] | None = None) -> tuple[bool, str]:
        plain = self.prepare_plain_text(text[:8000])
        if not plain:
            return False, "There is no text to speak."
        with self._lock:
            self._generation += 1
            generation = self._generation
            if winsound is not None:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except RuntimeError:
                    pass
        temporary_path: str | None = None
        try:
            from piper import PiperVoice, SynthesisConfig
            if self._neural_voice is None:
                self._neural_voice = PiperVoice.load(self.neural_model)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
                temporary_path = temporary.name
            length_scale = max(0.7, min(1.5, 1.0 - (self.rate * 0.035)))
            synthesis = SynthesisConfig(length_scale=length_scale, volume=self.volume / 100.0)
            with wave.open(temporary_path, "wb") as wav_file:
                self._neural_voice.synthesize_wav(plain, wav_file, syn_config=synthesis)
            with self._lock:
                if generation != self._generation:
                    return False, "Speech was superseded by a newer reply."
            self._emit_cues(on_cues, self.speech_cues_from_wave(temporary_path))
            winsound.PlaySound(temporary_path, winsound.SND_FILENAME)
            return True, "Spoken locally with Piper neural voice."
        except Exception as exc:
            return False, f"Neural speech is unavailable: {exc}"
        finally:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _speak_sapi(self, text: str, on_cues: Callable[[dict], None] | None = None) -> tuple[bool, str]:
        spoken = self.prepare_sapi_xml(text[:8000])
        if spoken == "<sapi></sapi>":
            return False, "There is no text to speak."
        safe_voice = self.voice.replace("'", "''")
        script = (
            "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$xml=[Console]::In.ReadToEnd(); "
            "$voice=New-Object -ComObject SAPI.SpVoice; "
            f"$voice.Rate={self.rate}; $voice.Volume={self.volume}; "
            f"$wanted='{safe_voice}'; "
            "for ($i=0; $i -lt $voice.GetVoices().Count; $i++) "
            "{ $candidate=$voice.GetVoices().Item($i); if ($candidate.GetDescription() -eq $wanted) "
            "{ $voice.Voice=$candidate; break } }; "
            "$null=$voice.Speak($xml,8)"
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._emit_cues(on_cues, self.speech_cues_from_text(text[:8000]))
            with self._lock:
                previous = self._process
                if previous and previous.poll() is None:
                    previous.terminate()
                process = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", shell=False,
                    creationflags=creation_flags,
                )
                self._process = process
            stdout, stderr = process.communicate(spoken, timeout=120)
            if process.returncode != 0:
                return False, stderr.strip() or "Windows speech failed."
            return True, "Spoken locally."
        except subprocess.TimeoutExpired as exc:
            if "process" in locals() and process.poll() is None:
                process.kill()
            return False, f"Local speech timed out: {exc}"
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            return False, f"Local speech is unavailable: {exc}"
        finally:
            with self._lock:
                if self._process is locals().get("process"):
                    self._process = None
