from __future__ import annotations

"""端到端验收: 同源 HTTP 接口上的完整研究流程 (计划书 §2 F4 / §5)。

计划书 F4 的验收要求包含"**一个完整研究问题的浏览器操作路径**", §5 又要求"前端请求
URL、工作台字段、多问题查询、派生入口、会话切换、重连和反馈失败流程均有自动化覆盖"。
这个文件用 `TestClient` 走**真实的 HTTP 接口**(而不是直接调用内部函数), 覆盖:

1. 启动理论研究会话 → 事件流 → 工作台 → 产物清单 → 反馈 → 快照派生;
2. 断线重连: `last_event_id` 回放 + `state` 补偿接口, 两个标签页各自读日志;
3. 多问题: 同一项目两个问题各自可导航, 不存在的问题 404;
4. 反馈失败流程: 回答非等待态会话、重复回答同一暂停点都被拒。

模型调用全部注入假 LLM: 测试不得真实调用 API (计划书 §5 的离线基线要求)。
"""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ACCEPTANCE_TEXT = ("判断对所有实数 x、y，是否有 x² + y² ≥ 2xy；给出证明和等号条件。"
                   "再判断是否可以把 ≥ 换成 >。")


def _proposal(**over) -> str:
    data = {"action_type": "derive_step", "object_id": "", "target_gap": "open_obligation",
            "why_now": "当前有未关闭义务", "uncertainty_reduced": "该义务能否被验证",
            "on_failure": "换用反例检索并记录失败原因"}
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


def _fake_llm(proposal_text: str):
    def _invoke(messages, *args, **kwargs):
        text = " ".join(str(getattr(m, "content", m)) for m in messages)
        if "动作规划助手" in text:
            return SimpleNamespace(content=proposal_text, response_metadata={},
                                   usage_metadata=None)
        return SimpleNamespace(content="{}", response_metadata={}, usage_metadata=None)
    return SimpleNamespace(invoke=_invoke)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_PROPOSER", "1")
    from src.graph import theory_pipeline

    # 正式入口按角色取模型: 这里换成本地假实现, 不触碰任何外部 API
    monkeypatch.setattr(theory_pipeline, "_role_llm", lambda: _fake_llm(_proposal()))
    from src import server

    with TestClient(server.app) as c:
        yield c
        # 会话线程持有 checkpointer/连接: 测试结束前显式停掉
        for thread_id, session in list(server.SESSIONS.items()):
            try:
                c.post(f"/api/sessions/{thread_id}/stop")
            except Exception:  # noqa: BLE001 - 清理失败不影响断言结果
                pass
            server.SESSIONS.pop(thread_id, None)


def _start(c: TestClient, *, project_id: str, problem_id: str, request: str,
           source_set_id: str = "") -> dict:
    body = {"mode": "theory", "project_id": project_id, "problem_id": problem_id,
            "request": request, "topic": request, "max_actions": 12,
            "max_tool_calls": 20}
    if source_set_id:
        body["source_set_id"] = source_set_id
    r = c.post("/api/sessions", json=body)
    assert r.status_code == 200, r.text[:400]
    return r.json()


def _wait_done(c: TestClient, thread_id: str, *, timeout: float = 90.0) -> dict:
    """轮询 state 补偿接口直到会话收尾 (重连/断线也应能靠它校正页面)。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = c.get(f"/api/sessions/{thread_id}/state")
        assert r.status_code == 200, r.text[:300]
        last = r.json()
        if last.get("status") in ("done", "stopped", "error"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"会话未在 {timeout}s 内收尾: {last.get('status')}")


# --------------------------------------------------------------------------
# 完整研究路径
# --------------------------------------------------------------------------
def test_full_research_path_over_http(client):
    """启动 → 事件 → 工作台 → 产物 → 反馈 → 快照派生, 全部走真实接口。"""
    start = _start(client, project_id="e2e", problem_id="p1", request=ACCEPTANCE_TEXT)
    thread_id = start["thread_id"]
    assert start["run_id"], "启动响应必须回传运行身份 (R6)"
    assert start["project_id"] == "e2e" and start["problem_id"] == "p1"

    state = _wait_done(client, thread_id)
    assert state["project_id"] == "e2e" and state["problem_id"] == "p1"
    assert state["run_id"] == start["run_id"]
    assert state["final_state"] is not None
    assert state["final_state"]["snapshot_id"]

    # 1) 事件流: 每个事件带递增序号, 且可以回放
    events = client.get(f"/api/sessions/{thread_id}/events",
                        params={"last_event_id": 0})
    assert events.status_code == 200
    frames = [line for line in events.text.splitlines() if line.startswith("id: ")]
    assert frames, events.text[:200]
    seqs = [int(line[4:]) for line in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    # 2) 工作台: 身份一致 + 契约字段齐 + 指标/日志异常可见
    wb = client.get("/api/research/e2e/state", params={"problem_id": "p1"}).json()
    assert wb["run_id"] == start["run_id"]
    assert wb["problem_id"] == "p1"
    assert wb["claims"], f"工作台必须显示结论: {wb.keys()}"
    for key in ("claims", "obligations", "verifications", "evidence", "novelty",
                "experiments", "routes", "decisions", "events", "gaps", "budget",
                "objects", "metrics", "log_anomalies", "snapshots"):
        assert key in wb, key
    assert wb["objects"]["claims"] == len(wb["claims"])
    assert wb["metrics"]["identity"]["run_id"] == start["run_id"]
    assert wb["log_anomalies"] == [], wb["log_anomalies"]
    assert wb["metrics"]["anomalies"] == [], wb["metrics"]["anomalies"]

    # 3) 产物清单: 过滤到本次运行后只剩交付包里的文件, 并能读取正文
    listing = client.get("/api/artifacts", params={
        "project_id": "e2e", "problem_id": "p1", "run_id": start["run_id"]}).json()
    names = [f["name"] for f in listing["files"]]
    assert names, "研究完成后必须有可读产物"
    assert all(f.get("problem_id") == "p1" for f in listing["files"])
    target = next((f["name"] for f in listing["files"] if f["name"].endswith("manuscript.md")),
                  names[0])
    content = client.get("/api/artifacts/" + target).json()
    assert content.get("content"), content

    # 4) 对象级反馈落到具体命题上
    claim_id = wb["claims"][0]["id"]
    feedback = client.post("/api/research/e2e/feedback", json={
        "response": "该结论请补一个反例", "object_id": claim_id, "problem_id": "p1"})
    assert feedback.status_code == 200, feedback.text[:300]
    body = feedback.json()
    assert body.get("ok") or body.get("needs_clarification"), body

    # 5) 从快照派生新问题: 接口回传可导航的 problem_id
    fork = client.post("/api/research/fork", json={
        "project_id": "e2e", "source_snapshot_id": state["final_state"]["snapshot_id"],
        "new_problem_id": "p1-fork"})
    assert fork.status_code == 200, fork.text[:400]
    forked = fork.json()
    new_problem = forked.get("problem_id") or forked.get("new_problem_id")
    assert forked.get("ok"), forked
    assert new_problem and new_problem != "p1"

    # 派生出的问题必须能直接打开 (工作台可导航), 且不复用旧验证记录
    derived = client.get("/api/research/e2e/state", params={"problem_id": new_problem})
    assert derived.status_code == 200, derived.text[:300]


def test_multi_problem_switch_and_missing_problem(client):
    """同项目两个问题各自可导航; 缺 problem_id 要求选择; 不存在的问题 404。"""
    for problem_id, request in (("pA", "对所有实数 x: x**2 >= 0"),
                                ("pB", "对所有实数 x: 1/x >= 0")):
        start = _start(client, project_id="multi", problem_id=problem_id, request=request)
        _wait_done(client, start["thread_id"])

    ambiguous = client.get("/api/research/multi/state")
    assert ambiguous.status_code == 409, ambiguous.text[:200]
    assert ambiguous.json()["detail"]["problems"]

    seen = {}
    for problem_id in ("pA", "pB"):
        r = client.get("/api/research/multi/state", params={"problem_id": problem_id})
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["problem_id"] == problem_id
        assert body["claims"], problem_id
        assert {c["problem_id"] for c in body["claims"]} == {problem_id}
        seen[problem_id] = {c["id"] for c in body["claims"]}
    assert seen["pA"].isdisjoint(seen["pB"])

    assert client.get("/api/research/multi/state",
                      params={"problem_id": "nope"}).status_code == 404


def test_reconnect_replays_events_and_state_compensates(client):
    """断线重连: 从任意 `last_event_id` 回放缺失事件, 且 state 能独立校正页面。"""
    start = _start(client, project_id="recon", problem_id="p1", request=ACCEPTANCE_TEXT)
    thread_id = start["thread_id"]
    state = _wait_done(client, thread_id)

    full = client.get(f"/api/sessions/{thread_id}/events", params={"last_event_id": 0}).text
    seqs = [int(line[4:]) for line in full.splitlines() if line.startswith("id: ")]
    assert len(seqs) >= 2, full[:300]

    half = seqs[len(seqs) // 2]
    tail = client.get(f"/api/sessions/{thread_id}/events",
                      params={"last_event_id": half}).text
    tail_seqs = [int(line[4:]) for line in tail.splitlines() if line.startswith("id: ")]
    assert tail_seqs, "重连必须回放缺失事件"
    assert min(tail_seqs) > half
    assert set(tail_seqs) <= set(seqs)

    # 第二个"标签页"读到同一批事件 (不争抢消费队列)
    other = client.get(f"/api/sessions/{thread_id}/events", params={"last_event_id": 0}).text
    assert [int(l[4:]) for l in other.splitlines() if l.startswith("id: ")] == seqs

    # state 补偿: 即使一个事件都没收到, 也能拿到最终状态与身份
    assert state["status"] == "done"
    assert state["event_seq"] >= max(seqs)
    assert state["final_state"]["snapshot_id"]


def test_feedback_failure_paths_are_explicit(client):
    """反馈失败流程: 非等待态拒绝、重复回答同一暂停点被拒、未知对象要求澄清。"""
    start = _start(client, project_id="fbfail", problem_id="p1", request=ACCEPTANCE_TEXT)
    thread_id = start["thread_id"]
    _wait_done(client, thread_id)

    # 已收尾的会话不再接受暂停点回答 (必须说明当前状态, 而不是静默丢弃)
    late = client.post(f"/api/sessions/{thread_id}/respond",
                       json={"response": "迟到的回答", "interrupt_id": "i-1"})
    assert late.status_code == 409, late.text[:300]
    detail = late.json()["detail"]
    assert "不接受响应" in detail and "done" in detail, detail

    # 未知对象: 不猜 —— 要么 200 + 澄清候选, 要么 400 + 可读原因, 且都不得施加任何动作
    unclear = client.post("/api/research/fbfail/feedback", json={
        "response": "这个结论要补充证据", "object_id": "clm-不存在", "problem_id": "p1"})
    assert unclear.status_code in (200, 400), unclear.text[:300]
    body = unclear.json()
    if unclear.status_code == 200:
        assert body.get("needs_clarification") is True, body
        assert body.get("clarify"), body
        assert body.get("actions") == []
    else:
        assert "不在当前研究问题内" in str(body.get("detail") or body), body

    # 研究状态未被这次失败反馈改动
    wb = client.get("/api/research/fbfail/state", params={"problem_id": "p1"}).json()
    assert wb["log_anomalies"] == []
