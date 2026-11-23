"""Install the optional local English voice runtime on Windows with Python 3.10."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1] / "models/gpt-sovits-en"
REV = "6d51ffceafbf240145619af4c92408a6e54b7924"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", action="store_true", help="install CPU PyTorch instead of CUDA 12.8")
    args = parser.parse_args()
    if os.name != "nt" or sys.version_info[:2] != (3, 10):
        parser.error("请用 Windows 64 位 Python 3.10 运行此脚本。")
    ROOT.mkdir(parents=True, exist_ok=True)
    engine = ROOT / "engine"
    if not engine.exists():
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            checkout = Path(tmp) / "engine"
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "fetch", "--depth", "1",
                            "https://github.com/RVC-Boss/GPT-SoVITS.git", REV], check=True)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"], check=True)
            checkout.rename(engine)
    head = subprocess.check_output(["git", "-C", str(engine), "rev-parse", "HEAD"], text=True).strip()
    if head != REV:
        raise SystemExit("已有 GPT-SoVITS 源码版本不同。请先将原 engine 目录备份到其他位置。")
    env = ROOT / "venv"
    python = env / "Scripts/python.exe"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(env)
    uv = shutil.which("uv")
    pip = ([uv, "--no-config", "pip", "install", "--python", str(python), "--native-tls"] if uv else
           [str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check"])
    torch_index = "https://pypi.org/simple" if args.cpu else "https://download.pytorch.org/whl/cu128"
    subprocess.run(pip + ["--index-url", torch_index,
                          "torch==2.8.0", "torchaudio==2.8.0"], check=True)
    subprocess.run(pip + ["--index-url", "https://pypi.org/simple", "numpy<2", "librosa==0.10.2",
                          "torchmetrics==1.5.2", "matplotlib", "g2p_en",
                          "nltk==3.9.1", "wordsegment", "rotary_embedding_torch", "x_transformers==2.4.14",
                          "safetensors", "PyYAML", "miniaudio", "audiotsm==0.1.2", "praat-parselmouth==0.4.7"], check=True)
    subprocess.run([str(python), "-c",
                    "import nltk,sys; [nltk.download(item, download_dir=sys.argv[1], raise_on_error=True)"
                    " for item in ('cmudict','averaged_perceptron_tagger','averaged_perceptron_tagger_eng')]",
                    str(ROOT / "nltk_data")], check=True)
    subprocess.run([str(python), "-c", "from text import english; from module.models import SynthesizerTrn;"
                    "from AR.models.t2s_model import Text2SemanticDecoder;"
                    "assert english.g2p('Ready.')"], cwd=engine,
                   env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, (engine / "GPT_SoVITS", engine))),
                        "NLTK_DATA": str(ROOT / "nltk_data")}, check=True)
    print("英配运行库已就绪。可在角色与迁移中导入带语音权重的拉普兰德人物包。")


if __name__ == "__main__":
    main()
