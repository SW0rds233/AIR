from __future__ import annotations

"""多订阅者 SSE 契约 (合并计划 §3.3 G15 / §8.4「同 run 两页面」)。

审计缺口原文: "`session_events()` 回放后仍 `session.events.get()`, 多个页面争抢队列;
`connected` 使用未落日志的 `current_seq+1`, 可能占下一业务 ID。"

这里固定的三件事都是**可失败**的:

1. **两个订阅者各自拿到完整事件**: 事件从持久日志按**各自游标**读, 不是从共享队列
   `get()` —— 队列只能被一个读者取走一条, 旧实现下 A 收到的事件在 B 里凭空消失;
2. **非业务帧不占业务序号**: `connected` / 心跳 / 合成终止帧的 `id:` 要么沿用当前
   游标要么不带 id; 若它占用了"下一条业务序号", 客户端记下的最后 id 会比真实事件大 1,
   于是下一条真实事件被当成重复丢掉 (旧实现的 `current_seq += 1`);
3. **断线重连按游标补齐**: 从中间游标订阅只收到之后的真实事件, 且序号严格递增、无重复。

用 ASGI 上的**真并发**流来测: 同一个应用、两个同时打开的 SSE 连接, 而不是复用同一个
客户端顺序读两次。
"""

import asyncio
import json
import time

import httpx
import pytest


def _events(text: str) -> list[tuple[int, dict]]:
    """把 SSE 文本解析为 `(id, payload)` 列表 (`id=0` 表示非业务帧)。"""
    out: list[tuple[int, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        seq = 0
        payload = None
        for line in block.splitlines():
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if payload is not None:
            out.append((seq, payload))
    return out


async def _collect(client: httpx.AsyncClient, url: str, *, limit_seconds: float = 60.0
                   ) -> list[tuple[int, dict]]:
    collected: list[tuple[int, dict]] = []
    deadline = time.monotonic() + limit_seconds
    async with client.stream("GET", url) as response:
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                parsed = _events(block + "\n\n")
                collected.extend(parsed)
                if parsed and parsed[-1][1].get("type") in ("done", "stopped", "error"):
                    return collected
            if time.monotonic() > deadline:
                return collected
    return collected


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    from src import server

    yield server
    server.shutdown_sessions()


def _start_session(server, request: str) -> dict:
    from fastapi.testclient import TestClient

    with TestClient(server.app) as client:
        body = client.post("/api/sessions", json={
            "request": request, "topic": request[:12], "project_id": "proj-sse",
            "problem_id": "p1", "source_policy": "user_kb"}).json()
    return body


def test_two_subscribers_each_get_the_complete_event_stream(app_client):
    """两个订阅者都能拿到**同一批**业务事件 (不是一人一半)。"""
    server = app_client
    body = _start_session(server, "判断参数为 2-(211,15,1) 的设计是否存在")
    thread_id = body["thread_id"]
    # 等收尾, 这样两端的日志是一致的 (确定性)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = server.SESSIONS[thread_id].status
        if status in ("done", "stopped", "error"):
            break
        time.sleep(0.2)
    log_seqs = [int(e["_seq"]) for e in server.SESSIONS[thread_id].event_log]

    async def main():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            return await asyncio.gather(
                _collect(client, f"/api/sessions/{thread_id}/events?last_event_id=0"),
                _collect(client, f"/api/sessions/{thread_id}/events?last_event_id=0"))

    first, second = asyncio.run(asyncio.wait_for(main(), timeout=120))
    first_seqs = {seq for seq, _ in first if seq}
    second_seqs = {seq for seq, _ in second if seq}
    assert first_seqs, "第一个订阅者没有收到任何业务事件"
    assert first_seqs == second_seqs, (
        f"两个订阅者收到的事件不同: 只在一侧 = {first_seqs ^ second_seqs}")
    # 两端都拿到了会话的完整业务历史 (日志里的每条都在)
    assert set(log_seqs) <= first_seqs, (
        f"订阅者漏掉了日志中的事件: {set(log_seqs) - first_seqs}")
    # 两端都看到了终止事件 (不空转)
    assert any(p.get("type") in ("done", "stopped", "error") for _, p in first)
    assert any(p.get("type") in ("done", "stopped", "error") for _, p in second)


def test_non_business_frames_do_not_consume_a_business_sequence(app_client):
    """`connected`/心跳/合成终止帧的 id 必须是**真实存在**的事件序号 (或不带 id)。

    旧实现给 `connected` 分配 `current_seq + 1`, 于是客户端记下的游标比任何真实事件
    都大 —— 下一条真实事件会被当成"重复"丢掉。这里从帧本身检查:
    任何带 `id:` 的帧, 其 id 必须落在日志的真实序号集合内。
    """
    server = app_client
    body = _start_session(server, "判断参数为 2-(211,15,1) 的设计是否存在")
    thread_id = body["thread_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.SESSIONS[thread_id].status in ("done", "stopped", "error"):
            break
        time.sleep(0.2)
    session = server.SESSIONS[thread_id]
    log_seqs = {int(e["_seq"]) for e in session.event_log}

    async def main():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            return await _collect(
                client, f"/api/sessions/{thread_id}/events?last_event_id=0")

    frames = asyncio.run(asyncio.wait_for(main(), timeout=120))
    business_ids = [seq for seq, _ in frames if seq]
    assert business_ids, "没有收到带序号的事件"
    # 序号严格递增且不重复 (客户端据此去重与判缺口)
    assert business_ids == sorted(business_ids), business_ids
    assert len(set(business_ids)) == len(business_ids), business_ids
    phantom = sorted(set(business_ids) - log_seqs)
    assert not phantom, f"帧用了日志里不存在的业务序号 (会挤掉真实事件): {phantom}"
    busy = [(seq, payload.get("type")) for seq, payload in frames
            if payload.get("type") in ("connected", "done", "stopped", "error")
            and seq]
    for seq, kind in busy:
        assert seq in log_seqs, f"{kind} 帧占用了非业务序号 {seq}"


def test_resuming_from_a_cursor_only_delivers_later_events(app_client):
    """从中间游标订阅: 只收到之后的真实事件 (重连不重复、不漏)。"""
    server = app_client
    body = _start_session(server, "判断参数为 2-(211,15,1) 的设计是否存在")
    thread_id = body["thread_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.SESSIONS[thread_id].status in ("done", "stopped", "error"):
            break
        time.sleep(0.2)
    session = server.SESSIONS[thread_id]
    log_seqs = sorted(int(e["_seq"]) for e in session.event_log)
    assert len(log_seqs) >= 2, log_seqs
    cut = log_seqs[len(log_seqs) // 2]

    async def main():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            return await _collect(
                client,
                f"/api/sessions/{thread_id}/events?last_event_id={cut}")

    frames = asyncio.run(asyncio.wait_for(main(), timeout=120))
    seen = [seq for seq, _ in frames if seq]
    assert all(seq > cut for seq in seen), seen
    assert seen == sorted(set(seen)), seen
    assert set(log_seqs) - set(seen) == {s for s in log_seqs if s <= cut}
