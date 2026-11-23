"""Build the Windows x64 starter and the example character from the installed app."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pet.characters import character, image_files, write_pack
from pet.voice_input import FILES, MODEL_DIR


def distribution_files():
    site = Path(sys.prefix) / "Lib/site-packages"
    pending = (ROOT / "requirements.txt").read_text("utf-8").splitlines()
    done = set()
    files = set()
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        dist = importlib.metadata.distribution(requirement.name)
        name = dist.metadata["Name"].lower()
        if name in done:
            continue
        done.add(name)
        pending.extend(dist.requires or [])
        for item in dist.files or ():
            p = Path(dist.locate_file(item)).resolve()
            if (p.is_relative_to(site) and p.is_file() and "__pycache__" not in p.parts
                    and p.suffix != ".pyc" and p.name not in ("direct_url.json", "uv_cache.json")):
                files.add(p)
    for p in sorted(files):
        yield p, "runtime/Lib/site-packages/" + p.relative_to(site).as_posix()


def build():
    if os.name != "nt" or sys.prefix == sys.base_prefix:
        raise RuntimeError("请在已安装 requirements.txt 的 Windows 项目虚拟环境中构建。")
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    role = character("muelsyse")
    write_pack(out / "Muelsyse.petpack", role.data, image_files(role))
    runtime = Path(sys.base_prefix)
    with tempfile.TemporaryDirectory(dir=out) as temp:
        dest = Path(temp) / "Muelsyse-Windows-x64.zip"
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=4) as z:
            for p in runtime.iterdir():
                if p.is_file() and p.suffix.lower() in (".exe", ".dll", ".txt"):
                    z.write(p, "runtime/" + p.name)
            for folder in ("DLLs", "Lib", "tcl"):
                for p in (runtime / folder).rglob("*"):
                    rel = p.relative_to(runtime)
                    if (p.is_file() and not {"site-packages", "__pycache__"} & set(rel.parts)
                            and p.suffix != ".pyc"):
                        z.write(p, "runtime/" + rel.as_posix())
            for p, name in distribution_files():
                z.write(p, name)
            for folder in ("pet", "assets/lapland", "assets/characters/muelsyse"):
                for p in (ROOT / folder).iterdir():
                    if p.is_file() and p.suffix in (".py", ".ps1", ".png", ".ico", ".webp", ".json", ".txt"):
                        z.write(p, p.relative_to(ROOT).as_posix())
            for name in FILES:
                z.write(MODEL_DIR / name, "models/whisper-base/" + name)
            z.write(ROOT / "LICENSE", "LICENSE")
            z.write(role.folder / "artwork.md", "assets/characters/muelsyse/artwork.md")
            z.write(ROOT / "docs/portable-start.md", "开始使用.md")
            z.write(ROOT / "docs/whisper-LICENSE.txt", "models/whisper-base/LICENSE.txt")
            z.writestr("config.json", json.dumps({"character_id": role.id, **role.data["settings"]}, ensure_ascii=False))
            z.writestr("Start.cmd", '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
                       'set "PYTHONHOME="\r\nset "PYTHONPATH="\r\nset "PYTHONNOUSERSITE=1"\r\n'
                       'start "" "runtime\\pythonw.exe" -m pet.main\r\n')
        dest.replace(out / dest.name)


if __name__ == "__main__":
    build()
