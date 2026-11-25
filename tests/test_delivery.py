import copy
import gc
import json
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image

from pet.app import MochiApp
from pet.config import DEFAULTS
from pet.dialogs import CapturePreview
from pet.history import ConversationHistory
from pet.llm import DeepSeekClient, LLMError, first_complete_segment, normalize_answer
from pet.neural_worker import azure_audio, english_beats, italian_beats, original_recording, render_italian
from pet.screen import set_dpi_awareness


ANSWER = {"headline_en": "Test card", "headline_zh": "测试卡",
          "segments": [{"en": "The first square is blue.", "zh": "第一块为蓝色。"},
                       {"en": "The second square is black.", "zh": "第二块为黑色。"}]}


class StreamTest(unittest.TestCase):
    def test_screen_and_explicit_reasoning_keep_the_configured_effort(self):
        for image, effort in ((Image.new("RGB", (8, 8)), "low"), (None, "high")):
            client = DeepSeekClient({"_api_key": "test", "reasoning_effort": effort})
            with patch.object(client, "_post", return_value={"choices": [{"message": {"content": json.dumps(ANSWER)}}]}) as post:
                client.ask(image, "test")
            payload = post.call_args.args[0]
            self.assertNotIn("thinking", payload)
            self.assertEqual(payload["reasoning_effort"], effort)

    def test_direction_is_inherited_without_becoming_speech_or_subtitles(self):
        rows = [{"en": "[MOCKING] [chuckles] Bravo, Dr. Grey. 12%!", "zh": "[MOCKING] 好，12%！"},
                {"en": "[MANIC] (whispers) Try again~.", "zh": "再试试。"}]
        first = first_complete_segment('{"segments":[' + json.dumps(rows[0]) + ',')
        result = normalize_answer({"segments": rows})
        self.assertEqual(first["segments"], result["segments"][:1])
        self.assertEqual(result["segments"], [
            {"en": "Bravo, Doctor Grey. twelve percent!", "zh": "好，12%！", "emotion": "MOCKING"},
            {"en": "Try again.", "zh": "再试试。", "emotion": "MOCKING"}])
        self.assertEqual(normalize_answer({"answer": "[UNKNOWN] Try it."})["segments"][0]["en"], "Try it.")
        line = normalize_answer({"segments": [{"it": "[WHISPER] Vieni qui.", "zh": "过来。"}]}, "it")
        self.assertEqual(line["segments"][0], {"it": "Vieni qui.", "zh": "过来。", "emotion": "WHISPER"})
        corrected = normalize_answer({"it": "[SHARP] (sospira) Salva il file.",
                                      "zh": "保存 `my_file[0]`，保留 {name}。"}, "it")
        self.assertEqual(corrected["segments"][0], {"it": "Salva il file.", "emotion": "NEUTRAL",
                                                   "zh": "保存 my_file[0]，保留 {name}。"})

    def lines(self, pieces):
        for piece in pieces:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            yield b"data: " + json.dumps(chunk).encode() + b"\n"

    def test_early_voice_requires_one_complete_top_level_spoken_field(self):
        for lang in ("en", "it"):
            line = '[MOCKING] Bravo, "Doctor"!'
            src = json.dumps({"metadata": {lang: "nested"}, lang: line, "zh": "好！"})
            stop = src.index(', "zh"')
            self.assertTrue(all(first_complete_segment(src[:i], lang) is None for i in range(stop)))
            early = first_complete_segment(src[:stop], lang)
            self.assertTrue(early["voice_ready"])
            self.assertEqual(early["segments"][0][lang], normalize_answer(json.loads(src), lang)["segments"][0][lang])
            self.assertEqual(early["segments"][0]["emotion"], "MOCKING")

    def test_complete_single_segment_keeps_longer_spoken_explanations(self):
        line = "Carefully compare the measurements. " * 17 + "Now check the final result."
        segment = {"en": line, "zh": "逐项比较测量值，再核对结果。"}
        self.assertEqual(normalize_answer({"segments": [segment]})["segments"], [segment])

    def test_first_segment_precedes_end(self):
        raw = json.dumps(ANSWER, ensure_ascii=False)
        split = raw.index("}, {") + 1
        first = []

        def stream():
            yield from self.lines(raw[:split])
            self.assertEqual(first[0]["segments"], ANSWER["segments"][:1])
            yield from self.lines(raw[split:])
            yield b"data: [DONE]\n"

        body = DeepSeekClient._read_stream(stream(), first.append)
        self.assertEqual(len(first), 1)
        self.assertEqual(json.loads(DeepSeekClient._extract_content(body)), ANSWER)

    def test_stream_break_after_first_is_not_replayed(self):
        raw = json.dumps(ANSWER)
        lines = self.lines([raw[:raw.index("}, {") + 1]])
        with patch("pet.llm.open_api") as post:
            response = post.return_value.__enter__.return_value
            response.headers.get_content_type.return_value = "text/event-stream"
            response.readline.side_effect = lambda _limit: next(lines, b"")
            first = []
            with self.assertRaises(LLMError):
                DeepSeekClient({"_api_key": "test"}).ask(None, "test", on_first=first.append)
            self.assertEqual((len(first), post.call_count), (1, 1))

    def test_segments_before_headlines(self):
        answer = {"segments": ANSWER["segments"], "headline_en": "Test", "headline_zh": "测试"}
        raw = json.dumps(answer)
        first = []
        result = DeepSeekClient._read_stream([*self.lines(raw), b"data: [DONE]"], first.append)
        self.assertEqual(first[0]["segments"], ANSWER["segments"][:1])
        self.assertEqual(json.loads(DeepSeekClient._extract_content(result)), answer)


class VoiceTest(unittest.TestCase):
    def test_review_plays_one_file_for_both_languages(self):
        import struct
        import wave
        from tools.evaluate_dialogue import play_review_preview
        play_fn = "tools.evaluate_dialogue.subprocess.run" if sys.platform == "darwin" else "winsound.PlaySound"
        with tempfile.TemporaryDirectory() as tmp, patch(play_fn) as play:
            folder = Path(tmp)
            (folder / "audio").mkdir()
            for lang, rate, value in (("en", 32000, 2000), ("it", 22050, -2000)):
                with wave.open(str(folder / "audio" / f"08-{lang}-1.wav"), "wb") as take:
                    take.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                    take.writeframes(struct.pack("<h", value) * rate)
            play_review_preview(folder)
            target = folder / "preview.wav"
            if sys.platform == "darwin":
                play.assert_called_once_with(["/usr/bin/afplay", str(target)], check=True)
            else:
                import winsound
                play.assert_called_once_with(str(target), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            with wave.open(str(target)) as joined:
                self.assertAlmostEqual(joined.getnframes() / joined.getframerate(), 2.5, places=2)
                for seconds, expected in ((.5, 2000), (1.25, 0), (2., -2000)):
                    joined.setpos(int(seconds * joined.getframerate()))
                    self.assertAlmostEqual(struct.unpack("<h", joined.readframes(1))[0], expected, delta=5)

    def test_reference_italian_route_and_cpu_recovery(self):
        from pet.speech import SpeechEngine

        with patch("pet.speech.italian_reference.installed_python", return_value=Path(sys.executable)):
            cfg = {"speech_provider": "vits", "speech_language": "it", "italian_reference_voice": True}
            engine = SpeechEngine(cfg, Path("unused"))
            self.assertEqual(Path(engine._neural_command()[1]).name, "italian_reference.py")
            self.assertEqual(engine._neural_env()["HF_HUB_OFFLINE"], "1")
            engine._use_cpu_fallback()
            self.assertEqual(Path(engine._neural_command()[1]).name, "neural_worker.py")
            for override in ({"voice_device": "cpu"}, {"verify_italian_voice": True}, {"italian_reference_voice": False}):
                engine = SpeechEngine({**cfg, **override}, Path("unused"))
                self.assertEqual(Path(engine._neural_command()[1]).name, "neural_worker.py")

    def test_mixed_dictation_preserves_paths_and_routes_inputs_independently(self):
        import numpy as np
        from pet.voice_input import VoiceInput, normalize_mixed_dictation

        self.assertEqual(normalize_mixed_dictation("到gitthub检查github.com和Pythonic"),
                         "到GitHub检查github.com和Pythonic")
        self.assertEqual(normalize_mixed_dictation("the deep sea"), "the deep sea")
        signal = np.full(16000, 2000, np.int16)
        with patch("pet.voice_input.SENSEVOICE_DIR") as folder, \
             patch("sherpa_onnx.OfflineRecognizer.from_sense_voice") as sense, \
             patch("pet.voice_input.load_recognizer") as whisper, \
             patch("pet.voice_input.recognize_audio", return_value="看看github") as decode:
            folder.__truediv__.return_value.is_file.return_value = True
            self.assertEqual(VoiceInput().transcribe(signal, threading.Event()), "看看GitHub")
            self.assertIs(decode.call_args.args[0], sense.return_value)
            VoiceInput("it").transcribe(signal, threading.Event())
            whisper.assert_called_once_with("it")
            folder.__truediv__.return_value.is_file.return_value = False
            VoiceInput().transcribe(signal, threading.Event())
            whisper.assert_called_with("zh")
            VoiceInput("auto").transcribe(signal, threading.Event())
            whisper.assert_called_with("")

    def test_italian_vits_changes_duration_with_the_pitch_contour(self):
        import numpy as np
        from pet.vits_voice import RATE, follow_contour, pitch_track

        audio = .2 * np.sin(2 * np.pi * 180 * np.arange(2 * RATE) / RATE)
        _, pitch = pitch_track(audio)
        for tempo in (1.08, 1.36):
            result = follow_contour(audio, pitch, tempo)
            self.assertAlmostEqual(len(result) / RATE, 2 / tempo, delta=.02)
            self.assertAlmostEqual(np.median(pitch_track(result)[1]), np.median(pitch), delta=.15)

    def test_generated_italian_tempo_preserves_voiced_pitch(self):
        import wave
        import numpy as np
        from pet.neural_worker import write_voice_wav

        rate = 22050
        samples = .2 * np.sin(2 * np.pi * 180 * np.arange(2 * rate) / rate)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "line.wav"
            for italian, setting in ((False, 0), (True, 0), (True, 2)):
                tempo = 2 ** (setting / 10)
                write_voice_wav(path, samples, rate, setting, italian_dialogue=italian)
                with wave.open(str(path)) as wav:
                    signal = np.frombuffer(wav.readframes(wav.getnframes()), "<i2")
                    self.assertEqual(wav.getframerate(), rate)
                self.assertAlmostEqual(len(signal) / rate, 2 / tempo, delta=.02)
                if setting == 0:
                    np.testing.assert_array_equal(signal, (samples * 32767).astype("<i2"))
                middle = signal[rate // 4:rate * 5 // 4]
                self.assertAlmostEqual(np.abs(np.fft.rfft(middle)).argmax(), 180, delta=2)

    def test_italian_live_voice_uses_one_take_unless_verification_is_enabled(self):
        from pet.speech import SpeechEngine
        from pet.vits_voice import ReferenceVoice

        voice = object.__new__(ReferenceVoice)
        voice.language, voice.targets = "it", {"MOCKING": None}
        for verify in (False, True):
            engine = SpeechEngine({"speech_provider": "vits", "speech_language": "it",
                                   "verify_italian_voice": verify}, Path("unused"))
            with patch.object(voice, "generate", return_value="primary") as generate, \
                 patch.object(voice, "check_italian", return_value="checked") as check, \
                 patch("pet.vits_voice.FILES", []):
                flag = engine._neural_env()["PET_VERIFY_ITALIAN"] == "1"
                audio = voice.render("Giochiamo?", "MOCKING", verify=flag)
                generate.assert_called_once_with("Giochiamo?", "MOCKING", tempo=1.)
                self.assertEqual(check.call_count, int(verify))
                self.assertEqual(audio, "checked" if verify else "primary")

    def test_fast_italian_sets_tempo_in_piper_without_repitching(self):
        from unittest.mock import Mock
        from pet.vits_voice import ReferenceVoice, RATE
        import numpy as np
        voice = Mock(language="it", targets={"NEUTRAL": None})
        voice.reference.return_value = ([200] * 12, .2)
        word = np.full(RATE // 4, .4, np.float32)
        voice.convert.return_value = word
        voice.piper.synthesize.return_value = [Mock(phonemes=".", audio_float_array=word)]
        with patch("pet.vits_voice.follow_contour", side_effect=AssertionError("resynthesis")):
            ReferenceVoice.generate(voice, "Ancora.", tempo=1.2)
        self.assertAlmostEqual(voice.piper.synthesize.call_args.args[1].length_scale, 1 / 1.2)

    def test_failed_gpu_retries_same_voice_on_cpu(self):
        import wave
        from pet.speech import SpeechEngine

        attempts = []
        def render(_command, _stop, _timeout, data, env):
            job = json.loads(data)
            attempts.append((env["PET_TTS_DEVICE"], job["provider"], job["language"], job["emotion"]))
            if env["PET_TTS_DEVICE"] == "cuda":
                return False
            with wave.open(job["path"], "wb") as out:
                out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                out.writeframes(bytes(3200))
            return True

        with tempfile.TemporaryDirectory() as tmp, patch("pet.speech.CACHE_DIR", Path(tmp)), \
             patch("pet.speech.cuda_installed", return_value=True), patch("pet.speech.run_worker", side_effect=render):
            engine = SpeechEngine({"speech_provider": "vits", "speech_language": "it"}, Path("unused"))
            path, used = engine.prepare_segment("Ciao.", threading.Event(), "vits", "MOCKING")
            self.assertEqual((used, engine.device), ("vits", "cpu"))
            self.assertTrue(path.is_file())
            self.assertEqual(attempts, [("cuda", "vits", "it", "MOCKING"), ("cpu", "vits", "it", "MOCKING")])

    def test_auto_gpu_recovery_reuses_cpu_until_cooldown_expires(self):
        from pet.speech import SpeechEngine
        with patch("pet.speech.cuda_installed", return_value=True):
            engine = SpeechEngine({"speech_provider": "vits", "speech_language": "it"}, Path("unused"))
        with patch("pet.speech.time.monotonic", return_value=10):
            engine._use_cpu_fallback()
        with patch.object(engine, "close_renderer") as close:
            with patch("pet.speech.time.monotonic", return_value=35):
                engine._retry_gpu_if_due()
            self.assertEqual(engine.device, "cpu")
            close.assert_not_called()
            with patch("pet.speech.time.monotonic", return_value=41):
                engine._retry_gpu_if_due()
            self.assertEqual(engine.device, "cuda")
            self.assertIsNone(engine.gpu_retry_at)
            close.assert_called_once()

    def test_cpu_setting_and_missing_gpu_keep_cpu_runtime(self):
        from pet.speech import SpeechEngine
        for installed, mode in ((True, "cpu"), (False, "auto")):
            with patch("pet.speech.cuda_installed", return_value=installed), patch("pet.speech.installed_voice", return_value=(None, None)):
                engine = SpeechEngine({"voice_device": mode, "speech_provider": "vits"}, Path("unused"))
                self.assertEqual(engine._neural_env()["PET_TTS_DEVICE"], "cpu")

    def test_prepared_worker_stops_mid_segment_and_removes_pending_file(self):
        from pet.speech import SpeechEngine
        worker = ("import json, pathlib, sys, time\n"
                  "job = json.loads(sys.stdin.buffer.readline())\n"
                  "pathlib.Path(job['path'] + '.pending').touch()\n"
                  "time.sleep(30)\n")
        with patch("pet.speech.installed_voice", return_value=(None, None)):
            engine = SpeechEngine({"speech_provider": "vits"}, Path("unused"))
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as tmp, patch("pet.speech.CACHE_DIR", Path(tmp)), \
             patch.object(engine, "_neural_command", return_value=[sys._base_executable, "-c", worker]):
            engine.warmup()
            proc = engine._renderer
            result = []
            task = threading.Thread(target=lambda: result.append(engine.prepare_segment("test", stop, "vits")))
            task.start()
            try:
                deadline = time.monotonic() + 3
                while not list(Path(tmp).glob("*.pending")) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(list(Path(tmp).glob("*.pending")))
            finally:
                stop.set()
                engine.close_renderer()
                task.join(3)
            self.assertEqual(result, [(None, "muted")])
            self.assertIsNotNone(proc.poll())
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_one_audio_file_for_a_complete_reply(self):
        from unittest.mock import Mock
        from pet.speech import wav_duration
        import wave

        app = object.__new__(MochiApp)
        app.events = queue.Queue()
        speech = Mock(provider="vits")
        stop = threading.Event()
        text = "First, choose a card. Now turn it over. Your move."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reply.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", ""))
                wav.writeframes(bytes(32000))
            duration = wav_duration(path)
            speech.prepare_segment.return_value = path, "vits"
            speech.play_file.return_value = True
            app._speech_worker(text, "MOCKING", 1, 2, stop, speech)
            speech.prepare_segment.assert_called_once_with(text, stop, "vits", emotion="MOCKING")
            speech.play_file.assert_called_once_with(path, stop)
            self.assertFalse(path.exists())
        self.assertEqual(app.events.get_nowait(), ("playback", 1, 2, "vits", duration))
        self.assertEqual(app.events.get_nowait(), ("speech_done", 1, 2))
        self.assertTrue(app.events.empty())

    def test_hands_free_gate_uses_level_preroll_and_silence(self):
        from array import array
        from pet.voice_input import SpeechGate, RATE

        # Mark frames as speech to test the independent volume and timing gates.
        with patch("webrtcvad.Vad") as vad:
            vad.return_value.is_speech.return_value = True
            gate = SpeechGate(-30)
            quiet = array("h", [0]) * RATE
            voice = array("h", [6500]) * (RATE // 3)
            self.assertIsNone(gate.feed(array("h", [500]) * RATE))
            self.assertIsNone(gate.feed(array("h", [6500]) * 320 + quiet))
            self.assertIsNone(gate.feed(voice + quiet[:RATE // 3]))
            self.assertIsNone(gate.feed(voice))
            turn = gate.feed(quiet)
            self.assertIsNotNone(turn)
            self.assertEqual(turn.count(6500), len(voice) * 2)
            self.assertIsNone(gate.feed(voice + quiet))
            gate.resume(skip_ms=320)
            self.assertIsNone(gate.feed(voice[:RATE // 4] + quiet))
            self.assertIsNotNone(gate.feed(voice + quiet))

    def test_reference_pauses_exclude_the_silence_around_a_take(self):
        import numpy as np
        from pet.vits_voice import RATE, reference_pause

        speech = np.ones(RATE // 2, dtype=np.float32) * .2
        take = np.concatenate((np.zeros(RATE), speech, np.zeros(round(.71 * RATE)), speech, np.zeros(RATE)))
        self.assertAlmostEqual(reference_pause(take), .71, delta=.02)

    def test_italian_retry_counts_words_without_penalizing_accents(self):
        from pet.vits_voice import words, word_errors

        expected = words("È lì, Dottore. L’hai vista?")
        self.assertEqual(word_errors(expected, words("E li Dottore, l'hai vista!")), 0)
        self.assertEqual(word_errors(expected, words("E li l'hai")), 2)

    def test_italian_delivery_keeps_short_pickups_and_selects_separate_references(self):
        import numpy as np
        from unittest.mock import Mock

        line = "Davvero, Dottore? Guarda la regina. La prossima mossa è mia!"
        self.assertEqual(list(italian_beats("Il dott. Rossi conta 3.5 punti. Davvero?")),
                         ["Il dott. Rossi conta 3.5 punti.", "Davvero?"])
        model = Mock(sample_rate=24000)
        model.generate.return_value = np.full(2400, .1, dtype=np.float32)
        with patch("pet.neural_worker.pitch_stats", return_value=(14, 220)):
            audio = render_italian(model, line, {"question": "q", "emphatic": "e", "prompt": "p"})
        self.assertEqual([(c.args[0], c.kwargs["voice"]) for c in model.generate.call_args_list],
                         [("Davvero, Dottore? Guarda la regina.", "q"), ("La prossima mossa è mia!", "e")])
        self.assertGreater(len(audio), 4800)

    def test_english_turns_pause_without_splitting_names_or_numbers(self):
        self.assertEqual(english_beats("Ask Dr. Bell about 2–5 samples at 1.5 volts."),
                         [("Ask Dr. Bell about 2–5 samples at 1.5 volts.", 0)])
        beats = english_beats("Very subtle, Doctor. Nobody noticed, but I did.")
        self.assertEqual([line for line, _ in beats], ["Very subtle, Doctor.", "Nobody noticed,", "but I did."])
        self.assertGreater(beats[1][1], beats[0][1])

    def test_vits_english_links_the_vocative_and_trims_decoder_padding(self):
        import numpy as np
        from unittest.mock import Mock
        from pet.vits_voice import RATE, ReferenceVoice

        voice = Mock(language="en", targets={"MOCKING": None})
        voice.reference.return_value = ([], .28)
        voice.convert.side_effect = lambda samples, emotion: samples
        word = np.pad(np.full(RATE // 4, .4, dtype=np.float32), RATE // 5)
        voice.piper.synthesize.return_value = [Mock(phonemes=[p], audio_float_array=word) for p in ("?", ".")]
        line = "Is that your best trick, Doctor? Look, Doctor Bell, a red, blue and gold card."
        audio = ReferenceVoice.generate(voice, line, "MOCKING", contour=False)
        spoken, cfg = voice.piper.synthesize.call_args.args
        self.assertEqual(spoken, "Is that your best trick Doctor? Look, Doctor Bell, a red, blue and gold card.")
        self.assertLess(cfg.length_scale, .8)
        active = np.flatnonzero(np.abs(audio) > .01)
        self.assertLess(active[0] / RATE, .06)
        self.assertLess((len(audio) - active[-1]) / RATE, .06)
        gap = np.diff(active).max() / RATE
        self.assertGreater(gap, .20)
        self.assertLess(gap, .33)

    def test_original_recording_matches_the_whole_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            clip = folder / "greeting.wav"
            clip.touch()
            catalog = [{"file": clip.name, "text": "What's your plan?"}]
            (folder / "original-en.json").write_text(json.dumps(catalog), encoding="utf-8")
            self.assertEqual(original_recording("WHAT’S your plan!", folder, folder), clip)
            self.assertIsNone(original_recording("What's your plan for today?", folder, folder))
            clip.unlink()
            self.assertIsNone(original_recording("What's your plan?", folder, folder))

    def test_azure_request_and_ssml_escaping(self):
        with patch.dict(os.environ, SPEECH_REGION="eastus", SPEECH_KEY="test"), patch("pet.neural_worker.open_api") as post:
            post.return_value.__enter__.return_value.read.return_value = b"audio"
            text = "Compare A & B when X < Y."
            self.assertEqual(azure_audio(text, "en-US-JennyNeural", "+10%"), b"audio")
            request = post.call_args.args[0]
            self.assertEqual(request.full_url, "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1")
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["ocp-apim-subscription-key"], "test")
            self.assertEqual(headers["x-microsoft-outputformat"], "riff-24khz-16bit-mono-pcm")
            prosody = ET.fromstring(request.data).find(".//{*}prosody")
            self.assertEqual((prosody.text, prosody.get("rate")), (text, "+10%"))


class DesktopTest(unittest.TestCase):
    """Real Tk selection and ImageGrab over a local solid-colour fixture."""

    def setUp(self):
        # Tcl interpreters from previous test windows must be collected on the Tk thread.
        gc.collect()
        set_dpi_awareness()
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(_api_key="test", muted=True, save_history=False)
        # Avoid taking over user hotkeys while exercising the dialog flow.
        cfg["hotkeys"] = {key: f"ctrl+shift+f{n}" for n, key in enumerate(cfg["hotkeys"], 17)}
        self.app = MochiApp(cfg)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app.archive = ConversationHistory(Path(folder.name) / "history.sqlite3")
        self.addCleanup(self.app.quit)
        self.card = tk.Toplevel(self.app.root)
        self.card.overrideredirect(True)
        self.card.geometry("260x180+25+25")
        self.card.configure(bg="#2255aa")
        self.card.attributes("-topmost", True)
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
        self.addCleanup(self.gate.set)
        self.app.client.ask = self.fake_model
        self.app.speech.wait_timed = lambda text, stop: stop.wait(0.01)
        self.app.root.update()

    def fake_model(self, image, question, history, follow_up, on_first, context=None):
        self.calls.append((image.copy() if image is not None else None, question, history, follow_up))
        on_first({**ANSWER, "segments": ANSWER["segments"][:1]})
        self.gate.wait(3)
        return ANSWER

    def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.root.update()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for desktop state")

    def test_continuous_conversation_rearms_without_reopening_microphone(self):
        from array import array
        from pet.voice_input import RATE

        app = self.app
        quiet = array("h", [0]) * RATE
        utterance = quiet + array("h", [6500]) * (RATE // 2) + quiet
        heard = []
        self.gate.clear()
        def respond(image, question, **kw):
            heard.append(question)
            kw["on_first"]({**ANSWER, "segments": ANSWER["segments"][:1]})
            self.gate.wait(3)
            return ANSWER

        app.client.ask = respond
        with (patch("miniaudio.CaptureDevice") as device, patch("webrtcvad.Vad") as vad,
              patch.object(app.voice_input, "transcribe", side_effect=["第一句", "第二句"]) as transcribe):
            vad.return_value.is_speech.return_value = True
            app._open_panel()
            app.conversation_button.invoke()
            send = device.return_value.start.call_args.args[0].send
            send(utterance)
            self.until(lambda: app.state == "speaking")
            send(utterance)  # Simulate the loudspeaker returning the assistant's words.
            self.assertFalse(app.conversation.armed)
            self.gate.set()
            self.until(lambda: app.conversation.armed)
            send(utterance)
            self.until(lambda: len(heard) == 2 and app.conversation.armed)
            self.assertEqual(heard, ["第一句", "第二句"])
            self.assertEqual(transcribe.call_count, 2)
            device.assert_called_once()
            device.return_value.close.assert_not_called()
            send(utterance)  # Closing before the posted event is handled must discard it.
            app._on_escape()
            app._poll_events()
            self.assertIsNone(app.conversation)
            self.assertEqual(transcribe.call_count, 2)
            device.return_value.close.assert_called_once()

    def select(self):
        self.app._on_hotkey("capture")
        self.until(lambda: self.app.selector and self.app.selector.canvas.winfo_ismapped())
        canvas = self.app.selector.canvas
        x = self.card.winfo_rootx() + 20 - canvas.winfo_rootx()
        y = self.card.winfo_rooty() + 20 - canvas.winfo_rooty()
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<B1-Motion>", x=x + 90, y=y + 60)
        canvas.event_generate("<ButtonRelease-1>", x=x + 90, y=y + 60)
        self.until(lambda: isinstance(self.app.dialog, CapturePreview))
        self.assertEqual(self.app.dialog.source.size, (90, 60))
        self.assertEqual(self.app.dialog.source.getpixel((45, 30)), (34, 85, 170))
        return self.app.dialog

    def test_ten_regions_and_followup(self):
        self.select().cancel()
        self.assertFalse(self.calls)
        for i in range(10):
            preview = self.select()
            self.assertEqual(len(self.calls), i)
            self.app.root.update_idletasks()
            preview.canvas.event_generate("<ButtonPress-1>", x=45, y=30)
            preview.canvas.event_generate("<ButtonRelease-1>", x=10, y=10)
            preview.send()
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            image, _, history, follow_up = self.calls[-1]
            self.assertFalse(history or follow_up)
            self.assertEqual(image.getpixel((12, 12)), (0, 0, 0))
            self.assertEqual(image.getpixel((50, 35)), (34, 85, 170))
            self.assertEqual(len(self.app.transcript), 1)
        self.app.show_input()
        self.app._entry_clear_placeholder()
        self.app.input_var.set("What colour remains?")
        self.app.submit_input()
        self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
        image, question, history, follow_up = self.calls[-1]
        self.assertTrue(follow_up)
        self.assertEqual((question, len(history)), ("What colour remains?", 2))
        self.assertEqual(image.getpixel((12, 12)), (0, 0, 0))
        self.assertEqual(len(self.app.transcript), 2)
        self.app.show_transcript()
        transcript = next(w for w in self.app.dialog.winfo_children() if isinstance(w, tk.Text))
        self.assertIn(question, transcript.get("1.0", "end"))
        self.app._close_dialog()
        self.app.quit()
        self.assertFalse(any(t.name in ("mochi-speech", "mochi-ask", "mochi-hotkeys") for t in threading.enumerate()))

    def test_pet_only_until_requested_and_idle_close(self):
        app = self.app
        self.assertFalse(app.panel_open)
        self.assertEqual(app.root.winfo_width(), app.PET_WIDTH)
        start = (app.root.winfo_x(), app.root.winfo_y())
        app.press_time = time.time()
        app._pet_release(None)
        app.root.update()
        self.assertTrue(app.panel_open)
        self.assertEqual(app.root.winfo_width(), app.W)
        app.panel_active_at = time.monotonic() - 60
        with patch.object(app.root, "winfo_pointerxy", return_value=(app.root.winfo_x()+210, app.root.winfo_y()+200)):
            app._check_panel()
        self.assertTrue(app.panel_open)
        with patch.object(app.root, "winfo_pointerxy", return_value=(-10000, -10000)):
            app.panel_active_at = time.monotonic() - 60
            app._check_panel()
        app.root.update()
        self.assertFalse(app.panel_open)
        self.assertEqual((app.root.winfo_x(), app.root.winfo_y()), start)
        self.assertFalse(app.hidden)
        self.assertFalse(app.talk_button.winfo_ismapped())
        app._on_hotkey("ask")
        app.root.update()
        self.assertTrue(app.panel_open and app.entry.winfo_ismapped())
        app._close_panel()
        self.assertFalse(app.hidden or app.input_visible)

    def test_two_line_captions_scroll_with_playback_and_allow_review(self):
        self.app._open_panel()
        caps = self.app.captions
        en, zh = caps.rows
        # Slightly wider than one line in each language, measured at the current DPI.
        texts = []
        for phrase, font in (("Your move, Doctor. ", self.app.spoken_font),
                             ("博士，轮到你了。", self.app.translation_font)):
            texts.append(phrase * (caps.viewport // font.measure(phrase) + 2))
        self.app._show_content(*texts, duration=2)
        self.app.root.update()
        for track, item, line in zip(caps.tracks, caps.rows, caps.line_heights):
            box = track.bbox(item)
            self.assertEqual(box[3] - box[1], 2 * line)
            self.assertEqual(track.winfo_height(), 2 * line)
        self.assertEqual(caps.overflow, [0, 0])
        self.assertIsNone(caps.timer)
        self.assertLessEqual(caps.winfo_y() + caps.winfo_height(),
                             self.app.canvas.bbox(self.app.project_window)[1])
        short = "轮到你了，博士。"
        long = "Prima scegli una carta, poi vediamo chi ride. " * 6
        self.app._show_content(long, short, duration=.5)
        self.app.root.update()
        self.assertEqual(caps.winfo_width(), self.app.subtitle_width)
        self.assertGreater(caps.overflow[0], caps.line_heights[0])
        self.assertEqual(caps.overflow[1], 0)
        original, translation = caps.tracks
        self.assertLess(original.winfo_y() + original.winfo_height(), translation.winfo_y())
        self.until(lambda: original.coords(en)[1] < 0 and caps.progress < 1)
        self.assertEqual(original.coords(en)[0], 2)
        self.assertEqual(translation.coords(zh), [2, 0])
        self.until(lambda: caps.progress == 1)
        self.assertLessEqual(original.bbox(en)[3], original.winfo_height() + 1)
        self.assertEqual(original.itemcget(en, "text"), long.strip())
        caps.event_generate("<MouseWheel>", delta=120)
        self.assertLess(caps.progress, 1)
        self.assertIsNone(caps.timer)
        self.app._show_content(long, short * 12, duration=4)
        self.assertTrue(all(caps.overflow))
        self.app.stop_speech()
        self.assertIsNone(caps.timer)
        self.app._show_content("Next.", "下一句。", duration=2)
        self.assertEqual(caps.progress, 0)
        self.assertIsNone(caps.timer)

    def test_stream_preview_waits_for_complete_answer_and_only_speaks_once(self):
        self.gate.clear()
        with patch.object(self.app.speech, "prepare_segment", return_value=(None, "muted")) as render:
            self.select().send()
            self.until(lambda: self.app.stream_started)
            render.assert_not_called()
            self.gate.set()
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            self.assertEqual(render.call_count, 1)
            self.assertEqual(render.call_args.args[0], " ".join(s["en"] for s in ANSWER["segments"]))
            caps = self.app.captions
            self.assertEqual(caps.tracks[1].itemcget(caps.rows[1], "text"), "".join(s["zh"] for s in ANSWER["segments"]))

    def test_first_spoken_field_plays_while_translation_is_pending(self):
        self.gate.clear()
        text = "Oh, really? Your move, Doctor."
        final = normalize_answer({"en": "[MOCKING] " + text, "zh": "当真？轮到你了，博士。"})
        def reply(_image, _question, **kwargs):
            early = first_complete_segment(json.dumps({"en": "[MOCKING] " + text})[:-1] + ",")
            kwargs["on_first"](early)
            self.gate.wait(3)
            return final
        self.app.client.ask = reply
        with patch.object(self.app.speech, "prepare_segment", return_value=(None, "muted")) as render:
            self.select().send()
            self.until(lambda: render.called)
            self.assertTrue(self.app.awaiting_answer)
            self.gate.set()
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            render.assert_called_once()
            self.assertEqual(render.call_args.args[0], text)
            self.assertEqual(self.app.translated_text, final["segments"][0]["zh"])

    def test_dsh_menu_selects_registered_project_and_displays_its_outline(self):
        from pet.dsh_projects import Project
        with tempfile.TemporaryDirectory() as tmp, patch("pet.config.CONFIG_PATH", Path(tmp) / "config.json"), \
                patch("pet.app.projects", return_value={"p": Project("p", "Fixture", Path(tmp), ())}), \
                patch("pet.app.project_context", return_value={"项目":"Fixture", "DSH 会话":"DSH fixture",
                     "最近对话摘录":[{"问题":"From DSH", "回答":"A project answer"}]}) as context:
            app = self.app
            app._open_panel()
            app._list_dsh_projects()
            self.assertEqual(app.project_menu.entrycget(1, "label"), "Fixture")
            app.project_menu.invoke(1)
            self.assertEqual((app.client.project, app.cfg["dsh_session"]), ("p", ""))
            app.show_dsh_context()
            context.assert_called_once_with("p", "")
            self.assertIn("DSH fixture", app.dialog.title())
            self.assertTrue(any(isinstance(w, tk.Text) and "A project answer" in w.get("1.0","end")
                                for w in app.dialog.winfo_children()))
            app._close_dialog()
            app._list_dsh_projects()
            app.project_menu.invoke(0)
            self.assertFalse(app.client.project)

    def test_browse_previous_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationHistory(Path(tmp) / "history.sqlite3")
            self.app.archive = store
            self.app.cfg["save_history"] = True
            image = Image.new("RGB", (32, 32))
            self.app._send_capture(image)
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            first = self.app.topic
            self.app._start_ask(image, "Earlier follow-up", follow_up=True)
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            self.app._send_capture(image)
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            self.app.show_history()
            dlg = self.app.dialog
            self.assertEqual(list(dlg.listing.get_children()), [self.app.topic, first])
            dlg.query.set("earlier FOLLOW-UP")
            self.assertEqual(dlg.listing.selection(), (first,))
            self.assertIn("Earlier follow-up", dlg.detail.get("1.0", "end"))
            self.assertIn(ANSWER["segments"][0]["zh"], dlg.detail.get("1.0", "end"))
            with patch("pet.dialogs.messagebox.askyesno", return_value=True):
                dlg.delete_selected()
            self.assertFalse(dlg.listing.get_children())
            self.app._close_dialog()
            self.app.cfg["save_history"] = False
            self.app._send_capture(image)
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            self.assertEqual(len(store.conversations()), 1)

    def test_settings_selects_italian_text_voice_and_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pet.config.CONFIG_PATH", Path(tmp) / "config.json"):
            self.app.show_settings()
            self.app.dialog.fields["speech_language"].set("意大利语")
            self.app.dialog.apply()
            self.assertEqual(self.app.client.language, "it")
            self.assertEqual(self.app.speech.language, "it")
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["speech_language"], "it")
            answer = {"headline_it": "La luna", "headline_zh": "月亮",
                      "segments": [{"it": "La luna riflette la luce del sole.", "zh": "月球反射阳光。"}]}
            spoken = []

            def fake_ask(image, question, history, follow_up, on_first):
                on_first(answer)
                return answer

            def record_segment(text, stop, provider):
                spoken.append(text)
                return None, "muted"

            self.app.client.ask = fake_ask
            self.app.speech.prepare_segment = record_segment
            self.app.speech.wait_timed = lambda *_: None
            self.app._start_ask(None, "月光从哪来？", False)
            self.until(lambda: not self.app.awaiting_answer and self.app.state == "idle")
            self.assertEqual(spoken, [answer["segments"][0]["it"]])
            self.assertEqual(self.app.captions.tracks[1].itemcget(self.app.captions.rows[1], "text"), answer["segments"][0]["zh"])
            self.assertIn(spoken[0], self.app.transcript[-1])

    def test_new_capture_cannot_import_the_previous_topic_from_the_archive(self):
        app = self.app
        app.cfg["save_history"] = True
        seen = []
        def answer(image, question, history, follow_up, on_first, context=None):
            seen.append((history, context))
            return ANSWER
        app.client.ask = answer
        with patch.object(app.archive, "handoff", return_value={"交接摘要": "旧图内容"}) as recall, \
             patch.object(app.archive, "relevant_notes", return_value=["旧笔记"]) as notes:
            app._send_capture(Image.new("RGB", (24, 24), "blue"))
            self.until(lambda: not app.awaiting_answer)
            app._send_capture(Image.new("RGB", (24, 24), "red"))
            self.until(lambda: not app.awaiting_answer)
            self.assertEqual(seen, [([], None), ([], None)])
            recall.assert_not_called()
            notes.assert_not_called()

    def test_typing_and_voice_leave_screenshot_topic_without_a_reset_phrase(self):
        app = self.app
        for voice in (False, True):
            app._send_capture(Image.new("RGB", (24, 24), "red"))
            self.until(lambda: not app.awaiting_answer)
            previous = app.topic
            app.show_input()
            app.input_var.set("具体怎么改？")
            app._placeholder_active = False
            app.capture_choice.invoke()
            app.submit_input()
            self.until(lambda: not app.awaiting_answer)
            self.assertTrue(self.calls[-1][3])
            self.assertIsNotNone(self.calls[-1][0])
            self.assertFalse(app.attach_capture.get())
            question = "今晚吃什么？"
            if voice:
                token = app.voice_turn = threading.Event()
                app._voice_result(token, question, "")
            else:
                app.show_input()
                app._entry_clear_placeholder()
                app.input_var.set(question)
                app.submit_input()
            self.until(lambda: not app.awaiting_answer)
            self.assertEqual(self.calls[-1], (None, question, [], False))
            self.assertIsNone(app.last_image)
            self.assertNotEqual(app.topic, previous)
            self.assertEqual(app.canvas.itemcget(app.capture_window, "state"), "hidden")

    def test_resize_panel_by_pixels_and_drag_without_changing_the_pet(self):
        app = self.app
        pet_size = (app.poses["wait"].width(), app.poses["wait"].height())
        with tempfile.TemporaryDirectory() as tmp, patch("pet.config.CONFIG_PATH", Path(tmp) / "cfg"):
            app.show_panel_size()
            dlg = app.dialog
            dlg.update()
            dlg.size.set("3 x 2")
            dlg.apply_button.invoke()
            dlg.update()
            self.assertIs(app.dialog, dlg)
            dlg.size.set("640 × 398")
            dlg.apply_button.invoke()
            self.until(lambda: app.dialog is None)
            self.assertTrue(app.panel_open)
            self.assertEqual((app.root.winfo_width(), app.root.winfo_height()), (640+app.PET_WIDTH, 398))
            app.show_input()
            app._entry_clear_placeholder()
            app.input_var.set("没写完的文字")
            app._show_content("A sentence. " * 120, "继续说。" * 120, duration=20)
            grip = app.root.nametowidget(app.canvas.itemcget(app.grip_window, "window"))
            grip.event_generate("<ButtonPress-1>", x=5, y=5, rootx=300, rooty=200)
            grip.event_generate("<B1-Motion>", x=50, y=35, rootx=345, rooty=230)
            grip.event_generate("<ButtonRelease-1>", x=55, y=41, rootx=350, rooty=236)
            app.root.update()
            self.assertEqual(app.cfg["panel_size"], {"width": 690, "height": 434})
            self.assertEqual(json.loads((Path(tmp)/"cfg").read_text("utf8"))["panel_size"], app.cfg["panel_size"])
            self.assertEqual(app.input_var.get(), "没写完的文字")
            self.assertIsNotNone(app.captions.timer)
            self.assertEqual(app.captions.winfo_width(), app.subtitle_width)
            self.assertEqual((app.poses["wait"].width(), app.poses["wait"].height()), pet_size)
            app._close_panel()
            app.root.update()
            self.assertEqual((app.root.winfo_width(), app.root.winfo_height()), (app.PET_WIDTH, 430))
            app._open_panel()
            app.root.update()
            self.assertEqual((app.root.winfo_width(), app.root.winfo_height()), (690+app.PET_WIDTH, 434))

    def test_gpu_renderer_is_reused_for_input_and_released_on_hide(self):
        app = self.app
        app.speech.provider, app.speech.device, app.speech.muted = "vits", "cuda", False
        with patch.object(app.speech, "warmup") as warmup, patch.object(app.speech, "close_renderer") as close:
            app.show_input()
            warmup.assert_called_once()
            timer = app.renderer_timer
            self.assertIsNotNone(timer)
            close.reset_mock()
            app.stop_speech(keep_idle=True)
            self.assertEqual(timer, app.renderer_timer)
            close.assert_not_called()
            app._hide_window()
            close.assert_called_once()
            self.assertIsNone(app.renderer_timer)

    def test_cpu_voice_stays_warm_while_chatting_and_releases_when_closed(self):
        app = self.app
        app.speech.provider, app.speech.device, app.speech.muted = "vits", "cpu", False
        with patch.object(app.speech, "warmup") as warmup, patch.object(app.speech, "close_renderer") as close:
            app.show_input()
            warmup.assert_called_once()
            app._cancel_renderer_timer()
            app._release_idle_renderer()
            self.assertIsNotNone(app.renderer_timer)
            close.assert_not_called()
            app._close_panel()
            close.assert_called_once()
            self.assertIsNone(app.renderer_timer)

    def test_compact_english_standby_wakes_on_open_and_clears_on_hide(self):
        app = self.app
        app.speech.provider, app.speech.muted = "vits", False
        app.speech.english_bundle = Path("fixture.safetensors")
        with patch.object(app.speech, "warmup") as warm, patch.object(app.speech, "close_renderer") as close:
            app._restore_window()
            warm.assert_called_once_with(standby=True)
            self.assertIsNotNone(app.standby_timer)
            app._open_panel()
            self.assertIsNone(app.standby_timer)
            self.assertEqual(warm.call_args.args, ())
            self.assertEqual(warm.call_args.kwargs, {})
            app._hide_window()
            self.assertIsNone(app.renderer_timer)
            close.assert_called_once()

    def test_lapland_poses_follow_a_request(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def delayed_ask(*args, **kwargs):
            entered.set()
            release.wait(4)
            return self.fake_model(*args, **kwargs)

        self.app.client.ask = delayed_ask
        self.gate.clear()
        self.app._send_capture(Image.new("RGB", (32, 32)))
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.app.pose, "curious")
        for seconds, expected in [(1, "ponder"), (3, "wait"), (7, "impatient")]:
            self.app.thinking_since = time.monotonic() - seconds
            self.until(lambda: self.app.pose == expected)
        release.set()
        self.until(lambda: self.app.pose == "smile")
        self.gate.set()
        self.until(lambda: self.app.pose == "victory")
        self.app._show_error("Unavailable", "暂时不可用")
        self.assertEqual(self.app.pose, "flustered")
        self.assertEqual(self.app.root.title(), "pet-Lapland")

    def test_early_stop_and_settings(self):
        self.gate.clear()
        self.select().send()
        self.until(lambda: self.app.stream_started)
        self.assertTrue(self.app.awaiting_answer)
        self.assertEqual(self.app.captions.tracks[1].itemcget(self.app.captions.rows[1], "text"), ANSWER["segments"][0]["zh"])
        self.app.begin_capture()
        self.assertFalse(self.app.capturing)
        self.app.stop_speech()
        self.gate.set()
        self.until(lambda: not self.app.awaiting_answer)
        self.assertIsNone(self.app.speech_stop)
        self.assertEqual(self.app.state, "idle")
        with tempfile.TemporaryDirectory() as tmp, patch("pet.config.CONFIG_PATH", Path(tmp) / "config.json"):
            self.app.show_settings()
            dialog = self.app.dialog
            dialog.fields["capture"].set(dialog.fields["ask"].get())
            dialog.apply()
            self.assertTrue(dialog.error.cget("text"))
            dialog.fields["capture"].set("ctrl+shift+f24")
            dialog.fields["confirm_capture"].set(False)
            dialog.fields["stream"].set(False)
            dialog.fields["speech_provider"].set("edge")
            dialog.apply()
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertNotIn("_api_key", saved)
            self.assertFalse(self.app.client.stream or saved["confirm_capture"])
            self.assertEqual(self.app.speech.provider, "edge")
            self.assertEqual(self.app.hotkeys.bindings["capture"], "ctrl+shift+f24")
            self.assertIn("Ctrl+Shift+F24", self.app.menu.entrycget(0, "label"))
            self.app.client.ask = self.fake_model
            with patch("pet.app.capture_region", return_value=Image.new("RGB", (32, 32))):
                self.app._capture_done((0, 0, 32, 32))
            self.until(lambda: not self.app.awaiting_answer)
            self.assertIsNone(self.app.dialog)


if __name__ == "__main__":
    unittest.main()
