"""Install the pinned PocketTTS bundles and fit independent EN/IT speaker prompts."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

import miniaudio
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "models" / "pocket-tts"
VOICES = ROOT / "assets" / "lapland" / "voice"
MODEL_REV = "58a6d00cf13d239b6748cb0769f35c580a8f606c"
DATA_REV = "79d7bd1daf455d6a8b7c2c71b24618e31f5d4ba4"
MODEL_REPO = "KevinAHM/pocket-tts-onnx"
BUNDLES = {"en": ("english_2026-04", "English"), "it": ("italian", "Italian")}
CLIPS = ("交谈1", "交谈2", "交谈3", "任命助理")
REFERENCES = {"en": ("任命助理", "交谈1"), "it": ("交谈3",)}
ITALIAN_DELIVERY = {"question": "交谈1", "emphatic": "完成高难行动"}
FILES = ("mimi_encoder.onnx", "text_conditioner.onnx", "mimi_decoder.onnx",
         "flow_lm_main.onnx", "flow_lm_flow.onnx", "bundle.json",
         "tokenizer.model", "bos_before_voice.npy")


def download(job):
    url, path, expected = job
    if path.exists() and path.stat().st_size == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
    try:
        for attempt in range(3):
            try:
                with urlopen(url, timeout=90) as source, temp.open("wb") as out:
                    shutil.copyfileobj(source, out)
                if temp.stat().st_size != expected:
                    raise OSError("Incomplete download: " + path.name)
                os.replace(temp, path)
                return
            except OSError:
                if attempt == 2:
                    raise
    finally:
        temp.unlink(missing_ok=True)


def jobs():
    source = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REV}/"
    for folder, names in [("", ["pocket_tts_onnx.py"]), ("onnx", ["LICENSE"])] + [
        ("onnx/" + bundle, FILES) for bundle, _ in BUNDLES.values()
    ]:
        tree = f"https://huggingface.co/api/models/{MODEL_REPO}/tree/{MODEL_REV}/{folder}"
        with urlopen(tree.rstrip("/"), timeout=30) as response:
            sizes = {row["path"]: row["size"] for row in json.load(response)}
        for name in names:
            rel = (Path(folder) / name).as_posix()
            yield source + rel, RUNTIME / rel, sizes[rel]
    for code, (_, language) in BUNDLES.items():
        folder = "Audio/" + language
        dataset = "None1145/Lappland"
        tree = f"https://huggingface.co/api/datasets/{dataset}/tree/{DATA_REV}/{folder}"
        with urlopen(tree, timeout=30) as response:
            sizes = {row["path"]: row["size"] for row in json.load(response)}
        entries = [(clip, str(index)) for index, clip in enumerate(CLIPS)]
        if code == "en":
            originals = json.loads((VOICES / "original-en.json").read_text(encoding="utf-8"))
            entries = [(row["clip"], Path(row["file"]).stem) for row in originals]
        else:
            entries.extend((clip, clip) for clip in ITALIAN_DELIVERY.values() if clip not in CLIPS)
        for clip, filename in entries:
            rel = folder + "/" + clip + ".wav"
            url = f"https://huggingface.co/datasets/{dataset}/resolve/{DATA_REV}/" + quote(rel)
            yield url, RUNTIME / "corpus" / code / f"{filename}.wav", sizes[rel]


def english_voice_bank(model):
    from pet.neural_worker import pitch_stats

    catalog = json.loads((VOICES / "original-en.json").read_text(encoding="utf-8"))
    clips = []
    spans = []
    medians = []
    durations = []
    for row in catalog:
        raw = (RUNTIME / "corpus" / "en" / row["file"]).read_bytes()
        pcm = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.FLOAT32,
                               nchannels=1, sample_rate=model.sample_rate)
        signal = np.asarray(pcm.samples)
        if not signal.size:
            raise ValueError("Empty recording: " + row["file"])
        span, median = pitch_stats(signal, model.sample_rate)
        spans.append(span)
        medians.append(median)
        durations.append(signal.size / model.sample_rate)
        encoded = model.mimi_encoder.run(None, {"audio": signal[None, None, :]})[0]
        clips.append(encoded.reshape(1, -1, model.conditioning_dim))
    # Give longer takes more influence without letting the longest monologues dominate.
    means = np.stack([clip.mean(axis=1, keepdims=True) for clip in clips])
    center = np.average(means, axis=0, weights=np.sqrt([clip.shape[1] for clip in clips])).astype(np.float32)
    bank = {"lines": np.array([row["text"] for row in catalog]),
            "pitch_spans": np.array(spans, dtype=np.float32),
            "pitch_medians": np.array(medians, dtype=np.float32),
            "durations": np.array(durations, dtype=np.float32),
            "frames": np.array([clip.shape[1] for clip in clips])}
    for i, clip in enumerate(clips):
        fitted = clip + 0.18 * (center - means[i])
        if not np.isfinite(fitted).all():
            raise ValueError("Invalid English reference: " + catalog[i]["file"])
        bank[f"clip_{i}"] = fitted
    return bank


def italian_voice_bank(model, anchor):
    bank = {}
    for style, clip in ITALIAN_DELIVERY.items():
        filename = str(CLIPS.index(clip)) if clip in CLIPS else clip
        raw = (RUNTIME / "corpus" / "it" / (filename + ".wav")).read_bytes()
        pcm = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.FLOAT32,
                               nchannels=1, sample_rate=model.sample_rate)
        signal = np.asarray(pcm.samples)
        encoded = model.mimi_encoder.run(None, {"audio": signal[None, None, :]})[0]
        prompt = encoded.reshape(1, -1, model.conditioning_dim)
        # Keep the connected-speech anchor. A short exclamation alone can swallow
        # articles and vocatives; borrow its delivery through a bounded channel offset.
        bank[style] = anchor + .34 * (prompt.mean(axis=1, keepdims=True) - anchor.mean(axis=1, keepdims=True))
        if not np.isfinite(bank[style]).all():
            raise ValueError("Invalid Italian reference: " + clip)
    return bank


def fit(code, bundle):
    from pocket_tts_onnx import PocketTTSOnnx

    model = PocketTTSOnnx(models_dir=str(RUNTIME / "onnx"), language=bundle, device="cpu")
    examples = []
    for index in range(len(CLIPS)):
        raw = (RUNTIME / "corpus" / code / f"{index}.wav").read_bytes()
        pcm = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=model.sample_rate).samples
        # Limit a conditioning example to ten seconds, preserving whole clips when shorter.
        samples = np.asarray(pcm[:10 * model.sample_rate], dtype=np.int16)
        audio = samples.astype(np.float32)[None, None, :] / 32768
        encoded = model.mimi_encoder.run(None, {"audio": audio})[0].reshape(1, -1, model.conditioning_dim)
        examples.append(encoded)
    # Fit a regularized channel offset to this speaker's clip means, retaining
    # the reference utterance's temporal structure. The anchor has unit prior weight.
    anchor = examples[0]
    means = np.stack([example.mean(axis=1, keepdims=True) for example in examples])
    offset = (means - means[0]).sum(axis=0) / (len(examples) + 1)
    prompt = (anchor + offset).astype(np.float32)
    if not np.isfinite(prompt).all():
        raise ValueError("Invalid voice prompt")
    target = VOICES / f"lapland-{code}.npy"
    np.save(target, prompt, allow_pickle=False)
    rate = model.sample_rate
    recordings = []
    for clip in REFERENCES[code]:
        stem = CLIPS.index(clip)
        raw = (RUNTIME / "corpus" / code / f"{stem}.wav").read_bytes()
        pcm = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=rate)
        recordings.append(np.asarray(pcm.samples))
    reference = np.concatenate(recordings)
    encoded = model.mimi_encoder.run(None, {"audio": reference[None, None, :]})[0].reshape(1, -1, model.conditioning_dim)
    if not np.isfinite(encoded).all():
        raise ValueError("Invalid expressive voice prompt")
    bank = english_voice_bank(model) if code == "en" else italian_voice_bank(model, encoded)
    with tempfile.NamedTemporaryFile(dir=VOICES, delete=False) as handle:
        temp = Path(handle.name)
    try:
        with temp.open("wb") as handle:
            np.savez(handle, prompt=encoded, **bank)
        os.replace(temp, VOICES / f"expressive-{code}.npz")
    finally:
        temp.unlink(missing_ok=True)
    with wave.open(str(VOICES / f"reference-{code}.wav"), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes((reference.clip(-1, 1) * 32767).astype("<i2").tobytes())
    print(code, "reference recordings:", len(bank["lines"]) if code == "en" else len(bank) + 1,
          "fallback sha256:", hashlib.sha256(target.read_bytes()).hexdigest(), flush=True)


if __name__ == "__main__":
    VOICES.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, jobs()))
    sys.path[:0] = [str(ROOT), str(RUNTIME)]
    for code, (bundle, _) in BUNDLES.items():
        fit(code, bundle)
