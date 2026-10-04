from __future__ import annotations

"""`src.sessions` 包: 会话生命周期、注册表与事件 (合并计划 §8 / M4)。

| 模块 | 职责 |
|---|---|
| `events.py` | 事件序号、有界可重放日志、缺口上报、事件→会话消息映射 |
| `controller.py` | `Session`: 身份、状态机、事件出口、interrupt 簿记、关闭时序 |
| `runtime.py` | 研究线程的归属资源: 按线程的日志桥、会话级用量、运行身份作用域、停止哨兵 |
| `store.py` | 活跃会话注册表、会话落盘、批关闭 |

**不建图**: "哪张图服务这个会话"由入口层决定并注入 (`server._make_session`),
本包因此可以脱离 FastAPI 与 LangGraph 单独测试。`sessions/` 下不得 import FastAPI。
"""

from src.sessions.controller import EVENT_LOG_LIMIT, Session
from src.sessions.events import (
    DEFAULT_LOG_LIMIT,
    EventLog,
    accepts_message,
    event_to_message,
    replay_gap_note,
)
from src.sessions.runtime import (
    STOP,
    StdoutBridge,
    bind_stdout_bridge,
    unbind_stdout_bridge,
)
from src.sessions.store import (
    SESSIONS,
    final_summary,
    persist_session,
    session_record,
    shutdown_sessions,
)

__all__ = [
    "DEFAULT_LOG_LIMIT",
    "EVENT_LOG_LIMIT",
    "SESSIONS",
    "STOP",
    "EventLog",
    "Session",
    "StdoutBridge",
    "accepts_message",
    "bind_stdout_bridge",
    "event_to_message",
    "final_summary",
    "persist_session",
    "replay_gap_note",
    "session_record",
    "shutdown_sessions",
    "unbind_stdout_bridge",
]
