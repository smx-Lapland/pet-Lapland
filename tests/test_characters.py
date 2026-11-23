import io
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

    def test_dsh_tasks_require_registered_directory(self):
        with patch("pet.dsh_tasks.projects", return_value={}):
            with self.assertRaises(ValueError):
                execute("missing", "", Task("status"))
