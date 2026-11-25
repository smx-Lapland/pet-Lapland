from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from .config import CACHE_DIR
from .gpt_sovits_en import RUNTIME, installed_voice
from . import italian_reference
from .voice_device import cuda_installed

log = logging.getLogger("mochi.speech")

try:
    import winsound
except Exception:  # pragma: no cover - only on Windows
    winsound = None

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SpeechEngine:
    def __init__(self, cfg: dict[str, Any], script_path: Path):
        self.voice = str(cfg.get("voice", "Zira"))
        self.provider = cfg.get("speech_provider", "lapland")
        self.language = "it" if cfg.get("speech_language") == "it" else "en"
        self.character_id = cfg.get("character_id", "lapland")
        self.english_bundle = None
        self.english_python = None
        self.italian_python = None
        if self.provider == "vits" and self.language == "en":
            self.english_bundle, self.english_python = installed_voice(self.character_id)
        if (self.provider == "vits" and self.language == "it" and cfg.get("italian_reference_voice")
                and not cfg.get("verify_italian_voice") and cfg.get("voice_device", "auto") == "auto"):
            self.italian_python = italian_reference.installed_python()
        self.device = ("cuda" if cfg.get("voice_device", "auto") == "auto"
                       and self.provider in ("lapland", "vits", "piper")
                       and (self.english_bundle or self.italian_python or cuda_installed()) else "cpu")
        self.gpu_retry_at = None
        self.neural_voice = (cfg.get("neural_voice_it", "it-IT-ElsaNeural") if self.language == "it"
                             else cfg.get("neural_voice", "en-US-JennyNeural"))
        self.rate = int(cfg.get("rate", 0))
        self.api = {k: cfg.get(k, "") for k in ("tts_base", "tts_model", "tts_voice", "tts_key_env")}
        self.muted = bool(cfg.get("muted", False))
        self.verify_italian_voice = bool(cfg.get("verify_italian_voice", False))
        self.script_path = script_path
        self._powershell = self._find_powershell() if sys.platform == "win32" else None
        self._mac_voice = None
        self._play_lock = threading.Lock()
        self._renderer_lock = threading.Lock()
        self._renderer = None
        self._standby = False

    def _neural_command(self):
        # Launch the interpreter directly so cancellation also stops native inference.
        if self.english_bundle:
            return [str(self.english_python), str(Path(__file__).with_name("gpt_sovits_en.py"))]
        if self.italian_python and self.device == "cuda":
            return [str(self.italian_python), str(Path(__file__).with_name("italian_reference.py"))]
        python = sys.executable
        if sys.platform == "win32":
            python = getattr(sys, "_base_executable", None) or python
        return [python,
                str(Path(__file__).with_name("neural_worker.py"))]

    def _sapi_command(self):
        return [self._powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", str(self.script_path)]

    def _neural_env(self):
        env = {**os.environ, "PET_TTS_DEVICE": self.device}
        if sys.platform == "win32":
            env["__PYVENV_LAUNCHER__"] = sys.executable
        else:
            env.pop("__PYVENV_LAUNCHER__", None)
        if self.provider == "vits" and self.language == "it":
            env["PET_VERIFY_ITALIAN"] = "1" if self.verify_italian_voice else "0"
        if self.english_bundle:
            env.update(__PYVENV_LAUNCHER__=str(RUNTIME / "venv/Scripts/python.exe"),
                       PET_ENGLISH_WEIGHTS=str(self.english_bundle), PYTHONUTF8="1")
        elif self.italian_python and self.device == "cuda":
            env.update(__PYVENV_LAUNCHER__=str(italian_reference.RUNTIME / "venv/Scripts/python.exe"),
                       HF_HUB_OFFLINE="1", PYTHONUTF8="1")
        if self.english_bundle or (self.italian_python and self.device == "cuda"):
            conda_bin = (self.english_python or self.italian_python).parent / "Library/bin"
            if conda_bin.is_dir():
                env["PATH"] = str(conda_bin) + os.pathsep + env.get("PATH", "")
        return env

    def _retry_gpu_if_due(self):
        if self.gpu_retry_at is not None and time.monotonic() >= self.gpu_retry_at:
            self.close_renderer()
            self.device = "cuda"
            self.gpu_retry_at = None

    def _use_cpu_fallback(self):
        self.device = "cpu"
        self.gpu_retry_at = time.monotonic() + 30

    def warmup(self, *, standby=False):
        if self.muted or self.provider not in ("sapi", "vits", "lapland"):
            return
        if self.provider == "sapi" and sys.platform == "darwin":
            return  # say renders each utterance directly.
        self._retry_gpu_if_due()
        with self._renderer_lock:
            if self._renderer is not None:
                if self._renderer.poll() is None:
                    if not standby and self._standby:
                        try:
                            self._renderer.stdin.write(b'{"wake":true}\n')
                            self._renderer.stdin.flush()
                        except OSError:
                            pass  # A job will observe the exit and use normal recovery.
                        self._standby = False
                    return
                self._renderer.stdin.close()
                self._renderer = None
            if self.provider == "sapi":
                cmd, env = self._sapi_command() + ["-Session"], None
            else:
                cmd = self._neural_command() + ["--session", self.provider, self.language]
                env = self._neural_env()
                if standby and self.english_bundle:
                    cmd.append("--standby")
            try:
                self._renderer = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, env=env)
                self._standby = standby and bool(self.english_bundle)
            except OSError:
                log.warning("Could not prestart local voice worker")

    def close_renderer(self, expected=None):
        with self._renderer_lock:
            proc = self._renderer
            if proc is None or (expected is not None and expected is not proc):
                return
            self._renderer = None
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        try:
            proc.stdin.close()
        except OSError:
            pass  # A write in flight may have lost its reader during cancellation.

    def _render_prepared(self, proc, job, path, stop_event, timeout):
        complete = False
        try:
            if not stop_event.is_set():
                proc.stdin.write(json.dumps(job, ensure_ascii=True).encode("ascii") + b"\n")
                proc.stdin.flush()
            deadline = time.monotonic() + timeout
            while not stop_event.is_set() and time.monotonic() < deadline:
                if path.is_file():
                    complete = wav_duration(path) > 0
                    return complete
                if proc.poll() is not None:
                    return None  # A failed warm worker can retry the ordinary one-shot path.
                stop_event.wait(.02)
        except BrokenPipeError:
            return None
        except (OSError, ValueError, EOFError, wave.Error):
            pass
        finally:
            if not complete:
                self.close_renderer(proc)
                proc.wait()
                Path(str(path) + ".pending").unlink(missing_ok=True)
                path.unlink(missing_ok=True)
        return False

    @staticmethod
    def _find_powershell() -> str:
        candidates = [
            Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
            Path(r"C:\Windows\System32\powershell.exe"),
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        return "powershell.exe"

    def stop(self) -> None:
        if winsound is not None:
            try:
                with self._play_lock:
                    winsound.PlaySound(None, 0)
            except Exception:
                pass
        self.close_renderer()

    def prepare_segment(self, text, stop_event, provider, emotion=None):
        if self.muted or stop_event.is_set():
            return None, "muted"
        self._retry_gpu_if_due()
        path = CACHE_DIR / f"tts_{uuid.uuid4().hex}.wav"
        job = {"path": str(path), "text": sanitize_for_tts(text, self.language), "rate": self.rate,
               "voice": self.voice if provider == "sapi" else self.neural_voice,
               "provider": provider, "language": self.language, "emotion": emotion}
        timeout = {"sapi": 30, "lapland": 90, "vits": 90, "api": 90, "piper": 90}.get(provider, 18)
        if provider == "vits" and self.language == "it":
            timeout = 150
        with self._renderer_lock:
            proc = self._renderer if provider == self.provider and not stop_event.is_set() else None
        if proc is not None:
            rendered = self._render_prepared(proc, job, path, stop_event, timeout)
            if rendered:
                return path, provider
            if stop_event.is_set():
                return None, "muted"
            if self.device == "cuda":
                log.warning("Local GPU worker failed; retrying the same voice on CPU")
                self._use_cpu_fallback()
                proc = None
            elif rendered is None:
                proc = None
        if provider in ("api", "piper", "azure", "edge", "lapland", "vits") and proc is None:
            if provider == "api":
                job.update(self.api)
            if provider == "piper":
                from .characters import character
                job["model_path"] = str(character(self.character_id).folder / "voice.onnx")
            while not stop_event.is_set():
                try:
                    ok = run_worker(self._neural_command(), stop_event, timeout,
                                    json.dumps(job).encode("utf-8"), env=self._neural_env())
                    if ok and wav_duration(path) > 0:
                        return path, provider
                except (OSError, ValueError, EOFError, wave.Error):
                    pass
                path.unlink(missing_ok=True)
                if stop_event.is_set():
                    return None, "muted"
                if self.device != "cuda":
                    break
                log.warning("Local GPU worker failed; retrying the same voice on CPU")
                self._use_cpu_fallback()
            log.warning("%s voice unavailable, using system speech", provider)
        paths = self.synthesize([text], stop_event)
        return (paths[0], "sapi") if paths else (None, "unavailable")

    def _render_mac_speech(self, texts, paths, stop_event):
        if not self._mac_voice:
            listing = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True,
                                     text=True, encoding="utf-8", check=True, timeout=10).stdout
            voices = []
            for line in listing.splitlines():
                match = re.match(r"^(.+?)\s+([a-z]{2,3})[_-]\S+\s+#", line)
                if match and match[2] == self.language:
                    voices.append(match[1].strip())
            if not voices:
                log.warning("No macOS system voice installed for %s", self.language)
                return False
            self._mac_voice = next((v for v in voices if v.casefold() == self.voice.casefold()), voices[0])
        for text, path in zip(texts, paths):
            command = ["/usr/bin/say", "-v", self._mac_voice, "-o", str(path),
                       "--file-format=WAVE", "--data-format=LEI16@22050"]
            if self.rate:
                command.extend(["-r", str(round(180 * 2 ** (max(-10, min(10, self.rate)) / 10)))])
            if not run_worker(command, stop_event, 30, sanitize_for_tts(text, self.language).encode("utf-8")):
                return False
            if wav_duration(path) <= 0:
                return False
        return True

    def synthesize(self, texts: list[str], stop_event=None) -> list[Path] | None:
        texts = [t for t in texts if t.strip()]
        stop_event = stop_event or threading.Event()
        if not texts or self.muted or stop_event.is_set():
            return None
        job_id = uuid.uuid4().hex[:12]
        paths = [CACHE_DIR / f"tts_{job_id}_{i}.wav" for i in range(len(texts))]
        job_path = CACHE_DIR / f"tts_{job_id}.json"
        result_path = CACHE_DIR / f"tts_{job_id}.result.json"
        job = {
            "voice": self.voice,
            "language": self.language,
            "rate": max(-10, min(10, self.rate)),
            "items": [
                {"text": sanitize_for_tts(text, self.language), "path": str(path)}
                for text, path in zip(texts, paths)
            ],
        }
        success = False
        try:
            if sys.platform == "darwin":
                success = self._render_mac_speech(texts, paths, stop_event)
                return paths if success else None
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            ok = run_worker(
                self._sapi_command() + ["-JobFile", str(job_path), "-ResultFile", str(result_path)],
                stop_event, 30,
            )
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                result = {}
            if not ok or not result.get("ok"):
                if not stop_event.is_set():
                    log.warning("local voice unavailable")
                return None
            items = result.get("items") or []
            if len(items) != len(texts) or any(not item.get("ok") for item in items):
                log.warning("local voice failed to synthesize a segment")
                return None
            for path in paths:
                if not path.exists() or path.stat().st_size < 1000:
                    log.error("tts produced an empty file: %s", path)
                    return None
            success = True
            return paths
        except Exception:
            log.exception("tts synthesis failed")
            return None
        finally:
            for path in [job_path, result_path] + ([] if success else paths):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def play_file(self, path: Path, stop_event) -> bool:
        if self.muted or stop_event.is_set():
            return False
        if sys.platform == "darwin":
            return run_worker(["/usr/bin/afplay", str(path)], stop_event, wav_duration(path) + 10)
        if winsound is None:
            return False
        try:
            duration = wav_duration(path)
        except Exception:
            duration = 4.0
        try:
            with self._play_lock:
                if stop_event.is_set():
                    return False
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            log.exception("audio playback unavailable")
            return False
        stop_event.wait(duration + 0.05)
        return True

    def wait_timed(self, text: str, stop_event, duration: float | None = None) -> None:
        stop_event.wait(subtitle_duration(text) if duration is None else duration)


def subtitle_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return min(90.0, max(2.8, words * 0.42 + 1.1))


def run_worker(command, stop_event, timeout, data=None, env=None) -> bool:
    if stop_event.is_set():
        return False
    with subprocess.Popen(command, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=CREATE_NO_WINDOW, env=env) as proc:
        try:
            if data:
                proc.stdin.write(data)
                proc.stdin.close()
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if stop_event.wait(0.05) or time.monotonic() >= deadline:
                    return False
            return proc.returncode == 0
        finally:
            if proc.poll() is None:
                proc.kill()


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        return frames / float(rate) if rate else 4.0


def sanitize_for_tts(text: str, language: str = "en") -> str:
    """Turn code-ish tokens into words a plain English voice can pronounce."""
    text = str(text or "")
    if language == "it":
        return re.sub(r"\s+", " ", re.sub(r"[*_`#>\[\]{}|]+", " ", text)).strip()
    text = re.sub(r"\bf-string\b", "F string", text, flags=re.I)
    text = re.sub(r"\bTypeError\b", "type error", text)
    text = re.sub(r"\bValueError\b", "value error", text)
    text = re.sub(r"\bNone\b", "none", text)
    text = re.sub(r"\bstr\b", "string", text)
    text = re.sub(r"[*_`#>\[\]{}|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "I found something worth a look."
