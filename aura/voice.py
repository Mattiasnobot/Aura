from __future__ import annotations

import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Callable


LevelCallback = Callable[[float, float], None]
TextCallback = Callable[[str], None]


class VoiceInput:
    """Cancelable, streaming, fully local microphone input for Aura."""

    SAMPLE_RATE = 16_000
    BLOCK_FRAMES = 800

    def __init__(self, *, engine: str = "auto", device: str | int | None = None,
                 language: str = "en", calibration_ms: int = 500,
                 silence_ms: int = 1200, max_seconds: int = 25,
                 noise_floor: float = 0.0, whisper_path: str | Path = "",
                 whisper_model: str | Path = "") -> None:
        self._lock = threading.RLock()
        self._finish = threading.Event()
        self._cancel = threading.Event()
        self._active = False
        self.engine = "auto"
        self.device = ""
        self.language = "en"
        self.calibration_ms = 500
        self.silence_ms = 1200
        self.max_seconds = 25
        self.noise_floor = 0.0
        self.whisper_path = Path()
        self.whisper_model = Path()
        self.configure(
            engine=engine, device=device, language=language,
            calibration_ms=calibration_ms, silence_ms=silence_ms,
            max_seconds=max_seconds, noise_floor=noise_floor,
            whisper_path=whisper_path, whisper_model=whisper_model,
        )

    def configure(self, *, engine: str, device: str | int | None, language: str,
                  calibration_ms: int, silence_ms: int, max_seconds: int,
                  noise_floor: float = 0.0, whisper_path: str | Path = "",
                  whisper_model: str | Path = "") -> None:
        self.engine = engine if engine in {"auto", "pocketsphinx", "whisper_cpp"} else "auto"
        self.device = "" if device in {None, ""} else str(device)
        self.language = re.sub(r"[^a-zA-Z-]", "", str(language))[:12] or "en"
        self.calibration_ms = max(200, min(int(calibration_ms), 2000))
        self.silence_ms = max(500, min(int(silence_ms), 3000))
        self.max_seconds = max(5, min(int(max_seconds), 60))
        self.noise_floor = max(0.0, min(float(noise_floor), 1.0))
        self.whisper_path = Path(whisper_path).expanduser() if str(whisper_path).strip() else Path()
        self.whisper_model = Path(whisper_model).expanduser() if str(whisper_model).strip() else Path()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @staticmethod
    def _imports() -> tuple[object | None, object | None]:
        try:
            import sounddevice as sound_device  # type: ignore
        except ImportError:
            sound_device = None
        try:
            from pocketsphinx import Decoder  # type: ignore
        except ImportError:
            Decoder = None
        return sound_device, Decoder

    def _whisper_executable(self) -> Path | None:
        if self.whisper_path.is_file():
            return self.whisper_path.resolve()
        for name in ("whisper-cli.exe", "whisper-cli", "main.exe", "main"):
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        return None

    def whisper_available(self) -> bool:
        return self._whisper_executable() is not None and self.whisper_model.is_file()

    def capabilities(self) -> dict:
        sound_device, Decoder = self._imports()
        active_engine = ("whisper_cpp" if self.engine in {"auto", "whisper_cpp"}
                         and self.whisper_available() else
                         "pocketsphinx" if Decoder is not None else "unavailable")
        return {
            "streaming": sound_device is not None,
            "pocketsphinx": Decoder is not None,
            "whisper_cpp": self.whisper_available(),
            "selected_engine": self.engine,
            "active_engine": active_engine,
        }

    def devices(self) -> list[dict]:
        sound_device, _ = self._imports()
        if sound_device is None:
            return []
        devices: list[dict] = []
        try:
            default_input = int(sound_device.default.device[0])
            host_apis = sound_device.query_hostapis()
            for index, device in enumerate(sound_device.query_devices()):
                if int(device.get("max_input_channels", 0)) < 1:
                    continue
                try:
                    sound_device.check_input_settings(
                        device=index, channels=1, dtype="int16", samplerate=self.SAMPLE_RATE)
                except Exception:
                    continue
                host_index = int(device.get("hostapi", 0))
                host = str(host_apis[host_index].get("name", "Audio")) if host_index < len(host_apis) else "Audio"
                devices.append({
                    "id": str(index), "name": str(device.get("name", f"Microphone {index}")),
                    "host": host, "default": index == default_input,
                })
        except Exception:
            return []
        return devices

    @staticmethod
    def _rms(raw: bytes) -> float:
        values = array("h")
        values.frombytes(raw[:len(raw) - (len(raw) % 2)])
        if os.sys.byteorder != "little":
            values.byteswap()
        if not values:
            return 0.0
        return math.sqrt(sum(value * value for value in values) / len(values)) / 32768.0

    @staticmethod
    def _meter(rms: float) -> float:
        decibels = 20 * math.log10(max(rms, 0.000001))
        return max(0.0, min(1.0, (decibels + 60) / 48))

    def _device_index(self) -> int | None:
        if not self.device:
            return None
        try:
            return int(self.device)
        except ValueError:
            return None

    def request_stop(self, *, cancel: bool = False) -> None:
        if cancel:
            self._cancel.set()
        else:
            self._finish.set()

    def stop(self) -> None:
        self.request_stop(cancel=True)

    def calibrate(self, *, on_level: LevelCallback | None = None,
                  duration_ms: int | None = None) -> tuple[bool, dict | str]:
        sound_device, _ = self._imports()
        if sound_device is None:
            return False, "Live microphone support is not installed. Text chat is ready."
        duration = max(200, min(int(duration_ms or self.calibration_ms), 2500))
        readings: list[float] = []
        try:
            with sound_device.RawInputStream(
                samplerate=self.SAMPLE_RATE, blocksize=self.BLOCK_FRAMES,
                device=self._device_index(), channels=1, dtype="int16",
            ) as stream:
                deadline = time.monotonic() + duration / 1000
                while time.monotonic() < deadline:
                    raw, _overflowed = stream.read(self.BLOCK_FRAMES)
                    rms = self._rms(bytes(raw))
                    readings.append(rms)
                    if on_level:
                        on_level(self._meter(rms), rms)
        except Exception as exc:
            return False, f"Microphone calibration failed safely: {exc}"
        if not readings:
            return False, "The microphone produced no calibration samples."
        ordered = sorted(readings)
        quiet = ordered[:max(1, round(len(ordered) * 0.7))]
        floor = sum(quiet) / len(quiet)
        self.noise_floor = floor
        return True, {
            "noise_floor": round(floor, 6),
            "threshold": round(max(0.012, floor * 2.6), 6),
            "meter": round(self._meter(floor), 3),
        }

    def listen(self, *, mode: str = "toggle", on_level: LevelCallback | None = None,
               on_partial: TextCallback | None = None,
               on_status: TextCallback | None = None) -> tuple[bool, str]:
        sound_device, Decoder = self._imports()
        if sound_device is None:
            return self._legacy_listen()
        if Decoder is None and not self.whisper_available():
            return False, "Offline recognition needs PocketSphinx or a configured Whisper.cpp model."
        with self._lock:
            if self._active:
                return False, "Aura is already listening."
            self._active = True
            self._finish.clear()
            self._cancel.clear()
        try:
            if on_status:
                on_status("calibrating")
            audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=80)

            def receive(indata: object, _frames: int, _time_info: object, _status: object) -> None:
                try:
                    audio_queue.put_nowait(bytes(indata))
                except queue.Full:
                    try:
                        audio_queue.get_nowait()
                        audio_queue.put_nowait(bytes(indata))
                    except (queue.Empty, queue.Full):
                        pass

            decoder = Decoder(samprate=self.SAMPLE_RATE) if Decoder is not None else None
            if decoder is not None:
                decoder.start_utt()
            recorded: list[bytes] = []
            calibration: list[float] = []
            calibration_deadline = time.monotonic() + self.calibration_ms / 1000
            started_at = time.monotonic()
            speech_started = False
            last_voice = started_at
            last_partial = ""
            next_partial = started_at
            threshold = max(0.012, self.noise_floor * 2.6) if self.noise_floor else 0.018
            if on_status and self.noise_floor:
                on_status("listening")
                calibration_deadline = started_at
            with sound_device.RawInputStream(
                samplerate=self.SAMPLE_RATE, blocksize=self.BLOCK_FRAMES,
                device=self._device_index(), channels=1, dtype="int16", callback=receive,
            ):
                while True:
                    now = time.monotonic()
                    if self._cancel.is_set():
                        return False, "Voice input cancelled."
                    if now - started_at >= self.max_seconds:
                        break
                    if self._finish.is_set() and recorded:
                        break
                    try:
                        raw = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    rms = self._rms(raw)
                    if on_level:
                        on_level(self._meter(rms), rms)
                    if now < calibration_deadline:
                        calibration.append(rms)
                    elif calibration:
                        ordered = sorted(calibration)
                        quiet = ordered[:max(1, round(len(ordered) * 0.7))]
                        self.noise_floor = sum(quiet) / len(quiet)
                        threshold = max(0.012, self.noise_floor * 2.6)
                        calibration.clear()
                        if on_status:
                            on_status("listening")
                    recorded.append(raw)
                    if decoder is not None:
                        decoder.process_raw(raw, False, False)
                    if rms >= threshold:
                        speech_started = True
                        last_voice = now
                    if decoder is not None and on_partial and now >= next_partial:
                        hypothesis = decoder.hyp()
                        partial = hypothesis.hypstr.strip() if hypothesis else ""
                        if partial and partial != last_partial:
                            last_partial = partial
                            on_partial(partial)
                        next_partial = now + 0.28
                    if mode != "hold" and speech_started and (now - last_voice) * 1000 >= self.silence_ms:
                        break
                    if mode != "hold" and not speech_started and now - started_at > 7:
                        return False, "I couldn’t hear speech. Check the selected microphone or try again."
            if on_status:
                on_status("processing")
            if decoder is not None:
                decoder.end_utt()
            if not recorded:
                return False, "No speech was captured. Hold the voice button while speaking."
            raw_audio = b"".join(recorded)
            if self.engine in {"auto", "whisper_cpp"} and self.whisper_available():
                whisper_result = self._recognize_whisper(raw_audio)
                if whisper_result[0] or self.engine == "whisper_cpp":
                    return whisper_result
            if decoder is not None:
                hypothesis = decoder.hyp()
                text = hypothesis.hypstr.strip() if hypothesis else ""
                if text:
                    return True, text
            return False, "I heard audio but couldn’t understand the words. Please retry or use text."
        except Exception as exc:
            return False, f"Voice input is unavailable: {exc}"
        finally:
            with self._lock:
                self._active = False
            self._finish.clear()
            self._cancel.clear()

    def _recognize_whisper(self, raw_audio: bytes) -> tuple[bool, str]:
        executable = self._whisper_executable()
        if executable is None or not self.whisper_model.is_file():
            return False, "Whisper.cpp is not configured."
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
                temporary_path = temporary.name
            with wave.open(temporary_path, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.SAMPLE_RATE)
                output.writeframes(raw_audio)
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                [str(executable), "-m", str(self.whisper_model.resolve()), "-f", temporary_path,
                 "-l", self.language, "-nt", "-np"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=90, shell=False, creationflags=creation_flags, check=False,
            )
            if completed.returncode != 0:
                return False, completed.stderr.strip() or "Whisper.cpp transcription failed."
            lines = []
            for line in completed.stdout.splitlines():
                cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", line).strip()
                if cleaned and not cleaned.lower().startswith(("whisper_", "system_info")):
                    lines.append(cleaned)
            text = " ".join(lines).strip()
            return (True, text) if text else (False, "Whisper.cpp returned no speech.")
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Whisper.cpp is unavailable: {exc}"
        finally:
            if temporary_path:
                Path(temporary_path).unlink(missing_ok=True)

    def _legacy_listen(self) -> tuple[bool, str]:
        try:
            import speech_recognition as sr  # type: ignore
        except ImportError:
            return False, "Voice input is optional and not installed. Text chat is ready."
        try:
            recognizer = sr.Recognizer()
            device_index = self._device_index()
            with sr.Microphone(device_index=device_index) as source:
                recognizer.adjust_for_ambient_noise(source, duration=self.calibration_ms / 1000)
                audio = recognizer.listen(source, timeout=7, phrase_time_limit=self.max_seconds)
            return True, recognizer.recognize_sphinx(audio)
        except sr.UnknownValueError:
            return False, "I couldn’t understand that. Please try again or use text."
        except sr.RequestError:
            return False, "Offline voice recognition needs PocketSphinx. Text chat is ready."
        except Exception as exc:
            return False, f"Voice input is unavailable: {exc}"
