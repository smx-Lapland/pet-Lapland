"""Push-to-talk recording and local multilingual transcription."""
from __future__ import annotations

import threading
from array import array
from pathlib import Path

RATE = 16000
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "whisper-base"
FILES = ("base-encoder.int8.onnx", "base-decoder.int8.onnx", "base-tokens.txt")


class VoiceInputError(Exception):
    pass


class VoiceInput:
    def __init__(self):
        self.device = None
        self.samples = array("h")
        self.recognizer = None
        self.decode_lock = threading.Lock()

    def start(self):
        import miniaudio

        if not all((MODEL_DIR / name).is_file() for name in FILES):
            raise VoiceInputError("缺少语音识别模型。请运行 tools/setup_voice_input.py 后再试。")
        self.samples = array("h")
        def collect():
            while True:
                self.samples.extend((yield))
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
        import sherpa_onnx

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
                encoder, decoder, tokens = (str(MODEL_DIR / name) for name in FILES)
                self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=encoder, decoder=decoder, tokens=tokens, language="", task="transcribe",
                    num_threads=2)
            chunks = []
            while audio.size and not cancelled.is_set():
                end = audio.size
                if end > 28 * RATE:
                    # Leave room in Whisper's context and split in a quiet 100 ms interval.
                    window = audio[22 * RATE:27 * RATE].reshape(-1, RATE // 10)
                    end = 22 * RATE + int(np.argmin(np.mean(window ** 2, axis=1))) * (RATE // 10) + RATE // 20
                stream = self.recognizer.create_stream()
                stream.accept_waveform(RATE, audio[:end])
                self.recognizer.decode_stream(stream)
                chunks.append(stream.result.text.strip())
                audio = audio[end:]
            return " ".join(part for part in chunks if part).strip()
