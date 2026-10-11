"""Paths owned by one web conversation.

Manual source libraries remain independent; only material acquired during a
conversation belongs here.
"""

import re
from pathlib import Path


def conversation_dir(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id or ""):
        raise ValueError("Invalid conversation ID")
    from src import config

    return config.DATA_DIR / "conversations" / session_id


def conversation_topic(session_id: str) -> str:
    """Reserved KB identity for a web conversation."""
    conversation_dir(session_id)  # Validate before using it in a topic.
    return f"conversation-{session_id}"


def session_id_from_topic(topic: str) -> str | None:
    # Web session IDs are UUID4 hex strings. Do not reinterpret a user-named
    # source library as a conversation merely because it shares a prefix.
    match = re.fullmatch(r"conversation-([0-9a-f]{32})", topic or "")
    return match.group(1) if match else None


def literature_dir(session_id: str) -> Path:
    return conversation_dir(session_id) / "literature"


def pdf_dir(session_id: str) -> Path:
    return literature_dir(session_id) / "pdfs"
