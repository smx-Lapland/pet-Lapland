from __future__ import annotations

import ctypes
import logging
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from PIL import Image, ImageGrab

log = logging.getLogger("mochi.screen")

TRANSPARENT = "#010203"
ACCENT = "#6EE7C7"


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def virtual_screen() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    x = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
    y = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
    w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
    h = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
    if w <= 0 or h <= 0:
        w, h = 1920, 1080
    return x, y, w, h


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        pass
    x, y, w, h = virtual_screen()
    return x, y, x + w, y + h


def capture_region(bbox: tuple[int, int, int, int]) -> Image.Image:
    vx, vy, vw, vh = virtual_screen()
    left, top, right, bottom = bbox
    left = max(vx, min(left, vx + vw))
    top = max(vy, min(top, vy + vh))
    right = max(left + 1, min(right, vx + vw))
    bottom = max(top + 1, min(bottom, vy + vh))
    full = ImageGrab.grab(bbox=None, include_layered_windows=True, all_screens=True)
    crop = full.crop((left - vx, top - vy, right - vx, bottom - vy))
    if crop.width < 2 or crop.height < 2:
        raise RuntimeError("empty capture")
    return crop.convert("RGB")


class RegionSelector:
    """Full-desktop dim overlay with a drag rectangle.

    The overlay is deliberately small and dependency-free: it dims the whole
    virtual desktop, lets the user drag a rectangle, and returns absolute
    physical-pixel coordinates. The screenshot itself is taken after the
    overlay is destroyed, so the dimmer never contaminates the image.
    """

    def __init__(
        self,
        root: tk.Misc,
        on_done: Callable[[tuple[int, int, int, int]], None],
        on_cancel: Callable[[], None],
    ):
        self.root = root
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.origin: tuple[int, int] | None = None
        self.rect_id: int | None = None
        self.size_id: int | None = None
        self.hint_id: int | None = None
        self.finished = False

    def start(self) -> None:
        vx, vy, vw, vh = virtual_screen()
        top = tk.Toplevel(self.root)
        self.top = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.34)
        top.configure(bg="#000000")
        # A leading minus in Tk geometry anchors to the opposite screen edge.
        top.geometry(f"{vw}x{vh}+{vx}+{vy}")
        top.lift()
        try:
            top.focus_force()
        except Exception:
            pass
        canvas = tk.Canvas(top, bg="#000000", highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas = canvas
        canvas.pack(fill="both", expand=True)

        cx, cy = vw // 2, 54
        canvas.create_rectangle(cx - 240, cy - 26, cx + 240, cy + 26, fill="#0B1220", outline="#334155", width=1)
        canvas.create_text(
            cx,
            cy,
            text="Drag to select   ·   Esc to cancel",
            fill="#F8FAFC",
            font=("Segoe UI", -17, "bold"),
        )
        self.hint_id = canvas.create_text(
            cx,
            vh - 44,
            text="Only the selected region will be sent to the model",
            fill="#CBD5E1",
            font=("Segoe UI", -12),
        )

        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<Button-3>", lambda _e: self._cancel())
        top.bind("<Escape>", lambda _e: self._cancel())
        top.bind("<Return>", lambda _e: self._cancel())
        top.protocol("WM_DELETE_WINDOW", self._cancel)
        top.after(80, lambda: self._safe_focus(top))

    @staticmethod
    def _safe_focus(top: tk.Toplevel) -> None:
        try:
            top.focus_force()
            top.grab_set()
        except Exception:
            pass

    def _press(self, event: tk.Event) -> None:
        self.origin = (int(event.x_root), int(event.y_root))
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        if self.size_id:
            self.canvas.delete(self.size_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=ACCENT, width=2, dash=(6, 3)
        )
        self.size_id = self.canvas.create_text(
            event.x + 10, event.y + 10, text="", anchor="nw", fill="#FFFFFF", font=("Consolas", -12, "bold")
        )

    def _drag(self, event: tk.Event) -> None:
        if not self.origin or not self.rect_id:
            return
        x0, y0 = self.origin
        x1, y1 = int(event.x_root), int(event.y_root)
        vx, vy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        self.canvas.coords(self.rect_id, x0 - vx, y0 - vy, x1 - vx, y1 - vy)
        self.canvas.coords(self.size_id, event.x + 12, event.y + 12)
        self.canvas.itemconfigure(self.size_id, text=f"{abs(x1 - x0)} × {abs(y1 - y0)}")

    def _release(self, event: tk.Event) -> None:
        if not self.origin or self.finished:
            return
        x0, y0 = self.origin
        x1, y1 = int(event.x_root), int(event.y_root)
        if abs(x1 - x0) < 16 or abs(y1 - y0) < 16:
            self._cancel()
            return
        bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._finish(bbox)

    def _finish(self, bbox: tuple[int, int, int, int]) -> None:
        if self.finished:
            return
        self.finished = True
        self._destroy()
        self.root.after(130, lambda: self.on_done(bbox))

    def _cancel(self) -> None:
        if self.finished:
            return
        self.finished = True
        self._destroy()
        self.root.after(40, self.on_cancel)

    def _destroy(self) -> None:
        if not self.top:
            return
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        self.top = None
        self.canvas = None


def save_debug_capture(image: Image.Image, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"capture-{stamp}.png"
    image.save(path)
    return path

