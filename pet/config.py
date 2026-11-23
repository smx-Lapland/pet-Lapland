from __future__ import annotations

import copy
import json
import os
import re
import threading
from .network import validate_endpoint
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
    "model_profile": "DeepSeek",
    "model_profiles": {},
    "model_key_env": "",
    "dsh_project": "",
    "dsh_session": "",
    "character_id": "lapland",
    "character_settings": {},
    "tts_base": "http://localhost:8880/v1",
    "tts_model": "tts-1",
    "tts_voice": "default",
    "tts_key_env": "",
    "voice": "Zira",
    "speech_provider": "lapland",
    "voice_device": "auto",
    "speech_language": "en",
    "mic_threshold": "较高",
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
    apply_credentials(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with _write_lock:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")


def validate_connection(base, model, key_env):
    base = base.strip().rstrip("/")
    validate_endpoint(base)
    if not model.strip():
        raise ValueError("请输入模型 ID，或点击「读取模型」。")
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("密钥栏填写环境变量名称，例如 MODEL_API_KEY。")
    return {"api_base": base, "model": model.strip(), "model_key_env": key_env}


def official_deepseek(base):
    return base.rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1")


def apply_credentials(cfg):
    env = cfg.get("model_key_env", "")
    if official_deepseek(cfg["api_base"]) and env in ("", "DEEPSEEK_API_KEY"):
        key, source = resolve_api_key()
    else:
        key = os.environ.get(env, "").strip() if env else ""
        source = env if key else ("missing" if env else "none")
    cfg["_api_key"], cfg["_key_source"] = key, source


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

    cred_path = dsh_home() / ".credentials.yaml"
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
