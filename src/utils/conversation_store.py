from __future__ import annotations

"""对话记录落盘。新会话存于 data/conversations/{session_id}/conversation.json。

旧版平铺 JSON 仍可读取；下一次保存时写入会话目录。

与 session_memory.py 的区别: session_memory 只记近期研究摘要供续写复用；
本模块保存完整消息和输入快照，供历史回看与断点续跑。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from src.utils.conversation_layout import conversation_dir


def _root() -> Path:
    from src import config

    return config.DATA_DIR / "conversations"


def _path(session_id: str) -> Path:
    return conversation_dir(session_id) / "conversation.json"


def _legacy_path(session_id: str) -> Path:
    return _root() / f"{session_id}.json"


def save_conversation(session_id: str, record: dict) -> None:
    """写入 (或更新) 一条会话记录, 自动补 session_id 与 updated_at。"""
    data = dict(record)
    data.setdefault("session_id", session_id)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _path(session_id).parent.mkdir(parents=True, exist_ok=True)
    _path(session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def list_conversations() -> list[dict]:
    """列出所有会话 (按更新时间倒序), 只返回元信息, 不含完整消息。"""
    root = _root()
    if not root.exists():
        return []
    items: list[dict] = []
    files = {f.stem: f for f in root.glob("*.json")}
    files.update({p.name: p / "conversation.json" for p in root.iterdir()
                  if p.is_dir() and (p / "conversation.json").is_file()})
    for f in sorted(files.values(), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "session_id": data.get("session_id", f.parent.name if f.name == "conversation.json" else f.stem),
            "thread_id": data.get("thread_id", ""),
            "topic": data.get("topic", ""),
            # `project_id` 是**共享资料的所有权判据**: 删除会话前要据此确认没有其他
            # 会话引用同一项目的向量库/缓存 (计划书 P0-4), 否则会误删别人的检索资料。
            "project_id": data.get("project_id", "") or (data.get("request") or {}).get("project_id", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "status": data.get("status", ""),
            "summary": data.get("summary", {}),
            "message_count": len(data.get("messages", [])),
        })
    return items


def load_conversation(session_id: str) -> dict | None:
    """读取单条会话记录 (含完整消息); 不存在返回 None。"""
    p = _path(session_id)
    if not p.exists():
        p = _legacy_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_conversation(session_id: str) -> bool:
    """删除会话目录及兼容期旧记录；不碰共享资料库或用户原始文献。"""
    directory = conversation_dir(session_id)
    legacy = _legacy_path(session_id)
    if not directory.exists() and not legacy.exists():
        return False
    try:
        if directory.exists():
            shutil.rmtree(directory)
        if legacy.exists():
            legacy.unlink()
        return True
    except Exception:
        return False
