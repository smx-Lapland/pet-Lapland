"""Separate the actor's delivery direction from the words shown and spoken."""
import re
import unicodedata

from num2words import num2words

EMOTIONS = ("NEUTRAL", "LAZY", "MOCKING", "MANIC", "WHISPER")
_ALIASES = {"SHARP": "NEUTRAL", "LOW": "WHISPER"}
_TAG = re.compile(r"\[(" + "|".join((*EMOTIONS, *_ALIASES)) + r")\]", re.I)
_UNKNOWN_DIRECTION = re.compile(r"^\s*\[[A-Z][A-Z_ -]{1,28}\]\s*")
_CUE = re.compile(r"(?:\[([^\]]{1,70})\]|\(([^()]{1,70})\)|\*([^*]{1,70})\*)")
_ACTION = re.compile(r"\b(?:laughs?|laughing|laughter|chuckl\w*|sigh\w*|whisper\w*|scoff\w*|smirk\w*|"
                     r"breathy|sarcastically|drawls?|cackl\w*|grins?|pauses?)\b", re.I)
_ITALIAN_CUE = re.compile(r"(?:ride|sospira|sussurra|ridacchia|sbuffa)(?: piano| a bassa voce| tra sé)?", re.I)


def split_direction(value):
    value = str(value or "").strip()
    tag = _TAG.match(value)
    value = _TAG.sub("", value)
    value = _UNKNOWN_DIRECTION.sub("", value)
    value = _CUE.sub(lambda m: "" if _ACTION.search(m.group()) or
                     _ITALIAN_CUE.fullmatch(m.group()[1:-1].strip()) else m.group(), value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "So" and ch not in "~\ufe0f\u200d")
    mood = tag[1].upper() if tag else None
    return _ALIASES.get(mood, mood), value.strip()


def spoken_english(text):
    text = re.sub(r"\bDr\.(?=\s|$)", "Doctor", text, flags=re.I)
    text = re.sub(r"\b(e\.g\.|i\.e\.|vs\.)(?=\s|$)",
                  lambda m: {"e.g.": "for example", "i.e.": "that is", "vs.": "versus"}[m[1].lower()], text, flags=re.I)
    def number(match):
        digits, ordinal, percent = match.groups()
        amount = digits.replace(",", "")
        if "." in amount:
            whole, fraction = amount.split(".")
            words = num2words(int(whole)) + " point " + " ".join(num2words(int(n)) for n in fraction)
        else:
            words = num2words(int(amount), to="ordinal" if ordinal else "cardinal")
        return words.replace("-", " ") + (" percent" if percent else "")
    return re.sub(r"(?<![\w.])((?:\d{1,3}(?:,\d{3})+|\d{1,16})(?:\.\d+)?)(st|nd|rd|th)?(%?)(?!\w)",
                  number, text)
