from __future__ import annotations

"""会话记忆: 记录最近一次研究的主题与已完成阶段。

用于支持"继续撰写"类指令: 用户说「结合已有的数据和报告，撰写综述」而未指明
主题时, Planner 从会话记忆里取回上次的主题, 复用其检索缓存。
"""

import json
import logging
from datetime import datetime

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

MEMORY_FILE = DATA_DIR / "session_memory.json"


def save_session_memory(topic: str, stages: list[str] | None = None) -> None:
    """保存会话记忆 (最近一次研究的主题与已完成阶段)。"""
    topic = (topic or "").strip()
    if not topic:
        return
    payload = {
        "topic": topic,
        "stages": list(stages or []),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"会话记忆保存失败: {e}")


def load_session_memory() -> dict | None:
    """加载会话记忆; 无记忆返回 None。"""
    try:
        if not MEMORY_FILE.exists():
            return None
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
