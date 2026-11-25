from __future__ import annotations

import math
import re
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlsplit
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from .hotkeys import parse_hotkey
from .voice_input import INPUT_LANGUAGES, MIC_THRESHOLDS
from .voice_device import DEVICE_LABELS


class MemoryDialog(tk.Toplevel):
    def __init__(self, root, store, close):
        super().__init__(root)
        self.title("长期记忆")
        self.geometry("500x335")
        self.transient(root)
        self.protocol("WM_DELETE_WINDOW", close)
        self.bind("<Escape>", lambda _: close())
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="保存希望当前角色在以后的对话中记住的事项。").pack(anchor="w")
        listing = tk.Listbox(frame, exportselection=False)
        listing.pack(fill="both", expand=True, pady=8)
        entry = ttk.Entry(frame)
        entry.pack(fill="x")
        status = ttk.Label(frame)
        status.pack(anchor="w")
        rows = []
        def refresh():
            rows[:] = store.memories()
            listing.delete(0, "end")
            for _, note in rows:
                listing.insert("end", note)
        def change(remove=False):
            try:
                if remove:
                    selected = listing.curselection()
                    if not selected:
                        return
                    store.forget(rows[selected[0]][0])
                else:
                    store.remember(entry.get())
                    entry.delete(0, "end")
                refresh()
                status.configure(text="已忘记所选事项。" if remove else "已记住。")
            except (OSError, sqlite3.Error, ValueError) as exc:
                status.configure(text=str(exc))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Button(bar, text="记住", command=change).pack(side="left")
        ttk.Button(bar, text="忘记选中事项", command=lambda: change(True)).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=close).pack(side="right")
        entry.bind("<Return>", lambda _: change())
        try:
            refresh()
        except (OSError, sqlite3.Error) as exc:
            status.configure(text=str(exc))
        self.grab_set()


class CapturePreview(tk.Toplevel):
    def __init__(self, root, image, endpoint, on_send, on_cancel):
        super().__init__(root)
        self.title("发送前预览")
        self.transient(root)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.source = image
        self.on_send = on_send
        self.on_cancel = on_cancel
        self.rectangles = []
        self.origin = None
        self.pending = None
        preview = image.copy()
        preview.thumbnail((max(1, min(900, self.winfo_screenwidth() - 80)),
                           max(1, min(550, self.winfo_screenheight() - 220))), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview, master=self)
        self.size = preview.size
        body = ttk.Frame(self, padding=12)
        body.pack()
        host = urlsplit(endpoint).hostname or endpoint
        ttk.Label(body, text=f"发送到 {host}  ·  {image.width} × {image.height}").pack(anchor="w")
        ttk.Label(body, text="拖动可遮盖敏感内容。追问会沿用确认后的图片。").pack(anchor="w", pady=(4, 8))
        self.canvas = tk.Canvas(body, width=preview.width, height=preview.height, bd=0,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        self.undo = ttk.Button(actions, text="撤销遮盖", command=self._undo, state="disabled")
        self.undo.pack(side="left")
        ttk.Button(actions, text="确认发送", command=self.send).pack(side="right")
        ttk.Button(actions, text="取消", command=self.cancel).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _: self.cancel())
        self.grab_set()
        self.focus_force()

    def _point(self, event):
        return max(0, min(event.x, self.size[0])), max(0, min(event.y, self.size[1]))

    def _press(self, event):
        self.origin = self._point(event)
        self.pending = self.canvas.create_rectangle(*self.origin, *self.origin, fill="black", outline="")

    def _drag(self, event):
        if self.origin is not None:
            self.canvas.coords(self.pending, *self.origin, *self._point(event))

    def _release(self, event):
        if self.origin is None:
            return
        self._drag(event)
        x, y = self._point(event)
        x0, y0 = self.origin
        if x != x0 and y != y0:
            sx, sy = self.source.width / self.size[0], self.source.height / self.size[1]
            box = (math.floor(min(x, x0) * sx), math.floor(min(y, y0) * sy),
                   math.ceil(max(x, x0) * sx) - 1, math.ceil(max(y, y0) * sy) - 1)
            self.rectangles.append((self.pending, box))
            self.undo.configure(state="normal")
        else:
            self.canvas.delete(self.pending)
        self.origin = self.pending = None

    def _undo(self):
        item, _ = self.rectangles.pop()
        self.canvas.delete(item)
        if not self.rectangles:
            self.undo.configure(state="disabled")

    def send(self):
        image = self.source.copy()
        draw = ImageDraw.Draw(image)
        for _, box in self.rectangles:
            draw.rectangle(box, fill="black")
        self.destroy()
        self.on_send(image)

    def cancel(self):
        self.destroy()
        self.on_cancel()


class PanelSizeDialog(tk.Toplevel):
    def __init__(self, root, size, limits, save, close):
        super().__init__(root)
        self.title("对话框尺寸")
        self.transient(root)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=14)
        body.pack()
        ttk.Label(body, text="宽 × 高（像素）").pack(anchor="w")
        self.size = tk.StringVar(self, value=f"{size[0]} × {size[1]}")
        min_w, min_h, max_w, max_h = limits
        presets = [f"{w} × {h}" for w, h in ((600, 390), (793, 430), (960, 540), (1280, 720))
                   if min_w <= w <= max_w and min_h <= h <= max_h]
        box = ttk.Combobox(body, textvariable=self.size, values=presets)
        box.pack(fill="x", pady=5)
        ttk.Label(body, text=f"宽 {min_w}–{max_w}，高 {min_h}–{max_h}").pack(anchor="w")
        error = ttk.Label(body, foreground="#A12622")
        error.pack(anchor="w")

        def apply(_event=None):
            match = re.fullmatch(r"\s*(\d{1,5})\s*[xX×＊*]\s*(\d{1,5})\s*", self.size.get())
            if match:
                w, h = map(int, match.groups())
                if min_w <= w <= max_w and min_h <= h <= max_h:
                    save(w, h)
                    close()
                    return
            error.configure(text="请输入范围内的宽 × 高，例如 793 × 430。")
        actions = ttk.Frame(body)
        actions.pack(anchor="e")
        ttk.Button(actions, text="取消", command=close).pack(side="left", padx=5)
        self.apply_button = ttk.Button(actions, text="应用", command=apply)
        self.apply_button.pack(side="left")
        self.bind("<Return>", apply)
        self.bind("<Escape>", lambda _: close())
        self.protocol("WM_DELETE_WINDOW", close)
        self.grab_set()
        box.focus_set()


class SettingsDialog(tk.Toplevel):
    def __init__(self, root, cfg, save, close):
        super().__init__(root)
        self.title(f"{root.title()} 设置")
        self.attributes("-topmost", True)
        self.transient(root)
        self.resizable(False, False)
        self.save = save
        self.close = close
        self.fields = {}
        body = ttk.Frame(self, padding=14)
        body.pack()
        fields = [
            ("model", "视觉模型", cfg["model"]),
            ("speech_language", "回答和朗读语言", "意大利语" if cfg.get("speech_language") == "it" else "英语"),
            ("mic_language", "麦克风输入语言", INPUT_LANGUAGES.get(cfg.get("mic_language"), INPUT_LANGUAGES["zh"])),
            ("mic_threshold", "连续对话收音门槛", cfg.get("mic_threshold", "较高")),
            ("speech_provider", "语音服务", cfg["speech_provider"]),
            ("voice_device", "本地神经语音运算", DEVICE_LABELS.get(cfg.get("voice_device"), DEVICE_LABELS["auto"])),
            ("voice", "系统朗读音色", cfg["voice"]),
            ("neural_voice", "云端英语音色", cfg["neural_voice"]),
            ("neural_voice_it", "云端意大利语音色", cfg.get("neural_voice_it", "it-IT-ElsaNeural")),
            ("rate", "语速（−10 至 10）", str(cfg["rate"])),
        ]
        for key, label in [("capture", "框选"), ("ask", "追问"), ("toggle", "显示 / 隐藏"), ("quit", "退出")]:
            fields.append((key, label + "快捷键", cfg["hotkeys"][key]))
        for row, (key, label, value) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            var = self.fields[key] = tk.StringVar(self, value=value)
            if key == "speech_provider":
                providers = ["lapland", "sapi", "edge", "azure", "api"]
                if cfg.get("speech_provider") == "piper":
                    providers.append("piper")
                if (Path(__file__).resolve().parents[1] / "models/piper-vits-reference/tone.onnx").is_file():
                    providers.insert(1, "vits")
                widget = ttk.Combobox(body, textvariable=var, values=providers, state="readonly")
            elif key == "speech_language":
                widget = ttk.Combobox(body, textvariable=var, values=("英语", "意大利语"), state="readonly")
            elif key == "voice_device":
                widget = ttk.Combobox(body, textvariable=var, values=tuple(DEVICE_LABELS.values()), state="readonly")
            elif key == "mic_threshold":
                widget = ttk.Combobox(body, textvariable=var, values=tuple(MIC_THRESHOLDS), state="readonly")
            elif key == "mic_language":
                widget = ttk.Combobox(body, textvariable=var, values=tuple(INPUT_LANGUAGES.values()), state="readonly")
            else:
                widget = ttk.Entry(body, textvariable=var, width=30)
            widget.grid(row=row, column=1, sticky="ew")
        row = len(fields)
        for key, label in [("confirm_capture", "每次框选后预览确认"), ("stream", "流式回答，台词就绪即朗读"),
                             ("italian_reference_voice", "意配使用原声参考模型（需安装，本机 GPU）"),
                             ("verify_italian_voice", "意配使用 VITS 并在播放前复核发音（等待更久）"),
                           ("muted", "静音，仅显示字幕"), ("save_history", "在本机保存新对话的文字记录")]:
            var = self.fields[key] = tk.BooleanVar(self, value=cfg[key])
            ttk.Checkbutton(body, text=label, variable=var).grid(row=row, columnspan=2, sticky="w")
            row += 1
        ttk.Label(body, wraplength=375, text="vits 意配可选快速合成或原声参考模型。lapland 使用 PocketTTS。Edge 和 Azure 会将口播文本发给微软。"
                  "Azure 需要 SPEECH_KEY 和 SPEECH_REGION 环境变量。").grid(row=row, columnspan=2, sticky="w", pady=6)
        self.error = ttk.Label(body, foreground="#A12622", wraplength=375)
        self.error.grid(row=row+1, columnspan=2, sticky="w")
        actions = ttk.Frame(body)
        actions.grid(row=row+2, columnspan=2, sticky="e")
        ttk.Button(actions, text="取消", command=close).pack(side="left", padx=5)
        ttk.Button(actions, text="保存", command=self.apply).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", close)
        self.bind("<Escape>", lambda _: close())
        self.grab_set()

    def apply(self):
        values = {key: var.get() for key, var in self.fields.items()}
        values["speech_language"] = "it" if values["speech_language"] == "意大利语" else "en"
        values["mic_language"] = next(k for k, label in INPUT_LANGUAGES.items() if label == values["mic_language"])
        values["voice_device"] = "cpu" if values["voice_device"] == DEVICE_LABELS["cpu"] else "auto"
        try:
            values["model"] = values["model"].strip()
            if not values["model"]:
                raise ValueError("请输入视觉模型名称。")
            if not re.fullmatch(r"en-[A-Za-z]{2,3}-[A-Za-z0-9]+Neural", values["neural_voice"]):
                raise ValueError("请输入英文神经音色，例如 en-US-JennyNeural。")
            if not re.fullmatch(r"it-[A-Za-z]{2,3}-[A-Za-z0-9]+Neural", values["neural_voice_it"]):
                raise ValueError("请输入意大利语神经音色，例如 it-IT-ElsaNeural。")
            if not re.fullmatch(r"-?\d+", values["rate"]) or not -10 <= int(values["rate"]) <= 10:
                raise ValueError("语速须为 −10 至 10 的整数。")
            values["rate"] = int(values["rate"])
            keys = {key: values.pop(key).strip().lower() for key in ("capture", "ask", "toggle", "quit")}
            parsed = [parse_hotkey(spec) for spec in keys.values()]
            if any(item is None for item in parsed) or len({item[:2] for item in parsed}) != len(keys):
                raise ValueError("请为四项操作设置有效且不同的快捷键。")
            values["hotkeys"] = keys
            self.save(values)
        except (ValueError, OSError) as exc:
            self.error.configure(text=str(exc))


class HistoryDialog(tk.Toplevel):
    def __init__(self, root, store, close, on_delete=None):
        super().__init__(root)
        self.store = store
        self.on_delete = on_delete
        self.title(f"{root.title()} · 对话历史")
        self.transient(root)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", close)
        self.bind("<Escape>", lambda _: close())
        self.geometry(f"{min(900, self.winfo_screenwidth() - 40)}x{min(540, self.winfo_screenheight() - 80)}")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="搜索文字或日期").pack(side="left", padx=(0, 8))
        self.query = tk.StringVar(self)
        search = ttk.Entry(toolbar, textvariable=self.query)
        search.pack(side="left", fill="x", expand=True)
        self.remove = ttk.Button(toolbar, text="删除此对话", command=self.delete_selected)
        self.remove.pack(side="left", padx=(8, 0))
        panes = ttk.Panedwindow(body, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=2)
        self.listing = ttk.Treeview(left, columns=("date",), show="tree headings",
                                    selectmode="browse", height=14)
        self.listing.heading("#0", text="对话")
        self.listing.heading("date", text="最近更新")
        self.listing.column("#0", width=150, minwidth=80)
        self.listing.column("date", width=120, minwidth=70)
        self.detail = tk.Text(right, width=42, wrap="word", padx=12, pady=8, relief="flat",
                              font=("Microsoft YaHei UI", 10), state="disabled")
        self.detail.tag_configure("time", foreground="#53647A", spacing1=6, spacing3=4)
        for parent, widget in [(left, self.listing), (right, self.detail)]:
            bar = ttk.Scrollbar(parent, command=widget.yview)
            widget.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
            widget.pack(fill="both", expand=True)
        self.listing.bind("<<TreeviewSelect>>", lambda _: self.show_selected())
        self.query.trace_add("write", lambda *_: self.refresh())
        self.refresh()
        self.grab_set()
        search.focus_set()

    def show_text(self, message):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", message)
        self.detail.configure(state="disabled")

    def refresh(self):
        selected = self.listing.selection()
        self.listing.delete(*self.listing.get_children())
        self.remove.state(["disabled"])
        query = self.query.get().strip()
        try:
            rows = self.store.conversations(query)
        except (OSError, sqlite3.Error):
            self.show_text("无法读取本地对话历史。请检查数据目录是否可访问。")
            return
        if not rows:
            self.show_text("没有匹配的对话。" if query else "暂无历史记录。开启本地保存后，完成的讲解和追问会显示在这里。")
            return
        for topic, title, updated in rows:
            self.listing.insert("", "end", iid=topic, text=title, values=(updated[:16],))
        topic = selected[0] if selected and self.listing.exists(selected[0]) else rows[0][0]
        self.listing.selection_set(topic)
        self.listing.see(topic)
        self.show_selected()

    def show_selected(self):
        selected = self.listing.selection()
        if not selected:
            return
        try:
            turns = self.store.turns(selected[0])
        except (OSError, sqlite3.Error):
            self.show_text("无法读取这段对话。")
            return
        self.remove.state(["!disabled"])
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        for stamp, text in turns:
            self.detail.insert("end", stamp + "\n", "time")
            self.detail.insert("end", text + "\n\n")
        self.detail.configure(state="disabled")
        self.detail.yview_moveto(0)

    def delete_selected(self):
        selected = self.listing.selection()
        if not selected or not messagebox.askyesno("删除对话", "从本机历史中删除这段对话的全部问答？", parent=self):
            return
        try:
            self.store.delete(selected[0])
        except (OSError, sqlite3.Error):
            self.show_text("删除失败，请检查数据目录是否可写。")
            return
        if self.on_delete:
            self.on_delete(selected[0])
        self.refresh()
