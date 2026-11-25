"""Offline English GPT-SoVITS v2 worker for Lapland's portable tensor bundle."""
import inspect
import json
import os
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "models" / "gpt-sovits-en"
BUNDLE = "lapland-en.safetensors"
FORMAT = "pet-lapland-gsv-v2-en-1"
MOODS = ("NEUTRAL", "MOCKING", "LAZY", "MANIC", "WHISPER")
LEXICON = {"ehi": "EY1", "ciao": "CH AW1", "bravo": "B R AA1 V OW0", "bene": "B EH1 N EY0",
           "molto": "M OW1 L T OW0", "siracusa": "S IY2 R AH0 K UW1 Z AH0",
           "affascinante": "AA2 F AA0 SH IY0 N AA1 N T EY0", "famiglia": "F AH0 M IY1 L Y AH0"}


def installed_voice(character_id):
    from .characters import character
    from .voice_device import venv_python

    try:
        folder = RUNTIME / "active" if character_id == "lapland" else character(character_id).folder
        bundle = folder / BUNDLE
        if bundle.is_file():
            python = venv_python(RUNTIME / "venv")
            if python:
                return bundle, python
    except (OSError, ValueError):
        pass
    return None, None


def shape_english_delivery(samples, mood):
    # Target voiced P10–P90 spans in semitones, measured from the actor's English takes.
    target = {"MOCKING": 15.9, "MANIC": 15.6, "NEUTRAL": 14., "LAZY": 12.3}.get(mood or "NEUTRAL")
    if not target:
        return samples
    import numpy as np
    try:
        import parselmouth
        from parselmouth.praat import call
    except ImportError:
        return samples  # Earlier optional runtimes still support the native trained voice.
    sound = parselmouth.Sound(samples.astype(float) / 32768, 32000)
    hz = sound.to_pitch_ac(time_step=.0125, pitch_floor=80, pitch_ceiling=620).selected_array["frequency"]
    voiced = hz[hz > 0]
    if voiced.size < 16:
        return samples
    low, center, high = np.percentile(voiced, [10, 50, 90])
    span = 12 * np.log2(high / low)
    if span < 2 or span >= target - .4:
        return samples
    gain = min(.34, target / span - 1)
    manip = call(sound, "To Manipulation", .0125, 80., 620.)
    tier = call(manip, "Extract pitch tier")
    # Preserve the line's own pitch center and stress timing. Cap the shift at two semitones.
    call(tier, "Formula", f"self * 2 ^ (min(2, max(-2, {gain} * 12 * log2(self / {center}))) / 12)")
    call([tier, manip], "Replace pitch tier")
    audio = call(manip, "Get resynthesis (overlap-add)").values[0]
    peak = np.max(np.abs(audio))
    return np.rint(audio * (32767 / max(1., peak / .98))).astype("<i2")


class EnglishVoice:
    def __init__(self, bundle, device):
        engine = RUNTIME / "engine"
        modules = engine / "GPT_SoVITS"
        sys.path[:0] = [str(modules), str(engine)]
        os.environ["NLTK_DATA"] = str(RUNTIME / "nltk_data")
        os.environ["HF_HUB_OFFLINE"] = "1"
        import torch
        import yaml
        from safetensors import safe_open
        from AR.models.t2s_model import Text2SemanticDecoder
        from module.models import SynthesizerTrn
        from text import english
        from pet.voice_device import cpu_voice_threads

        self.device = "cuda" if device == "cuda" and torch.cuda.is_available() else "cpu"
        torch.set_num_threads(cpu_voice_threads(4) if self.device == "cpu" else 2)
        cfg = json.loads((modules / "configs/s2.json").read_text("utf8"))
        self.vocoder = SynthesizerTrn(cfg["data"]["filter_length"] // 2 + 1,
                                      cfg["train"]["segment_size"] // cfg["data"]["hop_length"],
                                      n_speakers=cfg["data"]["n_speakers"], version="v2", **cfg["model"])
        del self.vocoder.enc_q
        semantic_cfg = yaml.safe_load((modules / "configs/s1longer-v2.yaml").read_text("utf8"))
        # Older installations also work. The patched runtime skips training metrics at startup.
        options = {"with_accuracy": False} if "with_accuracy" in inspect.signature(Text2SemanticDecoder).parameters else {}
        self.gpt = Text2SemanticDecoder(config=semantic_cfg, top_k=3, **options)
        with safe_open(str(bundle), framework="pt", device="cpu") as f:
            if (f.metadata() or {}).get("format") != FORMAT:
                raise ValueError("Invalid English voice weights")
            for prefix, model in (("gpt.model.", self.gpt), ("sovits.", self.vocoder)):
                model.load_state_dict({k[len(prefix):]: f.get_tensor(k) for k in f.keys() if k.startswith(prefix)})
                model.to(self.device).eval()
            self.refs = {m: tuple(f.get_tensor(f"ref.{m}.{k}").to(self.device) for k in ("phones", "prompt", "spec"))
                         for m in MOODS}
        for word, phones in LEXICON.items():
            english._g2p.cmu[word] = [phones.split()]

    def activate(self, device):
        import torch
        if device == "cuda" and self.device == "cpu" and torch.cuda.is_available():
            torch.set_num_threads(2)
            self.gpt.to("cuda")
            self.vocoder.to("cuda")
            self.refs = {name: tuple(t.to("cuda") for t in ref) for name, ref in self.refs.items()}
            self.device = "cuda"

    def render(self, text, mood, rate):
        import torch
        from text import cleaned_text_to_sequence, english

        text = text.replace("’", "'").replace("—", ", ").replace("…", "...")
        # Give the negative syllable its own stress in quick modal questions.
        text = re.sub(r"\b(would|could|should)n't\b", r"\1 not", text, flags=re.I)
        phones = english.g2p(english.text_normalize(text))
        target = torch.tensor(cleaned_text_to_sequence(phones, "v2"), device=self.device, dtype=torch.long)[None, :]
        if not target.numel():
            raise ValueError("Empty English utterance")
        ref, prompt, spec = self.refs.get(mood, self.refs["NEUTRAL"])
        full = torch.cat([ref, target], dim=1)
        length = torch.tensor([full.size(1)], device=self.device)
        bert = torch.zeros(1, 1024, full.size(1), device=self.device)
        # The upstream scripted layers spend seconds specializing the first utterance.
        with torch.inference_mode(), torch.jit.optimized_execution(False):
            limit = min(25 * 50, max(75, target.size(1) * 5 + 40))
            seed = 37 if mood == "MOCKING" else 19
            for take in (seed, 42):
                torch.manual_seed(take)
                tokens, count = self.gpt.infer_panel(full, length, prompt, bert, top_k=15, top_p=.88,
                                                           temperature=.85, early_stop_num=limit)
                if 0 < count < limit:
                    break
            else:
                raise ValueError("English semantic generation did not finish the phrase")
            audio = self.vocoder.decode(tokens[:, -count:].unsqueeze(0), target, spec,
                                        speed=2 ** (max(-10, min(10, rate)) / 10))[0, 0]
            if not torch.isfinite(audio).all() or audio.abs().max() < .001:
                raise ValueError("Invalid English speech waveform")
            pcm = (audio / (audio.abs().max() / .95).clamp(min=1) * 32767).short().cpu().numpy()
        return shape_english_delivery(pcm, mood)


def main():
    sys.path.insert(0, str(ROOT))
    from pet.neural_worker import play_original_line

    bundle = Path(os.environ["PET_ENGLISH_WEIGHTS"])
    voice = None

    def write(job, path):
        nonlocal voice
        if play_original_line(path, job["text"], "en", job["rate"]):
            return
        if voice is None:
            voice = EnglishVoice(bundle, os.environ.get("PET_TTS_DEVICE"))
        audio = voice.render(job["text"], job.get("emotion"), job["rate"])
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 32000, 0, "NONE", "not compressed"))
            wav.writeframes(audio.astype("<i2", copy=False).tobytes())

    if sys.argv[1:2] == ["--session"]:
        target_device = os.environ.get("PET_TTS_DEVICE")
        voice = EnglishVoice(bundle, "cpu" if "--standby" in sys.argv else target_device)
        for line in sys.stdin.buffer:
            job = json.loads(line)
            voice.activate(target_device)
            if job.get("wake"):
                continue
            target = Path(job["path"])
            pending = Path(str(target) + ".pending")
            write(job, pending)
            pending.replace(target)
    else:
        job = json.loads(sys.stdin.buffer.read())
        write(job, Path(job["path"]))


if __name__ == "__main__":
    main()
