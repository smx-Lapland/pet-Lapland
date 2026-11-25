import json
import io
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request

from pet.config import apply_credentials, save_config
from pet.dsh_projects import project_context, projects
from pet.history import ConversationHistory
from pet.llm import DeepSeekClient
from pet.network import event_lines, open_api, read_body, validate_endpoint


class ConnectionsTest(unittest.TestCase):
    def test_only_secure_or_loopback_endpoints_and_bounded_responses(self):
        for base in ("https://example.org/v1", "http://localhost:11434/v1", "http://[::1]:8000"):
            validate_endpoint(base)
        for base in ("http://example.org", "http://localhost.example.org", "https://user:pass@example.org",
                     "https://example.org/?token=value", "file:///example", "http://192.0.2.1"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                validate_endpoint(base)
        self.assertEqual(read_body(io.BytesIO(b"1234"), 4), b"1234")
        with self.assertRaises(ValueError):
            read_body(io.BytesIO(b"12345"), 4)
        with self.assertRaises(ValueError):
            list(event_lines(io.BytesIO(b"a" * (256 * 1024 + 1)), 10))

    def test_redirect_does_not_forward_request_or_credentials(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/elsewhere")
                self.end_headers()

            def do_GET(self):
                seen.append(self.path)
                self.send_response(204)
                self.end_headers()

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                req = Request(f"http://127.0.0.1:{server.server_port}/chat", data=b"test")
                req.add_unredirected_header("Authorization", "Bearer fixture")
                with self.assertRaises(HTTPError) as exc:
                    open_api(req, timeout=2)
                exc.exception.close()
                self.assertEqual(seen, ["/chat"])
            finally:
                server.shutdown()

    def test_project_session_membership_recency_and_empty_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DSH_HOME": tmp}):
            storage = Path(tmp) / "storages"
            cache = storage / "session_projcache/sessions"
            cache.mkdir(parents=True)
            first, recent, archived, foreign = ["session-" + str(uuid4()) for _ in range(4)]
            root = Path(tmp) / "one"
            for order, sid in enumerate((first, recent, archived, foreign)):
                response = "From " + str(root) + "; Bearer example-only-value"
                record = {"identity": {"cwd": str(root)}, "rows": {
                    "title": {"val": sid}, "sessionListMetadata": {"val": {"lastPromptAt": order}},
                    "turnOutline": {"val": {"turns": [{"prompt": str(i), "response": response} for i in range(6)]}}}}
                (cache / (sid + ".json")).write_text(json.dumps({"record": record}), "utf-8")
            db = {"global": {"workspaceIds": ["a", "b"], "archivedSessionIds": [archived]},
                  "tables": {"workspaces": {"a": {"path": str(root), "sessionIds": [first, recent, archived]},
                                             "b": {"path": str(Path(tmp) / "two"), "sessionIds": [foreign]}}}}
            (storage / "workspace.json").write_text(json.dumps(db), "utf-8")
            self.assertEqual(len(projects()), 2)
            found = project_context("a")
            self.assertEqual(found["项目"], "one")
            self.assertEqual(found["最近对话摘录"][0]["回答"], "From [本地目录]; Bearer [已隐藏]")
            self.assertNotIn("工作目录", found)
            self.assertEqual(found["DSH 会话"], recent)
            self.assertEqual([t["问题"] for t in found["最近对话摘录"]], ["3", "4", "5"])
            self.assertEqual(project_context("a", first)["DSH 会话"], first)
            self.assertEqual(project_context("b")["最近对话摘录"], [])
            for sid in (archived, foreign):
                with self.assertRaises(ValueError):
                    project_context("a", sid)
            with self.assertRaises(ValueError):
                project_context("deleted")

    def test_registered_empty_session_is_selectable(self):
        from pet.dsh_projects import sessions
        sid = "session-" + str(uuid4())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DSH_HOME": tmp}):
            store = Path(tmp) / "storages"
            cache = store / "session_projcache/sessions"
            cache.mkdir(parents=True)
            (store / "workspace.json").write_text(json.dumps({"global": {"workspaceIds": ["sample"]},
                "tables": {"workspaces": {"sample": {"path": tmp, "sessionIds": [sid]}}}}))
            (cache / (sid + ".json")).write_text(json.dumps({"record": {"identity": {"cwd": tmp},
                "rows": {"title": {"val": "New session"}}}}))
            self.assertEqual(sessions(projects()["sample"])[0][1], sid)
            self.assertEqual(project_context("sample", sid)["最近对话摘录"], [])

    def test_two_compatible_connections_and_summary(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, obj):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(obj).encode())

            def do_GET(self):
                self.reply({"data": [{"id": "fixture-a"}, {"id": "fixture-b"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers.get("Authorization"), body))
                text = ({"主要事项": ["test"], "次要细节": [], "未完成事项": []} if len(body["messages"]) == 2
                        and "交接记录员" in body["messages"][0]["content"] else
                        {"segments": [{"en": "[NEUTRAL] It's ready.", "zh": "准备好了。"}]})
                self.reply({"choices": [{"message": {"content": json.dumps(text, ensure_ascii=False)}}]})

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "official-test", "LOCAL_MODEL_KEY": "custom-test"
        }), tempfile.TemporaryDirectory() as tmp:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                for name, env, auth in [("fixture-a", "", None), ("fixture-b", "LOCAL_MODEL_KEY", "Bearer custom-test")]:
                    cfg = {"api_base": f"http://127.0.0.1:{server.server_port}/v1", "model": name,
                           "model_key_env": env, "_api_key": "official-test", "dsh_project": "p"}
                    apply_credentials(cfg)
                    engine = DeepSeekClient(cfg)
                    self.assertTrue(engine.available)
                    self.assertIn(name, engine.list_models())
                    with patch("pet.llm.project_context", return_value={"项目": "fixture-project"}) as project:
                        engine.ask(None, "hello")
                        project.assert_not_called()
                        self.assertNotIn("fixture-project", str(received[-1][-1]["messages"]))
                        answer = engine.ask(None, "当前项目的进度呢？")
                    self.assertEqual(answer["segments"][0]["emotion"], "NEUTRAL")
                    path, header, body = received[-1]
                    self.assertEqual((path, header, body["model"]), ("/v1/chat/completions", auth, name))
                    self.assertIn("fixture-project", str(body["messages"]))
                    self.assertNotIn("reasoning_effort", body)
                    self.assertEqual(engine.summarize({}, "test")["主要事项"], ["test"])
                    with patch("pet.config.CONFIG_PATH", Path(tmp) / "saved.json"):
                        save_config(cfg)
                    saved = (Path(tmp) / "saved.json").read_text("utf-8")
                    self.assertNotIn("official-test", saved)
                    self.assertNotIn("custom-test", saved)
            finally:
                server.shutdown()

    def test_project_memory_isolation_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            first = ConversationHistory.for_scope("a", "")
            first.remember("only A")
            first.append("topic", "title", "ask\n\nTitle\n\nOK\n答", "ask")
            self.assertEqual(ConversationHistory.for_scope("a", "").recent_context()[0], "topic")
            for scope in [("b", ""), ("", ""), ("a", "session2")]:
                other = ConversationHistory.for_scope(*scope)
                self.assertEqual(other.memories(), [])
                self.assertIsNone(other.recent_context()[0])
