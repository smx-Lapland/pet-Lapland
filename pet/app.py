from __future__ import annotations

import json
import logging
import math
import queue
import re
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, ImageTk

from . import APP_NAME
from .config import APP_DIR, CAPTURE_DIR, DEFAULTS, apply_credentials, save_config
from .connections import ConnectionDialog
from .characters import POSES, SHARE_KEYS, character
from .character_dialog import CharacterDialog
from . import dsh_tasks
from .dsh_projects import projects
from .task_dialog import TaskDialog
from .dialogs import CapturePreview, HistoryDialog, MemoryDialog, SettingsDialog
from .history import ConversationHistory
from .hotkeys import HotkeyManager
from .llm import DeepSeekClient, LLMError
from .screen import (
    RegionSelector,
    capture_region,
    save_debug_capture,
    virtual_screen,
    work_area,
)
from .speech import SpeechEngine, wav_duration, subtitle_duration
from .subtitles import ScrollingCaptions
from .voice_input import MIC_THRESHOLDS, SpeechGate, VoiceInput, VoiceInputError

log = logging.getLogger("mochi.app")

TRANSPARENT = "#010203"
BUBBLE = "#25222B"
BUBBLE_BORDER = "#665B67"
BUBBLE_SHADOW = "#18151D"
CREAM = "#FFF5E8"
WHITE = "#FAF7F4"
GOLD = "#E2C9AE"
TEAL = "#DAC0B1"
CONTROL = "#443B46"
CORAL = "#FF8A7A"
MUTED = "#CEC2CC"
SPRITES = APP_DIR / "assets" / "lapland"

DEFAULT_CAPTURE_QUESTION = "Explain what this is and what I should notice."


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kwargs):
    points: list[float] = []
    steps = 7
    for cx, cy, start in (
        (x2 - r, y1 + r, -90),
        (x2 - r, y2 - r, 0),
        (x1 + r, y2 - r, 90),
        (x1 + r, y1 + r, 180),
    ):
        for index in range(steps + 1):
            angle = math.radians(start + 90 * index / steps)
            points.extend([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return canvas.create_polygon(points, smooth=True, **kwargs)


def thinking_pose(elapsed: float) -> str:
    if elapsed < 0.45:
        return "curious"
    if elapsed < 2.6:
        return "ponder"
    if elapsed < 6.0:
        return "wait"
    # A brief impatient reaction, followed by quieter pauses on long requests.
    phase = (elapsed - 6.0) % 8.0
    if phase < 1.5:
        return "impatient"
    return "ponder" if phase < 4.5 else "wait"


class MochiApp:
    W, H = 960, 430
    PET_WIDTH = 167
    PANEL_IDLE_SECONDS = 30
    CONTENT_X = 198

    @property
    def subtitle_width(self):
        return self.W - self.CONTENT_X - 50

    def __init__(self, cfg: dict[str, Any], *, start_hidden: bool = False):
        self.cfg = cfg
        self.character = character(cfg.get("character_id", "lapland"))
        self.project_tasks = {}
        self.events: queue.Queue = queue.Queue()
        self.client = DeepSeekClient(cfg)
        self.speech = SpeechEngine(cfg, Path(__file__).with_name("tts_worker.ps1"))
        self.voice_input = VoiceInput()
        self.voice_turn: threading.Event | None = None
        self.conversation: SpeechGate | None = None

        self.request_speech = self.speech
        self.spoken_key = self.client.language
        self.state = "idle"
        self.last_image = None
        self.last_answer: dict[str, Any] | None = None
        self.history: list[dict[str, str]] = []
        self.transcript: list[str] = []
        self._load_project_history()
        self.compaction_lock = threading.Lock()
        self.pending_question = ""
        self.request_seq = 0
        self.awaiting_answer = False
        self.stream_started = False
        self.answer_voice_token = 0
        self.dialog: tk.Toplevel | None = None
        self.speech_token = 0
        self.speech_stop: threading.Event | None = None
        self.renderer_timer = None
        self.capturing = False
        self.closing = False
        self.hidden = start_hidden
        self.selector: RegionSelector | None = None
        self.input_visible = False
        self.drag_start: tuple[int, int] | None = None
        self.drag_origin: tuple[int, int] | None = None
        self.drag_moved = False
        self.press_time = 0.0
        self.pet_dy = 0.0
        self.phase = 0.0
        self.thinking_since = 0.0

        self.root = tk.Tk()
        self.panel_open = False
        self.compact_position = None
        self.panel_active_at = time.monotonic()
        self.root.withdraw()
        self.root.title(self.role_title)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            log.warning("transparentcolor unavailable")
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            self.root.attributes("-toolwindow", True)
        except Exception:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Escape>", self._on_escape)

        self._place_initial()
        self.canvas = tk.Canvas(
            self.root,
            width=self.W,
            height=self.H,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._build_bubble()
        self._build_input()
        self._build_pet()
        self._build_menu()
        self._bind_events()
        self._show_idle()
        self.canvas.itemconfigure(self.tail_id, state="hidden")
        if not self.client.available:
            self.root.after(350, self._show_missing_key)

        if not start_hidden:
            self._restore_window()

        self.hotkeys = HotkeyManager(self.cfg.get("hotkeys", {}), self.events)
        self.hotkeys.start()

        self.root.after(50, self._poll_events)
        self.root.after(50, self._animate)
        self.root.after(1000, self._check_panel)
        log.info(
            "%s started model=%s key_source=%s screen=%s",
            APP_NAME,
            self.cfg.get("model"),
            self.cfg.get("_key_source"),
            virtual_screen(),
        )

    # ---------- window ----------

    def _place_initial(self) -> None:
        saved = self.cfg.get("window") or {}
        x, y = saved.get("x"), saved.get("y")
        left, top, right, bottom = work_area()
        self.W = min(self.W, max(720, right - left - 26))
        if not isinstance(x, int) or not isinstance(y, int):
            x = right - self.PET_WIDTH - 26
            y = bottom - self.H - 18
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(int(x), vx + vw - self.PET_WIDTH))
        y = max(vy, min(int(y), vy + vh - self.H))
        self.root.geometry(f"{self.PET_WIDTH}x{self.H}+{x}+{y}")

    def _save_position(self) -> None:
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.cfg.setdefault("window", {})["x"] = int(x)
        self.cfg["window"]["y"] = int(y)
        try:
            save_config(self.cfg)
        except Exception:
            log.exception("could not save window position")

    def _restore_window(self) -> None:
        self.root.deiconify()
        try:
            self.root.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.hidden = False

    def _hide_window(self) -> None:
        self._close_panel()
        self.root.withdraw()
        self.hidden = True

    def _toggle_window(self) -> None:
        if self.capturing or self.dialog:
            return
        if self.hidden:
            self._restore_window()
        else:
            self._hide_window()

    def _open_panel(self):
        self.panel_active_at = time.monotonic()
        if not self.panel_open:
            self.panel_open = True
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.compact_position = (x, y)
            vx, _, vw, _ = virtual_screen()
            x = max(vx, min(x, vx + vw - self.W))
            self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
            self.canvas.itemconfigure(self.tail_id, state="normal")
            self.canvas.itemconfigure(self.talk_window, state="normal")
            self.canvas.itemconfigure(self.conversation_window, state="normal")
            self._warm_voice_for_input()

    def _close_panel(self):
        self._cancel_voice_input()
        self.stop_speech()
        self._set_input_visible(False)
        if self.root.focus_get() in (self.entry, self.talk_button, self.conversation_button):
            self.root.focus_set()
        self.panel_open = False
        self.canvas.itemconfigure(self.tail_id, state="hidden")
        self.canvas.itemconfigure(self.talk_window, state="hidden")
        self.canvas.itemconfigure(self.conversation_window, state="hidden")
        geometry = f"{self.PET_WIDTH}x{self.H}"
        if self.compact_position:
            x, y = self.compact_position
            geometry += f"+{x}+{y}"
            self.compact_position = None
        self.root.geometry(geometry)

    def _check_panel(self):
        if self.closing:
            return
        if self.panel_open and not self.hidden:
            now = time.monotonic()
            px, py = self.root.winfo_pointerxy()
            x, y = px - self.root.winfo_rootx(), py - self.root.winfo_rooty()
            reading = self.PET_WIDTH <= x < self.W and 0 <= y < self.H
            busy = self.awaiting_answer or self.conversation or self.voice_turn or self.speech_stop or self.input_visible
            if reading or busy or self.capturing or self.dialog:
                self.panel_active_at = now
            elif now - self.panel_active_at >= self.PANEL_IDLE_SECONDS:
                self._close_panel()
        self.root.after(1000, self._check_panel)

    # ---------- static UI ----------

    @property
    def role_title(self):
        return "pet-" + self.character.data["name_en"]

    def _backdrop(self):
        if self.character.id != "lapland":
            frame = Image.new("RGBA", (self.W - 184, 407), BUBBLE)
            background = self.character.folder / "background.png"
            with Image.open(background if background.is_file() else self.character.sprite("wait")) as img:
                if background.is_file():
                    layer = ImageOps.fit(img.convert("RGBA"), frame.size)
                    frame.alpha_composite(layer)
                else:
                    layer = img.convert("RGBA")
                    layer.thumbnail((320, 390), Image.Resampling.LANCZOS)
                    frame.alpha_composite(layer, (frame.width - layer.width, frame.height - layer.height))
            return Image.blend(Image.new("RGBA", frame.size, BUBBLE), frame, .22)
        with Image.open(SPRITES / "dialog-skin.webp") as skin:
            extra = self.W - 184 - skin.width
            backdrop = Image.new("RGBA", (skin.width + extra, skin.height))
            backdrop.paste(skin.crop((0, 0, 24, skin.height)), (0, 0))
            shade = skin.crop((24, 0, 78, skin.height))
            backdrop.paste(shade.resize((shade.width + extra, skin.height), Image.Resampling.BICUBIC), (24, 0))
            backdrop.paste(skin.crop((78, 0, skin.width, skin.height)), (78 + extra, 0))
            return backdrop

    def _build_bubble(self) -> None:
        c = self.canvas
        right = self.W - 16
        rounded_rect(c, 174, 16, right + 6, 422, 28, fill=BUBBLE_SHADOW, outline="")
        self.tail_id = c.create_polygon(176, 258, 126, 288, 176, 318, fill=BUBBLE, outline="", smooth=True)
        rounded_rect(c, 167, 7, right + 1, 415, 24, fill=BUBBLE_BORDER, outline="")
        skin = self._backdrop()
        self.bubble_skin = ImageTk.PhotoImage(skin, master=self.root)
        self.skin_item = c.create_image(168, 8, anchor="nw", image=self.bubble_skin)

        self.dot_id = c.create_oval(198, 33, 205, 40, fill=TEAL, outline="")
        self.role_label = c.create_text(215, 36, text=self.role_title, anchor="w", fill=CREAM, font=("Georgia", -16))
        self.status_id = c.create_text(
            320, 36, text="READY", anchor="w", fill=MUTED, font=("Segoe UI Semibold", -11)
        )
        self.talk_button = tk.Button(self.root, text="按住说话", font=("Microsoft YaHei UI", -12),
                                     bg=CONTROL, fg=CREAM, activebackground=TEAL, relief="flat",
                                     bd=0, cursor="hand2", takefocus=False)
        self.talk_window = c.create_window(right - 301, 35, window=self.talk_button, width=94, height=28,
                                           state="hidden")
        self.talk_button.bind("<ButtonPress-1>", self._press_to_talk)
        self.talk_button.bind("<ButtonRelease-1>", self._release_to_talk)
        self.talk_button.bind("<FocusOut>", lambda _: self._cancel_voice_input() if self.state == "listening" else None)
        self.conversation_button = tk.Button(
            self.root, text="开始对话", command=self._toggle_conversation, font=("Microsoft YaHei UI", -12),
            bg=TEAL, fg=BUBBLE, relief="flat", bd=0, cursor="hand2")
        self.conversation_window = c.create_window(right - 197, 35, window=self.conversation_button,
                                                    width=94, height=28, state="hidden")
        self.close_id = c.create_text(
            right - 22, 33, text="×", anchor="center", fill=MUTED, font=("Segoe UI", -22)
        )
        close_hit = c.create_oval(right - 40, 18, right - 4, 52, fill=BUBBLE, outline="")
        c.tag_lower(close_hit, self.close_id)
        c.tag_bind(close_hit, "<Button-1>", lambda _e: self._close_panel())
        c.tag_bind(self.close_id, "<Button-1>", lambda _e: self._close_panel())
        c.tag_bind(self.close_id, "<Enter>", lambda _e: c.itemconfigure(self.close_id, fill=CORAL))
        c.tag_bind(self.close_id, "<Leave>", lambda _e: c.itemconfigure(self.close_id, fill=MUTED))

        self.title_font = tkfont.Font(root=self.root, family="Georgia", size=-29)
        self.title_zh_font = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-15)
        self.spoken_font = tkfont.Font(root=self.root, family="Segoe UI Semibold", size=-22)
        self.translation_font = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=-19)
        self.headline_en_id = c.create_text(
            self.CONTENT_X, 78, text="", anchor="nw", fill=CREAM, font=self.title_font,
        )
        self.headline_zh_id = c.create_text(
            self.CONTENT_X, 122, text="", anchor="nw", fill=GOLD, font=self.title_zh_font,
        )
        self.captions = ScrollingCaptions(c, self.subtitle_width, (self.spoken_font, self.translation_font),
                                          (WHITE, CREAM))
        self.captions.set_backdrop(skin, self.CONTENT_X - 168, 196 - 8)
        c.create_window(self.CONTENT_X, 196, anchor="nw", window=self.captions)
        self.captions.bind("<Button-3>", self._popup_menu)
        c.create_line(self.CONTENT_X, 178, self.CONTENT_X + self.subtitle_width, 178, fill="#645863")
        self.hint_id = c.create_text(
            198, 393, text="", anchor="w", fill=MUTED, font=("Microsoft YaHei UI", -12),
        )

    def _build_input(self) -> None:
        c = self.canvas
        right = self.W - 38
        self.input_bg_id = rounded_rect(
            c, 190, 330, right, 380, 13, fill="#27232C", outline="#796B78", width=1
        )
        self.ask_bg_id = rounded_rect(c, right - 65, 338, right - 8, 372, 9, fill=TEAL, outline="")
        self.ask_text_id = c.create_text(
            right - 36, 355, text="发送", fill="#251F26", font=("Microsoft YaHei UI", -14, "bold")
        )
        self.input_var = tk.StringVar()
        self.entry = tk.Entry(
            self.root,
            textvariable=self.input_var,
            bd=0,
            relief="flat",
            bg="#27232C",
            fg=WHITE,
            insertbackground=WHITE,
            font=("Microsoft YaHei UI", -16),
            highlightthickness=0,
        )
        self.entry_window_id = c.create_window(
            206, 355, anchor="w", window=self.entry, width=right - 282, height=28
        )
        self.input_prompt_id = c.create_text(206, 355, anchor="w", text=self.input_placeholder,
                                             fill=MUTED, font=("Microsoft YaHei UI", -15))
        self.input_shortcut_id = c.create_text(right - 17, 355, anchor="e", text=self._shortcut_text("Ctrl+Alt+Space"),
                                               fill=MUTED, font=("Segoe UI", -12))
        for item in (self.input_prompt_id, self.input_shortcut_id, self.input_bg_id):
            c.tag_bind(item, "<Button-1>", lambda _e: self.show_input() if not self.input_visible else None)
        self.entry.bind("<Return>", lambda _e: self.submit_input())
        self.entry.bind("<FocusIn>", self._entry_focus_in)
        self.entry.bind("<FocusOut>", self._entry_focus_out)
        self.entry.bind("<KeyPress>", self._entry_keypress)
        self.entry.bind("<Button-1>", self._entry_click)
        c.tag_bind(self.ask_bg_id, "<Button-1>", lambda _e: self.submit_input())
        c.tag_bind(self.ask_text_id, "<Button-1>", lambda _e: self.submit_input())
        c.tag_bind(self.ask_bg_id, "<Enter>", lambda _e: c.itemconfigure(self.ask_bg_id, fill=CREAM))
        c.tag_bind(self.ask_bg_id, "<Leave>", lambda _e: c.itemconfigure(self.ask_bg_id, fill=TEAL))
        self._placeholder_active = False
        self._set_input_visible(False)

    @property
    def input_placeholder(self):
        return "输入想问" + self.character.data["name"] + "的话…"

    def _load_poses(self):
        self.poses = {}
        for pose in POSES:
            with Image.open(self.character.sprite(pose)) as original:
                sprite = original.convert("RGBA")
                sprite.thumbnail((164, 130), Image.Resampling.LANCZOS)
                # The Windows overlay uses a binary colour key. Low-opacity
                # pixels would otherwise show up as dark specks on light desktops.
                sprite.putalpha(sprite.getchannel("A").point(lambda a: 255 if a >= 96 else 0))
                self.poses[pose] = ImageTk.PhotoImage(sprite, master=self.root)
        self.root.iconphoto(True, self.poses["wait"])

    def _build_pet(self) -> None:
        c = self.canvas
        self._load_poses()
        self.pose = "wait"
        self.pet_sprite = c.create_image(85, 348, anchor="s", image=self.poses[self.pose], tags=("pet",))
        c.tag_bind("pet", "<ButtonPress-1>", self._pet_press)
        c.tag_bind("pet", "<B1-Motion>", self._pet_drag)
        c.tag_bind("pet", "<ButtonRelease-1>", self._pet_release)
        c.tag_bind("pet", "<Button-3>", self._popup_menu)
        c.tag_bind("pet", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("pet", "<Leave>", lambda _e: c.configure(cursor=""))

    def _set_pose(self, pose: str) -> None:
        if pose != self.pose:
            self.canvas.itemconfigure(self.pet_sprite, image=self.poses[pose])
            self.pose = pose

    def _build_menu(self) -> None:
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#162033",
            fg=WHITE,
            activebackground="#2C3E5A",
            activeforeground=CREAM,
            bd=0,
        )
        menu.add_command(label=self._shortcut_text("Capture region   Ctrl+Alt+S"), command=self.begin_capture)
        menu.add_command(label=self._shortcut_text("Ask about this screen   Ctrl+Alt+Space"), command=self.toggle_input)
        menu.add_separator()
        menu.add_command(label="Stop speaking", command=self.stop_speech)
        self._mute_index = menu.index("end") + 1
        menu.add_command(label=self._mute_label(), command=self.toggle_mute)
        menu.add_command(label=self._shortcut_text(f"Hide {self.role_title}   Ctrl+Alt+H"), command=self._hide_window)
        menu.add_command(label="完整讲解记录", command=self.show_transcript)
        menu.add_command(label="对话历史…", command=self.show_history)
        menu.add_command(label="长期记忆…", command=self.show_memories)
        menu.add_command(label="模型与项目…", command=self.show_connections)
        menu.add_command(label="交给 DSH 执行…", command=self.show_project_task)
        menu.add_command(label="角色与迁移…", command=self.show_characters)
        menu.add_command(label="设置…", command=self.show_settings)
        menu.add_separator()
        menu.add_command(label=self._shortcut_text("Quit   Ctrl+Alt+Q"), command=self.quit)
        self.menu = menu

    def _shortcut_text(self, text):
        # Replace in one pass so swapping two bindings also swaps their labels.
        labels = {spec: self.cfg["hotkeys"][action].title()
                  for action, spec in DEFAULTS["hotkeys"].items()}
        return re.sub(r"Ctrl\+Alt\+(?:Space|S|H|Q)\b", lambda match: labels[match[0].lower()], text)

    def _close_dialog(self):
        if self.dialog:
            self.dialog.destroy()
            self.dialog = None

    def show_settings(self):
        if not self.dialog and not self.capturing:
            self.dialog = SettingsDialog(self.root, self.cfg, self._save_settings, self._close_dialog)

    def show_connections(self):
        if not self.dialog and not self.capturing:
            self._cancel_voice_input()
            self.dialog = ConnectionDialog(self.root, self.cfg, self._save_settings, self._close_dialog)

    def show_characters(self):
        if not self.dialog and not self.capturing:
            self._cancel_voice_input()
            self.dialog = CharacterDialog(self)

    def role_settings(self, role):
        if role.id == self.character.id:
            return {k: self.cfg.get(k, DEFAULTS[k]) for k in SHARE_KEYS}
        return {**role.data["settings"], **self.cfg.get("character_settings", {}).get(role.id, {})}

    def activate_character(self, role):
        if role.id == self.character.id:
            self._close_dialog()
            return
        settings = self.role_settings(role)
        stored = {**self.cfg.get("character_settings", {}), self.character.id: self.role_settings(self.character)}
        self._save_settings({**settings, "character_id": role.id, "character_settings": stored,
                             "model_profile": role.data["name"] + " · " + settings["model"]})

    def show_project_task(self):
        if self.dialog or self.capturing:
            return
        project = projects().get(self.cfg.get("dsh_project"))
        if not project:
            self.show_connections()
            if self.dialog:
                self.dialog.status.configure(text="请先选择并保存 DSH 项目，再打开「交给 DSH 执行」。")
            return
        self._cancel_voice_input()
        scope = (self.character.id, project.id, self.cfg.get("dsh_session", ""))
        draft = self.input_var.get().strip() if self.input_visible and not self._placeholder_active else ""
        self.dialog = TaskDialog(self, project, scope, draft, self.character.data["tasks"])

    def start_project_task(self, scope, text):
        previous = self.project_tasks.get(scope)
        if previous and previous.status == "执行中":
            return
        job = self.project_tasks[scope] = dsh_tasks.Task(text)
        archive = self.archive if self.cfg["save_history"] else None
        def run():
            try:
                result = dsh_tasks.execute(scope[1], scope[2], job)
                status = "已停止" if job.stop.is_set() else "已完成"
            except (ValueError, OSError) as exc:
                result, status = str(exc), "执行失败"
            except Exception:
                result, status = "无法运行 DSH，请检查项目和 CLI。", "执行失败"
            if archive and status == "已完成":
                try:
                    archive.append(uuid.uuid4().hex, "DSH 任务", text + "\n\nDSH 任务\n\n" + result, text)
                except (OSError, sqlite3.Error):
                    log.warning("DSH task history unavailable")
            self.events.put(("dsh_done", scope, job, status, result))
        threading.Thread(target=run, daemon=False, name="pet-dsh").start()

    def _load_project_history(self):
        project = self.cfg.get("dsh_project", "")
        if self.character.id != "lapland":
            project = self.character.id + ":" + project
        self.archive = ConversationHistory.for_scope(project, self.cfg.get("dsh_session", ""))
        self.topic = uuid.uuid4().hex
        self.history, self.transcript = [], []
        if self.cfg["save_history"]:
            try:
                topic, self.history, self.transcript = self.archive.recent_context()
                self.topic = topic or self.topic
            except (OSError, sqlite3.Error):
                log.warning("Could not resume saved conversation")

    def _save_settings(self, values):
        cfg = {**self.cfg, **values}
        changed_keys = cfg["hotkeys"] != self.cfg["hotkeys"]
        role_changed = cfg.get("character_id") != self.cfg.get("character_id")
        scope_changed = role_changed or any(cfg.get(k) != self.cfg.get(k) for k in ("dsh_project", "dsh_session"))
        route_changed = any(cfg.get(k) != self.cfg.get(k) for k in ("model", "api_base", "model_key_env"))
        if "api_base" in values or "model_key_env" in values:
            apply_credentials(cfg)
        save_config(cfg)
        if scope_changed or route_changed:
            self._cancel_voice_input()
            self.request_seq += 1
            self.awaiting_answer = self.stream_started = False
            if scope_changed or cfg["api_base"] != self.cfg["api_base"]:
                self.last_image = None
            self.last_answer = None
        self.stop_speech()
        self.cfg = cfg
        if role_changed:
            self.character = character(cfg["character_id"])
            self.root.title(self.role_title)
            self.canvas.itemconfigure(self.role_label, text=self.role_title)
            self.canvas.itemconfigure(self.input_prompt_id, text=self.input_placeholder)
            skin = self._backdrop()
            self.captions.set_backdrop(skin, self.CONTENT_X - 168, 196 - 8)
            self.bubble_skin = ImageTk.PhotoImage(skin, master=self.root)
            self.canvas.itemconfigure(self.skin_item, image=self.bubble_skin)
            self._load_poses()
            self.pose = ""
            self._set_pose("wait")
            self.hide_input()
        self.client = DeepSeekClient(cfg)
        if scope_changed:
            self._load_project_history()
        if scope_changed or route_changed:
            self._show_idle()
        self.speech = SpeechEngine(cfg, Path(__file__).with_name("tts_worker.ps1"))
        if changed_keys:
            self.hotkeys.stop()
            self.hotkeys.join(timeout=1)
            self.hotkeys = HotkeyManager(values["hotkeys"], self.events)
            self.hotkeys.start()
        self.menu.destroy()
        self._build_menu()
        self._close_dialog()
        self.canvas.itemconfigure(self.input_shortcut_id, text=self._shortcut_text("Ctrl+Alt+Space"))
        self._set_hint(f"已切换至 {self.client.model}" if "model_profile" in values else "设置已保存")

    def show_history(self):
        if not self.dialog and not self.capturing:
            self.dialog = HistoryDialog(self.root, self.archive, self._close_dialog, self._forget_topic)

    def _forget_topic(self, topic):
        if topic == self.topic:
            self.request_seq += 1
            self.awaiting_answer = False
            self.stop_speech()
            self.topic = uuid.uuid4().hex
            self.history.clear()
            self.transcript.clear()
            self.last_image = self.last_answer = None
            self._show_idle()

    def show_memories(self):
        if not self.dialog and not self.capturing:
            self.dialog = MemoryDialog(self.root, self.archive, self._close_dialog)

    def show_transcript(self):
        if self.dialog or self.capturing:
            return
        dlg = self.dialog = tk.Toplevel(self.root)
        dlg.title("当前选区 · 完整讲解记录")
        dlg.transient(self.root)
        dlg.attributes("-topmost", True)
        transcript = tk.Text(dlg, width=68, height=24, wrap="word", padx=12, pady=10)
        scroll = tk.Scrollbar(dlg, command=transcript.yview)
        transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        transcript.pack(expand=True, fill="both")
        transcript.insert("end", "\n\n".join(self.transcript) or "完成一次讲解后，可在这里查看双语记录。")
        transcript.configure(state="disabled")
        dlg.protocol("WM_DELETE_WINDOW", self._close_dialog)
        dlg.bind("<Escape>", lambda _: self._close_dialog())
        dlg.grab_set()

    def _mute_label(self) -> str:
        return "Unmute voice" if self.speech.muted else "Mute voice"

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-3>", self._popup_menu)
        self.root.bind("<Control-Return>", lambda _e: self.submit_input())

    # ---------- state / text ----------

    def _set_text(self, item: int, text: str, y: int | None = None, x: int | None = None):
        text = text or ""
        if not text:
            self.canvas.itemconfigure(item, text="", state="hidden")
            return None
        self.canvas.itemconfigure(item, text=text, state="normal")
        if y is not None:
            self.canvas.coords(item, x if x is not None else self.canvas.coords(item)[0], y)
        return self.canvas.bbox(item)

    def _show_content(self, en: str, zh: str, *, duration: float | None = None):
        self.captions.show(en, zh, duration)

    def _set_status(self, text: str, color: str = TEAL) -> None:
        self.canvas.itemconfigure(self.status_id, text=text)
        self.canvas.itemconfigure(self.dot_id, fill=color)

    def _set_hint(self, text: str) -> None:
        self._set_text(self.hint_id, self._shortcut_text(text))

    def _show_idle(self) -> None:
        self.state = "idle"
        self._set_status("READY", TEAL)
        self._set_headline(f"Hi, I'm {self.character.data['name_en']}.", f"你好，我是{self.character.data['name']}。")
        self._show_content(
            "Click to start a conversation, or select part of your screen.",
            "点「开始对话」就能直接说话，也可以框选屏幕让我讲解。",
        )
        self._set_hint("Ctrl+Alt+S 框选   ·   右键查看对话历史")

    def _set_headline(self, en: str, zh: str) -> None:
        for item, text, font in [(self.headline_en_id, en, self.title_font),
                                 (self.headline_zh_id, zh, self.title_zh_font)]:
            text = " ".join(text.split())
            if font.measure(text) > 342:
                while text and font.measure(text + "…") > 342:
                    text = text[:-1]
                text = text.rstrip() + "…"
            self._set_text(item, text)

    def _show_missing_key(self) -> None:
        if self.client.available:
            return
        self.state = "error"
        self._set_pose("flustered")
        self._set_status("NO KEY", CORAL)
        self._set_headline("I need a key.", "我还差一把钥匙。")
        self._show_content(
            "Choose a model connection or configure its API key.",
            f"请设置 {self.client.key_env}，或在「模型与项目」切换服务。",
        )
        self._set_hint("右键 → 模型与项目")

    def _show_thinking(self, follow_up: bool, has_image: bool) -> None:
        self.state = "thinking"
        self.thinking_since = time.monotonic()
        self._set_pose("curious")
        self.thinking_label = "LOOKING" if has_image else "THINKING"
        self._set_status(self.thinking_label, GOLD)
        if not has_image:
            self._set_headline("I'm listening.", "我在听，博士。")
            self._show_content("", self.pending_question)
        elif follow_up:
            self._set_headline("Good question.", "这个问题不错。")
            self._show_content(
                "I'm looking at the same region again, with your question in mind.",
                "我正在结合你的问题，再看一遍刚才的区域。",
            )
        else:
            self._set_headline("Let me take a look.", "我看一眼。")
            self._show_content(
                "Reading the region you selected. This usually takes a few seconds.",
                "正在读取你选中的区域，通常只需要几秒钟。",
            )
        self._set_hint("正在准备回答")

    def _show_error(self, en: str, zh: str) -> None:
        self.state = "error"
        self._set_pose("flustered")
        self._set_status("HMM", CORAL)
        self._set_headline("That didn't work.", "这次没成功。")
        self._show_content(en, zh)
        self._set_hint("Try again with Ctrl+Alt+S   ·   right-click for menu")

    # ---------- input ----------

    def _toggle_conversation(self):
        if self.conversation is not None:
            self._cancel_voice_input()
            return
        if self.hidden or self.capturing or self.dialog or self.closing:
            return
        if not self.client.available:
            self._show_missing_key()
            return
        self._cancel_voice_input()
        self.hide_input()
        self.stop_speech(keep_idle=True)
        self.request_seq += 1
        self.awaiting_answer = self.stream_started = False
        level = MIC_THRESHOLDS.get(self.cfg.get("mic_threshold"), MIC_THRESHOLDS["较高"])
        try:
            gate = SpeechGate(level)
            def collect(samples):
                utterance = gate.feed(samples)
                if utterance is not None:
                    self.events.put(("utterance", gate, utterance))
            self.voice_input.start(collect)
        except (VoiceInputError, ImportError) as exc:
            self._show_error("Microphone unavailable", str(exc))
            return
        self.conversation = gate
        self._warm_voice_for_input()
        self.conversation_button.configure(text="结束对话", bg=CORAL)
        self.state = "idle"
        self._set_status("LISTENING", TEAL)
        self._set_hint("麦克风已开启 · 直接说话，停顿后自动发送 · Esc 结束")

    def _resume_conversation(self):
        if (self.conversation is None or self.voice_turn or self.awaiting_answer
                or self.speech_stop or self.closing):
            return
        self.conversation.resume(skip_ms=320)  # Ignore the loudspeaker tail after playback.
        self.state = "idle"
        self._set_status("LISTENING", TEAL)
        self._set_hint("麦克风已开启 · 可以继续说话 · Esc 结束")

    def _press_to_talk(self, _event=None):
        if self.hidden or self.capturing or self.dialog or self.closing or self.state == "listening":
            return "break"
        if not self.client.available:
            self._show_missing_key()
            return "break"
        self._cancel_voice_input()
        self.stop_speech(keep_idle=True)
        self.request_seq += 1  # Ignore any outstanding streamed answer after an interruption.
        self.awaiting_answer = False
        self.stream_started = False
        try:
            self.voice_input.start()
        except VoiceInputError as exc:
            self._show_error("Microphone unavailable", str(exc))
            return "break"
        self.voice_turn = threading.Event()
        self.talk_button.focus_set()
        self._warm_voice_for_input()
        self.talk_button.grab_set()
        self.talk_button.configure(text="松开发送", bg=CORAL)
        self.state = "listening"
        self._set_status("LISTENING", CORAL)
        self._set_hint("正在听你说话 · 松开发送 · Esc 取消")
        return "break"

    def _release_to_talk(self, _event=None):
        if self.state != "listening" or self.voice_turn is None:
            return "break"
        token = self.voice_turn
        samples = self.voice_input.finish()
        self.state = "transcribing"
        self.talk_button.grab_release()
        self.talk_button.configure(text="按住说话", bg=CONTROL)
        self._transcribe_voice(samples, token)
        return "break"

    def _transcribe_voice(self, samples, token):
        self._set_status("TRANSCRIBING", GOLD)
        self._set_hint("正在本机识别 · Esc 取消")
        def decode():
            try:
                text = self.voice_input.transcribe(samples, token)
            except Exception:
                log.exception("voice transcription failed")
                self.events.put(("voice_input", token, "", "本机语音识别失败，请检查识别模型后重试。"))
            else:
                self.events.put(("voice_input", token, text, ""))
        threading.Thread(target=decode, daemon=True, name="lapland-transcribe").start()

    def _cancel_voice_input(self):
        continuous = self.conversation is not None
        if not continuous and self.voice_turn is None:
            return
        if self.voice_turn is not None:
            self.voice_turn.set()
        self.voice_turn = None
        if continuous:
            self.conversation = None
            self.voice_input.finish()
            self.conversation_button.configure(text="开始对话", bg=TEAL)
        elif self.state == "listening":
            self.voice_input.finish()
            self.talk_button.grab_release()
        self.talk_button.configure(text="按住说话", bg=CONTROL)
        if self.state in ("idle", "listening", "transcribing"):
            self.state = "idle"
            self._set_status("READY", TEAL)
        self._set_hint("麦克风已关闭" if continuous else "已取消语音输入")

    def _voice_result(self, token, text, error):
        if token is not self.voice_turn or token.is_set() or self.closing:
            return
        self.voice_turn = None
        if error:
            self._cancel_voice_input()
            self._show_error("Voice input unavailable", error)
        elif not text:
            self.state = "idle"
            self._set_status("READY", TEAL)
            self._set_hint("没听清。请按住说话，说完再松开。")
            self._resume_conversation()
        else:
            self._start_ask(self.last_image, text, follow_up=self.last_image is not None,
                            from_conversation=self.conversation is not None)
            if self.awaiting_answer:
                self._show_content("", "你说：" + text)
                self._set_hint(f"正在等{self.character.data['name']}回答")

    def _set_input_visible(self, visible: bool) -> None:
        state = "normal" if visible else "hidden"
        for item in (
            self.ask_bg_id, self.ask_text_id,
            self.entry_window_id,
        ):
            self.canvas.itemconfigure(item, state=state)
        for item in (self.input_prompt_id, self.input_shortcut_id):
            self.canvas.itemconfigure(item, state="hidden" if visible else "normal")
        if visible:
            self.canvas.itemconfigure(self.hint_id, state="hidden")
        self.input_visible = visible
        if visible:
            self._entry_show_placeholder()
            self.root.after(20, self._focus_entry)
        else:
            self.canvas.itemconfigure(self.hint_id, state="normal")

    def _entry_show_placeholder(self) -> None:
        if self._placeholder_active:
            return
        if self.input_var.get().strip():
            self._placeholder_active = False
            return
        self._placeholder_active = True
        self.entry.configure(fg=MUTED)
        self.entry.insert(0, self.input_placeholder)

    def _entry_clear_placeholder(self) -> None:
        if self._placeholder_active:
            self._placeholder_active = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=WHITE)

    def _entry_focus_in(self, _event=None) -> None:
        if self._placeholder_active:
            self.entry.icursor("end")

    def _entry_keypress(self, event):
        if not self._placeholder_active:
            return None
        if event.keysym in (
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Super_L", "Super_R", "Caps_Lock", "Num_Lock",
        ):
            return None
        self._entry_clear_placeholder()
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Home", "End"):
            return "break"
        return None

    def _entry_click(self, _event=None) -> None:
        if self._placeholder_active:
            self._entry_clear_placeholder()
            self.root.after_idle(lambda: self.entry.icursor(0))

    def _entry_focus_out(self, _event=None) -> None:
        if self.input_visible and not self.input_var.get().strip():
            self._entry_show_placeholder()

    def _focus_entry(self) -> None:
        if self.state == "listening" or not self.input_visible:
            return
        try:
            self.root.focus_force()
            self.entry.focus_force()
            self.entry.icursor("end")
        except Exception:
            pass

    def show_input(self) -> None:
        if self.capturing or self.dialog:
            return
        self._restore_window()
        self._open_panel()
        if self.awaiting_answer:
            return
        if not self.client.available:
            self._show_missing_key()
            return
        self._cancel_voice_input()
        log.info("show input")
        self.stop_speech(keep_idle=True)
        self._set_input_visible(True)
        self._warm_voice_for_input()
        if self.state != "thinking":
            self._set_pose("curious")
            self._set_status("ASK", TEAL)
        self._set_hint("Enter to ask   ·   Esc to close")

    def hide_input(self) -> None:
        if self.input_visible:
            self._cancel_voice_input()
            log.info("hide input")
            if self.state == "idle":
                self._set_pose("wait")
                self._set_status("READY", TEAL)
        self._set_input_visible(False)
        self._entry_clear_placeholder()
        self.input_var.set("")
        if self.state == "speaking":
            self._set_hint("Click me to stop   ·   Ctrl+Alt+S for a new capture")
        elif self.state == "thinking":
            self._set_hint("Only this region is sent to the model   ·   Esc stops the voice")
        elif self.state == "error":
            self._set_hint("Try again with Ctrl+Alt+S   ·   right-click for menu")
        else:
            self._set_hint("Ctrl+Alt+S capture   ·   Ctrl+Alt+Space ask   ·   right-click for menu")

    def toggle_input(self) -> None:
        if self.input_visible:
            self.hide_input()
        else:
            self.show_input()

    def submit_input(self) -> None:
        if not self.input_visible or self._placeholder_active or self.state == "thinking":
            return
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self.hide_input()
        self.stop_speech(keep_idle=True)
        if self.last_image is not None:
            self._start_ask(self.last_image, text, follow_up=True)
        else:
            self._start_ask(None, text, follow_up=False)

    # ---------- capture ----------

    def begin_capture(self) -> None:
        if self.capturing or self.awaiting_answer or self.dialog:
            return
        if not self.client.available:
            self._restore_window()
            self._open_panel()
            self._show_missing_key()
            return
        self._cancel_voice_input()
        self.capturing = True
        self.stop_speech()
        self.hide_input()
        # Do not withdraw the root: a withdrawn Tk root also hides its
        # overrideredirect Toplevel on Windows. An alpha-zero window is still
        # mapped, so it will not appear in the screenshot, while the selector
        # can be mapped above the desktop.
        try:
            self.root.attributes("-alpha", 0.0)
            self.root.deiconify()
        except Exception:
            self.root.withdraw()
        self.root.after(160, self._open_selector)

    def _open_selector(self) -> None:
        try:
            self.selector = RegionSelector(
                self.root,
                on_done=self._capture_done,
                on_cancel=self._capture_cancel,
            )
            self.selector.start()
        except Exception:
            log.exception("could not open selector")
            if self.selector:
                self.selector._destroy()
                self.selector = None
            self.capturing = False
            self._restore_window()
            self._open_panel()
            self._show_error("I couldn't open the screenshot overlay.", "我打不开截屏选择层。")

    def _capture_done(self, bbox: tuple[int, int, int, int]) -> None:
        self.selector = None
        try:
            image = capture_region(bbox)
        except Exception:
            log.exception("capture failed")
            self.capturing = False
            self._restore_window()
            self._open_panel()
            self._show_error("I couldn't capture that region.", "我没能截取这个区域。")
            return
        if image.width < 16 or image.height < 16:
            self.capturing = False
            self._restore_window()
            self._open_panel()
            self._show_error("That region was too small to read.", "这个区域太小了，读不清。")
            return
        self._restore_window()
        if self.cfg.get("confirm_capture", True):
            self.dialog = CapturePreview(self.root, image, self.client.base, self._send_capture, self._capture_cancel)
        else:
            self._send_capture(image)

    def _send_capture(self, image) -> None:
        self.dialog = None
        self.capturing = False
        if self.cfg.get("save_captures"):
            try:
                save_debug_capture(image, CAPTURE_DIR)
            except Exception:
                log.exception("could not save debug capture")
        self._restore_window()
        self.hide_input()
        self.last_image = image
        self.last_answer = None
        self.history = []
        self.transcript.clear()
        self.topic = uuid.uuid4().hex
        self._start_ask(image, DEFAULT_CAPTURE_QUESTION, follow_up=False)

    def _capture_cancel(self) -> None:
        self.dialog = None
        self.selector = None
        self.capturing = False
        self._restore_window()

    # ---------- model ----------

    def _start_ask(self, image, question: str, follow_up: bool, *, from_conversation=False) -> None:
        self._open_panel()
        if not from_conversation:
            self._cancel_voice_input()
        self.request_seq += 1
        seq = self.request_seq
        self.pending_question = question
        self.request_speech = self.speech
        self.answer_voice_token = self.speech_token
        self.spoken_key = self.client.language
        note = self.archive.requested_note(question)
        if note:
            self.hide_input()
            try:
                self.archive.remember(note)
            except (OSError, ValueError, sqlite3.Error):
                self._show_error("Memory unavailable", "这条记忆没能保存，请检查本地数据目录。")
                self._resume_conversation()
                return
            self.awaiting_answer = False
            acknowledgement = "Me lo ricorderò." if self.spoken_key == "it" else "I'll remember that."
            answer = {"headline_" + self.spoken_key: acknowledgement, "headline_zh":"已记住",
                      "segments":[{self.spoken_key:acknowledgement, "zh":"已记住：" + note}]}
            self._present_answer(answer)
            return
        self.awaiting_answer = True
        self.stream_started = False
        history = list(self.history)
        try:
            context = self.archive.handoff(self.topic, len(history)//2) if self.cfg["save_history"] else {}
            notes = self.archive.relevant_notes(question)
            if notes:
                context["用户指定的长期记忆"] = notes
        except (OSError, sqlite3.Error, ValueError):
            context = {}
            log.warning("Could not load conversation memory")
        self.hide_input()
        self._show_thinking(follow_up, image is not None)
        if self.panel_open and not self.hidden:
            self._cancel_renderer_timer()
            self.request_speech.warmup()
        threading.Thread(
            target=self._ask_worker,
            args=(self.client, image, question, history, seq, follow_up, context),
            daemon=True,
            name="mochi-ask",
        ).start()

    def _ask_worker(self, client, image, question: str, history: list[dict[str, str]], seq: int, follow_up: bool, context=None) -> None:
        try:
            answer = client.ask(image, question, history=history, follow_up=follow_up,
                                     on_first=lambda first: self.events.put(("first", seq, first)),
                                     **({"context": context} if context else {}))
        except LLMError as exc:
            self.events.put(("error", seq, exc.en, exc.zh))
        except Exception:
            log.exception("unexpected ask failure")
            self.events.put(("error", seq, "Something went wrong on my side.", "我这边出了点问题。"))
        else:
            self.events.put(("answer", seq, answer))

    def _present_answer(self, answer, *, preview=False):
        self.last_answer = answer
        segments = answer["segments"]
        self.spoken_text = " ".join(s[self.spoken_key].strip() for s in segments if s[self.spoken_key].strip())
        self.translated_text = "".join(s["zh"].strip() for s in segments)
        self._set_headline(answer["headline_" + self.spoken_key], answer["headline_zh"])
        self._show_content(self.spoken_text, self.translated_text)
        if self.hidden or not self.panel_open or self.answer_voice_token != self.speech_token:
            return
        self.state = "speaking"
        self._set_pose("smile")
        self._set_status("RECEIVING" if preview else "PREPARING VOICE", GOLD)
        self._set_hint("点角色停止朗读   ·   Ctrl+Alt+S 框选")
        if preview:
            return
        self._cancel_renderer_timer()
        self.speech_stop = threading.Event()
        threading.Thread(
            target=self._speech_worker,
            args=(self.spoken_text, segments[0].get("emotion"), self.request_seq, self.speech_token,
                  self.speech_stop, self.request_speech),
            daemon=False,  # Let cancellation reap the worker and remove its temporary audio.
            name="mochi-speech",
        ).start()

    def _on_answer(self, seq: int, answer: dict[str, Any]) -> None:
        if seq != self.request_seq:
            return
        self.awaiting_answer = False
        self._present_answer(answer)
        self.history.extend([
            {"role": "user", "content": self.pending_question},
            {"role": "assistant", "content": " ".join(s["zh"] or s[self.spoken_key] for s in answer["segments"])},
        ])
        self.history = self.history[-6:]
        lines = [self.pending_question, answer["headline_" + self.spoken_key] + " · " + answer["headline_zh"]]
        lines.extend(segment[self.spoken_key] + "\n" + segment["zh"] for segment in answer["segments"])
        body = "\n\n".join(lines)
        self.transcript.append(body)
        if self.cfg["save_history"]:
            try:
                self.archive.append(self.topic, answer["headline_zh"] or answer["headline_" + self.spoken_key],
                                    body, self.pending_question)
            except (OSError, sqlite3.Error) as exc:
                log.warning("could not save conversation (%s)", type(exc).__name__)
                self._set_hint("本轮未能保存，可从「完整讲解记录」复制")
            else:
                if self.compaction_lock.acquire(blocking=False):
                    threading.Thread(target=self._compact_history, args=(self.client, self.archive), daemon=True,
                                     name="lapland-memory").start()
                if isinstance(self.dialog, HistoryDialog):
                    self.dialog.refresh()
        if self.speech_stop is None:
            self.state = "idle"
            self._set_status("READY", TEAL)
            self._resume_conversation()

    def _compact_history(self, client, archive):
        try:
            while self.cfg["save_history"] and not self.closing and self.archive is archive and self.client is client:
                pending = archive.summary_batch()
                if not pending:
                    break
                revision, cursor, summary, rows = pending
                text = "\n\n".join(part for _, part in rows)
                # Oversized individual turns are processed completely before advancing the cursor.
                for offset in range(0, len(text), 8000):
                    if self.closing or not self.cfg["save_history"] or self.archive is not archive or self.client is not client:
                        return
                    summary = client.summarize(summary, text[offset:offset + 8000])
                if not archive.commit_summary(revision, cursor, rows[-1][0], summary):
                    break
        except Exception as exc:
            log.warning("Summary deferred (%s)", type(exc).__name__)
        finally:
            self.compaction_lock.release()

    def _speech_worker(self, text, emotion, seq, token, stop_event, speech):
        path = None
        try:
            if stop_event.is_set():
                return
            try:
                direction = {"emotion": emotion} if emotion else {}
                path, used = speech.prepare_segment(text, stop_event, speech.provider, **direction)
                duration = wav_duration(path) if path else subtitle_duration(text)
            except Exception as exc:
                log.warning("voice unavailable (%s)", type(exc).__name__)
                used, duration = "unavailable", subtitle_duration(text)
            if stop_event.is_set():
                return
            self.events.put(("playback", seq, token, used, duration))
            played = False
            if path and used != "unavailable":
                try:
                    played = speech.play_file(path, stop_event)
                except Exception as exc:
                    log.warning("playback unavailable (%s)", type(exc).__name__)
                if not played and not stop_event.is_set():
                    self.events.put(("audio_unavailable", seq, token))
            if not played and not stop_event.is_set():
                speech.wait_timed(text, stop_event)
        finally:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    log.warning("could not remove temporary audio")
            if not stop_event.is_set():
                self.events.put(("speech_done", seq, token))

    def _on_playback(self, seq, token, used, duration):
        if seq != self.request_seq or token != self.speech_token or not self.last_answer:
            return
        self._show_content(self.spoken_text, self.translated_text, duration=duration)
        voiced = used not in ("muted", "unavailable")
        self._set_status("SPEAKING" if voiced else "SUBTITLES", CORAL if voiced else TEAL)
        if not voiced and not self.speech.muted:
            self._set_hint("语音不可用，继续显示字幕")
        elif used == "sapi" and self.speech.provider != "sapi":
            self._set_hint("已切换至所选语言的系统语音")

    def _on_speech_done(self, seq: int, token: int) -> None:
        if seq != self.request_seq or token != self.speech_token:
            return
        if not self._warm_voice_for_input():
            self.request_speech.close_renderer()
        self.speech_stop = None
        self._set_pose("victory")
        self.state = "idle"
        self._set_status("READY", TEAL)
        self._set_hint("Ctrl+Alt+S 框选   ·   右键查看对话历史")
        self._resume_conversation()

    def _warm_voice_for_input(self):
        if self.hidden or not self.panel_open or self.speech.muted or self.speech.provider not in ("sapi", "lapland", "vits"):
            return False
        if self.renderer_timer is None:
            self.speech.warmup()
        self._cancel_renderer_timer()
        self.renderer_timer = self.root.after(30000, self._release_idle_renderer)
        return True

    def _cancel_renderer_timer(self):
        if self.renderer_timer is not None:
            self.root.after_cancel(self.renderer_timer)
            self.renderer_timer = None

    def _release_idle_renderer(self):
        self.renderer_timer = None
        if self.panel_open and not self.hidden and (self.conversation or self.input_visible or self.voice_turn):
            self.renderer_timer = self.root.after(30000, self._release_idle_renderer)
        else:
            self.speech.close_renderer()

    def stop_speech(self, *, keep_idle=False) -> None:
        self.captions.stop()
        if self.speech_stop is not None:
            self.speech_stop.set()
        if not (keep_idle and self.renderer_timer is not None):
            self._cancel_renderer_timer()
            self.speech.stop()
        self.speech_token += 1
        self.speech_stop = None
        if self.state == "speaking":
            self._set_pose("wait")
            self.state = "idle"
            self._set_status("READY", TEAL)
            self._set_hint("Ctrl+Alt+Space to ask a follow-up   ·   Ctrl+Alt+S for a new region")
            self._resume_conversation()

    def toggle_mute(self) -> None:
        self.stop_speech()
        self.speech.muted = not self.speech.muted
        self.cfg["muted"] = self.speech.muted
        save_config(self.cfg)
        try:
            self.menu.entryconfigure(self._mute_index, label=self._mute_label())
        except Exception:
            pass
        if self.speech.muted:
            self._set_hint("Voice muted   ·   Ctrl+Alt+S capture   ·   right-click → Unmute")
        else:
            self._set_hint("Voice on   ·   Ctrl+Alt+S capture   ·   right-click → Mute")

    # ---------- events ----------

    def _poll_events(self) -> None:
        if self.closing:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
                if self.closing:
                    return
            except Exception:
                log.exception("event handler failed: %r", event[:2] if isinstance(event, tuple) else event)
        self.root.after(50, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "task_dictation":
            if self.dialog is event[1]:
                self.dialog.decoded(event[2], event[3])
        elif kind == "dsh_done":
            _, scope, job, status, result = event
            job.status, job.output = status, result
            if isinstance(self.dialog, TaskDialog) and self.dialog.scope == scope:
                self.dialog.refresh()
            elif scope == (self.character.id, self.cfg.get("dsh_project"), self.cfg.get("dsh_session", "")):
                self._set_hint(f"DSH 任务{job.status} · 右键查看结果")
        elif kind == "utterance":
            _, gate, samples = event
            if gate is self.conversation and self.voice_turn is None and not self.closing:
                self.voice_turn = threading.Event()
                self.state = "transcribing"
                self._transcribe_voice(samples, self.voice_turn)
        elif kind == "voice_input":
            self._voice_result(*event[1:])
        elif kind == "hotkey":
            self._on_hotkey(event[1])
        elif kind == "hotkey_error":
            log.warning("hotkey unavailable: %s", event)
            self._set_hint(f"{event[2]} is unavailable ({event[3]})   ·   use right-click → Capture")
        elif kind == "answer":
            self._on_answer(event[1], event[2])
        elif kind == "first":
            if event[1] == self.request_seq and self.awaiting_answer and not self.stream_started:
                self.stream_started = True
                self._present_answer(event[2], preview=True)
        elif kind == "error":
            if event[1] == self.request_seq:
                self.awaiting_answer = False
                self.stop_speech()
                self._show_error(event[2], event[3])
                self._resume_conversation()
        elif kind == "playback":
            self._on_playback(*event[1:])
        elif kind == "audio_unavailable" and event[1:3] == (self.request_seq, self.speech_token):
            self._set_status("SUBTITLES", TEAL)
            self._set_hint("语音不可用，继续显示字幕")
        elif kind == "speech_done":
            self._on_speech_done(event[1], event[2])

    def _on_hotkey(self, action: str) -> None:
        log.info("hotkey action=%s", action)
        if action == "capture":
            self.begin_capture()
        elif action == "ask":
            self.toggle_input()
        elif action == "toggle":
            self._toggle_window()
        elif action == "quit":
            self.quit()

    def _on_escape(self, _event=None) -> None:
        if self.voice_turn is not None or self.conversation is not None:
            self._cancel_voice_input()
        elif self.input_visible:
            self.hide_input()
        elif self.panel_open and self.state not in ("speaking", "thinking"):
            self._close_panel()
        else:
            self.stop_speech()

    def _popup_menu(self, event) -> None:
        self._cancel_voice_input()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ---------- pet interaction ----------

    def _pet_press(self, event) -> None:
        self.drag_start = (int(event.x_root), int(event.y_root))
        self.drag_origin = (self.root.winfo_x(), self.root.winfo_y())
        self.drag_moved = False
        self.press_time = time.time()

    def _pet_drag(self, event) -> None:
        if not self.drag_start or not self.drag_origin:
            return
        dx = int(event.x_root) - self.drag_start[0]
        dy = int(event.y_root) - self.drag_start[1]
        if abs(dx) + abs(dy) > 4:
            self.drag_moved = True
            self.compact_position = None
        vx, vy, vw, vh = virtual_screen()
        width = self.W if self.panel_open else self.PET_WIDTH
        x = max(vx, min(self.drag_origin[0] + dx, vx + vw - width))
        y = max(vy, min(self.drag_origin[1] + dy, vy + vh - self.H))
        self.root.geometry(f"+{x}+{y}")

    def _pet_release(self, _event) -> None:
        if self.drag_moved:
            self._save_position()
        elif time.time() - self.press_time < 0.5:
            if self.state == "speaking":
                self.stop_speech()
            elif self.panel_open:
                self._close_panel()
            else:
                self._open_panel()
        self.drag_start = None
        self.drag_origin = None

    # ---------- animation ----------

    def _animate(self) -> None:
        if self.closing:
            return
        self.phase += 0.075
        target = math.sin(self.phase) * 2.8
        delta = target - self.pet_dy
        if abs(delta) > 0.01:
            self.canvas.move("pet", 0, delta)
            self.pet_dy = target

        if self.state == "thinking":
            elapsed = time.monotonic() - self.thinking_since
            self._set_pose(thinking_pose(elapsed))
            self._set_status(self.thinking_label + "." * (int(elapsed * 2) % 4), GOLD)

        self.root.after(50, self._animate)

    # ---------- lifecycle ----------

    def quit(self) -> None:
        if self.closing:
            return
        self.closing = True
        for job in self.project_tasks.values():
            job.stop.set()
        if isinstance(self.dialog, TaskDialog):
            self._close_dialog()
        self._cancel_voice_input()
        try:
            if self.selector:
                self.selector._destroy()
        except Exception:
            pass
        self.stop_speech()
        try:
            self.hotkeys.stop()
            self.hotkeys.join(timeout=1)
        except Exception:
            pass
        try:
            # This root owns its Tcl interpreter, including pending selector
            # delays and animation callbacks that may outlive their widgets.
            for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(timer)
            self.root.destroy()
        except Exception:
            pass
        log.info("%s stopped", APP_NAME)

    def run(self) -> None:
        self.root.mainloop()
