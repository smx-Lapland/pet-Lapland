import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from pet.config import DEFAULTS
from pet.hotkeys import parse_hotkey
from pet.mac_hotkeys import KEY_CODES
from pet.screen import capture_region, virtual_screen, work_area
from pet.speech import SpeechEngine


class MacRoutingTest(unittest.TestCase):
    def test_saved_position_stays_on_a_visible_display(self):
        from pet.app import MochiApp

        # The left screen starts lower, leaving a gap above it inside the virtual desktop.
        displays = [((0, 0, 1440, 900), (0, 25, 1440, 850)),
                    ((-1280, 500, 0, 1300), (-1280, 525, 0, 1250))]
        app = Mock(PET_WIDTH=170, PET_HEIGHT=430, H=430)
        with patch.object(sys, "platform", "darwin"), patch("pet.screen._mac_displays", return_value=displays):
            for x, y, expected in [(-900, 10, "+0+25"), (-900, 600, "+-900+600")]:
                app.cfg = {"window": {"x": x, "y": y}}
                MochiApp._place_initial(app)
                app.root.geometry.assert_called_with("170x430" + expected)

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
    def test_launcher_from_another_directory_and_duplicate_launch(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "桌宠 source"
            install.mkdir()
            shutil.copytree(repo / "pet", install / "pet", ignore=shutil.ignore_patterns("__pycache__"))
            (install / "assets").symlink_to(repo / "assets", target_is_directory=True)
            (install / ".venv").symlink_to(sys.prefix, target_is_directory=True)
            script = install / "run_pet.command"
            shutil.copy2(repo / script.name, script)
            args = ["/bin/sh", str(script), "--start-hidden"]
            env = {**os.environ, "HOME": tmp, "DSH_HOME": str(Path(tmp) / ".dsh")}
            log = install / "pet.log"
            with subprocess.Popen(args, env=env, cwd=tmp) as first:
                try:
                    deadline = time.monotonic() + 25
                    while not log.exists() or "started model=" not in log.read_text(encoding="utf-8"):
                        self.assertIsNone(first.poll(), "launcher exited before the Tk application started")
                        self.assertLess(time.monotonic(), deadline, "launcher did not reach the Tk event loop")
                        time.sleep(.1)
                    subprocess.run(args, env=env, cwd=tmp, check=True, timeout=10)
                    self.assertIsNone(first.poll())
                finally:
                    if first.poll() is None:
                        first.kill()

    def test_secondary_click_cancels_capture_before_reading_pixels(self):
        import tkinter as tk
        from pet.screen import RegionSelector

        root = tk.Tk()
        self.addCleanup(root.destroy)
        for gesture in ("<Button-2>", "<Control-Button-1>"):
            with self.subTest(gesture=gesture):
                sent, cancelled = Mock(), Mock()
                selector = RegionSelector(root, sent, cancelled)
                selector.start()
                root.update()
                selector.canvas.event_generate(gesture, x=25, y=25)
                root.after(160, root.quit)
                root.mainloop()
                self.assertIsNone(selector.top)
                cancelled.assert_called_once_with()
                sent.assert_not_called()

    def test_stopping_task_closes_descendants_that_hold_its_output(self):
        from pet.dsh_tasks import Task, execute

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            job = Task("status")
            ready = folder / "ready"
            child = "from pathlib import Path; import time; Path('ready').touch(); time.sleep(12)"
            parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}])"

            def stop_after_launch():
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.03)
                job.stop.set()

            stopper = threading.Thread(target=stop_after_launch)
            with patch("pet.dsh_tasks.projects", return_value={"p": Mock(path=folder)}), \
                    patch("pet.dsh_tasks.project_context", return_value={}), \
                    patch("pet.dsh_tasks.launcher", return_value=[sys.executable, "-c", parent]):
                stopper.start()
                start = time.monotonic()
                try:
                    self.assertEqual(execute("p", "", job), "")
                finally:
                    stopper.join()
                self.assertTrue(ready.exists())
                self.assertLess(time.monotonic() - start, 8)

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
