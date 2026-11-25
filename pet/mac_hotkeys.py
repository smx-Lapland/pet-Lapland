"""Register only the configured shortcuts with the macOS event dispatcher."""
import ctypes as C


# Translate the virtual keys accepted by the shared settings parser to macOS
# hardware key codes (ANSI layout for letters and digits).
ANSI_KEYS = {
    "A": 0, "B": 11, "C": 8, "D": 2, "E": 14, "F": 3, "G": 5, "H": 4,
    "I": 34, "J": 38, "K": 40, "L": 37, "M": 46, "N": 45, "O": 31, "P": 35,
    "Q": 12, "R": 15, "S": 1, "T": 17, "U": 32, "V": 9, "W": 13, "X": 7,
    "Y": 16, "Z": 6, "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25,
}
KEY_CODES = {ord(key): code for key, code in ANSI_KEYS.items()}
KEY_CODES.update({0x20: 49, 0x1B: 53, 0x0D: 36, 0x09: 48, 0x08: 51, 0x2E: 117,
                  0x24: 115, 0x23: 119, 0x21: 116, 0x22: 121, 0x25: 123, 0x26: 126,
                  0x27: 124, 0x28: 125})
KEY_CODES.update(enumerate((122, 120, 99, 118, 96, 97, 98, 100, 101, 109,
                            103, 111, 105, 107, 113, 106, 64, 79, 80, 90), start=0x70))


class HotKeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


class EventType(C.Structure):
    _fields_ = [("event_class", C.c_uint32), ("kind", C.c_uint32)]


Handler = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
SIGNATURE = int.from_bytes(b"Moch", "big")


class MacHotkeys:
    """Tk's Aqua main loop dispatches registered Carbon events on the UI thread."""

    def __init__(self, bindings, event_queue):
        self.bindings = bindings
        self.events = event_queue
        self.actions = {}
        self.refs = []
        self.handler = C.c_void_p()
        self.callback = Handler(self._pressed)

    def _pressed(self, _next, event, _data):
        key = HotKeyID()
        status = self.carbon.GetEventParameter(event, int.from_bytes(b"----", "big"),
                                               int.from_bytes(b"hkid", "big"), None,
                                               C.sizeof(key), None, C.byref(key))
        if status or key.signature != SIGNATURE or key.id not in self.actions:
            return -9874  # eventNotHandledErr
        self.events.put(("hotkey", self.actions[key.id]))
        return 0

    def start(self):
        from .hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_hotkey

        ptr, uint = C.c_void_p, C.c_uint32
        try:
            api = self.carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            api.GetEventDispatcherTarget.argtypes = []
            api.GetEventDispatcherTarget.restype = ptr
            api.InstallEventHandler.argtypes = [ptr, Handler, uint, C.POINTER(EventType), ptr, C.POINTER(ptr)]
            api.RegisterEventHotKey.argtypes = [uint, uint, HotKeyID, ptr, uint, C.POINTER(ptr)]
            api.GetEventParameter.argtypes = [ptr, uint, uint, C.POINTER(uint), uint, C.POINTER(uint), ptr]
            api.UnregisterEventHotKey.argtypes = [ptr]
            api.RemoveEventHandler.argtypes = [ptr]
            target = api.GetEventDispatcherTarget()
            pressed = EventType(int.from_bytes(b"keyb", "big"), 5)
            status = api.InstallEventHandler(target, self.callback, 1, C.byref(pressed), None, C.byref(self.handler))
            if status:
                raise OSError(f"InstallEventHandler ({status})")
        except (AttributeError, OSError) as exc:
            self.events.put(("hotkey_error", "all", "全局快捷键", str(exc)))
            return

        modifiers = {MOD_ALT: 0x800, MOD_CONTROL: 0x1000, MOD_SHIFT: 0x200, MOD_WIN: 0x100}
        for index, (action, spec) in enumerate(self.bindings.items(), 1):
            item = parse_hotkey(spec)
            if not item:
                self.events.put(("hotkey_error", action, spec, "无法解析"))
                continue
            mods, key, label = item
            mask = sum(value for flag, value in modifiers.items() if flag & mods)
            ref = ptr()
            status = api.RegisterEventHotKey(KEY_CODES[key], mask, HotKeyID(SIGNATURE, index),
                                             target, 0, C.byref(ref))
            if status:
                self.events.put(("hotkey_error", action, label, f"无法注册 ({status})"))
            else:
                self.actions[index] = action
                self.refs.append(ref)

    def stop(self):
        for ref in self.refs:
            self.carbon.UnregisterEventHotKey(ref)
        self.refs.clear()
        if self.handler:
            self.carbon.RemoveEventHandler(self.handler)
            self.handler = C.c_void_p()
        self.actions.clear()
