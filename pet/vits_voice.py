"""Local Piper speech with a language- and emotion-specific Lapland reference."""
import json
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort
import parselmouth
import miniaudio
from parselmouth.praat import call
from piper import PiperVoice
from piper.config import PiperConfig, SynthesisConfig
from .voice_input import FILES, MODEL_DIR, load_recognizer, recognize_audio

ROOT = Path(__file__).resolve().parents[1]
RATE = 22050
MODELS = ROOT / "models" / "piper-vits-reference"
REFERENCES = ROOT / "assets" / "lapland" / "voice" / "emotions"


def pitch_track(samples):
    pitch = parselmouth.Sound(samples, RATE).to_pitch_ac(time_step=.0125, pitch_floor=80, pitch_ceiling=620)
    hz = pitch.selected_array["frequency"]
    return pitch.xs()[hz > 0], 12 * np.log2(hz[hz > 0])


def reference_pause(samples):
    frame = RATE // 100
    frames = samples[:len(samples) // frame * frame].reshape(-1, frame)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    voiced = np.flatnonzero(rms > max(.002, np.percentile(rms, 90) * .045))
    gaps = (np.diff(voiced) - 1) * frame / RATE
    gaps = gaps[(gaps >= .09) & (gaps < 1.)]
    return float(np.clip(np.median(gaps), .12, .85)) if gaps.size else .15


def follow_contour(audio, reference):
    times, notes = pitch_track(audio)
    if len(notes) < 12 or len(reference) < 12:
        return audio
    # Guide the overall arc using the reference, retaining local syllable stresses.
    knots = np.linspace(0, 1, 7)
    phase = np.linspace(0, 1, len(notes))
    ref_curve = np.array([np.median(v) for v in np.array_split(reference, 7)])
    own_curve = np.array([np.median(v) for v in np.array_split(notes, 7)])
    shift = np.clip(.34 * (ref_curve - own_curve), -2.3, 2.3)
    target = 2 ** ((notes + np.interp(phase, knots, shift)) / 12)
    sound = parselmouth.Sound(audio, RATE)
    manip = call(sound, "To Manipulation", .0125, 80, 620)
    tier = call("Create PitchTier", "delivery", 0, sound.duration)
    for t, hz in zip(times, target):
        call(tier, "Add point", t, hz)
    call([tier, manip], "Replace pitch tier")
    return call(manip, "Get resynthesis (overlap-add)").values[0].astype(np.float32)


def spectrum(audio):
    padded = np.pad(audio, (384, 384), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, 1024)[::256]
    bins = np.fft.rfft(frames * np.hanning(1025)[:-1])
    return np.ascontiguousarray(np.sqrt(np.abs(bins.T) ** 2 + 1e-6)[None], dtype=np.float32)


def words(line):
    folded = unicodedata.normalize("NFKD", line.casefold().replace("’", "'"))
    return re.findall(r"[^\W_]+", "".join(c for c in folded if not unicodedata.combining(c)))


def word_errors(expected, heard):
    costs = list(range(len(heard) + 1))
    for i, word in enumerate(expected, 1):
        previous, costs = costs, [i]
        for j, actual in enumerate(heard, 1):
            costs.append(min(previous[j] + 1, costs[-1] + 1, previous[j - 1] + (actual != word)))
    return costs[-1]


def resample(audio, source_rate, target_rate):
    fmt = miniaudio.SampleFormat.FLOAT32
    data = miniaudio.convert_frames(fmt, 1, source_rate, np.asarray(audio, dtype="<f4").tobytes(),
                                    fmt, 1, target_rate)
    return np.frombuffer(data, dtype="<f4")


class ReferenceVoice:
    def __init__(self, language):
        self.language = language
        self.recognizer = None
        model = MODELS / f"lapland-{language}.onnx"
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        def session(path):
            return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        config = PiperConfig.from_dict(json.loads(Path(str(model) + ".json").read_text("utf-8")))
        self.piper = PiperVoice(session(model), config)
        self.converter = session(MODELS / "tone.onnx")
        with np.load(MODELS / f"{language}-references.npz", allow_pickle=False) as bank:
            self.targets = {name: bank[name] for name in bank.files}

    def reference(self, emotion):
        with wave.open(str(REFERENCES / self.language / (emotion + ".wav"))) as wav:
            reference = np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768
        return pitch_track(reference)[1], reference_pause(reference)

    def convert(self, audio, emotion):
        return self.converter.run(None, {"source": spectrum(audio), "target": self.targets[emotion]})[0][0, 0]

    def generate(self, text, emotion="NEUTRAL", rate=0, *, contour=True):
        if emotion not in self.targets:
            emotion = "NEUTRAL"
        reference_pitch, pause = self.reference(emotion)
        gap = np.zeros(round(RATE * pause), np.float32)
        cfg = SynthesisConfig(length_scale=2 ** (-max(-10, min(10, rate)) / 10))
        parts = []
        for chunk in self.piper.synthesize(text, cfg):
            audio = chunk.audio_float_array
            if len(audio) < 512:
                continue
            converted = self.convert(audio, emotion)
            if parts:
                parts.append(gap)
            parts.append(follow_contour(converted, reference_pitch) if contour else converted)
        if not parts:
            raise ValueError("No speech generated")
        output = np.concatenate(parts)
        if not np.isfinite(output).all() or np.max(np.abs(output)) < .001:
            raise ValueError("Invalid speech waveform")
        return output * (.94 / np.max(np.abs(output)))

    def render(self, text, emotion=None):
        emotion = emotion if emotion in self.targets else "NEUTRAL"
        primary = self.generate(text, emotion)
        if self.language != "it" or not all((MODEL_DIR / f).is_file() for f in FILES):
            return primary
        expected = words(text)
        if not expected:
            return primary
        try:
            return self.check_italian(primary, text, expected, emotion)
        except (OSError, ImportError, RuntimeError, ValueError):
            # The first take is already usable if optional verification assets fail.
            return primary

    def check_italian(self, primary, text, expected, emotion):
        if self.recognizer is None:
            self.recognizer = load_recognizer("it")

        def errors(audio):
            heard = recognize_audio(self.recognizer, resample(audio, RATE, 16000))
            return word_errors(expected, words(heard))

        few_errors = max(1, len(expected) // 3)
        best_errors = errors(primary)
        if best_errors <= few_errors:
            return primary
        # Retry uncertain diction with native Italian articulation and the same actor take.
        from .neural_worker import synthesize_pocket

        if not (ROOT / "models/pocket-tts/onnx/italian/bundle.json").is_file():
            return primary
        pitch, pause = self.reference(emotion)
        spoken, rate = synthesize_pocket(text, "it", emotion, pause)
        native = resample(spoken, rate, RATE)
        directed = follow_contour(self.convert(native, emotion), pitch)
        candidate_errors = errors(directed)
        # Small transcription differences are common even on the original recordings.
        # Keep the Piper performance unless the converted retry recovers multiple words.
        return directed if candidate_errors + 1 < best_errors else primary
