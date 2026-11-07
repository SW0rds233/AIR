"""Bound and redact data before it reaches progress events or their journal."""

from __future__ import annotations

import re
from typing import Any


_HIDDEN = "[已隐藏]"
_SECRET_KEYS = ("apikey", "token", "secret", "password", "passwd",
                "credential", "authorization", "cookie")
_ASSIGNMENT = re.compile(
    r"(?i)\b[a-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_API_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (normalized in {"key", "auth", "env", "headers"}
            or normalized.endswith(("key", "headers"))
            or any(part in normalized for part in _SECRET_KEYS))


def _redact_text(value: str) -> str:
    value = _ASSIGNMENT.sub(_HIDDEN, value)
    value = _BEARER.sub(_HIDDEN, value)
    return _API_TOKEN.sub(_HIDDEN, value)


def redact_event_value(value: Any, *, max_chars: int = 2000,
                       max_items: int = 30, max_depth: int = 3,
                       _depth: int = 0) -> Any:
    """Use one recursive rule for live previews and durable event payloads."""
    if _depth > max_depth:
        return "[嵌套内容已省略]"
    if isinstance(value, dict):
        return {_redact_text(str(key))[:80]:
                (_HIDDEN if _sensitive_key(str(key)) else redact_event_value(
                    item, max_chars=max_chars, max_items=max_items,
                    max_depth=max_depth, _depth=_depth + 1))
                for key, item in list(value.items())[:max_items]}
    if isinstance(value, (list, tuple)):
        return [redact_event_value(item, max_chars=max_chars,
                                   max_items=max_items, max_depth=max_depth,
                                   _depth=_depth + 1)
                for item in value[:max_items]]
    if isinstance(value, str):
        return _redact_text(value)[:max_chars]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))[:max_chars]
