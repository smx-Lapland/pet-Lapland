from PIL import Image, ImageDraw, ImageFont

FONT_CN = r"C:\Windows\Fonts\msyh.ttc"
FONT_EN = r"C:\Windows\Fonts\segoeui.ttf"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"


def font(size: int, mono: bool = False):
    path = FONT_MONO if mono else FONT_EN
    return ImageFont.truetype(path, size)


def cn(size: int, bold: bool = False):
    # msyh.ttc contains multiple faces; index 0 is regular, 1 is bold on this host.
    return ImageFont.truetype(FONT_CN, size, index=1 if bold else 0)


def arrow(draw, x1, y1, x2, y2, color="#6EE7C7", width=4):
    draw.line((x1, y1, x2, y2), fill=color, width=width)
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 14
    pts = [
        (x2, y2),
        (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45)),
        (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45)),
    ]
    draw.polygon(pts, fill=color)


def box(draw, x1, y1, x2, y2, title, lines, accent, fill="#111C2F"):
    draw.rounded_rectangle((x1, y1, x2, y2), radius=22, fill=fill, outline="#2C3E5A", width=2)
    draw.rounded_rectangle((x1, y1, x2, y1 + 8), radius=4, fill=accent)
    draw.text((x1 + 26, y1 + 28), title, font=cn(30, True), fill="#F8FAFC")
    y = y1 + 82
    for line in lines:
        draw.text((x1 + 26, y), line, font=cn(22), fill="#B8C7DC")
        y += 38


# ---------- architecture ----------
W, H = 1900, 1080
img = Image.new("RGB", (W, H), "#080D18")
d = ImageDraw.Draw(img)
for x in range(0, W, 64):
    d.line((x, 0, x, H), fill="#0E1727", width=1)
for y in range(0, H, 64):
    d.line((0, y, W, y), fill="#0E1727", width=1)

d.text((80, 62), "MOCHI / SYSTEM ARCHITECTURE", font=font(24, True), fill="#6EE7C7")
d.text((80, 104), "从一次框选，到一段能听懂的解释", font=cn(48, True), fill="#F8FAFC")
d.text((82, 170), "Desktop pet · screen region capture · vision model · bilingual subtitles · English TTS",
       font=font(22), fill="#93A4BC")

boxes = [
    (70, 280, 390, 640, "1 · 桌面交互", ["透明置顶窗", "全局热键", "全屏选区遮罩"], "#6EE7C7"),
    (450, 280, 770, 640, "2 · 截图管线", ["Pillow 全屏截图", "裁切选区", "JPEG · 1600px"], "#7DD3FC"),
    (830, 280, 1150, 640, "3 · 模型层", ["DeepSeek-V4.1-Flash", "system prompt", "JSON schema"], "#F4C95D"),
    (1210, 280, 1530, 640, "4 · 表现层", ["分段字幕", "SAPI 生成 WAV", "winsound 顺序播放"], "#FF8A7A"),
    (1570, 280, 1840, 640, "5 · 本地状态", ["DSH 密钥", "config.json", "pet.log"], "#A78BFA"),
]
for b in boxes:
    box(d, *b)

# flow arrows
arrow(d, 390, 430, 450, 430)
arrow(d, 770, 430, 830, 430)
arrow(d, 1150, 430, 1210, 430)
arrow(d, 1530, 430, 1570, 430, color="#A78BFA", width=3)

for x, label in [(420, "Ctrl+Alt+S"), (800, "data:image/jpeg"), (1180, "segments[]"), (1550, "sync")]:
    d.text((x - 48, 246), label, font=font(16, True), fill="#64748B")

# lower explanatory row
d.rounded_rectangle((70, 720, 1840, 940), radius=26, fill="#0B1424", outline="#26344D", width=2)
d.text((100, 748), "关键设计", font=cn(24, True), fill="#F8FAFC")
cols = [
    ("01", "直接视觉理解", "不做 OCR，少一层误差；代码、UI、图表统一处理。"),
    ("02", "分段字幕", "2–4 段英文 + 中文；按 WAV 真实时长切换。"),
    ("03", "隐私边界", "仅热键触发；默认不落盘；只上传选区。"),
    ("04", "薄壳强模型", "Python/Tkinter 负责交互，模型负责理解。"),
]
for i, (num, title, desc) in enumerate(cols):
    x = 100 + i * 435
    d.text((x, 800), num, font=font(24, True), fill="#F4C95D")
    d.text((x + 46, 798), title, font=cn(23, True), fill="#E2E8F0")
    # simple wrap
    words = list(desc)
    line = ""
    y = 840
    for ch in words:
        line += ch
        if len(line) >= 18:
            d.text((x, y), line, font=cn(19), fill="#93A4BC")
            line = ""
            y += 30
    if line:
        d.text((x, y), line, font=cn(19), fill="#93A4BC")

d.text((80, 1000), "Privacy boundary: selected region only  ·  no microphone  ·  no background capture",
       font=font(18), fill="#64748B")
d.text((1560, 1000), "v0.1 · 2026-11-22", font=font(18), fill="#64748B")

img.save(r"E:\Mochi\assets\architecture.png")

# ---------- state machine ----------
W, H = 1900, 560
img = Image.new("RGB", (W, H), "#080D18")
d = ImageDraw.Draw(img)
for x in range(0, W, 64):
    d.line((x, 0, x, H), fill="#0E1727", width=1)
for y in range(0, H, 64):
    d.line((0, y, W, y), fill="#0E1727", width=1)

d.text((70, 48), "MOCHI / INTERACTION STATES", font=font(22, True), fill="#6EE7C7")
d.text((70, 86), "桌宠状态机", font=cn(42, True), fill="#F8FAFC")

states = [
    ("IDLE", "等待框选", 90, "#6EE7C7"),
    ("SELECTING", "拖拽选区", 430, "#7DD3FC"),
    ("THINKING", "视觉模型", 770, "#F4C95D"),
    ("SPEAKING", "语音 + 字幕", 1110, "#FF8A7A"),
    ("IDLE", "准备追问", 1450, "#6EE7C7"),
]
for label, cn_label, x, color in states:
    d.rounded_rectangle((x, 190, x + 300, 360), radius=26, fill="#111C2F", outline=color, width=3)
    d.text((x + 28, 214), label, font=font(26, True), fill=color)
    d.text((x + 28, 260), cn_label, font=cn(28, True), fill="#F8FAFC")
    d.text((x + 28, 312), "state", font=font(16), fill="#64748B")

for x in (390, 730, 1070, 1410):
    arrow(d, x, 275, x + 38, 275)
    d.text((x - 6, 234), "event", font=font(15), fill="#64748B")

d.text((90, 414), "Esc / too small  →  IDLE      ·      model error  →  ERROR      ·      click pet  →  stop speech",
       font=cn(20), fill="#93A4BC")
d.text((90, 456), "新一次 Ctrl+Alt+S 会开启新上下文；追问沿用上一张截图 + 最近 3 轮对话。",
       font=cn(20), fill="#93A4BC")
d.text((70, 512), "v0.1 · 2026-11-22", font=font(16), fill="#64748B")
img.save(r"E:\Mochi\assets\state-machine.png")

print("diagrams saved")
