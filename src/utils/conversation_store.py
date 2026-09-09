from __future__ import annotations

"""对话记录落盘: 把每个会话的完整对话 (消息 / 节点进度 / 产物摘要) 持久化到
data/conversations/{session_id}.json, 供关闭服务/浏览器后回看历史对话。

与 session_memory.py 的区别: session_memory 只记"最近一次研究的主题/阶段"供 Planner
续写复用; 本模块记"完整对话流水"供前端历史回看 (Phase 1) 与断点续跑 (Phase 2)。
"""

import json
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR

CONVERSATIONS_DIR = DATA_DIR / "conversations"


def _path(session_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{session_id}.json"


def save_conversation(session_id: str, record: dict) -> None:
    """写入 (或更新) 一条会话记录, 自动补 session_id 与 updated_at。"""
    data = dict(record)
    data.setdefault("session_id", session_id)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    _path(session_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def list_conversations() -> list[dict]:
    """列出所有会话 (按更新时间倒序), 只返回元信息, 不含完整消息。"""
    if not CONVERSATIONS_DIR.exists():
        return []
    items: list[dict] = []
    for f in sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "session_id": data.get("session_id", f.stem),
            "thread_id": data.get("thread_id", ""),
            "topic": data.get("topic", ""),
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
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
