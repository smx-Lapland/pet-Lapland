"""Divide bilingual captions into measured, single-line pages for the overlay."""

import re


def _lines(text, measure, width):
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+(?:['’\-][A-Za-zÀ-ÿ0-9]+)*|\s+|.", text)
    lines, line = [], ""
    for word in words:
        word = " " if word.isspace() else word
        candidate = (line + word).lstrip()
        if measure(candidate) <= width:
            line = candidate
            continue
        if line.strip():
            lines.append(line.rstrip())
        line = ""
        for char in word.lstrip():
            if line and measure(line + char) > width:
                lines.append(line)
                line = ""
            line += char
    if line.strip():
        lines.append(line.rstrip())
    return lines or [""]


def subtitle_pages(spoken, translation, spoken_font, translation_font, width):
    # When the two texts have matching sentence boundaries, turn the page at
    # the same thought in both languages, even when the translation is shorter.
    boundary = r"(?<=[.!?])(?=\s)|(?<=[。！？])"
    originals = [s for s in re.split(boundary, spoken) if s.strip()]
    translations = [s for s in re.split(boundary, translation) if s.strip()]
    groups = [(spoken, translation)]
    if len(originals) == len(translations) and len(originals) > 1:
        groups = []
        for en, zh in zip(originals, translations):
            if groups:
                merged_en, merged_zh = groups[-1][0] + en, groups[-1][1] + zh
                if spoken_font.measure(merged_en) <= width and translation_font.measure(merged_zh) <= width:
                    groups[-1] = merged_en, merged_zh
                    continue
            groups.append((en.lstrip(), zh.lstrip()))
    pages = []
    for en, zh in groups:
        pages.extend(_pair_lines(en, zh, spoken_font, translation_font, width))
    return pages


def _pair_lines(spoken, translation, spoken_font, translation_font, width):
    original = _lines(spoken, spoken_font.measure, width)
    translated = _lines(translation, translation_font.measure, width)
    count = max(len(original), len(translated))
    return [(original[i * len(original) // count], translated[i * len(translated) // count])
            for i in range(count)]
