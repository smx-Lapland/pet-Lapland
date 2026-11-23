from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
CACHE_DIR = APP_DIR / "cache"
CAPTURE_DIR = APP_DIR / "captures"
LOG_PATH = APP_DIR / "pet.log"

DEFAULTS: dict[str, Any] = {
    "model": "deepseek-flash",
    "api_base": "https://api.deepseek.com",
    "voice": "Zira",
    "speech_provider": "lapland",
    "speech_language": "en",
    "neural_voice": "en-US-JennyNeural",
    "neural_voice_it": "it-IT-ElsaNeural",
    "confirm_capture": True,
    "stream": True,
    "rate": 0,
    "muted": False,
    "max_image_side": 1600,
    "jpeg_quality": 88,
    "save_captures": False,
    "save_history": True,
    "reasoning_effort": "low",
    "hotkeys": {
        "capture": "ctrl+alt+s",
        "ask": "ctrl+alt+space",
        "toggle": "ctrl+alt+h",
        "quit": "ctrl+alt+q",
    },
    "window": {"x": None, "y": None},
}

_write_lock = threading.Lock()


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists() is False:
        save_config(copy.deepcopy(DEFAULTS))


def load_config() -> dict[str, Any]:
    ensure_dirs()
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _deep_merge(cfg, loaded)
        except Exception:
            pass
    key, source = resolve_api_key()
    cfg["_api_key"] = key
    cfg["_key_source"] = source
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with _write_lock:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)


def resolve_api_key() -> tuple[str, str]:
    """Find a key without ever writing it to disk or logging it.

    Priority: explicit environment variable, then the DSH credential store.
    DSH keeps its DeepSeek key in ``$DSH_HOME/.credentials.yaml`` for the
    official provider, so the pet can reuse the same account the user already
    signed in with.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key, "env"

    dsh_home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
    cred_path = dsh_home / ".credentials.yaml"
    try:
        text = cred_path.read_text(encoding="utf-8")
    except Exception:
        return "", "missing"
    match = re.search(r"DEEPSEEK_API_KEY\s*:\s*(sk-[A-Za-z0-9_\-]+)", text)
    if match:
        return match.group(1).strip(), "dsh"
    return "", "missing"


def clean_old_cache(max_age_seconds: int = 3 * 3600) -> None:
    import time

    if not CACHE_DIR.exists():
        return
    now = time.time()
    for path in CACHE_DIR.glob("*"):
        try:
            if path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
        except Exception:
            pass
