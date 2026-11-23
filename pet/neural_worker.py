"""Produce one PCM WAV from an English caption in a cancellable child process."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.request
import wave
from pathlib import Path
from xml.etree import ElementTree as ET


def azure_audio(text: str, voice: str, rate: str) -> bytes:
    region = os.environ.get("SPEECH_REGION", "").strip()
    key = os.environ.get("SPEECH_KEY", "").strip()
    if not key or not re.fullmatch("[a-z0-9-]+", region):
        raise ValueError("Azure credentials unavailable")
    speak = ET.Element("speak", version="1.0", xmlns="http://www.w3.org/2001/10/synthesis",
                       attrib={"xml:lang": "-".join(voice.split("-")[:2])})
    speaker = ET.SubElement(speak, "voice", name=voice)
    ET.SubElement(speaker, "prosody", rate=rate).text = text
    request = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ET.tostring(speak, encoding="utf-8"),
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm", "User-Agent": "pet-Lapland"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


async def edge_audio(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def main() -> None:
    job = json.loads(sys.stdin.buffer.read())
    path = Path(job["path"])
    if job["provider"] == "lapland":
        lapland_audio(path, job["text"], job["language"], job["rate"])
        return
    rate = f"{max(-90, min(100, int(job['rate']) * 10)):+d}%"
    args = (job["text"], job["voice"], rate)
    if job["provider"] == "azure":
        path.write_bytes(azure_audio(*args))
    elif job["provider"] == "edge":
        import miniaudio

        decoded = miniaudio.decode(asyncio.run(edge_audio(*args)), nchannels=1, sample_rate=24000)
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            out.writeframes(decoded.samples.tobytes())
    else:
        raise ValueError("unknown provider")


def reduce_decoder_tones(audio, sample_rate):
    """Attenuate persistent narrow decoder peaks measured in the 24 kHz bundles."""
    import numpy as np

    if sample_rate != 24000 or audio.size < 3840:
        return audio
    frames = np.lib.stride_tricks.sliding_window_view(audio, 960)[::240]
    power = np.mean(frames ** 2, axis=1)
    active = frames[power > max(1e-5, np.percentile(power, 90) * 0.012)]
    if len(active) < 12:
        return audio
    spectra = np.abs(np.fft.rfft(active * np.hanning(960))) + 1e-12
    # Compare each peak to its immediate neighbourhood across active speech.
    taps = np.arange(-480, 481)
    window = np.exp(-0.5 * (taps / 150) ** 2)
    kernel = np.zeros(len(taps))
    for frequency in (6000, 7200):
        i = frequency // 25
        ratio = spectra[:, i] / np.sqrt(spectra[:, i - 3] * spectra[:, i + 3])
        excess_db = 20 * np.log10(np.median(ratio))
        if excess_db > 3:
            gain = 10 ** (-min(5, excess_db - 2) / 20)
            band = 2 * window * np.cos(2 * np.pi * frequency * taps / sample_rate) / window.sum()
            kernel += (gain - 1) * band
    if not kernel.any():
        return audio
    padded = np.pad(audio, len(taps) // 2, mode="reflect")
    return audio + np.convolve(padded, kernel.astype(np.float32), mode="valid")


def original_recording(text: str, voices: Path, corpus: Path) -> Path | None:
    def words(line):
        line = unicodedata.normalize("NFKC", line).casefold().replace("’", "'")
        return re.findall(r"[^\W_]+(?:'[^\W_]+)*|[+%=<>/]", line)

    catalog = voices / "original-en.json"
    if not catalog.is_file():
        return None
    requested = words(text)
    for entry in json.loads(catalog.read_text(encoding="utf-8")):
        if words(entry["text"]) == requested:
            clip = corpus / entry["file"]
            if clip.is_file():
                return clip
    return None


def pitch_stats(audio, rate):
    """Return the voiced pitch span in semitones and median frequency in Hz."""
    import numpy as np
    import parselmouth

    if len(audio) < rate // 8:
        return 0.0, 0.0
    pitch = parselmouth.Sound(audio, rate).to_pitch_ac(time_step=.015, pitch_floor=80, pitch_ceiling=600)
    hz = pitch.selected_array["frequency"]
    voiced = hz[hz > 0]
    if voiced.size < 8:
        return 0.0, 0.0
    low, high = np.percentile(voiced, [10, 90])
    return float(12 * np.log2(high / low)), float(np.median(voiced))


def firm_english_line(text):
    return bool(re.match(r"(?:now[, ]+)?(?:watch|look|listen|show me|try me|just try|go on|your move|"
                         r"tell me|let's see|is that all|you(?:'ll|’ll) have to try)\b", text, re.I))


def english_reference(text, bank, animated=False, understated=False):
    """Match the new line's cadence to the original English recordings."""
    import math
    import numpy as np

    def cadence(line):
        ends = re.findall(r"[.!?…]+", line)
        ending = ends[-1][-1] if ends else "."
        words = re.findall(r"[a-z]+(?:['’][a-z]+)*", line.casefold())
        question = ending == "?"
        wh_question = question and bool(re.search(
            r"(?:^|[,;:.!?…—])\s*(?:what|when|where|which|who|why|how)\b", line, re.I))
        breaks = len(re.findall(r"[,;:—]+|[.!?…]+(?=\s+\S)", line))
        return len(words), ending, wh_question, breaks

    count, ending, wh, breaks = cadence(text)
    firm = firm_english_line(text)
    spans = bank["pitch_spans"] if animated and "pitch_spans" in bank else None
    medians = bank["pitch_medians"] if "pitch_medians" in bank else None
    durations = bank["durations"] if "durations" in bank else None
    ranked = []
    for i, line in enumerate(bank["lines"].tolist()):
        n, end, is_wh, pauses = cadence(line)
        if n < 2:  # Laughter and the title still contribute to the fitted speaker mean.
            continue
        score = (2.5 * (ending != end) + 0.85 * (wh != is_wh)
                 + abs(math.log((count + 1) / (n + 1))) + 0.25 * min(4, abs(breaks - pauses)))
        if animated and ("!" in line or "..." in line or line.startswith("Don't you just love")):
            score -= 0.8
        if spans is not None:
            score -= 0.25 * min(20, spans[i])
        if medians is not None and medians[i] > 0:
            center = 250 if firm else (285 if animated else (195 if understated else 245))
            score += 0.34 * abs(12 * math.log2(medians[i] / center))
        if firm and firm_english_line(line):
            score -= 2.4
        if durations is not None and (animated or understated):
            pace = n / durations[i]
            score += 0.85 * abs(math.log(pace / (3.4 if animated else 1.9)))
        ranked.append((score, i))
    ranked.sort()
    if not ranked:
        return bank["prompt"]
    frames = bank["frames"]
    best = ranked[0][1]
    lead = bank[f"clip_{best}"]
    # Short retorts carry the inflection but contain few vowels. Prepend a longer take
    # from the same actor so the model also hears connected speech.
    if frames[best] < 50:
        support = next((i for _, i in ranked if 50 <= frames[i] <= 125), None)
        if support is not None:
            lead = np.concatenate((bank[f"clip_{support}"], lead), axis=1)
    return lead


def english_beats(text):
    """Split at turns in spoken English. Keep names, lists and abbreviations together."""
    start = 0
    beats = []
    for mark in re.finditer(r"[!?…]+(?=\s)|\.+(?=\s)|[,;](?=\s)|[—–]", text):
        piece = text[start:mark.start()].strip()
        rest = text[mark.end():].strip()
        punct = mark.group()
        if not piece or not rest:
            continue
        if punct in ("—", "–") and piece[-1].isdigit() and rest[0].isdigit():
            continue
        if punct == "." and re.search(r"\b(?:[A-Za-z]|Dr|Mr|Mrs|Ms|Prof|etc|vs|e\.g|i\.e)$", piece, re.I):
            continue
        pivot = bool(re.match(r"(?:but|except|yet|instead|actually)\b", rest, re.I))
        if punct == "," and not pivot:
            if len(piece.split()) < 7 or not re.match(r"(when|while|if|after|before|although)\b", piece, re.I):
                continue
        pause = 0.38 if pivot or punct in ("—", "…", "...") else 0.30
        if punct == "," and not pivot:
            pause = 0.18
        beats.append((text[start:mark.end()].strip(), pause))
        start = mark.end()
    beats.append((text[start:].strip(), 0))
    return beats


def trim_phrase_join(audio, rate, trim_start, trim_end):
    """Remove excess decoder padding at joins while preserving soft consonants."""
    import numpy as np

    active = np.flatnonzero(np.abs(audio) > max(.0004, np.max(np.abs(audio)) * .002))
    if not active.size:
        return audio
    margin = round(.045 * rate)
    lo = max(0, active[0] - margin) if trim_start else 0
    hi = min(len(audio), active[-1] + margin + 1) if trim_end else len(audio)
    audio = audio[lo:hi].copy()
    ramp = np.linspace(0, 1, min(round(rate * .004), len(audio) // 2), dtype=np.float32)
    if ramp.size and trim_start:
        audio[:len(ramp)] *= ramp
    if ramp.size and trim_end:
        audio[-len(ramp):] *= ramp[::-1]
    return audio


def render_english(model, text, bank, voices, corpus):
    import numpy as np

    parts = []
    rate = model.sample_rate
    beats = english_beats(text)
    previous = np.empty(0, dtype=np.float32)
    for i, (line, pause) in enumerate(beats):
        short_setup = i > 0 and len(beats[i - 1][0].split()) <= 6
        firm = firm_english_line(line)
        animated = (firm or line.endswith("!") or (short_setup and line.endswith("?"))
                    or bool(re.match(r"(but|except|yet|actually|instead|oh|bravo|bravissimo|bellissimo)\b", line, re.I)))
        clip = original_recording(line, voices, corpus) if len(beats) > 1 else None
        if clip is not None:
            import miniaudio

            pcm = miniaudio.decode(clip.read_bytes(), output_format=miniaudio.SampleFormat.FLOAT32,
                                   nchannels=1, sample_rate=rate)
            audio = np.asarray(pcm.samples)
        else:
            prompt = english_reference(line, bank, animated, understated=bool(pause and not animated))
            audio = model.generate(line, voice=prompt, frames_after_eos=7)
        if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
            raise ValueError("Invalid synthesized phrase")
        if clip is None and (animated or pause):
            span, _ = pitch_stats(np.concatenate((previous, audio)), rate)
            _, median = pitch_stats(audio, rate)
            ceiling = 320 if animated else 275
            if (animated and span < 16) or median > ceiling:
                other = model.generate(line, voice=prompt, frames_after_eos=7)
                if (0.78 * audio.size <= other.size <= 1.28 * audio.size and np.isfinite(other).all()
                        and np.max(np.abs(other)) > 0.001):
                    other_span, _ = pitch_stats(np.concatenate((previous, other)), rate)
                    _, other_median = pitch_stats(other, rate)
                    def take_score(width, center):
                        variation = min(20, width) if animated else 0
                        return variation - max(0, center - ceiling) / 10
                    if other_median and take_score(other_span, other_median) > take_score(span, median):
                        audio = other
        previous = audio[-2 * rate:]
        if clip is None:
            audio = emphasize_syllables(audio, rate, 2.5 if animated else 1.0)
            if pause and len(line.split()) <= 6 and firm_english_line(beats[i + 1][0]) and not animated:
                audio *= 0.84
                pause = max(pause, 0.36)
        if len(beats) > 1:
            audio = trim_phrase_join(audio, rate, i > 0, bool(pause))
        parts.append(audio)
        if pause:
            parts.append(np.zeros(round(rate * pause), dtype=np.float32))
    return np.concatenate(parts)


def emphasize_syllables(audio, rate, boost_db):
    """Give the existing syllable stresses a little more weight without shifting pitch."""
    import numpy as np

    # Follow syllable energy, not individual waveform peaks or short sibilants.
    window, hop = round(rate * .05), round(rate * .0125)
    if len(audio) < 2 * window:
        return audio
    frames = np.lib.stride_tricks.sliding_window_view(audio, window)[::hop]
    levels = 10 * np.log10(np.maximum(np.mean(frames * frames, axis=1), 1e-10))
    active = levels[levels >= levels.max() - 22]
    if active.size < 5:
        return audio
    middle, strong = np.percentile(active, [50, 90])
    strength = np.clip((levels - middle) / max(3.0, strong - middle), 0, 1)
    gain_db = strength * boost_db
    gain = 10 ** (gain_db / 20)
    centers = np.arange(len(gain)) * hop + window // 2
    return audio * np.interp(np.arange(len(audio)), centers, gain).astype(np.float32)


def italian_beats(text):
    # Keep names, decimal points and abbreviations together in ordinary explanations.
    start = 0
    for mark in re.finditer(r"[.!?…]+(?=\s+\S)|[,;](?=\s+(?:ma|però|invece)\b)|[—–](?=\s*[^\d\s])", text, re.I):
        part = text[start:mark.start()].strip()
        if not part or (mark.group() == "." and re.search(r"\b(?:[A-Z]|sig|sigg|dott|prof|ecc|es)$", part, re.I)):
            continue
        yield text[start:mark.end()].strip()
        start = mark.end()
    if text[start:].strip():
        yield text[start:].strip()


def render_italian(model, text, bank):
    import numpy as np

    beats = []
    # PocketTTS can swallow a vocative when synthesizing a two-word utterance in isolation.
    # Keep short pickups with the next clause so the decoder has connected speech.
    for clause in italian_beats(text):
        if beats and len(beats[-1].split()) < 5:
            beats[-1] += " " + clause
        else:
            beats.append(clause)
    if len(beats) > 1 and len(beats[-1].split()) < 4:
        ending = beats.pop()
        beats[-1] += " " + ending
    parts = []
    rate = model.sample_rate
    for i, line in enumerate(beats):
        if "?" in line and not line.endswith("!"):
            style = "question"
        elif line.endswith("!") or re.match(r"(?:guarda|adesso|forza|avanti|ma dai|però|invece|ecco)\b", line, re.I):
            style = "emphatic"
        else:
            style = "prompt"
        audio = model.generate(line, voice=bank[style], frames_after_eos=7)
        if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
            raise ValueError("Invalid Italian phrase")
        if style != "prompt" and len(line.split()) >= 4:
            span, _ = pitch_stats(audio, rate)
            if span < 9:
                other = model.generate(line, voice=bank[style], frames_after_eos=7)
                if .85 * len(audio) <= len(other) <= 1.20 * len(audio) and np.isfinite(other).all():
                    other_span, median = pitch_stats(other, rate)
                    if 145 <= median <= 325 and min(other_span, 16) > span + 1:
                        audio = other
        audio = emphasize_syllables(audio, rate, .9 if style == "prompt" else 1.8)
        if len(beats) > 1:
            audio = trim_phrase_join(audio, rate, i > 0, i + 1 < len(beats))
        parts.append(audio)
        if i + 1 < len(beats):
            gap = .34 if style == "question" or line.endswith(("…", "...")) else .19
            parts.append(np.zeros(round(rate * gap), dtype=np.float32))
    return np.concatenate(parts)


def lapland_audio(path: Path, text: str, language: str, rate: int) -> None:
    import numpy as np

    root = Path(__file__).resolve().parents[1]
    runtime = root / "models" / "pocket-tts"
    voices = root / "assets" / "lapland" / "voice"
    if language == "en":
        clip = original_recording(text, voices, runtime / "corpus" / "en")
        if clip is not None:
            if not rate:
                shutil.copyfile(clip, path)
            else:
                import miniaudio

                pcm = miniaudio.decode(clip.read_bytes(), output_format=miniaudio.SampleFormat.FLOAT32,
                                       nchannels=1, sample_rate=44100)
                write_voice_wav(path, np.asarray(pcm.samples), pcm.sample_rate, rate)
            return
    bundle = {"en": "english_2026-04", "it": "italian"}[language]
    sys.path.insert(0, str(runtime))
    from pocket_tts_onnx import PocketTTSOnnx

    class VoiceModel(PocketTTSOnnx):
        def _model_file(self, stem):
            if stem == "mimi_decoder" and (self.bundle_dir / "mimi_decoder.onnx").is_file():
                return "mimi_decoder.onnx"
            return super()._model_file(stem)

    bundle_dir = runtime / "onnx" / bundle
    full_models = ("flow_lm_main.onnx", "flow_lm_flow.onnx", "mimi_decoder.onnx")
    precision = "fp32" if all((bundle_dir / name).is_file() for name in full_models) else "int8"
    temperature, steps = (0.65, 8) if language == "en" else (0.7, 4)
    model = VoiceModel(models_dir=str(runtime / "onnx"), language=bundle, device="cpu",
                       precision=precision, temperature=temperature, lsd_steps=steps)
    expressive = voices / f"expressive-{language}.npz"
    if expressive.is_file():
        with np.load(expressive, allow_pickle=False) as profile:
            if language == "en" and "lines" in profile:
                audio = render_english(model, text, profile, voices, runtime / "corpus" / "en")
            elif language == "it" and {"question", "emphatic"} <= set(profile.files):
                audio = render_italian(model, text, profile)
            else:
                audio = model.generate(text, voice=profile["prompt"], frames_after_eos=7)
    else:
        prompt = np.load(voices / f"lapland-{language}.npy", allow_pickle=False)
        audio = model.generate(text, voice=prompt, frames_after_eos=7)
    if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) < 0.001:
        raise ValueError("Empty or invalid synthesized audio")
    audio = reduce_decoder_tones(audio, model.sample_rate)
    write_voice_wav(path, audio, model.sample_rate, rate)


def write_voice_wav(path, audio, sample_rate, rate):
    import numpy as np

    if rate:
        from audiotsm import wsola
        from audiotsm.io.array import ArrayReader, ArrayWriter

        writer = ArrayWriter(1)
        wsola(1, speed=2 ** (max(-10, min(10, rate)) / 10)).run(ArrayReader(audio.reshape(1, -1)), writer)
        audio = writer.data[0]
    if audio.size:
        audio = audio / max(1.0, np.max(np.abs(audio)) / 0.98)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    main()
