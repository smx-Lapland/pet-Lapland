import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pet.config import DEFAULTS
from pet.hotkeys import parse_hotkey
from pet.mac_hotkeys import KEY_CODES
from pet.screen import capture_region, virtual_screen, work_area
from pet.speech import SpeechEngine


class MacRoutingTest(unittest.TestCase):
    def test_default_shortcuts_have_distinct_native_keys(self):
        for action, code in {"capture": 1, "ask": 49, "toggle": 4, "quit": 12}.items():
            with self.subTest(action=action), patch.object(sys, "platform", "darwin"):
                mods, key, _ = parse_hotkey(DEFAULTS["hotkeys"][action])
                self.assertEqual((mods, KEY_CODES[key]), (3, code))

    def test_retina_capture_uses_global_points_on_secondary_display(self):
        displays = [((0, 0, 1440, 900), (0, 25, 1440, 850)),
                    ((-1280, -100, 0, 700), (-1280, -75, 0, 700))]
        box = (-1100, 20, -1000, 80)
        with patch.object(sys, "platform", "darwin"), patch("pet.screen._mac_displays", return_value=displays), \
                patch("pet.screen.ImageGrab.grab", return_value=Image.new("RGBA", (200, 120))) as grab:
            self.assertEqual(virtual_screen(), (-1280, -100, 2720, 1000))
            self.assertEqual(work_area(-1200, 100), displays[1][1])
            self.assertEqual(capture_region(box).mode, "RGB")
            grab.assert_called_once_with(bbox=box)

    def test_system_voice_uses_selected_language_and_virtual_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "platform", "darwin"), \
                patch("pet.speech.CACHE_DIR", Path(tmp)), patch("pet.speech.subprocess.run") as listing, \
                patch("pet.speech.run_worker") as worker:
            listing.return_value.stdout = "Samantha en_US # Hello\nAlice it_IT # Ciao\n"
            def render(command, stop, timeout, data):
                with wave.open(command[command.index("-o") + 1], "wb") as wav:
                    wav.setparams((1, 2, 22050, 0, "NONE", ""))
                    wav.writeframes(bytes(22050))
                return True
            worker.side_effect = render
            engine = SpeechEngine({"speech_provider": "sapi", "speech_language": "it"}, Path("unused"))
            engine.warmup()
            path, provider = engine.prepare_segment("Ciao.", threading.Event(), "sapi")
            self.assertTrue(path.is_file())
            self.assertEqual(provider, "sapi")
            command, _, _, data = worker.call_args.args
            self.assertEqual(command[:3], ["/usr/bin/say", "-v", "Alice"])
            self.assertEqual(data, b"Ciao.")
            self.assertEqual(engine._neural_command()[0], sys.executable)


@unittest.skipUnless(sys.platform == "darwin", "requires Aqua and the macOS event dispatcher")
class NativeMacTest(unittest.TestCase):
    def test_system_speech_renders_pcm(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pet.speech.CACHE_DIR", Path(tmp)):
            engine = SpeechEngine({"speech_provider": "sapi", "voice": "Samantha"}, Path("unused"))
            path, provider = engine.prepare_segment("One.", threading.Event(), "sapi")
            self.assertEqual(provider, "sapi")
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertGreater(wav.getnframes(), 0)

    def test_window_registers_shortcuts_opens_and_restores(self):
        from pet.app import MochiApp

        shortcuts = {key: f"ctrl+shift+f{n}" for n, key in enumerate(DEFAULTS["hotkeys"], 17)}
        app = MochiApp({**DEFAULTS, "hotkeys": shortcuts, "muted": True, "save_history": False})
        self.addCleanup(app.quit)
        app.root.update()
        self.assertEqual(app.root.tk.call("tk", "windowingsystem"), "aqua")
        self.assertEqual(len(app.hotkeys.refs), 4)
        app._open_panel()
        self.assertTrue(app.panel_open)
        app._hide_window()
        app.root.tk.call("::tk::mac::ReopenApplication")
        self.assertFalse(app.hidden)
