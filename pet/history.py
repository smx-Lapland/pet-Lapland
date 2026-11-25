from __future__ import annotations

import os
import hashlib
import sqlite3
import re
import json
from contextlib import closing
from pathlib import Path


def token_estimate(text):
    ascii_count = sum(c.isascii() for c in text)
    return (ascii_count + 3) // 4 + len(text) - ascii_count


_COMMON_WORDS = frozenset((
    "我的 你的 这个 那个 一个 一下 什么 怎么 如何 可以 觉得 现在 时候 我们 你们 的是 的吗 帮我 看看 一点 告诉 我想 我是 这是 有点 请问 帮忙 "
    "the is are was were what why how my your about this that and for you can me do to of in it i a does please "
    "il la lo le un una di si per che come mi tu io"
).split())


def _terms(text):
    text = text.casefold()
    words = set(re.findall(r"[a-zà-öø-ÿ0-9]+", text) + re.findall(r"(?=([\u3400-\u9fff]{2}))", text))
    return words - _COMMON_WORDS


def changes_subject(question):
    return bool(re.search(r"换(?:个|一个)?话题|另一个问题|聊(?:点|些)别的|别提(?:刚才|上次)|不要(?:再)?(?:联系|参考|回顾)(?:之前|上次)|"
                          r"(?:别|不想|不用|不要|不再|先不)(?:再)?(?:看|说|聊|提|管|参考)(?:刚才|之前|上|这|那|张|个|的)*(?:截图|图片|画面|图)|"
                          r"(?:图片?|截图|画面)(?:先)?(?:不用|不看|别管|不说|不聊)|"
                          r"\b(?:new topic|change the subject|ignore (?:that|the) screenshot|cambiamo (?:argomento|discorso))\b", question, re.I))


def asks_about_capture(question):
    """Only attach a saved selection when the question explicitly points to it."""
    if changes_subject(question):
        return False
    return bool(re.search(
        r"(?:这|那|上|刚才的)(?:一?张|个|幅)?(?:截图|图片|图像|画面|选区|图(?!书))|"
        r"(?:截图|图片|画面|选区|^图)(?:里|中)|(?:截图|图片|画面|选区|^图)上(?:面|方|的|有|是|写|显示|说|标|画|第)|^追问截图|"
        r"\b(?:in|on|from|look at|explain|what does) (?:this|that|the|my|the last) (?:screenshot|image|selection|picture)\b|"
        r"^\s*(?:what|which) colou?r (?:remains|is that)\b|\b(?:nello screenshot|nell.immagine|in questa immagine|nella schermata)\b",
        question, re.I))


def requests_recall(question):
    return bool(re.search(r"还记得|回顾|记得.{0,8}(?:说过|提过|聊过)|(?:上次|之前|以前|先前|刚才).{0,8}(?:说|聊|提|谈|约定)|"
                          r"\b(?:last time|do you remember|we discussed|ti ricordi|l.altra volta)\b", question, re.I))


def recent_messages(history, question, *, screenshot, follow_up):
    if changes_subject(question):
        return []
    if screenshot:
        return history[-6:] if follow_up else []
    if requests_recall(question):
        return history[-6:]
    pair = history[-2:]
    refers_back = re.search(r"^(?:那(?:么|呢|你呢|怎么办|然后)|所以|然后呢|为什么[呢呀？?]*$|继续|再说|还有呢|对了刚才|它|他呢|她呢|确实|没错|"
                            r"(?:why|perché)[?!.\s]*$|\b(?:go on|tell me more|what about that|and then|e poi|continua)\b)",
                            question, re.I)
    if refers_back or _terms(question) & _terms(" ".join(m["content"] for m in pair)):
        return pair
    return []


def compact_turn(body, length, *, include_spoken=False):
    question, _, rest = body.partition("\n\n")
    if length is not None:
        question, rest = body[:length], body[length:].lstrip("\n")
    # Archive paragraphs contain spoken text followed by its Chinese translation.
    paragraphs = rest.split("\n\n")
    if include_spoken and " · " in paragraphs[0] and len(paragraphs) > 1:
        return question, "\n".join(paragraphs[1:]).strip()
    answer = " ".join(part.strip().rsplit("\n", 1)[-1] for part in (paragraphs[1:] or paragraphs) if part.strip())
    return question, answer or rest


class ConversationHistory:
    """Store completed text turns separately from the model's active context."""

    def __init__(self, path: Path | None = None):
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        self.path = path or local / "Mochi" / "history.sqlite3"

    @classmethod
    def for_scope(cls, project, session):
        store = cls()
        if project:
            identity = hashlib.sha256(f"{project}/{session}".encode("utf-8")).hexdigest()
            store.path = store.path.parent / "projects" / (identity + ".sqlite3")
        return store

    def _ensure_tables(self, db):
        db.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, topic TEXT NOT NULL, title TEXT NOT NULL,
                    body TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        db.execute("CREATE INDEX IF NOT EXISTS turns_topic ON turns (topic, id)")
        db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, note TEXT NOT NULL UNIQUE)")
        db.execute("""CREATE TABLE IF NOT EXISTS rollup (
            id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER, through_id INTEGER, summary TEXT)""")
        db.execute("INSERT OR IGNORE INTO rollup VALUES(1,0,0,'{}')")

    def append(self, topic: str, title: str, body: str, question: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_tables(db)
            if "question_len" not in {row[1] for row in db.execute("PRAGMA table_info(turns)")}:
                db.execute("ALTER TABLE turns ADD COLUMN question_len INTEGER")
            db.execute("INSERT INTO turns (topic, title, body, question_len) VALUES (?, ?, ?, ?)",
                       (topic, title, body, len(question) if question is not None else None))

    def recent_context(self):
        if not self.path.is_file():
            return None, [], []
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
            if not columns:
                return None, [], []
            size = "question_len" if "question_len" in columns else "NULL"
            rows = db.execute(f"""SELECT topic, body, {size} FROM turns
                WHERE topic=(SELECT topic FROM turns ORDER BY id DESC LIMIT 1)
                ORDER BY id DESC LIMIT 3""").fetchall()
        messages, transcript = [], []
        for _, body, length in reversed(rows):
            question, reply = compact_turn(body, length, include_spoken=True)
            transcript.append(body)
            if question and reply:
                messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": reply}])
        return rows[0][0] if rows else None, messages, transcript

    def memories(self):
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
                return []
            return db.execute("SELECT id, note FROM memories ORDER BY id DESC").fetchall()

    def relevant_notes(self, question):
        asked = _terms(question)
        scored = [(len(_terms(note) & asked), note_id, note) for note_id, note in self.memories()]
        result, remaining = [], 480
        for score, _, note in sorted(scored, reverse=True)[:2]:
            if not score:
                break
            cost = token_estimate(note)
            if cost <= remaining:
                result.append(note)
                remaining -= cost
            elif not result:
                result.append(note[:remaining] + "…")
                break
        return result

    def remember(self, note):
        note = note.strip()
        if not note:
            raise ValueError("请输入要记住的事项。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            self._ensure_tables(db)
            db.execute("INSERT OR IGNORE INTO memories(note) VALUES (?)", (note,))

    def forget(self, note_id):
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute("DELETE FROM memories WHERE id=?", (note_id,))

    def handoff(self, topic, recent_count=3):
        if not self.path.is_file():
            return {}
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.execute("BEGIN")
            columns = {r[1] for r in db.execute("PRAGMA table_info(turns)")}
            if not columns:
                return {}
            checkpoint, summary = 0, "{}"
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='rollup'").fetchone():
                checkpoint, summary = db.execute("SELECT through_id,summary FROM rollup WHERE id=1").fetchone()
            size = "question_len" if "question_len" in columns else "NULL"
            rows = db.execute(f"""SELECT body,{size} FROM turns WHERE id>? AND id NOT IN
                (SELECT id FROM turns WHERE topic=? ORDER BY id DESC LIMIT ?) ORDER BY id DESC LIMIT 16""",
                              (checkpoint, topic, recent_count))
            pending, remaining = [], 850
            for body, length in rows:
                q, a = compact_turn(body, length)
                item = q + "\n" + a
                if token_estimate(item) > remaining:
                    while item and token_estimate(item) > remaining:
                        item = item[:max(0, int(len(item) * .8))]
                    if item:
                        pending.append(item + "…")
                    break
                pending.append(item)
                remaining -= token_estimate(item)
        return {k: v for k, v in (("交接摘要", json.loads(summary)), ("近期补充", pending[::-1])) if v}

    def summary_batch(self):
        """Return whole, previously unseen turns, keeping the latest three out of the rollup."""
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.execute("BEGIN")
            revision, cursor, summary = db.execute("SELECT revision,through_id,summary FROM rollup WHERE id=1").fetchone()
            rows = db.execute("""SELECT id,body,question_len FROM turns WHERE id>?
                AND id < (SELECT id FROM turns ORDER BY id DESC LIMIT 1 OFFSET 2)
                ORDER BY id LIMIT 32""", (cursor,))
            batch, cost, full = [], 0, False
            for row_id, body, length in rows:
                q, a = compact_turn(body, length)
                item = f"用户：{q}\n回答：{a}"
                size = token_estimate(item)
                if batch and cost + size > 2400:
                    full = True
                    break
                batch.append((row_id, item))
                cost += size
        if batch and (full or cost >= 800 or len(batch) >= 6):
            return revision, cursor, json.loads(summary), batch
        return None

    def commit_summary(self, revision, cursor, through, summary):
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            return db.execute("""UPDATE rollup SET revision=revision+1,through_id=?,summary=?
                WHERE id=1 AND revision=? AND through_id=?""",
                (through, json.dumps(summary, ensure_ascii=False), revision, cursor)).rowcount == 1

    @staticmethod
    def requested_note(text):
        text = text.strip().strip("。.!！")
        suffix = re.fullmatch(r"(.+?)[，,。；;！!]\s*(?:请|請|帮我|幫我)?(?:记住|記住)"
                              r"(?:这个|這個|这件事|這件事|我的名字|我的偏好)?", text, re.S)
        if suffix:
            return suffix.group(1).strip()
        match = re.fullmatch(
            r"(?:请(?:你)?|請(?:你)?|帮我|幫我)?(?:记住|記住|记一下|記一下|记下来|記下來)"
            r"[\s，,:：]*([^?？]+)|(?:please\s+)?remember\s+(?:that\s+)?([^?？]+)",
            text, re.I | re.S)
        if match:
            return (match.group(1) or match.group(2)).strip(" ，,:：。.!！")
        return None

    def conversations(self, query: str = "") -> list[tuple]:
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            return db.execute("""
                SELECT topics.topic, first.title, datetime(last.created, 'localtime')
                FROM (
                    SELECT topic, MIN(id) AS first_id, MAX(id) AS last_id
                    FROM turns GROUP BY topic
                    HAVING MAX(instr(lower(body), lower(?))) > 0
                        OR MAX(instr(date(created, 'localtime'), ?)) > 0
                ) topics
                JOIN turns first ON first.id = topics.first_id
                JOIN turns last ON last.id = topics.last_id
                ORDER BY topics.last_id DESC
            """, (query, query)).fetchall()

    def turns(self, topic: str) -> list[tuple]:
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            return db.execute("""
                SELECT datetime(created, 'localtime'), body FROM turns WHERE topic = ? ORDER BY id
            """, (topic,)).fetchall()

    def delete(self, topic: str) -> None:
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            self._ensure_tables(db)
            db.execute("DELETE FROM turns WHERE topic = ?", (topic,))
            db.execute("UPDATE rollup SET revision=revision+1, through_id=0, summary='{}' WHERE id=1")
