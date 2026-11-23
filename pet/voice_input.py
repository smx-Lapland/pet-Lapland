"""Microphone capture, hands-free turn detection, and local transcription."""
from __future__ import annotations

import threading
from array import array
from collections import deque
from pathlib import Path

import numpy as np

RATE = 16000
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "whisper-base"
FILES = ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt")
MIC_THRESHOLDS = {"灵敏": -36, "较高": -30, "很高": -24}


class VoiceInputError(Exception):
    pass


class SpeechGate:
    """Emit one utterance, then discard input until the reply has finished."""

    frame_size = RATE // 50  # WebRTC takes exactly 20 ms of mono PCM per frame.

    def __init__(self, threshold_db):
        import webrtcvad

        self.vad = webrtcvad.Vad(3)
        self.floor = 32768 * 10 ** (threshold_db / 20)
        self.noise = 0.0
        self.lock = threading.Lock()
        self.armed = False
        self.resume()

    def resume(self, skip_ms=0):
        with self.lock:
            self.pending = bytearray()
            self.preroll = deque(maxlen=22)
            self.onsets = deque(maxlen=10)
            self.audio = bytearray()
            self.quiet = self.voiced = 0
            self.skip = RATE * skip_ms // 1000 * 2
            self.armed = True

    def feed(self, samples):
        with self.lock:
            if not self.armed:
                return None
            data = memoryview(samples).cast("B")
            discard = min(self.skip, len(data))
            self.skip -= discard
            self.pending.extend(data[discard:])
            size = self.frame_size * 2
            offset = 0
            while offset + size <= len(self.pending):
                frame = bytes(self.pending[offset:offset + size])
                offset += size
                result = self._frame(frame)
                if result is not None:
                    self.armed = False
                    self.pending.clear()
                    return result
            del self.pending[:offset]
            return None

    def _frame(self, frame):
        pcm = np.frombuffer(frame, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(pcm * pcm)))
        speech = self.vad.is_speech(frame, RATE)
        threshold = max(self.floor, self.noise * 3)
        if not self.audio:
            if not speech:
                self.noise += .025 * (min(rms, self.floor * 2) - self.noise)
            self.preroll.append(frame)
            self.onsets.append(speech and rms >= threshold)
            if sum(self.onsets) >= 4:
                self.audio = bytearray(b"".join(self.preroll))
                self.voiced = sum(self.onsets)
                self.preroll.clear()
                self.onsets.clear()
            return None

        self.audio.extend(frame)
        if speech and rms >= threshold * .32:
            self.quiet = 0
            self.voiced += 1
        else:
            self.quiet += 1
        if self.quiet < 40 and len(self.audio) < 2 * RATE * 45:
            return None
        samples = None
        if self.voiced >= 8:
            samples = array("h")
            samples.frombytes(self.audio)
        self.audio.clear()
        self.quiet = self.voiced = 0
        return samples


def load_recognizer(language=""):
    import sherpa_onnx

    encoder, decoder, tokens = (str(MODEL_DIR / name) for name in FILES)
    return sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=encoder, decoder=decoder, tokens=tokens, language=language, task="transcribe", num_threads=2)


def recognize_audio(recognizer, audio, cancelled=None):
    import numpy as np

    chunks = []
    while audio.size and (cancelled is None or not cancelled.is_set()):
        end = audio.size
        if end > 28 * RATE:
            # Split in a quiet interval within Whisper's context window.
            window = audio[22 * RATE:27 * RATE].reshape(-1, RATE // 10)
            end = 22 * RATE + int(np.argmin(np.mean(window ** 2, axis=1))) * (RATE // 10) + RATE // 20
        stream = recognizer.create_stream()
        stream.accept_waveform(RATE, audio[:end])
        recognizer.decode_stream(stream)
        chunks.append(stream.result.text.strip())
        audio = audio[end:]
    return " ".join(part for part in chunks if part)


class VoiceInput:
    def __init__(self):
        self.device = None
        self.samples = array("h")
        self.recognizer = None
        self.decode_lock = threading.Lock()

    def start(self, on_samples=None):
        import miniaudio

        if not all((MODEL_DIR / name).is_file() for name in FILES):
            raise VoiceInputError("缺少语音识别模型。请运行 tools/setup_voice_input.py 后再试。")
        self.samples = array("h")
        receive = self.samples.extend if on_samples is None else on_samples
        def collect():
            while True:
                receive((yield))
        stream = collect()
        next(stream)
        try:
            self.device = miniaudio.CaptureDevice(input_format=miniaudio.SampleFormat.SIGNED16,
                                                  nchannels=1, sample_rate=RATE, buffersize_msec=40)
            self.device.start(stream)
        except Exception as exc:
            self.finish()
            raise VoiceInputError("无法打开麦克风。请检查 Windows 默认输入设备和麦克风权限。") from exc

    def finish(self):
        if self.device is not None:
            self.device.close()
            self.device = None
        samples, self.samples = self.samples, array("h")
        return samples

    def transcribe(self, samples, cancelled):
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32) / 32768
        block = RATE // 50
        frames = audio[:len(audio) // block * block].reshape(-1, block)
        voiced = np.flatnonzero(np.mean(frames ** 2, axis=1) > 0.002 ** 2)
        if voiced.size < 6:
            return ""
        audio = audio[max(0, (voiced[0] - 10) * block):(voiced[-1] + 12) * block]
        with self.decode_lock:
            if cancelled.is_set():
                return ""
            if self.recognizer is None:
                self.recognizer = load_recognizer()
            return recognize_audio(self.recognizer, audio, cancelled)
