from __future__ import annotations

"""会话事件日志与游标 (合并计划 M4: "抽出会话/事件模块, 独立 SSE 游标")。

这里检查三件在 M4 里必须成立的事:
1. 序号是全序且连续的 —— 客户端按 `_seq` 去重与判缺口才有意义;
2. 按游标重放能补齐缺失事件, 且**日志被裁掉时如实报告缺口**, 不假装补齐;
3. 断点续跑重复抛出的同一 interrupt 不进会话历史 (只影响落盘, SSE 照常推送)。
"""

import queue

from src.sessions.events import (
    EventLog,
    accepts_message,
    event_to_message,
    iter_types,
    replay_gap_note,
)


def test_seq_is_total_and_ordered():
    log = EventLog(sink=queue.Queue())
    first = log.append({"type": "node", "text": "a"})
    second = log.append({"type": "log", "text": "b"})
    assert first["_seq"] == 1
    assert second["_seq"] == 2
    assert log.seq == 2
    # 事件本体不被改写 (只多一个 _seq)
    assert first["text"] == "a"
    assert "_seq" not in {"type": "node"}


def test_every_event_goes_to_the_live_queue_even_if_not_a_message():
    """`connected` 这类不进会话历史的事件仍必须进实时队列 (否则前端收不到它)。"""
    sink: queue.Queue = queue.Queue()
    log = EventLog(sink=sink)
    log.append({"type": "connected"})
    assert sink.get_nowait()["type"] == "connected"
    assert event_to_message({"type": "connected"}) is None


def test_replay_returns_only_events_after_the_cursor():
    log = EventLog()
    for index in range(5):
        log.append({"type": "node", "text": str(index)})
    replayed, truncated = log.replay(2)
    assert [e["text"] for e in replayed] == ["2", "3", "4"]
    assert truncated is False
    assert iter_types(replayed) == ["node", "node", "node"]


def test_replay_reports_a_real_gap_instead_of_pretending_to_catch_up():
    """日志被裁掉时必须报告缺口: 客户端据此重新加载投影。"""
    log = EventLog(limit=3)
    for index in range(6):
        log.append({"type": "node", "text": str(index)})
    # 只剩最近 3 条 (_seq 4/5/6); 游标 1 已经追不上了
    replayed, truncated = log.replay(1)
    assert truncated is True, "游标早于日志起点时必须报缺口"
    assert truncated is True and replay_gap_note(True) != ""
    assert [e["_seq"] for e in replayed] == [4, 5, 6]
    # 游标正好在起点上一条时不算缺口
    _, edge = log.replay(3)
    assert edge is False


def test_log_is_bounded():
    log = EventLog(limit=2)
    for index in range(5):
        log.append({"type": "node", "text": str(index)})
    assert [_seq for _seq in (e["_seq"] for e in log.tail(10))] == [4, 5]
    assert log.seq == 5, "裁掉旧日志不得回退序号"


def test_interrupt_dedupe_only_drops_consecutive_identical_titles():
    messages: list[dict] = []
    first = event_to_message({"type": "interrupt", "payload": {"title": "选择方向"}})
    assert accepts_message(messages, first) is True
    messages.append(first or {})
    same = event_to_message({"type": "interrupt", "payload": {"title": "选择方向"}})
    assert accepts_message(messages, same) is False, "断点续跑重复抛出的同一中断只记一次"
    other = event_to_message({"type": "interrupt", "payload": {"title": "补充资料"}})
    assert accepts_message(messages, other) is True, "不同中断必须照常记录"
    # 中间隔了一条普通消息后, 同一标题的再次中断是真实的第二次, 不能再丢
    messages.append({"role": "node", "text": "x"})
    assert accepts_message(messages, same) is True


def test_non_message_events_are_not_recorded():
    assert accepts_message([], None) is False


def test_message_mapping_covers_the_lifecycle_events():
    assert event_to_message({"type": "node", "text": "t"}) == {"role": "node", "text": "t"}
    assert event_to_message({"type": "done"}) == {"role": "done", "text": "完成"}
    assert event_to_message({"type": "stopped"}) == {"role": "stopped", "text": "已停止"}
    assert event_to_message({"type": "error", "message": "boom"}) == {
        "role": "error", "text": "boom"}
    assert event_to_message({"type": "unknown-kind"}) is None


def test_session_uses_the_shared_event_log():
    """会话对象不得再自己维护第二份序号/日志 (否则两份状态会各自漂移)。"""
    from src import server

    session = server.Session.__new__(server.Session)
    session.events = queue.Queue()
    session.event_stream = EventLog(sink=session.events, limit=server.EVENT_LOG_LIMIT)
    session.messages = []
    session.emit({"type": "node", "text": "hello"})
    assert session.event_seq == 1
    assert session.event_log[0]["text"] == "hello"
    assert session.messages == [{"role": "node", "text": "hello"}]
    session.emit({"type": "connected"})
    assert session.event_seq == 2
    assert session.messages == [{"role": "node", "text": "hello"}], "心跳不占历史"
