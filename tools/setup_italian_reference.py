"""Install the optional GPU Italian reference renderer and prepare its five local prompts."""
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pet.italian_reference import REFERENCES, RUNTIME


def main():
    if os.name != "nt" or not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise SystemExit("请使用 Windows x64 Python 3.10 至 3.12。")
    corpus = ROOT / "models/pocket-tts/corpus/it"
    if not all((corpus / name).is_file() for name in REFERENCES.values()):
        raise SystemExit("请先运行 tools/setup_lapland_voice.py 安装意配角色录音。")
    python = RUNTIME / "venv/Scripts/python.exe"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(RUNTIME / "venv")
    pip = [str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--index-url"]
    subprocess.run(pip + ["https://download.pytorch.org/whl/cu128", "torch==2.8.0", "torchaudio==2.8.0"], check=True)
    subprocess.run(pip + ["https://pypi.org/simple", "faster-qwen3-tts==0.3.2", "qwen-tts==0.1.1",
                          "numpy<2", "librosa==0.11.0", "audiotsm==0.1.2", "miniaudio>=1.61,<2"], check=True)
    from huggingface_hub import snapshot_download
    snapshot_download("Qwen/Qwen3-TTS-12Hz-0.6B-Base", revision="5d83992436eae1d760afd27aff78a71d676296fc",
                      allow_patterns=["*.safetensors", "*.json", "*.txt", "README.md"], local_dir=RUNTIME / "base")
    subprocess.run([str(python), str(ROOT / "pet/italian_reference.py"), "--prepare"], check=True)


if __name__ == "__main__":
    main()
