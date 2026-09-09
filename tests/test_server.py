"""对话式协作 Web 界面测试: 会话循环 + 初始状态 + HTTP 校验 (离线测试)

运行:
    python tests/test_server.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.console import ensure_utf8_console

ensure_utf8_console()

import src.server as server
from src.server import StartRequest, build_initial_state, _final_summary, Session, _run_session


class _S(TypedDict, total=False):
    step: str
    resp: str


def _mock_graph(checkpointer=None):
    from langgraph.graph import StateGraph, END
    from langgraph.types import interrupt

    g = StateGraph(_S)

    def start(state):
        return {"step": "started"}

    def pause(state):
        v = interrupt({"type": "outline", "title": "请确认", "content": "大纲内容",
                       "hint": "回车确认", "path": ""})
        return {"step": "paused", "resp": v}

    g.add_node("start", start)
    g.add_node("pause", pause)
    g.set_entry_point("start")
    g.add_edge("start", "pause")
    g.add_edge("pause", END)
    return g.compile(checkpointer=checkpointer)


def test_build_initial_state():
    req = StartRequest(topic="测试主题", keywords=["k1"], subtopics=["s1"],
                       max_revisions=5, skip_retrieval=False)
    s = build_initial_state(req)
    assert s["interactive"] is True
    assert s["research_topic"] == "测试主题"
    assert s["topic_keywords"] == ["k1"]
    assert s["sub_topics"] == ["s1"]
    assert s["max_revisions"] == 5
    assert s["skip_retrieval"] is False


def test_build_initial_state_request_and_skip_flag():
    """自然语言请求与 skip-retrieval 请求标志透传; 缓存解析已迁移至 planner"""
    req = StartRequest(topic="", request="我想研究射频指纹识别", skip_retrieval=True)
    s = build_initial_state(req)
    assert s["research_request"] == "我想研究射频指纹识别"
    assert s["research_topic"] == ""
    assert s["skip_retrieval"] is True  # 请求标志透传, 缓存加载移至 research_planner_node


def test_final_summary():
    final = {"draft_path": "d.md", "review_score": 40, "revision_count": 2, "total_cost": 1.5}
    s = _final_summary(final)
    assert s["draft_path"] == "d.md"
    assert s["review_score"] == 40
    assert s["revision_count"] == 2
    assert s["total_cost"] == 1.5
    assert s["paper_tex_path"] == ""


def test_session_interrupt_resume_roundtrip():
    """会话在 interrupt 暂停 → respond 续跑 → done"""
    orig = server.build_pipeline
    server.build_pipeline = _mock_graph
    try:
        session = Session("t-test")
        t = threading.Thread(target=_run_session, args=(session, {"interactive": True}))
        t.start()

        # 等待 interrupt
        ev = session.events.get(timeout=10)
        assert ev["type"] == "node", f"首个事件应为节点进度, 实际 {ev}"
        ev = session.events.get(timeout=10)
        assert ev["type"] == "interrupt"
        assert ev["payload"]["title"] == "请确认"
        assert session.status == "waiting"

        # 响应
        session.responses.put("y")

        # 等待 done
        done = None
        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "done":
                done = ev
                break
        assert session.status == "done"
        assert done["state"]["review_score"] == "N/A"
        t.join(timeout=10)
        assert not t.is_alive()
    finally:
        server.build_pipeline = orig


def test_session_stop_unblocks_interrupt():
    """stop 解除 interrupt 阻塞并发出 stopped 事件"""
    orig = server.build_pipeline
    server.build_pipeline = _mock_graph
    try:
        session = Session("t-stop")
        t = threading.Thread(target=_run_session, args=(session, {"interactive": True}))
        t.start()

        # 跳过 node 事件, 拿到 interrupt
        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "interrupt":
                break

        session.stop_flag.set()
        session.responses.put(server._STOP)

        # 等待 stopped
        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "stopped":
                break
        assert session.status == "stopped"
        t.join(timeout=10)
        assert not t.is_alive()
    finally:
        server.build_pipeline = orig


def test_session_streams_stdout_as_log_events():
    """子智能体的 print 输出桥接为 SSE 'log' 事件"""
    from langgraph.graph import StateGraph, END

    def _mock_graph(checkpointer=None):
        g = StateGraph(_S)

        def node(state):
            print("  子智能体进度: 下载 1/5")
            return {"step": "done"}

        g.add_node("node", node)
        g.set_entry_point("node")
        g.add_edge("node", END)
        return g.compile(checkpointer=checkpointer)

    orig = server.build_pipeline
    server.build_pipeline = _mock_graph
    try:
        session = server.Session("t-log")
        t = threading.Thread(target=server._run_session, args=(session, {"interactive": False}))
        t.start()
        log_lines = []
        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "log":
                log_lines.append(ev["text"])
            if ev["type"] == "done":
                break
        t.join(timeout=5)
        assert any("子智能体进度" in l for l in log_lines)
    finally:
        server.build_pipeline = orig


def test_http_validation_and_static():
    """HTTP 校验: 空主题 400 / 首页 200 / artifacts 200"""
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    assert client.get("/").status_code == 200
    assert client.get("/api/artifacts").status_code == 200
    r = client.post("/api/sessions", json={"topic": "   "})
    assert r.status_code == 400


def test_conversation_endpoints():
    """历史会话端点: 列表 / 详情 / 404"""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.utils.conversation_store as store

    tmp = Path(tempfile.mkdtemp())
    orig = store.CONVERSATIONS_DIR
    store.CONVERSATIONS_DIR = tmp
    try:
        store.save_conversation("cs-1", {
            "session_id": "cs-1", "thread_id": "t", "topic": "主题",
            "created_at": "2026-09-08T10:00:00", "status": "done",
            "summary": {"figure_count": 3},
            "messages": [{"role": "user", "text": "hi"}],
        })
        client = TestClient(server.app)
        r = client.get("/api/conversations")
        assert r.status_code == 200
        items = r.json()["conversations"]
        assert len(items) == 1 and items[0]["session_id"] == "cs-1"
        assert items[0]["topic"] == "主题"

        r2 = client.get("/api/conversations/cs-1")
        assert r2.status_code == 200
        assert r2.json()["topic"] == "主题"
        assert len(r2.json()["messages"]) == 1

        assert client.get("/api/conversations/nope").status_code == 404
    finally:
        store.CONVERSATIONS_DIR = orig


def test_run_session_persists_conversation():
    """_run_session 结束后把完整对话 (含用户输入) 落盘到 conversation_store"""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.utils.conversation_store as store

    tmp = Path(tempfile.mkdtemp())
    orig_dir = store.CONVERSATIONS_DIR
    store.CONVERSATIONS_DIR = tmp
    orig_bp = server.build_pipeline
    server.build_pipeline = _mock_graph
    try:
        session = Session("t-persist", topic="持久化测试")
        server.SESSIONS[session.thread_id] = session
        t = threading.Thread(target=_run_session, args=(session, {"interactive": True}))
        t.start()

        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "interrupt":
                break

        client = TestClient(server.app)
        r = client.post(f"/api/sessions/{session.thread_id}/respond", json={"response": "y"})
        assert r.status_code == 200

        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "done":
                break
        t.join(timeout=10)

        items = store.list_conversations()
        assert len(items) == 1
        assert items[0]["session_id"] == session.session_id
        assert items[0]["topic"] == "持久化测试"
        assert items[0]["status"] == "done"

        rec = store.load_conversation(session.session_id)
        roles = [m["role"] for m in rec["messages"]]
        assert roles[-1] == "done"
        assert "user" in roles
        assert "interrupt" in roles
    finally:
        server.build_pipeline = orig_bp
        server.SESSIONS.pop("t-persist", None)
        store.CONVERSATIONS_DIR = orig_dir


def test_clear_cache_endpoint():
    """清除缓存端点: 删除检索缓存/向量库/检查点, 保留 outputs"""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    orig_data_dir = server.DATA_DIR
    tmp = Path(tempfile.mkdtemp())
    server.DATA_DIR = tmp
    try:
        (tmp / "pipeline_cache").mkdir(parents=True)
        (tmp / "pipeline_cache" / "x.json").write_text("{}", encoding="utf-8")
        (tmp / "chroma").mkdir()
        (tmp / "chroma" / "index").write_text("", encoding="utf-8")
        (tmp / "pipeline_checkpoints.sqlite").write_text("", encoding="utf-8")

        client = TestClient(server.app)
        r = client.delete("/api/cache")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert not (tmp / "pipeline_cache").exists()
        assert not (tmp / "chroma").exists()
        assert not (tmp / "pipeline_checkpoints.sqlite").exists()
    finally:
        server.DATA_DIR = orig_data_dir


def test_resume_from_interrupt_after_restart():
    """断点续跑: 停在 interrupt → 模拟重启(清空 SESSIONS) → resume → 重新收到 interrupt → respond → done"""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    import src.utils.conversation_store as store

    tmp = Path(tempfile.mkdtemp())
    orig_dir = store.CONVERSATIONS_DIR
    store.CONVERSATIONS_DIR = tmp
    orig_ckpt_dir = server.CHECKPOINT_DIR
    server.CHECKPOINT_DIR = tmp / "checkpoints"
    orig_bp = server.build_pipeline
    server.build_pipeline = _mock_graph
    try:
        session_id = "resume-1"
        checkpointer, conn = server._make_checkpointer(session_id)
        session = Session("t-resume", topic="测试", session_id=session_id,
                          checkpointer=checkpointer, checkpoint_conn=conn)
        session.request = {"topic": "测试", "request": "", "keywords": [],
                           "subtopics": [], "time_range": "2019-2026",
                           "max_revisions": 3, "skip_retrieval": False}
        t = threading.Thread(target=_run_session, args=(session, {"interactive": True}), daemon=True)
        t.start()
        while True:
            ev = session.events.get(timeout=10)
            if ev["type"] == "interrupt":
                break
        server._persist_session(session)
        # 模拟重启: 清空内存中的活跃会话 (checkpoint 已持久化在 SQLite)
        server.SESSIONS.clear()

        client = TestClient(server.app)
        r = client.post(f"/api/sessions/{session_id}/resume")
        assert r.status_code == 200
        assert r.json()["thread_id"] == "t-resume"

        resumed = server.SESSIONS["t-resume"]
        while True:
            ev = resumed.events.get(timeout=10)
            if ev["type"] == "interrupt":
                break

        r2 = client.post("/api/sessions/t-resume/respond", json={"response": "y"})
        assert r2.status_code == 200
        while True:
            ev = resumed.events.get(timeout=10)
            if ev["type"] == "done":
                break
        assert resumed.status == "done"
    finally:
        server.build_pipeline = orig_bp
        server.CHECKPOINT_DIR = orig_ckpt_dir
        server.SESSIONS.clear()
        store.CONVERSATIONS_DIR = orig_dir


def test_resume_missing_session_returns_404():
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    assert client.post("/api/sessions/nope/resume").status_code == 404


if __name__ == "__main__":
    tests = [
        test_build_initial_state,
        test_build_initial_state_request_and_skip_flag,
        test_final_summary,
        test_session_interrupt_resume_roundtrip,
        test_session_stop_unblocks_interrupt,
        test_session_streams_stdout_as_log_events,
        test_http_validation_and_static,
        test_conversation_endpoints,
        test_run_session_persists_conversation,
        test_clear_cache_endpoint,
        test_resume_from_interrupt_after_restart,
        test_resume_missing_session_returns_404,
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
