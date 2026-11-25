import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from pet.history import ConversationHistory, asks_about_capture, recent_messages


class HistoryTest(unittest.TestCase):
    def test_capture_references_are_specific_to_the_question(self):
        for text in ("这张图里的红字怎么改？", "请解释一下截图里写的内容", "What's in this screenshot?",
                     "Cosa c’è nello screenshot?"):
            self.assertTrue(asks_about_capture(text), text)
        for text in ("今晚吃什么？", "教我怎么截屏", "如何上传图片？", "这张截图先不聊，我想问个别的",
                     "换个话题，这张图不用管了", "Where can I take a screenshot?"):
            self.assertFalse(asks_about_capture(text), text)

    def test_recall_is_scoped_to_the_current_exchange(self):
        history = [{"role": "user", "content": "车票怎么退？"}, {"role": "assistant", "content": "看订单。"},
                   {"role": "user", "content": "我的铅笔掉地上了。"}, {"role": "assistant", "content": "在桌脚旁。"}]
        def recent(q, screenshot=False, follow_up=False):
            return recent_messages(history, q, screenshot=screenshot, follow_up=follow_up)
        self.assertEqual(recent("这朵花叫什么？"), [])
        self.assertEqual(recent("还有呢？"), history[-2:])
        self.assertEqual(recent("铅笔是什么颜色？"), history[-2:])
        self.assertEqual(recent("上次说过什么？"), history)
        self.assertEqual(recent("这是什么？", True, False), [])
        self.assertEqual(recent("看它左边。", True, True), history)
        self.assertEqual(recent("换个话题，那你怎么看？", True, True), [])

    def test_recent_dialogue_keeps_the_spoken_words_and_technical_subtitle(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConversationHistory(Path(folder) / "history.db")
            store.append("a", "线索", "再说一次\n\nA clue · 线索\n\nTry port eighty.\n试试端口 80。", "再说一次")
            self.assertEqual(store.recent_context()[1][-1]["content"], "Try port eighty.\n试试端口 80。")

    def test_resume_legacy_and_roll_forward_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConversationHistory(Path(folder) / "history.db")
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY, topic TEXT,title TEXT,body TEXT,created TEXT)")
                db.execute("INSERT INTO turns VALUES(1,'a','旧版',?,CURRENT_TIMESTAMP)",
                           ("问题\n\n标题\n\nAnswer.\n旧版回答",))
            self.assertEqual(store.recent_context()[1][-1]["content"], "旧版回答")
            for index in range(1, 9):
                question = f"问题 {index}\n\n第二段"
                store.append("a", "标题", f"{question}\n\n标题\n\nAnswer.\n回答 {index}", question)
            revision, cursor, _, batch = store.summary_batch()
            self.assertEqual([i for i, _ in batch], list(range(1, 7)))
            summary = {"主要事项": ["约好了周五"]}
            self.assertTrue(store.commit_summary(revision, cursor, batch[-1][0], summary))
            self.assertFalse(store.commit_summary(revision, cursor, batch[-1][0], summary))
            self.assertIsNone(store.summary_batch())
            self.assertEqual(store.handoff("a"), {"交接摘要": summary})
            recent = store.recent_context()[1]
            self.assertEqual(len(recent), 6)
            self.assertEqual(recent[-2]["content"], question)
            self.assertEqual(recent[-1]["content"], "回答 8")
            store.delete("a")
            self.assertEqual(store.handoff("a"), {})
            self.assertFalse(store.commit_summary(revision + 1, batch[-1][0], 9, summary))

    def test_explicit_notes_survive_reopen_and_can_be_forgotten(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConversationHistory(Path(folder) / "history.db")
            note = store.requested_note("我的名字叫小林，请记住我的名字。")
            self.assertEqual(note, "我的名字叫小林")
            self.assertEqual(store.requested_note("请记住，我喜欢暖灰色。"), "我喜欢暖灰色")
            self.assertIsNone(store.requested_note("你还记得我的名字吗？"))
            store.remember(note)
            reopened = ConversationHistory(store.path)
            self.assertEqual(reopened.relevant_notes("我的名字是什么？"), [note])
            self.assertEqual(reopened.relevant_notes("我的车票多少钱？"), [])
            reopened.forget(reopened.memories()[0][0])
            self.assertEqual(store.memories(), [])

    def test_reopen_search_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new" / "history.sqlite3"
            store = ConversationHistory(path)
            self.assertEqual(store.conversations(), [])
            self.assertFalse(path.exists())
            store.append("a", "苹果", "苹果\nSeven apples.")
            store.append("b", "梨", "梨\nThree pears.")
            store.append("a", "追问", "追问\n一共十个。100%")
            reopened = ConversationHistory(path)
            self.assertEqual([row[:2] for row in reopened.conversations()], [("a", "苹果"), ("b", "梨")])
            for term in ("APPLES", "十个", "100%"):
                self.assertEqual([row[0] for row in reopened.conversations(term)], ["a"])
            turns = reopened.turns("a")
            self.assertEqual([body for _, body in turns], ["苹果\nSeven apples.", "追问\n一共十个。100%"])
            self.assertIn("a", [row[0] for row in reopened.conversations(turns[0][0][:10])])
            reopened.delete("a")
            self.assertEqual(reopened.turns("a"), [])
            self.assertEqual([row[0] for row in store.conversations()], ["b"])
