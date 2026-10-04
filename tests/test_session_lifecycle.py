from __future__ import annotations

"""会话生命周期与关闭时序 (合并计划 §8 / M4 `src/sessions/{controller,store}.py`)。

这些用例的作用是**先把现有行为固定住, 再搬代码**: `Session` 的关闭时序里有真实事故
记忆 (后台研究线程仍在用 sqlite 连接时 close → Windows 上 `0xC0000005` 崩进程, 崩点在
C 层, `except Exception` 拦不住), 因此"拆分前后行为一致"必须由用例证明, 而不是靠阅读。

覆盖四类边界:
1. `emit()` 的序号全序、事件同时进队列与可重放日志、心跳不进会话历史;
2. interrupt 期间 `stop_flag` 置位后的收尾路径 (不重复答复、状态为 stopped);
3. `shutdown()` 在 worker 仍存活时返回 False 且**不**关连接; worker 已退出时返回 True 并关连接;
4. `shutdown_sessions()` 先给所有会话发停止信号再逐个等待 (不是串行等待);
5. 两个会话并行时各自的序号与会话历史互不串。

运行:
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_session_lifecycle.py -q
"""

import queue
import threading
import time

import pytest

from src import server

def _checkpointer_of(session) -> object:
    """会话的检查点连接 (桩图要与真实图用同一个, 否则续跑读不到状态)。"""
    return getattr(session, "checkpointer", None)



# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
class _FakeConn:
    """可观测的 sqlite 连接替身 (只关心"有没有被 close")。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _stub_graph(checkpointer=None):
    """一个最小可跑图: 一个节点后进入 interrupt, 便于驱动等待态。"""
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt

    class _S(dict):
        pass

    g = StateGraph(_S)

    def pause(state):
        interrupt({"type": "outline", "title": "请确认", "content": "内容", "hint": ""})
        return {}

    g.add_node("pause", pause)
    g.set_entry_point("pause")
    g.add_edge("pause", END)
    return g.compile(checkpointer=checkpointer)


def _quiet_graph(checkpointer=None):
    """一个不 interrupt 的图: 跑完即结束, worker 会正常退出。"""
    from langgraph.graph import END, StateGraph

    class _S(dict):
        pass

    g = StateGraph(_S)
    g.add_node("noop", lambda state: {})
    g.set_entry_point("noop")
    g.add_edge("noop", END)
    return g.compile(checkpointer=checkpointer)


@pytest.fixture
def stub_pipeline(monkeypatch):
    """让 `Session(...)` 不真建图 (与 tests/test_server.py 同一手法)。"""
    monkeypatch.setattr(server, "_build_team_app",
                        lambda session: _quiet_graph(_checkpointer_of(session)))
    return _quiet_graph


def _session(thread_id: str, **kwargs):
    """构造真实 `Session` (图被 stub 替换, 不触发重型依赖)。

    默认走理论引擎 —— 那条路径的图工厂可以被测试替换成"不 interrupt 的假图";
    调用方显式传 `mode` 时尊重它 (团队会话的构造契约同样要被覆盖)。
    """
    kwargs.setdefault("mode", "theory")
    return server.Session(thread_id, **kwargs)


def _drive_to_interrupt(session, timeout: float = 15.0):
    """跑 worker 直到它停在 interrupt, 返回 interrupt 事件。"""
    thread = threading.Thread(target=server._run_session,
                              args=(session, {"interactive": True}), daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = session.events.get(timeout=timeout)
        if event["type"] == "interrupt":
            return thread, event
    raise AssertionError("未能到达 interrupt")


@pytest.fixture(autouse=True)
def _clean_registry():
    """用例前后都不得留下注册表/线程桥残留 (否则会污染同批次其它用例)。"""
    yield
    for thread_id in list(server.SESSIONS):
        session = server.SESSIONS.pop(thread_id, None)
        if session is not None:
            session.stop_flag.set()
            try:
                session.responses.put(server._STOP)
            except Exception:  # noqa: BLE001
                pass
            session.shutdown(timeout=5.0)
    with server._STDOUT_LOCK:
        server._STDOUT_BRIDGES.clear()


# ----------------------------------------------------------------------
# 1. emit(): 序号、队列、日志、会话历史
# ----------------------------------------------------------------------
def test_emit_assigns_total_order_and_fills_queue_and_log(stub_pipeline):
    session = _session("t-emit")
    first = session.emit({"type": "node", "text": "a"})
    second = session.emit({"type": "log", "text": "b"})

    assert first["_seq"] == 1 and second["_seq"] == 2
    assert session.event_seq == 2
    # 三条出路都拿到同一个事件: 实时队列、可重放日志、会话历史
    assert session.events.get_nowait()["text"] == "a"
    assert session.events.get_nowait()["text"] == "b"
    assert [e["text"] for e in session.event_log] == ["a", "b"]
    assert session.messages == [{"role": "node", "text": "a"},
                                {"role": "node", "text": "b"}]


def test_emit_heartbeat_does_not_enter_session_history(stub_pipeline):
    """`connected` 这类事件要进队列/日志, 但不得占会话历史。"""
    session = _session("t-heartbeat")
    session.emit({"type": "connected"})
    assert session.event_seq == 1
    assert session.events.get_nowait()["type"] == "connected"
    assert session.event_log[0]["type"] == "connected"
    assert session.messages == []


def test_emit_dedupes_consecutive_identical_interrupts(stub_pipeline):
    """断点续跑会重新抛出同一 interrupt: 历史里只留一张卡片, 事件照常推送。"""
    session = _session("t-dedupe")
    payload = {"type": "outline", "title": "请确认", "content": "c", "hint": ""}
    session.emit({"type": "interrupt", "payload": payload})
    session.emit({"type": "interrupt", "payload": dict(payload)})
    assert [m["role"] for m in session.messages] == ["interrupt"], session.messages
    # 两次事件都必须推给客户端 (只有落盘去重)
    assert session.events.get_nowait()["type"] == "interrupt"
    assert session.events.get_nowait()["type"] == "interrupt"
    assert session.event_seq == 2


def test_event_views_are_views_not_second_copies(stub_pipeline):
    """`event_seq`/`event_log`/`event_lock` 必须与 EventLog 是同一份数据。"""
    session = _session("t-views")
    session.emit({"type": "node", "text": "x"})
    assert session.event_seq == session.event_stream.seq
    assert session.event_log is session.event_stream.records
    assert session.event_lock is session.event_stream.lock


# ----------------------------------------------------------------------
# 2. interrupt 期间的停止收尾
# ----------------------------------------------------------------------
def test_stop_flag_while_waiting_ends_as_stopped_without_double_answer(monkeypatch):
    monkeypatch.setattr(server, "_build_team_app",
                        lambda session: _stub_graph(_checkpointer_of(session)))
    session = _session("t-stop-waiting")
    thread, _ = _drive_to_interrupt(session)
    assert session.status == "waiting"
    assert session.pending_interrupt_id, "等待中必须登记当前暂停点 ID"

    session.stop_flag.set()
    session.responses.put(server._STOP)

    stopped = None
    for _ in range(5):
        event = session.events.get(timeout=15)
        if event["type"] == "stopped":
            stopped = event
            break
    assert stopped is not None, "停止后必须发出 stopped 事件"
    assert session.status == "stopped"
    # 暂停点被消费且已清空: 不会出现"停完还挂着等待"
    assert session.pending_interrupt_id == ""
    assert len(session.answered_interrupts) == 1
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_stop_flag_set_before_iteration_ends_as_stopped(monkeypatch):
    """已置位的 stop_flag 必须让循环收尾为 stopped (而不是继续跑到 done)。"""
    monkeypatch.setattr(server, "_build_team_app",
                        lambda session: _quiet_graph(_checkpointer_of(session)))
    session = _session("t-stop-early")
    session.stop_flag.set()
    server._run_session(session, {"interactive": False})
    assert session.status == "stopped"
    assert session.events.get_nowait()["type"] == "stopped"


# ----------------------------------------------------------------------
# 3. shutdown() 的连接关闭时序 (真实事故边界)
# ----------------------------------------------------------------------
def test_shutdown_refuses_to_close_while_worker_alive(stub_pipeline):
    """worker 仍存活时不关连接并返回 False (宁可有句柄残留, 也不崩进程)。"""
    session = _session("t-shutdown-alive")
    conn = _FakeConn()
    session.checkpoint_conn = conn
    release = threading.Event()
    session.worker_thread = threading.Thread(target=release.wait, daemon=True)
    session.worker_thread.start()
    try:
        started = time.monotonic()
        assert session.shutdown(timeout=0.3) is False
        assert conn.closed is False, "线程还在跑时绝不能 close 连接"
        assert session.error, "必须如实记录未关闭的原因"
        # 确实是"等到超时"而不是立刻返回
        assert time.monotonic() - started >= 0.25
    finally:
        release.set()
        session.worker_thread.join(timeout=5)


def test_shutdown_closes_connection_after_worker_exits(stub_pipeline):
    session = _session("t-shutdown-done")
    conn = _FakeConn()
    session.checkpoint_conn = conn
    session.worker_thread = threading.Thread(target=lambda: None, daemon=True)
    session.worker_thread.start()
    session.worker_thread.join(timeout=5)
    assert session.shutdown(timeout=1.0) is True
    assert conn.closed is True
    # 幂等: 第二次没有连接可关, 仍然成功
    assert session.shutdown(timeout=1.0) is True
    assert session.checkpoint_conn is None


def test_shutdown_without_worker_or_connection_is_success(stub_pipeline):
    session = _session("t-shutdown-empty")
    assert session.shutdown(timeout=0.1) is True


def test_shutdown_reports_close_failure_without_raising(stub_pipeline):
    """close() 抛错时如实返回 False, 不向上抛 (调用方要能看到"没关掉")。"""
    class _BadConn:
        def close(self):
            raise OSError("句柄被占用")

    session = _session("t-shutdown-bad")
    session.checkpoint_conn = _BadConn()
    assert session.shutdown(timeout=0.1) is False
    assert "关闭" in (session.error or "")


# ----------------------------------------------------------------------
# 4. shutdown_sessions(): 先全部发信号, 再逐个等待
# ----------------------------------------------------------------------
def test_shutdown_sessions_signals_all_before_waiting(stub_pipeline):
    """必须先给**所有**会话发停止信号, 再逐个等待 —— 否则多会话串行收敛白等。

    用"第一个会话的 shutdown 会阻塞"来证明这一点: 若实现是"逐个发信号+等待",
    第二个会话在第一个超时之前拿不到停止信号。
    """
    first = _session("t-batch-1")
    second = _session("t-batch-2")
    first.checkpoint_conn = _FakeConn()
    second.checkpoint_conn = _FakeConn()

    observed: dict[str, bool] = {}
    first_release = threading.Event()

    def slow_shutdown(timeout: float = 10.0) -> bool:
        # 在"等待"阶段检查第二个会话是否**已经**收到停止信号
        observed["second_stop_flag"] = second.stop_flag.is_set()
        observed["second_has_stop_response"] = False
        try:
            observed["second_has_stop_response"] = second.responses.get_nowait() is server._STOP
        except queue.Empty:
            pass
        first_release.wait(timeout=5)
        # 真正的关闭动作照常发生 (只是被上面的人工等待拖慢)
        conn, first.checkpoint_conn = first.checkpoint_conn, None
        if conn is not None:
            conn.close()
        return True

    first.shutdown = slow_shutdown  # type: ignore[method-assign]
    server.SESSIONS[first.thread_id] = first
    server.SESSIONS[second.thread_id] = second

    runner = threading.Thread(target=server.shutdown_sessions, daemon=True)
    runner.start()
    try:
        deadline = time.monotonic() + 5
        while "second_stop_flag" not in observed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert observed.get("second_stop_flag") is True, (
            "第一个会话还在等待时, 第二个会话就必须已经收到停止信号")
        assert observed.get("second_has_stop_response") is True, (
            "停止信号必须已投入第二个会话的响应队列 (解除 interrupt 阻塞)")
        assert first.stop_flag.is_set()
    finally:
        first_release.set()
        runner.join(timeout=10)

    # 注册表被清空, 且两个连接都关掉了
    assert server.SESSIONS == {}
    assert first.checkpoint_conn is None
    assert second.checkpoint_conn is None


def test_shutdown_sessions_counts_failures(stub_pipeline):
    """仍有线程存活的会话计为失败, 且不阻止其它会话正常关闭。"""
    stuck = _session("t-batch-stuck")
    ok = _session("t-batch-ok")
    stuck.checkpoint_conn = _FakeConn()
    ok.checkpoint_conn = _FakeConn()
    release = threading.Event()
    stuck.worker_thread = threading.Thread(target=release.wait, daemon=True)
    stuck.worker_thread.start()
    server.SESSIONS[stuck.thread_id] = stuck
    server.SESSIONS[ok.thread_id] = ok

    try:
        failed = server.shutdown_sessions()
        assert failed == 1, f"应只有 1 个会话关闭失败, 实际 {failed}"
        assert stuck.checkpoint_conn is not None
        assert ok.checkpoint_conn is None
        assert server.SESSIONS == {}
    finally:
        release.set()
        stuck.worker_thread.join(timeout=5)


def test_shutdown_sessions_on_empty_registry(stub_pipeline):
    assert server.shutdown_sessions() == 0


# ----------------------------------------------------------------------
# 5. 两会话并行: 序号与历史互不串
# ----------------------------------------------------------------------
def test_two_sessions_do_not_share_sequence_or_history(stub_pipeline):
    first = _session("t-parallel-1")
    second = _session("t-parallel-2")
    barrier = threading.Barrier(2)

    def worker(session, count, label):
        barrier.wait(timeout=10)
        for index in range(count):
            session.emit({"type": "node", "text": f"{label}-{index}"})

    threads = [threading.Thread(target=worker, args=(first, 3, "a")),
               threading.Thread(target=worker, args=(second, 5, "b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert first.event_seq == 3, first.event_seq
    assert second.event_seq == 5, second.event_seq
    assert [m["text"] for m in first.messages] == ["a-0", "a-1", "a-2"]
    assert [m["text"] for m in second.messages] == ["b-0", "b-1", "b-2", "b-3", "b-4"]
    # 各自的序号连续且完整 (没有别的会话的事件混进来)
    assert [e["_seq"] for e in first.event_log] == [1, 2, 3]
    assert [e["_seq"] for e in second.event_log] == [1, 2, 3, 4, 5]


def test_session_identity_attributes_are_initialised(stub_pipeline):
    """构造签名与属性名是公共契约 (server.Session 的调用方依赖它们)。"""
    conn = _FakeConn()
    session = _session("t-attrs", topic="主题", session_id="sid-1",
                       checkpoint_conn=conn, run_id="run-1", mode="theory")
    assert session.thread_id == "t-attrs"
    assert session.session_id == "sid-1"
    assert session.topic == "主题"
    assert session.run_id == "run-1"
    assert session.mode == "theory"
    assert session.checkpoint_conn is conn
    assert session.status == "running"
    assert session.final_state is None and session.error is None
    assert session.messages == [] and session.request == {}
    assert session.pending_interrupt_id == "" and session.answered_interrupts == []
    assert session.worker_thread is None
    assert isinstance(session.events, queue.Queue)
    assert isinstance(session.responses, queue.Queue)
    assert session.stop_flag.is_set() is False
    assert session.created_at
    assert session.config["configurable"]["thread_id"] == "t-attrs"
