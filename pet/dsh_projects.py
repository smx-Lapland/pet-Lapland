"""Read registered DSH workspaces and their cached conversation outlines."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .config import dsh_home


def _json(path):
    try:
        value = json.loads(path.read_text("utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    path: Path
    sessions: tuple[str, ...]


def projects():
    db = _json(dsh_home() / "storages/workspace.json")
    state = db.get("global", {})
    rows = db.get("tables", {}).get("workspaces", {})
    archived = set(state.get("archivedSessionIds", []))
    result = {}
    for key in state.get("workspaceIds", []):
        row = rows.get(key, {})
        if not row.get("path"):
            continue
        path = Path(row["path"])
        ids = tuple(s for s in row.get("sessionIds", []) if isinstance(s, str) and s not in archived)
        result[key] = Project(key, row.get("title") or str(path), path, ids)
    return result


def sessions(project):
    result = []
    for sid in project.sessions:
        try:
            UUID(sid.removeprefix("session-"))
        except ValueError:
            continue
        record = _json(dsh_home() / "storages/session_projcache/sessions" / (sid + ".json")).get("record", {})
        identity = record.get("identity", {})
        if not identity.get("cwd") or Path(identity["cwd"]) != project.path:
            continue
        rows = record.get("rows", {})
        turns = rows.get("turnOutline", {}).get("val", {}).get("turns", [])
        finished = [t for t in turns if isinstance(t, dict) and isinstance(t.get("response"), str)
                    and t["response"].strip()]
        if finished:
            title = rows.get("title", {}).get("val") or sid
            updated = rows.get("sessionListMetadata", {}).get("val", {}).get("lastPromptAt")
            result.append((updated or identity.get("createdAt") or 0, sid, title, finished))
    return sorted(result, key=lambda item: item[0], reverse=True)


def project_context(project_id, session_id=""):
    project = projects().get(project_id)
    if project is None:
        raise ValueError("所选项目已从 DSH 移除。请重新选择项目。")
    # Only the local subprocess needs the workspace's absolute directory.
    private_roots = (str(project.path), str(Path.home()), str(dsh_home()))
    def excerpt(text, limit):
        for root in private_roots:
            for spelling in {root, root.replace("\\", "/")}:
                text = re.sub(re.escape(spelling), "[本地目录]", text, flags=re.IGNORECASE)
        text = re.sub(r"(?i)\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{16,}|github_pat_[\w]{16,})", "[已隐藏]", text)
        text = re.sub(r"(?i)\b(Bearer\s+)[\w./+=~-]+", r"\1[已隐藏]", text)
        return text[:limit]

    title = project.path.name if project.title == str(project.path) else project.title
    info = {"项目": excerpt(title, 160)}
    choices = sessions(project)
    item = next((row for row in choices if not session_id or row[1] == session_id), None)
    if item:
        info["DSH 会话"] = excerpt(str(item[2]), 160)
        info["最近对话摘录"] = [
            {"问题": excerpt(str(t.get("prompt") or ""), 350), "回答": excerpt(t["response"], 650)} for t in item[3][-3:]]
    elif session_id:
        raise ValueError("所选 DSH 会话暂无可用记录。请在「模型与项目」中重新选择。")
    else:
        info["最近对话摘录"] = []
    return info
