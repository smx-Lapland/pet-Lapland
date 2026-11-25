import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pet.characters import character, image_files, import_pack, write_pack
from pet.dsh_tasks import Task, execute
from pet.llm import DeepSeekClient


class CharacterTest(unittest.TestCase):
    def test_lapland_chat_uses_examples_for_the_selected_language(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, LOCALAPPDATA=tmp):
            builtin = character("lapland")
            pack = Path(tmp) / "lapland.petpack"
            write_pack(pack, builtin.data, {"portrait.png": builtin.sprite("wait")})
            installed = import_pack(pack)
            for role in (builtin, installed):
                for lang in ("en", "it"):
                    with self.subTest(role=role.id, language=lang):
                        client = DeepSeekClient({"character_id": role.id, "_api_key": "test", "speech_language": lang})
                        line = {"en": "Your move.", "it": "Tocca a te."}[lang]
                        content = json.dumps({lang: "[MOCKING] " + line, "zh": "轮到你了。"})
                        with patch.object(client, "_post", return_value={"choices": [{"message": {"content": content}}]}) as post:
                            answer = client.ask(None, "换你啦")
                        payload = post.call_args.args[0]
                        messages = payload["messages"]
                        prompt = messages[0]["content"]
                        self.assertEqual(payload["thinking"], {"type": "disabled"})
                        self.assertNotIn("reasoning_effort", payload)
                        self.assertIn(builtin.data["persona"].splitlines()[0], prompt)
                        self.assertEqual("【英语原声对白范例】" in prompt, lang == "en")
                        self.assertNotIn("Heh. Ciao, Doctor.", prompt)
                        self.assertEqual("【意大利语对白范例】" in prompt, lang == "it")
                        self.assertNotIn('示范 {"user"', prompt)
                        self.assertIn("换你啦", messages[-1]["content"])
                        self.assertEqual(payload["temperature"], .8)
                        self.assertEqual([m["role"] for m in messages[1:-1]], ["user", "assistant"] * 6)
                        self.assertIn("Italiano parlato" if lang == "it" else "Spoken English", messages[-1]["content"])
                        for example in messages[2:-1:2]:
                            fields = json.loads(example["content"])
                            self.assertEqual(list(fields), [lang, "zh", "headline_" + lang, "headline_zh"])
                            self.assertTrue(fields[lang].startswith("["))
                        self.assertEqual(answer["segments"], [{lang: line, "zh": "轮到你了。", "emotion": "MOCKING"}])

    def test_original_lines_are_available_on_request_and_screen_has_style_reminder(self):
        from PIL import Image
        client = DeepSeekClient({"_api_key": "fixture"})
        with patch.object(client, "_post", return_value={"choices": [{"message": {
                "content": json.dumps({"en": "[LOW] Hello."})}}]}) as post:
            answer = client.ask(Image.new("RGB", (8, 8)), "按原版问候说一遍")
        messages = post.call_args.args[0]["messages"]
        self.assertIn("Heh. Ciao, Doctor.", messages[0]["content"])
        self.assertIn("Spoken English", messages[-1]["content"][0]["text"])
        self.assertEqual(messages[-1]["content"][1]["type"], "image_url")
        self.assertEqual(answer["segments"][0]["emotion"], "WHISPER")

    def test_share_import_and_use_new_persona(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            role = character("muelsyse")
            data = {**role.data, "settings": {**role.data["settings"], "_api_key": "never-export",
                                              "model_key_env": "MY_CHAT_KEY", "tts_key_env": "MY_VOICE_KEY",
                                              "dsh_project": "private-id"}}
            pack = Path(tmp) / "one.petpack"
            write_pack(pack, data, image_files(role))
            with zipfile.ZipFile(pack) as z:
                self.assertEqual(set(z.namelist()), {"character.json", "portrait.png"})
                manifest = z.read("character.json")
                self.assertNotIn(b"never-export", manifest)
                self.assertNotIn(b"private-id", manifest)
            installed = import_pack(pack)
            self.assertEqual(installed.data["settings"]["model_key_env"], "")
            self.assertEqual(installed.data["settings"]["tts_key_env"], "")
            own = import_pack(pack, local_copy=True)
            self.assertEqual(own.data["settings"]["tts_key_env"], "MY_VOICE_KEY")
            self.assertEqual(installed.sprite("smile"), installed.sprite("wait"))
            self.assertEqual(installed.data["tasks"], role.data["tasks"])
            client = DeepSeekClient({"character_id": installed.id, "_api_key": "test"})
            with patch.object(client, "_post", return_value={"choices": [{"message": {"content": json.dumps({
                "segments": [{"en": "Ready.", "zh": "好了。"}]})}}]}) as post:
                client.ask(None, "hello")
                prompt = post.call_args.args[0]["messages"][0]["content"]
                self.assertIn("缪尔赛思", prompt)
                self.assertNotIn("Siracusan", prompt)
                self.assertNotIn("拉普兰德", prompt)
                self.assertNotIn("Spoken English.", post.call_args.args[0]["messages"][-1]["content"])

    def test_only_unedited_stock_lapland_pack_inherits_updated_dialogue(self):
        old = "An old stock persona\nwith two lines"
        fingerprint = hashlib.sha256(old.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, LOCALAPPDATA=tmp), \
                patch("pet.characters._STOCK_LAPLAND_PERSONAS", {fingerprint}):
            role = character()
            p = Path(tmp) / "test.petpack"
            custom = old + " and my own changes"
            for persona, expected in ((old.replace("\n", "\r\n"), role.data["persona"].strip()), (custom, custom)):
                write_pack(p, {**role.data, "persona": persona}, {"portrait.png": role.sprite("wait")})
                loaded = import_pack(p)
                self.assertEqual(loaded.data["persona"], expected)

    def test_invalid_pack_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as z:
                z.writestr("../injected", "bad")
                z.writestr("character.json", "{}")
                z.writestr("portrait.png", b"bad")
            raw.seek(0)
            with self.assertRaises(ValueError):
                import_pack(raw)
            self.assertFalse(list(Path(tmp).rglob("character.json")))
            self.assertFalse(list(Path(tmp).rglob("injected")))

    def test_english_weight_file_must_be_a_voice_tensor_bundle(self):
        from pet.gpt_sovits_en import BUNDLE
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, LOCALAPPDATA=tmp):
            role = character("lapland")
            pack = Path(tmp) / "role.petpack"
            assets = image_files(role)
            assets.pop(BUNDLE, None)
            write_pack(pack, role.data, assets)
            with zipfile.ZipFile(pack, "a") as z:
                z.writestr(BUNDLE, b"invalid voice weights")
            with self.assertRaises(ValueError):
                import_pack(pack)
            self.assertFalse(list(Path(tmp).rglob("character.json")))

    def test_dsh_tasks_require_registered_directory(self):
        with patch("pet.dsh_tasks.projects", return_value={}):
            with self.assertRaises(ValueError):
                execute("missing", "", Task("status"))
