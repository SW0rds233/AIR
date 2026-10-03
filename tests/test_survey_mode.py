from __future__ import annotations

"""综述 (survey) 模式的入口契约 (计划书 §5: "survey/theory 两模式可运行")。

为什么单独测这个
----------------
`theory` 模式的研究循环有确定性验证器 (sympy/z3/...), 因此可以完全离线跑通并被
大量测试覆盖。`survey` 模式是一条**多智能体写作流水线**, 每个节点都要真实调用 LLM
(规划、检索判定、写稿、审稿), 没有 API key 时无法端到端跑完 —— 这也意味着它长期
缺少自动化覆盖。

这里覆盖"可运行性"中**可离线验证的那一半**: 入口、会话身份、暂停点状态、事件流、
停止、会话历史与恢复路由都用假 LLM 走真实 HTTP 接口。完整稿件质量仍需人工/联网评测
(见 `evals/rubric.md`), 本文件不声称覆盖它。

修订记录 (2026-09-24 复核)
--------------------------
本文件首版有两处期望与已实现的契约不符, 会让整套测试**卡死或必然失败**, 已按契约修正:

1. 综述会话没有 `project_id`。`project_id/problem_id` 是理论研究模式的对象范围
   (`build_theory_initial_state` 才生成), 前端也只在 theory 模式发送这两个字段
   (`src/web/src/app.ts`)。综述模式的身份是 `thread_id/session_id/run_id`,
   因此这里断言运行身份, 不再要求 `project_id` —— 否则等于要求一个不存在的东西。
2. 事件流是 **SSE 长连接**: 会话未收尾时 `GET /events` 不会返回, 用 `.text` (或
   `.stream`) 读都会永久阻塞 —— TestClient 的传输层会一直等 ASGI app 跑完, 整套
   测试因此挂死。正确做法是先让会话收尾 (stop/等 done) 再读完整流, 与
   `test_e2e_http.py`、`test_workbench_api.py` 一致。
"""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _fake_llm(*_args, **_kwargs):
    """假 LLM: 任何调用都返回结构化 JSON 或空稿, 不触网。"""
    def _invoke(messages, *a, **k):
        text = " ".join(str(getattr(m, "content", m)) for m in messages)
        if "研究计划" in text or "提取结构化" in text:
            payload = {
                "topic": "测试综述主题",
                "keywords": ["测试", "test", "TST"],
                "sub_topics": [],
                "time_range": "2019-2026",
                "stages": ["research"],
                "reuse_previous": False,
            }
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False),
                                   response_metadata={}, usage_metadata=None)
        return SimpleNamespace(content="# 稿\n\n占位内容。", response_metadata={},
                               usage_metadata=None)
    return SimpleNamespace(invoke=_invoke, stream=lambda *a, **k: iter([]))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    # 综述流水线在每个节点里 `from src.config import build_llm`: 直接替换配置函数
    monkeypatch.setattr(config, "build_llm", _fake_llm)
    from src import server

    with TestClient(server.app) as c:
        yield c
        # 会话线程持有 SQLite 检查点连接: 不关闭的话 Windows 上临时目录无法清理
        server.shutdown_sessions()


def _wait_status(c: TestClient, thread_id: str, wanted: set[str],
                 *, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = c.get(f"/api/sessions/{thread_id}/state")
        assert r.status_code == 200, r.text[:300]
        last = r.json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.2)
    raise AssertionError(f"会话未进入 {wanted}: {last.get('status')}")


def test_survey_session_starts_and_reports_identity(client):
    """默认模式是综述; 启动后必须能查到自己的状态 (而不是 500/404)。"""
    r = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作",
        "topic": "测试综述主题", "keywords": ["测试"], "skip_retrieval": True,
    })
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    thread_id = body["thread_id"]
    assert body["mode"] == "survey"
    # 综述运行同样需要运行身份 (R6): run_id 决定 outputs/{run_id}/ 的产物归属
    assert body["run_id"], "综述运行同样需要运行身份 (R6)"
    assert body["session_id"] and body["thread_id"]
    # project_id/problem_id 属于理论研究模式的研究对象范围: 综述请求不携带,
    # 服务端也不应凭空造一个 (前端只在 theory 模式发送这两个字段)
    assert body["project_id"] == "" and body["problem_id"] == ""

    state = _wait_status(client, thread_id, {"running", "waiting", "done", "stopped",
                                             "error"})
    assert state["mode"] == "survey"
    assert state["thread_id"] == thread_id
    assert state["run_id"] == body["run_id"]
    assert isinstance(state["event_seq"], int)


def test_survey_session_can_be_stopped_and_is_listed(client):
    """停止后会话必须进入 stopped 且出现在历史列表里 (可回看/恢复)。"""
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "skip_retrieval": True,
    }).json()
    thread_id = start["thread_id"]

    stop = client.post(f"/api/sessions/{thread_id}/stop")
    assert stop.status_code == 200, stop.text[:200]
    state = _wait_status(client, thread_id, {"stopped", "done", "error"})
    assert state["status"] in ("stopped", "done", "error")

    convs = client.get("/api/conversations").json()
    assert any(c["session_id"] == start["session_id"] for c in convs["conversations"]), \
        convs
    # 历史详情可回看
    detail = client.get(f"/api/conversations/{start['session_id']}")
    assert detail.status_code == 200, detail.text[:200]


def test_survey_events_are_replayable(client):
    """综述会话的事件流同样带序号且可回放 (F2 对两种模式一致)。

    必须先让会话收尾: `/events` 是 SSE 长连接, 会话还在跑时这个请求**不会返回**
    (TestClient 的传输层会一直等 ASGI app 跑完, 用 `.text` 或 `.stream` 都一样)。
    这里先用 stop 接口收尾, 再读完整事件流 —— 与 `test_e2e_http.py` 的做法一致。
    """
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "skip_retrieval": True,
    }).json()
    thread_id = start["thread_id"]
    client.post(f"/api/sessions/{thread_id}/stop")
    _wait_status(client, thread_id, {"stopped", "done", "error"})

    full = client.get(f"/api/sessions/{thread_id}/events",
                      params={"last_event_id": 0}).text
    seqs = [int(line[4:]) for line in full.splitlines() if line.startswith("id: ")]
    assert seqs, full[:300]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    # 从中间某个序号起必须只回放之后的
    if len(seqs) >= 2:
        cut = seqs[len(seqs) // 2]
        tail = client.get(f"/api/sessions/{thread_id}/events",
                          params={"last_event_id": cut}).text
        tail_seqs = [int(line[4:]) for line in tail.splitlines()
                     if line.startswith("id: ")]
        assert all(seq > cut for seq in tail_seqs), tail_seqs


def test_survey_respond_requires_waiting_state(client):
    """非等待态不得接受暂停点回答 (两种模式同一约束)。"""
    start = client.post("/api/sessions", json={
        "request": "检索并总结测试主题的相关工作", "topic": "测试综述主题",
        "skip_retrieval": True,
    }).json()
    thread_id = start["thread_id"]
    state = _wait_status(client, thread_id, {"running", "waiting", "done", "stopped",
                                             "error"})
    if state["status"] == "waiting":
        pytest.skip("本用例要求非等待态; 该运行已进入等待")
    r = client.post(f"/api/sessions/{thread_id}/respond",
                    json={"response": "随便回答", "interrupt_id": "i-x"})
    assert r.status_code == 409, r.text[:300]


def test_survey_related_read_only_endpoints_work(client):
    """综述模式用到的工作台外围接口: 资料源 / 上下文 / 产物。"""
    assert client.get("/api/sources").status_code == 200
    assert client.get("/api/contexts").status_code == 200
    artifacts = client.get("/api/artifacts").json()
    assert "files" in artifacts and "roots" in artifacts

    # 不存在的资料源: 200 + 明确的"不可绑定"原因 (R0: 缺失资料要能说清),
    # 而不是 404 —— 前端要拿 reason 展示, start 时不可用才返回 409
    unknown = client.get("/api/sources/从来没有的库")
    assert unknown.status_code == 200, unknown.text[:200]
    body = unknown.json()
    assert body["bindable"] is False
    assert body["reason"], body

    # 不存在的上下文: 路由语义上就是"没有这条记录" → 404 (而不是 500)
    assert client.get("/api/contexts/从来没有的主题/notes").status_code == 404


def test_start_request_mode_defaults_to_survey():
    """契约: 未指定 mode 时必须默认综述, 而不是悄悄跑到理论模式。"""
    from src.server import StartRequest

    assert StartRequest(topic="t").mode == "survey"
    assert StartRequest(topic="t", mode="theory").mode == "theory"
