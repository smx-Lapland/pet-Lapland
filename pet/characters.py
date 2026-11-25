"""Portable, data-only characters and their explicitly shareable settings."""
import io
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import APP_DIR, DEFAULTS, validate_connection
from .gpt_sovits_en import BUNDLE, FORMAT, MOODS, RUNTIME

POSES = ("wait", "curious", "ponder", "impatient", "smile", "victory", "flustered")
IMAGES = {f"{name}.png" for name in (*POSES, "portrait", "background")}
VOICE_KEYS = ("speech_provider", "speech_language", "voice", "neural_voice", "neural_voice_it",
              "tts_base", "tts_model", "tts_voice", "tts_key_env")
CHAT_KEYS = ("model", "api_base", "model_key_env")
SHARE_KEYS = CHAT_KEYS + VOICE_KEYS
MAX_FILE = 12 * 1024 * 1024
MODEL_FILES = {"voice.onnx", "voice.onnx.json"}
MAX_MODEL = 256 * 1024 * 1024
VOICE_LIMITS = {"voice.onnx": MAX_MODEL, "voice.onnx.json": 256000, BUNDLE: 288 * 1024 * 1024}

# Exact stock text shipped in previous Lapland packs, including the local bilingual edition.
_STOCK_LAPLAND_PERSONAS = {
    "1acad74acd52faac007e60e33d06d1e49a001135e14601577da1e757fe4f9810",
    "07f38add08b78351abafe44650b71abc492f4ad56337c50b0bc89c77030f674e",
    "900ef52eec9d788747e72e9c161de7cbc5bd59266bdf95ab5df5f1489b36122d",
}


def library():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mochi" / "characters"


@dataclass
class Character:
    id: str
    folder: Path
    data: dict

    def sprite(self, pose):
        path = self.folder / (pose + ".png")
        return path if path.is_file() else self.folder / "portrait.png"


def clean_manifest(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("不支持的角色包版本。")
    fields = {}
    for k, limit in (("name", 32), ("name_en", 50), ("persona", 18000)):
        value = data.get(k)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"角色字段 {k} 缺失或过长。")
        fields[k] = value.strip()
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict) or len(tasks) > 30 or any(
        not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip()
        or "|" in k or "\n" in k or len(k) > 60 or len(v) > 6000 for k, v in tasks.items()
    ):
        raise ValueError("任务模板须为「名称 → 任务文字」的映射。")
    raw = data.get("settings", {})
    if not isinstance(raw, dict):
        raise ValueError("角色接口设置无效。")
    cfg = {k: raw.get(k, DEFAULTS[k]) for k in SHARE_KEYS}
    cfg["speech_provider"] = raw.get("speech_provider", "sapi")
    if any(not isinstance(v, str) for v in cfg.values()):
        raise ValueError("接口设置必须为文本。")
    cfg.update(validate_connection(cfg["api_base"], cfg["model"], cfg["model_key_env"]))
    if cfg["speech_provider"] not in ("sapi", "edge", "azure", "api", "piper", "vits", "lapland") or cfg["speech_language"] not in ("en", "it"):
        raise ValueError("角色包的朗读服务或语言无效。")
    validate_connection(cfg["tts_base"], cfg["tts_model"], cfg["tts_key_env"])
    if not cfg["tts_voice"].strip():
        raise ValueError("请填写语音 API 的音色 ID。")
    return {"version": 1, **fields, "tasks": dict(tasks), "settings": cfg}


def character(key="lapland"):
    if key == "lapland":
        folder = APP_DIR / "assets/lapland"
        return Character(key, folder, {"version": 1, "name": "拉普兰德", "name_en": "Lapland",
                         "persona": (folder / "dsh-persona.txt").read_text("utf-8"),
                         "tasks": {"检查当前项目": "检查当前项目的结构、测试入口和未完成事项，给出简短报告。"},
                         "settings": {k: DEFAULTS[k] for k in SHARE_KEYS}})
    if key != "muelsyse" and not re.fullmatch(r"[0-9a-f]{32}", str(key)):
        raise ValueError("角色编号无效。")
    folder = APP_DIR / "assets/characters/muelsyse" if key == "muelsyse" else library() / key
    data = clean_manifest(json.loads((folder / "character.json").read_text("utf-8-sig")))
    if data["name_en"] == "Lapland":
        fingerprint = hashlib.sha256(data["persona"].replace("\r\n", "\n").encode()).hexdigest()
        if fingerprint in _STOCK_LAPLAND_PERSONAS:
            data["persona"] = character("lapland").data["persona"].strip()
    return Character(key, folder, data)


def available_characters():
    keys = ["lapland", "muelsyse"]
    if library().is_dir():
        keys.extend(p.parent.name for p in library().glob("*/character.json"))
    result = {}
    for key in keys:
        try:
            result[key] = character(key)
        except (OSError, ValueError):
            continue
    return result


def image_files(role):
    result = {name: role.folder / name for name in IMAGES | VOICE_LIMITS.keys() if (role.folder / name).is_file()}
    result.setdefault("portrait.png", role.sprite("wait"))
    if role.id == "lapland":
        result["background.png"] = role.folder / "dialog-skin.webp"
        if (RUNTIME / "active" / BUNDLE).is_file():
            result[BUNDLE] = RUNTIME / "active" / BUNDLE
    return result


def write_pack(dest, data, assets):
    manifest = clean_manifest(data)
    dest = Path(dest)
    with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("character.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if "portrait.png" not in assets:
                raise ValueError("请选择一张基础立绘。")
            check_voice_files(manifest, set(assets))
            for name, path in assets.items():
                if name in VOICE_LIMITS:
                    if path.stat().st_size > VOICE_LIMITS[name]:
                        raise ValueError("语音模型超过角色包的大小限制。")
                    if name == BUNDLE:
                        validate_english_weights(path)
                    z.write(path, name)
                    continue
                if name not in IMAGES:
                    raise ValueError("角色图片名称无效。")
                with Image.open(path) as im:
                    if im.width * im.height > 16000000:
                        raise ValueError("单张图片最多 1600 万像素。")
                    encoded = io.BytesIO()
                    pixels = im.convert("RGBA")
                    pixels.info.clear()
                    pixels.save(encoded, format="PNG")
                if encoded.tell() > MAX_FILE:
                    raise ValueError("单张角色图片超过 12 MB。")
                z.writestr(name, encoded.getvalue())
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def import_pack(source, *, local_copy=False):
    library().mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(dir=library()) as tmp, zipfile.ZipFile(source) as z:
        stage = Path(tmp) / "character"
        stage.mkdir()
        entries = z.infolist()
        names = {i.filename for i in entries}
        if (len(names) != len(entries) or not {"portrait.png", "character.json"} <= names
                or names - IMAGES - VOICE_LIMITS.keys() - {"character.json"}
                or any(i.file_size > VOICE_LIMITS.get(i.filename, MAX_FILE) for i in entries)
                or z.getinfo("character.json").file_size > 256000):
            raise ValueError("角色包包含无效的文件、大小或目录。")
        data = clean_manifest(json.loads(z.read("character.json").decode("utf-8-sig")))
        if not local_copy:
            # External manifests cannot select secrets from the recipient's environment.
            for field in ("model_key_env", "tts_key_env"):
                data["settings"][field] = ""
        check_voice_files(data, names)
        if MODEL_FILES <= names:
            if z.getinfo("voice.onnx.json").file_size > 256000:
                raise ValueError("Piper 配置过大。")
            from piper.config import PiperConfig
            try:
                PiperConfig.from_dict(json.loads(z.read("voice.onnx.json")))
            except (TypeError, KeyError, ValueError) as exc:
                raise ValueError("Piper 模型配置无效。") from exc
            for name in MODEL_FILES:
                with z.open(name) as src, (stage / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        if BUNDLE in names:
            with z.open(BUNDLE) as src, (stage / BUNDLE).open("wb") as dst:
                shutil.copyfileobj(src, dst)
            validate_english_weights(stage / BUNDLE)
        (stage / "character.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        for name in names & IMAGES:
            raw = z.read(name)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format != "PNG" or im.width * im.height > 16000000:
                    raise ValueError("角色图片须为不超过 1600 万像素的 PNG。")
                pixels = im.convert("RGBA")
                pixels.info.clear()
                pixels.save(stage / name, format="PNG")
        stage.rename(library() / key)
    return character(key)


def check_voice_files(manifest, names):
    if (names & MODEL_FILES or manifest["settings"]["speech_provider"] == "piper") and not MODEL_FILES <= names:
        raise ValueError("本地 Piper 音色需要 .onnx 权重和同名的 .onnx.json 配置。")


def validate_english_weights(path):
    from safetensors import SafetensorError, safe_open
    try:
        with safe_open(str(path), framework="numpy") as tensors:
            names = set(tensors.keys())
            required = {f"ref.{m}.{key}" for m in MOODS for key in ("prompt", "spec", "phones")}
            if ((tensors.metadata() or {}).get("format") == FORMAT and required <= names
                    and any(k.startswith("gpt.") for k in names) and any(k.startswith("sovits.") for k in names)):
                for key in required:
                    tensor = tensors.get_slice(key)
                    shape = tensor.get_shape()
                    prefix, dtype = ([1, 1025], "F32") if key.endswith(".spec") else ([1], "I64")
                    if shape[:-1] != prefix or not 1 <= shape[-1] <= 1024 or tensor.get_dtype() != dtype:
                        raise ValueError("英配参考特征的形状或数据类型无效。")
                return
    except (OSError, ValueError, RuntimeError, SafetensorError) as exc:
        raise ValueError("英配权重文件损坏。") from exc
    raise ValueError("英配权重缺少 GPT-SoVITS v2 参数或情绪参考。")
