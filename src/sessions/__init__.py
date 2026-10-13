from __future__ import annotations

"""`src.sessions` 包: 会话生命周期、注册表与事件。"""

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
