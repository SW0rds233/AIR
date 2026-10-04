from __future__ import annotations

"""会话入口契约 (统一引擎): 身份 / 事件流 / 停止 / 历史 / 暂停点。

本文件原为 `test_survey_mode.py` —— 那套用例固定的是**引擎无关**的会话契约, 与
"综述流程图"无关, 因此随旧综述图退役而迁移到团队会话引擎 (`mode="team"`)。

要守住的四件事 (与用哪个引擎无关):
1. **身份**: 启动后能查到自己的状态 (thread/session/run 一致), 不是 500/404;
2. **事件流**: SSE 带递增序号, 且从中间游标能回放后续事件;
3. **停止**: stop 后会话进入 stopped/done, 并出现在历史列表里可回看;
4. **暂停点**: 非等待态不接受 respond (409), 不把回答送错对象。

历史遗留说明 (2026-09-24 复核, 仍适用):
- 综述/团队会话的身份是 `thread_id/session_id/run_id` **加上** `project_id/problem_id`
  (合并计划 §3.1 G03 已改为: 身份在创建 run 之前分配, 并整份注入团队 —— 团队按
  project 建研究库、按 problem/run 裁剪对象, 所以这几个 ID 必须与 HTTP 返回一致);
- `/events` 是 SSE 长连接: 会话未收尾时读 `.text` 会永久阻塞, 必须先让会话收尾
  (stop / 等 done) 再读完整流。
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    with TestClient(server.app) as c:
        yield c
        # 会话线程持有 SQLite 检查点连接: 不关闭的话 Windows 上临时目录无法清理
        server.shutdown_sessions()


def _wait_status(c: TestClient, thread_id: str, wanted: set[str],
                 *, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = c.get(f"/api/sessions/{thread_id}/state")
        assert r.status_code == 200, r.text[:300]
        last = r.json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.2)
    return last


def test_team_session_starts_and_reports_identity(client):
    """团队会话启动后必须能查到自己的状态 (而不是 500/404)。"""
    r = client.post("/api/sessions", json={
        "request": "检索并总结某方向近年的研究进展, 写一篇综述",
        "topic": "测试综述主题", "keywords": ["测试"],
        "mode": "team", "project_id": "entry", "problem_id": "p1",
    })
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["mode"] == "team"
    assert body["run_id"], "统一入口同样需要运行身份"
    assert body["session_id"] and body["thread_id"]
    # G03: 团队**必须**拿到研究身份 —— 它按 project 建研究库、按 problem/run 裁剪对象
    # 与交付包。因此这里断言的是"HTTP 返回的 ID 与团队装配实际使用的 ID 完全一致",
    # 而不是"空着也行"(旧行为: 先建 TeamRun 再回填 request, 团队拿的是自动 ID)。
    assert body["project_id"] == "entry" and body["problem_id"] == "p1"
    from src import server as _server

    team = _server.SESSIONS[body["thread_id"]].app.session.team
    assert (team.project_id, team.problem_id, team.run_id) == (
        body["project_id"], body["problem_id"], body["run_id"])

    state = _wait_status(client, body["thread_id"],
                         {"running", "waiting", "done", "stopped", "error"})
    assert state["mode"] == "team"
    assert state["thread_id"] == body["thread_id"]
    assert state["run_id"] == body["run_id"]
    assert isinstance(state["event_seq"], int)


def test_team_session_can_be_stopped_and_is_listed(client):
    """停止后会话必须进入 stopped 且出现在历史列表里 (可回看/恢复)。"""
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "mode": "team", "project_id": "entry-stop", "problem_id": "p1",
    }).json()
    thread_id = start["thread_id"]

    stop = client.post(f"/api/sessions/{thread_id}/stop")
    assert stop.status_code == 200, stop.text[:200]
    state = _wait_status(client, thread_id, {"stopped", "done", "error"})
    assert state["status"] in ("stopped", "done", "error"), state

    convs = client.get("/api/conversations").json()
    assert any(c["session_id"] == start["session_id"] for c in convs["conversations"]), convs
    detail = client.get(f"/api/conversations/{start['session_id']}")
    assert detail.status_code == 200, detail.text[:200]


def test_team_events_are_replayable(client):
    """事件流带序号且可回放 (断线重连依赖它)。

    必须先让会话收尾: `/events` 是 SSE 长连接, 会话还在跑时这个请求不会返回。
    """
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "mode": "team", "project_id": "entry-replay", "problem_id": "p1",
    }).json()
    thread_id = start["thread_id"]
    client.post(f"/api/sessions/{thread_id}/stop")
    _wait_status(client, thread_id, {"stopped", "done", "error"})

    full = client.get(f"/api/sessions/{thread_id}/events",
                      params={"last_event_id": 0}).text
    seqs = [int(line[4:]) for line in full.splitlines() if line.startswith("id: ")]
    assert seqs, full[:300]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    if len(seqs) >= 2:
        cut = seqs[len(seqs) // 2]
        tail = client.get(f"/api/sessions/{thread_id}/events",
                          params={"last_event_id": cut}).text
        tail_seqs = [int(line[4:]) for line in tail.splitlines()
                     if line.startswith("id: ")]
        assert all(seq > cut for seq in tail_seqs), tail_seqs


def test_team_respond_requires_waiting_state(client):
    """非等待态不得接受暂停点回答 (所有引擎同一约束)。"""
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "mode": "team", "project_id": "entry-respond", "problem_id": "p1",
    }).json()
    thread_id = start["thread_id"]
    state = _wait_status(client, thread_id, {"running", "waiting", "done", "stopped",
                                             "error"})
    if state["status"] == "waiting":
        pytest.skip("本用例要求非等待态; 该运行已进入等待")
    r = client.post(f"/api/sessions/{thread_id}/respond",
                    json={"response": "随便回答", "interrupt_id": "i-x"})
    assert r.status_code == 409, r.text[:300]


def test_read_only_endpoints_work(client):
    """工作台外围只读接口: 资料源 / 上下文 / 产物。"""
    assert client.get("/api/sources").status_code == 200
    assert client.get("/api/contexts").status_code == 200
    artifacts = client.get("/api/artifacts").json()
    assert "files" in artifacts and "roots" in artifacts

    # 不存在的资料源: 200 + 明确的"不可绑定"原因 (R0: 缺失资料要能说清),
    # 而不是 404 —— 前端要拿 reason 展示, start 时不可用才返回 409
    unknown = client.get("/api/sources/从来没有的库")
    assert unknown.status_code == 200, unknown.text[:200]
    assert unknown.json()["bindable"] is False
    assert unknown.json()["reason"]

    # 不存在的上下文: 路由语义上就是"没有这条记录" → 404 (而不是 500)
    assert client.get("/api/contexts/从来没有的主题/notes").status_code == 404
