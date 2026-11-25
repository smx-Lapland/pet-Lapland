"""Blind, bilingual dialogue review through the desktop chat client using fictional cases only."""
import argparse
import csv
import itertools
import json
import random
import re
import shutil
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.config import APP_DIR, CACHE_DIR, DEFAULTS, apply_credentials, load_config
from pet.llm import DeepSeekClient


# Short screen excerpts are rendered locally. No desktop capture or saved history is used.
CASES = [
    ("闲聊", "舞会上大家都等别人先去拿最后一块蛋糕。你等吗？", "", ""),
    ("疲惫", "盯着屏幕太久了，眼睛酸，我想歇会。", "", ""),
    ("报错", "这次为什么出错，先改哪一处？", "items = ['A', 'B']\nprint(items[2])\nIndexError: list index out of range", ""),
    ("纠错", "不对，上面这行是赋值。直接告诉我你看错了哪里。", "total = 0", "That line starts a loop."),
    ("信息不足", "能看出来具体哪个文件丢了吗？", "File not found: [cropped]", ""),
    ("意外挑战", "你刚说没人解得开这个局。那个新人闭着眼就赢了你。", "", ""),
    ("提醒", "我差点把带私人地址的邀请函发进公开群。现在还在草稿箱。", "", ""),
    ("反语", "排队一小时，窗口刚好在我面前关了。夸一句他们的效率吧。", "", ""),
    ("身体状态", "用第一人称说你跑上楼喘不上气了，却还想赢。", "", ""),
    ("双重否定", "用你自己的口气说：我不是不想再来一局，我是怕你输不起。保留这两层意思。", "", ""),
    ("反问与转折", "先反问我是不是真以为你会走，然后告诉我你要留下看结局。", "", ""),
    ("严肃求助", "同步之前发现两份文件不同，我怕覆盖重要内容。只说最先做什么。", "", ""),
]
SCORES = ("去客服腔", "口语地道", "情绪与实听", "中译保真")


def render_review(folder):
    from pet.speech import SpeechEngine

    with (folder / "review.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    CACHE_DIR.mkdir(exist_ok=True)
    audio = folder / "audio"
    audio.mkdir(exist_ok=True)
    settings = load_config()
    voice_settings = {k: settings[k] for k in ("voice_device", "rate", "verify_italian_voice",
                                               "italian_reference_voice")}
    for lang in ("en", "it"):
        engine = SpeechEngine({**DEFAULTS, **voice_settings, "speech_provider": "vits", "speech_language": lang},
                              APP_DIR / "pet/tts_worker.ps1")
        stop = threading.Event()
        try:
            engine.warmup()
            for row in rows:
                if row["语言"] != lang:
                    continue
                if not re.fullmatch(r"\d{2}-(en|it)-\d+", row["编号"]):
                    raise ValueError("Invalid blind sample ID")
                target = audio / (row["编号"] + ".wav")
                path, provider = engine.prepare_segment(row["台词"], stop, "vits", row["标签"])
                if provider != "vits" or not path:
                    if path:
                        path.unlink(missing_ok=True)
                    raise RuntimeError(f"{lang} character voice unavailable")
                shutil.move(path, target)
        finally:
            engine.close_renderer()


def play_review_preview(folder):
    """Play one bilingual WAV after a completed voice review, without opening a player."""
    import miniaudio
    import winsound

    sample_rate = 32000
    takes = [miniaudio.decode_file(str(folder / "audio" / f"08-{lang}-1.wav"),
                                   nchannels=1, sample_rate=sample_rate).samples.tobytes()
             for lang in ("en", "it")]
    preview = folder / "preview.wav"
    with wave.open(str(preview), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        output.writeframes(takes[0] + bytes(sample_rate) + takes[1])
    winsound.PlaySound(str(preview), winsound.SND_FILENAME | winsound.SND_NODEFAULT)


def generate(job):
    model, lang, index = job
    scene, question, excerpt, correction = CASES[index]
    cfg = {**DEFAULTS, "model": model, "speech_language": lang}
    apply_credentials(cfg)
    client = DeepSeekClient(cfg)
    image = None
    if excerpt:
        image = Image.new("RGB", (600, 120), "white")
        ImageDraw.Draw(image).multiline_text((14, 12), excerpt, fill="black", font_size=19, spacing=5)
    history = [{"role": "assistant", "content": correction}] if correction else []
    previews = []
    result = client.ask(image, question, history=history, follow_up=bool(history), on_first=previews.append)
    line = result["segments"][0]
    spoken = line[lang]
    flags = []
    if re.match(r"^(sure\b|of course\b|here is\b|let me explain\b|certo[!,]|ecco a te\b|posso aiutarti\b)", spoken, re.I):
        flags.append("客服起手")
    if not line.get("emotion") or not line["zh"]:
        flags.append("缺标签或译文")
    if len(spoken.split()) > 35:
        flags.append("超过35词")
    if not previews or previews[0]["segments"][0][lang] != spoken:
        flags.append("检查流式首段")
    return (index, lang, model), (scene, question, line.get("emotion", ""), spoken, line["zh"], "、".join(flags))


def compare_translations(folder):
    """Change only the translator, holding each spoken take fixed for the paired comparison."""
    cfg = {**DEFAULTS, "model": "deepseek-flash"}
    apply_credentials(cfg)
    client = DeepSeekClient(cfg)
    def translate(row):
        payload = {"model": client.model, "stream": False, "thinking": {"type": "disabled"},
                   "temperature": .2, "max_tokens": 320, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content":
                        '把给定的一句角色台词译成口语简体中文。保留说话者、人称、否定范围、反问、反讽和转折。'
                        '“out of breath”表示喘不上气。只译台词，不补剧情。仅输出 JSON：{"zh":"译文"}。'},
                       {"role": "user", "content": row["台词"]}]}
        result = json.loads(client._extract_content(client._post(payload, None)))
        return [row["编号"], row["台词"], row["中文"], result["zh"]]
    with (folder / "review.csv").open(encoding="utf-8-sig", newline="") as f:
        paired = [r for r in csv.DictReader(f) if r["场景"] in ("反语", "身体状态", "双重否定", "反问与转折")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(translate, paired))
    with (folder / "translations.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(("编号", "同一条外语台词", "随台词生成的译文", "单独请求译文"))
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="new output directory")
    parser.add_argument("--models", nargs="+", default=["deepseek-flash", "deepseek-v4-pro"])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audio-only", action="store_true", help="render the blind sheet and play one bilingual preview")
    mode.add_argument("--compare-translation", action="store_true", help="retranslate the four fidelity cases from an existing review")
    args = parser.parse_args()
    if args.audio_only:
        render_review(args.out)
        play_review_preview(args.out)
        return
    if args.compare_translation:
        compare_translations(args.out)
        return
    if len(set(args.models)) != len(args.models):
        parser.error("model names must be distinct")
    args.out.mkdir(parents=True, exist_ok=False)
    jobs = itertools.product(args.models, ("en", "it"), range(len(CASES)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = dict(pool.map(generate, jobs))
    keys = {}
    rng = random.SystemRandom()
    with (args.out / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        rows = csv.writer(f)
        rows.writerow(("编号", "语言", "场景", "用户", "标签", "台词", "中文", "自动提示", *SCORES))
        for i in range(len(CASES)):
            for lang in ("en", "it"):
                models = args.models.copy()
                rng.shuffle(models)
                for column, model in enumerate(models, 1):
                    key = f"{i+1:02}-{lang}-{column}"
                    keys[key] = model
                    rows.writerow((key, lang, *answers[i, lang, model], "", "", "", ""))
    (args.out / "key.json").write_text(json.dumps(keys, indent=2), "utf-8")
    print(f"{len(keys)} blind samples written to {args.out}")


if __name__ == "__main__":
    main()
