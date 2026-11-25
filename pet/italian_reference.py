"""Local Italian reference-conditioned speech worker. Heavy imports stay in the worker."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "models" / "italian-reference-tts"
BANK = RUNTIME / "lapland-it-prompts.npz"
REFERENCES = {"WHISPER": "信赖触摸.wav", "MOCKING": "观看作战记录.wav", "MANIC": "3星结束行动.wav",
              "LAZY": "完成高难行动.wav", "NEUTRAL": "0.wav"}


def installed_python():
    from pet.voice_device import venv_python

    files = (BANK, RUNTIME / "base/model.safetensors", RUNTIME / "base/speech_tokenizer/model.safetensors")
    try:
        return venv_python(RUNTIME / "venv") if all(f.is_file() for f in files) else None
    except (OSError, ValueError):
        return None


def build_bank():
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    from tempfile import NamedTemporaryFile

    catalog = json.loads((ROOT / "assets/lapland/voice/original-it.json").read_text("utf8"))
    lines = {row["file"]: row["text"] for row in catalog}
    model = Qwen3TTSModel.from_pretrained(str(RUNTIME / "base"), device_map="cuda:0",
                                          dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
    bank = {}
    for mood, name in REFERENCES.items():
        audio, rate = sf.read(ROOT / "models/pocket-tts/corpus/it" / name, dtype="float32")
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if mood == "WHISPER":
            audio = np.pad(audio, (0, rate // 2))
        prompt = model.create_voice_clone_prompt(ref_audio=(audio, rate), ref_text=lines[name])[0]
        bank[mood + ".codes"] = prompt.ref_code.cpu().numpy()
        bank[mood + ".embedding"] = prompt.ref_spk_embedding.float().cpu().numpy()
        bank[mood + ".text"] = np.array(lines[name])
    with NamedTemporaryFile(dir=RUNTIME, delete=False) as f:
        pending = Path(f.name)
    try:
        with pending.open("wb") as f:
            np.savez(f, **bank)
        os.replace(pending, BANK)
    finally:
        pending.unlink(missing_ok=True)


class ItalianVoice:
    def __init__(self):
        import numpy as np
        import torch
        from faster_qwen3_tts import FasterQwen3TTS

        self.model = FasterQwen3TTS.from_pretrained(str(RUNTIME / "base"), device="cuda", dtype=torch.bfloat16,
                                                   max_seq_len=1536, local_files_only=True)
        self.prompts = {}
        with np.load(BANK, allow_pickle=False) as bank:
            for mood in REFERENCES:
                prompt = {"ref_code": [torch.as_tensor(bank[mood + ".codes"], device="cuda", dtype=torch.long)],
                          "ref_spk_embedding": [torch.as_tensor(bank[mood + ".embedding"], device="cuda",
                                                                dtype=torch.bfloat16)],
                          "icl_mode": [True], "x_vector_only_mode": [False]}
                self.prompts[mood] = prompt, str(bank[mood + ".text"])
        self.model.warmup()

    def render(self, text, mood):
        import numpy as np
        import torch

        if mood not in self.prompts:
            mood = "NEUTRAL"
        prompt, transcript = self.prompts[mood]
        torch.manual_seed(137 if mood == "WHISPER" else 37)
        limit = min(1200, 55 + 2 * len(text))
        waves, rate = self.model.generate_voice_clone(
            text=text, language="Italian", voice_clone_prompt=prompt, ref_text=transcript,
            temperature=.8 if mood == "WHISPER" else .75, top_p=.92 if mood == "WHISPER" else .9,
            non_streaming_mode=True if mood != "WHISPER" else None, max_new_tokens=limit)
        audio = np.asarray(waves[0], dtype=np.float32).reshape(-1)
        if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < .001:
            raise ValueError("Invalid Italian reference voice output")
        if len(audio) / rate >= (limit - 1) * .08:
            raise ValueError("Italian reference voice did not finish the line")
        return audio, rate


def shape_syllable_stress(audio, sample_rate):
    """Bring out existing vowel accents only when neighbouring syllables sound similarly loud."""
    import numpy as np
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import find_peaks
    from numpy.lib.stride_tricks import sliding_window_view

    step, window = round(.01 * sample_rate), round(.04 * sample_rate)
    if len(audio) < 3 * window:
        return audio
    frames = sliding_window_view(np.pad(audio, window // 2), window)[::step]
    frequency = np.fft.rfftfreq(window, 1 / sample_rate)
    voice_band = (frequency >= 180) & (frequency <= 1900)
    spectrum = np.fft.rfft(frames * np.hanning(window))
    power = np.sum(np.abs(spectrum[:, voice_band]) ** 2, axis=1)
    level = 10 * np.log10(np.maximum(power / max(np.percentile(power, 95), 1e-9), 1e-9))
    envelope = gaussian_filter1d(np.maximum(level, -45), 1.2)
    nuclei, _ = find_peaks(envelope, height=-22, distance=9, prominence=1.8)
    if nuclei.size < 3:
        return audio

    gains = np.zeros(len(envelope))
    # Compare within a phrase. A long pause often marks a new breath and register.
    for phrase in np.split(nuclei, np.flatnonzero(np.diff(nuclei) > 45) + 1):
        if len(phrase) < 3:
            continue
        peaks = envelope[phrase]
        contrast = np.percentile(np.abs(np.diff(peaks)), 75)
        # 3.7 dB is toward the expressive end of the measured Italian takes.
        strength = np.clip(3.7 / max(contrast, .25) - 1, 0, .85)
        if strength == 0:
            continue
        local = np.array([np.median(peaks[max(0, i - 2):i + 3]) for i in range(len(peaks))])
        offsets = np.clip((peaks - local) * strength, -1.8, 2.6)
        first, last = max(0, phrase[0] - 8), min(len(envelope) - 1, phrase[-1] + 8)
        positions = np.arange(first, last + 1)
        gains[positions] = np.interp(positions, np.r_[first, phrase, last], np.r_[0, offsets, 0])
    if not gains.any():
        return audio
    gain = np.interp(np.arange(len(audio)), np.arange(len(gains)) * step, gaussian_filter1d(gains, 1.2))
    shaped = audio * np.exp(gain * (np.log(10) / 20)).astype(np.float32)
    # Match energy so the contrast comes from the accents rather than playback volume.
    match = np.sqrt(np.sum(audio ** 2, dtype=np.float64) / np.sum(shaped ** 2, dtype=np.float64))
    return shaped * min(match, .98 / np.max(np.abs(shaped)))


def shorten_internal_silence(audio, sample_rate):
    """Shorten long, near-silent gaps while retaining both sides of each breath."""
    import numpy as np

    step = max(1, sample_rate // 100)
    frames = len(audio) // step
    if frames < 80:
        return audio
    peaks = np.max(np.abs(audio[:frames * step].reshape(frames, step)), axis=1)
    active = np.flatnonzero(peaks > .002)
    pieces = []
    cursor = 0
    for i in np.flatnonzero(np.diff(active) > 80):
        # Keep 270 ms of room at each end. Crossfade only inside the quiet block.
        left = (active[i] + 28) * step
        right = (active[i + 1] - 27) * step
        mix = np.linspace(0, 1, step, dtype=audio.dtype)
        pieces.extend((audio[cursor:left], audio[left:left+step] * (1-mix) + audio[right:right+step] * mix))
        cursor = right + step
    return np.concatenate([*pieces, audio[cursor:]]) if pieces else audio


def write_dialogue(path, audio, sample_rate, rate):
    import soundfile as sf
    from pet.neural_worker import trim_phrase_join

    audio = trim_phrase_join(audio, sample_rate, True, True)
    audio = shape_syllable_stress(shorten_internal_silence(audio, sample_rate), sample_rate)
    if rate:
        from audiotsm import wsola
        from audiotsm.io.array import ArrayReader, ArrayWriter
        import numpy as np

        # Only apply time stretching when a different playback rate was explicitly selected.
        speed = 2 ** (max(-10, min(10, rate)) / 10)
        frame = 1024
        writer = ArrayWriter(1)
        padded = np.pad(audio, (0, 4 * frame))
        wsola(1, speed=speed, frame_length=frame).run(ArrayReader(padded.reshape(1, -1)), writer)
        audio = writer.data[0, :round(len(audio) / speed)]
    sf.write(str(path), audio, sample_rate, format="WAV", subtype="PCM_16")


def main():
    sys.path.insert(0, str(ROOT))
    os.environ["HF_HUB_OFFLINE"] = "1"
    if "--prepare" in sys.argv:
        build_bank()
        return
    from pet.neural_worker import play_original_line

    session = "--session" in sys.argv
    voice = ItalianVoice() if session else None
    for line in sys.stdin.buffer:
        job = json.loads(line)
        path = Path(job["path"])
        pending = Path(str(path) + ".pending") if session else path
        try:
            if not play_original_line(pending, job["text"], "it", job["rate"]):
                if voice is None:
                    voice = ItalianVoice()
                audio, sample_rate = voice.render(job["text"], job.get("emotion"))
                write_dialogue(pending, audio, sample_rate, job["rate"])
            if session:
                pending.replace(path)
        except Exception:
            pending.unlink(missing_ok=True)
            raise
        if not session:
            return


if __name__ == "__main__":
    main()
