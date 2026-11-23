from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from queue import Queue

log = logging.getLogger("mochi.hotkeys")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_NAMES = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
}

KEY_NAMES = {
    "space": 0x20,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "pause": 0x13,
    "capslock": 0x14,
}


def parse_hotkey(spec: str) -> tuple[int, int, str] | None:
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key: int | None = None
    for part in parts:
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
            continue
        if key is not None:
            return None
        if part in KEY_NAMES:
            key = KEY_NAMES[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            key = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            key = 0x6F + int(part[1:])
        else:
            return None
    if key is None:
        return None
    label = " + ".join(("Ctrl" if p in ("ctrl", "control") else "Alt" if p == "alt" else "Shift" if p == "shift" else "Win" if p in ("win", "meta") else p.upper()) for p in parts)
    return mods, key, label


class HotkeyManager(threading.Thread):
    def __init__(self, bindings: dict[str, str], event_queue: Queue):
        super().__init__(name="mochi-hotkeys", daemon=True)
        self.bindings = bindings
        self.event_queue = event_queue
        self._stopping = threading.Event()
        self._thread_id: int | None = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._registered: list[int] = []

    def run(self) -> None:
        self._thread_id = int(self._kernel32.GetCurrentThreadId())
        log.info("hotkey thread started tid=%s", self._thread_id)
        parsed: dict[int, str] = {}
        for index, (action, spec) in enumerate(self.bindings.items(), start=1):
            item = parse_hotkey(spec)
            if not item:
                self.event_queue.put(("hotkey_error", action, spec, "无法解析"))
                continue
            mods, key, label = item
            if self._user32.RegisterHotKey(None, index, mods | MOD_NOREPEAT, key):
                parsed[index] = action
                self._registered.append(index)
                log.info("registered hotkey %s = %s", action, label)
            else:
                err = ctypes.get_last_error()
                self.event_queue.put(("hotkey_error", action, label, f"被占用 (code {err})"))
                log.warning("failed to register %s (%s) err=%s", action, label, err)

        if not parsed:
            log.warning("no hotkeys registered")
            return

        msg = wintypes.MSG()
        try:
            while not self._stopping.is_set():
                ret = int(self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
                if ret == 0 or ret == -1:
                    log.info("hotkey message loop ended ret=%s", ret)
                    break
                log.debug("hotkey message %s wparam=%s", msg.message, msg.wParam)
                if msg.message == WM_HOTKEY:
                    action = parsed.get(int(msg.wParam))
                    if action:
                        self.event_queue.put(("hotkey", action))
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.exception("hotkey message loop failed")
        finally:
            for index in self._registered:
                self._user32.UnregisterHotKey(None, index)
            self._registered.clear()
            log.info("hotkey thread stopped")

    def stop(self) -> None:
        self._stopping.set()
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
