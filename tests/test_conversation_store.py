"""对话记录落盘测试: save / list / load 往返 (离线测试)

运行:
    python tests/test_conversation_store.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.console import ensure_utf8_console

ensure_utf8_console()

import src.utils.conversation_store as store


def _tmp_store():
    tmp = Path(tempfile.mkdtemp())
    orig = store.CONVERSATIONS_DIR
    store.CONVERSATIONS_DIR = tmp
    return tmp, orig


def test_save_list_load_roundtrip():
    tmp, orig = _tmp_store()
    try:
        record = {
            "session_id": "s1",
            "thread_id": "t1",
            "topic": "射频指纹识别技术",
            "created_at": "2026-09-08T10:00:00",
            "status": "done",
            "summary": {"review_score": 40, "figure_count": 7},
            "messages": [
                {"role": "user", "text": "研究射频指纹识别"},
                {"role": "node", "text": "[figures] 图表生成: 7 张"},
                {"role": "interrupt", "title": "请确认", "hint": "回车确认"},
                {"role": "done", "text": "完成"},
            ],
        }
        store.save_conversation("s1", record)

        items = store.list_conversations()
        assert len(items) == 1
        assert items[0]["session_id"] == "s1"
        assert items[0]["topic"] == "射频指纹识别技术"
        assert items[0]["status"] == "done"
        assert items[0]["message_count"] == 4
        assert "messages" not in items[0], "列表不应包含完整消息"

        loaded = store.load_conversation("s1")
        assert loaded is not None
        assert loaded["session_id"] == "s1"
        assert len(loaded["messages"]) == 4
        assert loaded["messages"][0]["role"] == "user"
        assert loaded["summary"]["figure_count"] == 7
        assert loaded["updated_at"]

        assert store.load_conversation("missing") is None
    finally:
        store.CONVERSATIONS_DIR = orig


def test_save_fills_session_id_and_updated_at():
    tmp, orig = _tmp_store()
    try:
        store.save_conversation("s2", {"topic": "主题"})
        data = store.load_conversation("s2")
        assert data["session_id"] == "s2"
        assert data["updated_at"]
        assert data["topic"] == "主题"
    finally:
        store.CONVERSATIONS_DIR = orig


def test_list_orders_desc_and_skips_bad_files():
    tmp, orig = _tmp_store()
    try:
        store.save_conversation("a", {"topic": "A"})
        time.sleep(1.1)
        store.save_conversation("b", {"topic": "B"})
        (tmp / "bad.json").write_text("{not json", encoding="utf-8")

        items = store.list_conversations()
        assert [i["session_id"] for i in items] == ["b", "a"]
    finally:
        store.CONVERSATIONS_DIR = orig


if __name__ == "__main__":
    tests = [
        test_save_list_load_roundtrip,
        test_save_fills_session_id_and_updated_at,
        test_list_orders_desc_and_skips_bad_files,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
