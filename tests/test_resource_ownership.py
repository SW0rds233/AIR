from __future__ import annotations

"""M4 全局资源归属: 同时跑两个会话时不得串日志、串费用、串图表 (合并计划 §7.4 / M4)。

合并计划的退出条件是"同时研究两题不串证据/费用/图表"。这三件事此前都是**进程级
单例**: `sys.stdout` 被最后一个启动的会话替换 (于是 A 的日志转发给 B, 断开一个会话
还会让另一个彻底丢失日志), `cost_tracker.tracker` 是一个全局累计器 (于是每个 run
的成本报告都是历史累计), 图表目录是所有运行共用的 `outputs/figures/` (文件名只是
局部序号 `taxonomy_0.png`, 于是互相覆盖)。

这些用例只检查**归属**这一条不变量, 不碰研究逻辑。
"""

import sys
import threading

import pytest


@pytest.fixture(autouse=True)
def _clean_stdout_bindings():
    """用例前后都不得留下绑定的日志桥 (否则会污染同批次其它用例的输出)。"""
    from src import server

    yield
    with server._STDOUT_LOCK:
        server._STDOUT_BRIDGES.clear()


def _bare_session(monkeypatch, label: str):
    """构造一个不建图的会话替身: 只保留 emit 出口 (真正被断言的部分)。"""
    from datetime import datetime

    class _Fake:
        def __init__(self):
            self.thread_id = label
            self.session_id = label
            self.run_id = label
            self.events = []
            self.created_at = datetime.now().isoformat(timespec="seconds")

        def emit(self, event):
            self.events.append(event)

    return _Fake()


def test_two_sessions_do_not_steal_each_others_stdout(monkeypatch):
    """两个会话并发时, 每个线程的 print 只进自己会话的事件流。"""
    from src import server

    original = server._install_stdout_dispatcher()
    session_a = _bare_session(monkeypatch, "A")
    session_b = _bare_session(monkeypatch, "B")

    started = threading.Barrier(2)
    finished = threading.Barrier(2)

    def worker(session, text):
        server._bind_stdout_bridge(session)
        try:
            started.wait(timeout=10)
            print(text)                    # 走 dispatcher -> 本线程的桥
            finished.wait(timeout=10)
        finally:
            server._unbind_stdout_bridge()

    threads = [
        threading.Thread(target=worker, args=(session_a, "alpha-line")),
        threading.Thread(target=worker, args=(session_b, "beta-line")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    texts_a = [e.get("text") for e in session_a.events]
    texts_b = [e.get("text") for e in session_b.events]
    assert "alpha-line" in texts_a, texts_a
    assert "beta-line" not in texts_a, f"A 收到了 B 的日志: {texts_a}"
    assert "beta-line" in texts_b, texts_b
    assert "alpha-line" not in texts_b, f"B 收到了 A 的日志: {texts_b}"
    # 全局 stdout 始终是派发器, 不会被某个会话的桥接器顶掉
    assert isinstance(sys.stdout, server._StdoutDispatcher)
    assert sys.stdout.orig is original


def test_unbinding_one_session_keeps_the_other_session_logging(monkeypatch):
    """一个会话结束不得让另一个会话的日志静默消失 (此前会 '恢复' 成上一个桥)。

    这是一个**并发**用例: 两个会话线程同时在跑, 其中一个先结束 (解绑 + 恢复
    `sys.stdout`), 另一个必须继续把日志记到自己名下 —— 早期实现会在恢复时把
    后结束的会话的桥接器顶掉, 于是它的日志静默丢失。
    """
    from src import server

    server._install_stdout_dispatcher()
    session_first = _bare_session(monkeypatch, "first")
    session_second = _bare_session(monkeypatch, "second")
    first_bound = threading.Event()
    second_done = threading.Event()

    def second_worker():
        server._bind_stdout_bridge(session_second)
        try:
            first_bound.wait(timeout=10)
            print("second-line")
        finally:
            server._unbind_stdout_bridge()
        second_done.set()

    def first_worker():
        server._bind_stdout_bridge(session_first)
        try:
            print("first-before")
            first_bound.set()
            second_done.wait(timeout=10)   # 第二个会话此时结束并"恢复" stdout
            print("first-after")
        finally:
            server._unbind_stdout_bridge()

    threads = [threading.Thread(target=first_worker),
               threading.Thread(target=second_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    texts_first = [e.get("text") for e in session_first.events]
    texts_second = [e.get("text") for e in session_second.events]
    assert "first-before" in texts_first, texts_first
    assert "first-after" in texts_first, (
        "第二个会话结束后, 第一个会话的日志丢失了: " + repr(texts_first))
    assert texts_second == ["second-line"], texts_second


def test_usage_tracker_is_bound_per_session():
    """两会话的用量必须分开累计 (否则成本报告是历史累计, 无法核对单次花费)。"""
    from src.utils.cost_tracker import (
        UsageTracker,
        bind_session_tracker,
        current_tracker,
        unbind_session_tracker,
    )

    results: dict[str, dict] = {}
    barrier = threading.Barrier(2)

    def worker(name: str):
        bind_session_tracker()
        try:
            barrier.wait(timeout=10)
            current_tracker().add_call(
                "deepseek-v4-flash",
                {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                stage=name)
            results[name] = current_tracker().summary()
        finally:
            unbind_session_tracker()

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert results["a"]["total_calls"] == 1, results["a"]
    assert results["b"]["total_calls"] == 1, results["b"]
    assert results["a"]["total_input_tokens"] == 100, results["a"]
    # 未绑定时落回模块级单例 (旧调用点行为不变)
    assert isinstance(current_tracker(), UsageTracker)


def test_figure_dir_is_scoped_by_run(tmp_path, monkeypatch):
    """图表目录按 run 身份分层: 文件名只是局部序号, 共用目录会互相覆盖。"""
    from src import config
    from src.rag import figure_generator

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    # 未绑定 run 身份时保持原来的扁平目录 (向后兼容)
    assert figure_generator._ensure_figure_dir() == tmp_path / "figures"
    with figure_generator.figure_scope_bound("run-A"):
        first = figure_generator._ensure_figure_dir()
    with figure_generator.figure_scope_bound("run-B"):
        second = figure_generator._ensure_figure_dir()
    assert first == tmp_path / "figures" / "run-A"
    assert second == tmp_path / "figures" / "run-B"
    assert first.exists() and second.exists()
    # 目录名不得含路径分隔符等危险字符
    with figure_generator.figure_scope_bound("../../etc"):
        assert figure_generator._ensure_figure_dir().parent == tmp_path / "figures"
    # G18: 归属再下分到任务与产物版本 —— 同一次运行里的两张图也不得互相覆盖
    with figure_generator.figure_scope_bound("run-A", task_id="task-1",
                                             artifact_version=2):
        scoped = figure_generator._ensure_figure_dir()
    assert scoped == tmp_path / "figures" / "run-A" / "task-1" / "v2"
    with figure_generator.figure_scope_bound("run-A", task_id="task-1",
                                             artifact_version=3):
        other = figure_generator._ensure_figure_dir()
    assert other != scoped and other.exists()


def test_session_memory_is_bound_to_the_session(tmp_path, monkeypatch):
    """会话记忆不得再是"全局最近一次": 两个会话各记各的, 读回不串。"""
    from src.utils import session_memory
    from src.utils.run_scope import run_scope_bound

    monkeypatch.setattr(session_memory, "MEMORY_DIR", tmp_path / "session_memory")
    monkeypatch.setattr(session_memory, "MEMORY_FILE", tmp_path / "session_memory.json")

    with run_scope_bound(session_id="sess-a"):
        session_memory.save_session_memory("主题 A", ["research"])
    with run_scope_bound(session_id="sess-b"):
        session_memory.save_session_memory("主题 B", ["write"])

    # 各会话读回自己的主题, 而不是"最后写入的那个"
    assert session_memory._read(session_memory.memory_path("sess-a"))["topic"] == "主题 A"
    assert session_memory._read(session_memory.memory_path("sess-b"))["topic"] == "主题 B"
    with run_scope_bound(session_id="sess-a"):
        assert session_memory.load_session_memory()["topic"] == "主题 A"
    with run_scope_bound(session_id="sess-b"):
        assert session_memory.load_session_memory()["topic"] == "主题 B"
    # 没有归属时退回汇总文件 (旧行为: 最近一次), 不报错也不返回错会话的数据
    assert session_memory.load_session_memory()["topic"] == "主题 B"
    # 身份为空时不写无归属的分片
    assert session_memory.memory_path("") is None
