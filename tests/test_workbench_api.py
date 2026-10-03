from __future__ import annotations

"""Web 科研工作台接口契约测试 (计划书 §9.5)。

前端 `src/web/index.html` 依赖 `/api/research/{project_id}/state` 的字段结构;
这里用一个真实跑完的 theory 项目核对契约, 避免前后端字段名悄悄漂移。
"""

import pytest

TEXT = ("判断对所有实数 x、y，是否有 x**2 + y**2 >= 2xy；给出证明和等号条件。"
        "再判断是否可以把 >= 换成 >。")


@pytest.fixture()
def theory_project(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src.graph import theory_pipeline

    theory_pipeline.run_theory_pipeline(request=TEXT, topic="acc",
                                        project_id="wb", problem_id="p1")
    return "wb"


def _frontend_source() -> str:
    """前端页面逻辑 (F4 已从内联脚本迁到 src/web/src/app.ts)。"""
    from src import server

    return (server.WEB_DIR / "src" / "app.ts").read_text(encoding="utf-8")


def test_index_contains_workbench_controls():
    """DOM 控件在模板里, 控件逻辑在 app.ts 里 (不再检查内联脚本)。"""
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    html = client.get("/").text
    for needle in ('id="runmode"', 'data-tab="workbench"', 'id="workbench"',
                   'id="files"'):
        assert needle in html, needle
    source = _frontend_source()
    for needle in ("refreshWorkbench", "submitWorkbenchFeedback", "forkFromWorkbench",
                   "renderWorkbench", "/api/research/", 'id="wbfeedback"'):
        assert needle in source, needle


def test_research_state_contract(theory_project):
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    r = client.get(f"/api/research/{theory_project}/state")
    assert r.status_code == 200, r.text[:300]
    d = r.json()

    # 顶层键
    for key in ("project_id", "problem_id", "spec", "claims", "obligations",
                "verifications", "evidence", "experiments", "routes", "novelty",
                "decisions", "events", "gaps", "budget", "objects"):
        assert key in d, key

    # 问题规格
    assert d["spec"]["problem_statement"], "必须能显示研究问题"
    # 结论: 状态/支持方式/覆盖/条件/未覆盖因素都是前端要渲染的字段
    assert d["claims"], "应有结论"
    for c in d["claims"]:
        for key in ("id", "statement", "status", "support_kind", "coverage",
                    "validation_status", "conditions", "not_covered"):
            assert key in c, (key, c)
    # 义务: 状态与验证结果分开
    assert d["obligations"]
    for o in d["obligations"]:
        for key in ("id", "statement", "kind", "status", "validation_status",
                    "claim_id", "required", "detail"):
            assert key in o, (key, o)
    # 验证记录带工具/状态/覆盖, 供"验证记录"表渲染
    for v in d["verifications"]:
        for key in ("id", "claim_id", "tool", "validation_status", "scope", "stale"):
            assert key in v, (key, v)
    # 事件摘要 (研究过程)
    assert d["events"], "应有研究过程事件"
    for e in d["events"]:
        assert e["kind"] and e["detail"], e
    # 进度统计
    assert d["objects"]["claims"] == len(d["claims"])
    assert d["budget"]["max_actions"] > 0
    assert isinstance(d["budget"]["done"], bool)
    # 计划书 §5.2 / §7.2: 模型选中情况与问题类型能力声明必须在工作台可见
    assert "model_selection" in d
    assert "models" in d["model_selection"] and "claims" in d["model_selection"]
    for c in d["claims"]:
        assert "claim_type" in c and "model_ref" in c
        entry = d["model_selection"]["claims"][c["id"]]
        assert entry["capability"], "能力声明不得为空"
        assert entry["capability_action"] in ("proceed", "clarify")
        assert entry["selected_state"] in ("selected", "missing_selection",
                                           "not_required")


def test_research_state_is_read_only(theory_project):
    """只读接口: 连续读取不得改动任何对象版本。"""
    from fastapi.testclient import TestClient

    from src import server
    from src.research.store import KIND_CLAIM, KIND_OBLIGATION, ResearchStore

    client = TestClient(server.app)
    store = ResearchStore(theory_project)
    before = (store.version_index(KIND_CLAIM), store.version_index(KIND_OBLIGATION))
    for _ in range(3):
        assert client.get(f"/api/research/{theory_project}/state").status_code == 200
    after = (store.version_index(KIND_CLAIM), store.version_index(KIND_OBLIGATION))
    assert before == after, "工作台读取不得写入研究状态"
    store.close()


def test_research_state_unknown_project_404():
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    assert client.get("/api/research/no-such-project/state").status_code == 404


def test_step_event_carries_research_progress():
    """SSE 的 node 事件必须带 research 摘要, 供前端显示进度。"""
    from src import server

    class FakeSession:
        mode = "theory"
        request = {"project_id": "no-such"}

    assert server._research_progress(FakeSession(), "theory_step", {}) is None  # 无项目不报错


# --------------------------------------------------------------------------
# F0: 问题身份与工作台契约 (计划书 §2 F0 / §3 R6)
# --------------------------------------------------------------------------
@pytest.fixture()
def two_problem_project(tmp_path, monkeypatch):
    """同一项目下两个研究问题: 各自的命题/义务必须互不可见。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src.graph import theory_pipeline

    theory_pipeline.run_theory_pipeline(
        request="对所有实数 x: x**2 >= 0", topic="A", project_id="multi", problem_id="pA")
    theory_pipeline.run_theory_pipeline(
        request="对所有实数 x: 1/x >= 0", topic="B", project_id="multi", problem_id="pB")
    # 每个问题的命题都必须带上自己的 problem_id (R6 归属)
    import src.research.store as store_mod
    from src.research.schemas import ResearchSpec  # noqa: F401 - 仅为可读性说明数据形状

    store = store_mod.ResearchStore("multi", db_path=config.DATA_DIR / "research" / "multi.sqlite")
    owners = {c.get("problem_id") for c in store.list_latest("claim")}
    store.close()
    assert owners == {"pA", "pB"}, f"命题未按问题归属: {owners}"
    return "multi"


def test_state_requires_problem_id_when_project_has_many(two_problem_project):
    """未指定 problem_id 且项目有多个问题 → 409 与可选清单, 不擅自取第一个。"""
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    r = client.get(f"/api/research/{two_problem_project}/state")
    assert r.status_code == 409, r.text[:300]
    detail = r.json()["detail"]
    assert detail["problems"] or detail.get("message")
    assert "problem_id" in detail["message"] or "研究问题" in detail["message"]


def test_state_scopes_objects_to_the_requested_problem(two_problem_project):
    """A/B 两问题: 切换工作台只见各自对象; 不存在的问题 404。"""
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    seen: dict[str, set] = {}
    for pid in ("pA", "pB"):
        r = client.get(f"/api/research/{two_problem_project}/state?problem_id={pid}")
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["problem_id"] == pid
        # 每条命题都必须标注所属问题, 且只能是当前问题
        assert d["claims"], pid
        assert {c["problem_id"] for c in d["claims"]} == {pid}
        seen[pid] = {c["id"] for c in d["claims"]}
        # 义务必须属于当前问题的命题
        claim_ids = seen[pid]
        obligation_claims = {o["claim_id"] for o in d["obligations"]}
        assert obligation_claims <= claim_ids, pid
    assert seen["pA"].isdisjoint(seen["pB"]), "两个问题的命题不得混在一起"

    r404 = client.get(f"/api/research/{two_problem_project}/state?problem_id=nope")
    assert r404.status_code == 404


def test_state_objects_counts_are_complete(theory_project):
    """F0-3: objects 必须给出前端读取的全部计数字段 (缺失会被显示成 0)。"""
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    d = client.get(f"/api/research/{theory_project}/state").json()
    counts = d["objects"]
    required = ("claims", "claims_supported", "claims_refuted", "claims_open",
                "claims_blocked", "obligations", "obligations_open",
                "obligations_blocked", "obligations_closed", "evidence",
                "verifications", "models", "routes")
    for key in required:
        assert key in counts, key
        assert isinstance(counts[key], int), key
    # 计数必须与实际对象一致 (不能是缺字段兜成的 0)
    assert counts["claims"] == len(d["claims"])
    assert counts["obligations"] == len(d["obligations"])
    assert counts["evidence"] == len(d["evidence"])
    assert counts["verifications"] == len(d["verifications"])
    statuses = [c["status"] for c in d["claims"]]
    assert counts["claims_supported"] == statuses.count("supported")
    assert counts["claims_refuted"] == statuses.count("refuted")


def test_problems_endpoint_lists_project_problems(two_problem_project):
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    d = client.get(f"/api/research/{two_problem_project}/problems").json()
    ids = {p["problem_id"] for p in d["problems"]}
    assert ids == {"pA", "pB"}
    assert all(p["statement"] for p in d["problems"])


def test_fork_creates_navigable_new_problem(tmp_path, monkeypatch):
    """F0-5: 派生后新问题必须能被直接打开、继续研究, 且旧问题保持不变。"""
    from fastapi.testclient import TestClient

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src.graph import theory_pipeline
    from src.research.store import ResearchStore

    theory_pipeline.run_theory_pipeline(request="对所有实数 x: x**2 >= 0", topic="fk",
                                        project_id="forkp", problem_id="src")
    store = ResearchStore("forkp", db_path=config.DATA_DIR / "research" / "forkp.sqlite")
    snapshot_id = store.list_snapshots()[0]["snapshot_id"] \
        if hasattr(store, "list_snapshots") else ""
    if not snapshot_id:
        snap = store.load_snapshot()
        snapshot_id = snap.snapshot_id
    before_claims = {c["id"] for c in store.list_latest("claim")}
    store.close()

    client = TestClient(server_module().app)
    r = client.post("/api/research/fork", json={
        "project_id": "forkp", "problem_id": "src",
        "new_problem_id": "derived1", "snapshot_id": snapshot_id,
    })
    assert r.status_code == 200, r.text[:400]
    d = r.json()
    assert d["problem_id"] == "derived1"
    assert d["navigable"] is True, "派生后新问题必须可打开 (不能只导入命题)"

    # 新问题可直接加载工作台, 命题为待重验, 且归属新问题
    state = client.get("/api/research/forkp/state?problem_id=derived1")
    assert state.status_code == 200, state.text[:300]
    body = state.json()
    assert body["claims"], "派生问题应有待重验命题"
    assert {c["problem_id"] for c in body["claims"]} == {"derived1"}
    assert all(c["status"] == "proposed" for c in body["claims"])
    assert not body["verifications"], "派生不得复制验证记录"
    assert {p["problem_id"] for p in body["problems"]} == {"src", "derived1"}

    # 旧问题的对象保持原样
    old = client.get("/api/research/forkp/state?problem_id=src").json()
    assert {c["id"] for c in old["claims"]} == before_claims
    assert old["claims"], "旧问题结论不得被派生动作清空"


def server_module():
    from src import server

    return server


def test_frontend_does_not_concat_query_with_ampersand():
    """F0-1: 手拼 '?problem_id=...&_=' 在无 problem_id 时会生成非法路径。"""
    source = _frontend_source()
    assert "/state' + q + '&_='" not in source
    assert "researchStateUrl" in source
    assert "searchParams" in source


def test_start_same_problem_different_request_conflicts(tmp_path, monkeypatch):
    """F0-4: 同 ID 改题不得静默研究旧题。"""
    from fastapi.testclient import TestClient

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    client = TestClient(server.app)
    first = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "cf1", "problem_id": "p1",
        "request": "对所有实数 x: x**2 >= 0",
    })
    assert first.status_code == 200, first.text[:300]

    conflict = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "cf1", "problem_id": "p1",
        "request": "对所有实数 x: 1/x >= 0",
    })
    assert conflict.status_code == 409, conflict.text[:300]
    detail = conflict.json()["detail"]
    assert detail["problem_id"] == "p1"
    assert set(detail["options"]) == {"resume", "new_problem"}
    assert "1/x" in detail["requested"]

    # 显式 resume 同一问题: 允许, 且复用已落盘规格
    resumed = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "cf1", "problem_id": "p1",
        "request": "对所有实数 x: 1/x >= 0", "resume": True,
    })
    assert resumed.status_code == 200, resumed.text[:300]
    assert resumed.json()["spec_reused"] is True

    # 换一个 problem_id 研究新问题: 允许
    other = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "cf1", "problem_id": "p2",
        "request": "对所有实数 x: 1/x >= 0",
    })
    assert other.status_code == 200, other.text[:300]
    # 停止并**等后台图线程真正退出**: 线程若在夹具恢复 DATA_DIR 之后还在跑,
    # 会把会话记录写回仓库。`shutdown_sessions()` 先发停止信号、join 线程, 再关
    # 检查点连接 (从 SESSIONS 里 pop 掉只会让线程失去被 join 的机会)。
    for resp in (first, resumed, other):
        tid = resp.json().get("thread_id", "")
        if tid:
            client.post(f"/api/sessions/{tid}/stop")
    server_module().shutdown_sessions()


# --------------------------------------------------------------------------
# F1 / F2: 当前研究状态、事件重连与反馈错误处理
# --------------------------------------------------------------------------
def test_candidate_confirmation_uses_stable_id():
    """F1-4: 候选确认必须按稳定 ID, 序号错位时不得选错路线。"""
    from src.graph.theory_pipeline import _resolve_candidate_choice
    from src.research.schemas import ClaimQuestion

    candidates = [
        ClaimQuestion(statement="A", candidate_id="cand-a"),
        ClaimQuestion(statement="B", candidate_id="cand-b"),
        ClaimQuestion(statement="C", candidate_id="cand-c", recommended=True),
    ]
    # 按 ID 确认 → 精确命中, 与顺序无关
    assert _resolve_candidate_choice(candidates, "cand-c")[0] == 2
    assert _resolve_candidate_choice(candidates, "cand-a")[0] == 0
    # 纯序号仍然可用 (向后兼容)
    assert _resolve_candidate_choice(candidates, "1")[0] == 1
    # 无法识别的回答 → 采用推荐项, 而不是猜某个序号
    assert _resolve_candidate_choice(candidates, "随便")[0] == 2
    assert _resolve_candidate_choice(candidates, "")[0] == 2


def test_confirm_candidate_rejects_unknown_id():
    """候选 ID 不在列表内时必须拒绝, 不得按旧序号猜测。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimQuestion, ResearchSpec
    from src.research.store import ResearchStore

    spec = ResearchSpec(
        project_id="cand1",
        candidates=[ClaimQuestion(statement="A", candidate_id="cand-a")],
        confirmed=False,
    )
    store = ResearchStore("cand1", db_path=".pytest_tmp/cand1.sqlite")
    engine = TheoryEngine(spec, store, budget=ResearchBudget(max_actions=5))
    assert engine.confirm_candidate(0, candidate_id="cand-missing") is False
    assert any("cand-missing" in n for n in engine.notes)
    assert not engine.spec.confirmed
    assert engine.confirm_candidate(0, candidate_id="cand-a") is True
    store.close()


def test_session_state_endpoint_reports_wait_state():
    """F2: 状态补偿接口 —— 重连后据 status/pending interrupt 校正页面。"""
    from src import server

    session = server.Session("t-state", mode="survey")
    server.SESSIONS[session.thread_id] = session
    try:
        session.emit({"type": "node", "text": "步骤"})
        session.status = "waiting"
        session.pending_interrupt_id = "int-abc"
        session.request = {"project_id": "p", "problem_id": "q"}
        from fastapi.testclient import TestClient

        d = TestClient(server.app).get(f"/api/sessions/{session.thread_id}/state").json()
        assert d["status"] == "waiting"
        assert d["pending_interrupt_id"] == "int-abc"
        assert d["event_seq"] >= 1
        assert d["project_id"] == "p" and d["problem_id"] == "q"
    finally:
        server.SESSIONS.pop(session.thread_id, None)


def test_session_event_log_is_replayable():
    """F2: 事件必须带递增序号并保留在日志里, 供断线重连回放。"""
    from src import server

    session = server.Session("t-replay", mode="survey")
    try:
        for i in range(5):
            session.emit({"type": "node", "text": f"步骤 {i}"})
        seqs = [e["_seq"] for e in session.event_log]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert session.event_seq == len(session.event_log)
        # 按 last_event_id 过滤即重连时要补发的内容
        missing = [e for e in session.event_log if e["_seq"] > 3]
        assert len(missing) == 2
    finally:
        server.SESSIONS.pop(session.thread_id, None)


def test_respond_requires_waiting_state_and_is_idempotent():
    """F2: 只接受等待态响应; 同一暂停点重复提交不得二次施加。"""
    from fastapi.testclient import TestClient

    from src import server

    session = server.Session("t-respond", mode="survey")
    server.SESSIONS[session.thread_id] = session
    client = TestClient(server.app)
    try:
        # 运行中 → 拒绝
        session.status = "running"
        r = client.post(f"/api/sessions/{session.thread_id}/respond",
                        json={"response": "x"})
        assert r.status_code == 409, r.text[:200]

        # 等待态 → 接受
        session.status = "waiting"
        session.pending_interrupt_id = "int-1"
        r = client.post(f"/api/sessions/{session.thread_id}/respond",
                        json={"response": "选 A", "interrupt_id": "int-1"})
        assert r.status_code == 200, r.text[:200]
        assert session.responses.get_nowait() == "选 A"

        # 同一暂停点重复提交 → 不再入队
        session.answered_interrupts.append("int-1")
        r2 = client.post(f"/api/sessions/{session.thread_id}/respond",
                         json={"response": "再选 B", "interrupt_id": "int-1"})
        assert r2.status_code == 200 and r2.json().get("duplicate") is True
        assert session.responses.empty(), "重复响应不得二次施加"

        # 过期暂停点 ID → 拒绝
        session.pending_interrupt_id = "int-2"
        r3 = client.post(f"/api/sessions/{session.thread_id}/respond",
                         json={"response": "x", "interrupt_id": "int-1"})
        assert r3.status_code == 409
    finally:
        server.SESSIONS.pop(session.thread_id, None)


def test_feedback_object_selector_binds_target(tmp_path):
    """F1-5: 显式选择的作用对象优先于语义解析; 不存在的对象被拒绝。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="fbobj", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("fbobj", db_path=tmp_path / "fbobj.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=5))
    assert engine.bootstrap()
    claim = engine._claims()[0]

    # 不存在于当前问题的对象 → 请求澄清, 不猜
    bad = engine.submit_feedback("这个结论请给反例", target_object_id="clm-nope")
    assert bad["ok"] is False and bad["needs_clarification"] is True
    assert "clm-nope" in bad["clarify"]

    # 显式选中真实命题 → 施加到该对象
    ok = engine.submit_feedback("这个结论请给反例", target_object_id=claim.id)
    assert ok["ok"] is True, ok
    assert any(a.get("object_id") == claim.id for a in ok.get("applied", [])), ok
    store.close()


def test_state_payload_exposes_feedback_targets(theory_project):
    """工作台必须提供反馈可选对象 (假设/结论/推导步骤), 供选择器渲染。"""
    from fastapi.testclient import TestClient

    from src import server

    d = TestClient(server.app).get(f"/api/research/{theory_project}/state").json()
    for key in ("assumptions", "steps", "claims"):
        assert key in d, key
        assert isinstance(d[key], list), key


def test_frontend_has_single_state_object_and_resume_semantics():
    """F1: 前端必须有统一的研究状态对象, 且不再各处直接改全局变量。"""
    source = _frontend_source()
    assert "const currentResearch = {" in source
    assert "function syncResearchGlobals()" in source
    # 新会话必须清空项目/问题绑定
    assert "projectId: '', problemId: ''" in source
    # 续研与启动分开
    assert "startWithResume" in source and "interrupt_id" in source
    # 事件重连: 记录序号并回放
    assert "lastEventId" in source and "last_event_id=" in source
    # 反馈对象选择器
    assert "buildObjectPicker" in source and "feedbackObjectOptions" in source
    # 提交响应前先确认 HTTP 成功
    assert "body.ok !== false" in source
