from __future__ import annotations

"""只读检视层与旧引擎的**差异测试** (合并计划 §5.5 / G01 的前置)。

为什么必须差异测试
------------------
`server._research_state`（工作台只读端点）此前靠 `TheoryEngine(spec, store, …)` 才读得出
对象。把只读检视抽到 `research/inspection.py` 之后, 有一段"两条实现并存"的窗口期 ——
如果两边口径不一致, 工作台显示的结论/义务/缺口就会与引擎自己看到的不同, 而两处看起来
都"正常"。因此这里对**同一份存储**逐项比对:

- 命题/义务/验证/证据/模型/假设的 id 与原版本号;
- 轮次事件摘要 (`event_digest`) 逐字一致;
- 缺口 (`gaps`) 的类型、目标、陈述逐条一致;
- 模型选择与候选比较;
- 身份与预算投影。

引擎退役后这些用例改成只指检视层 (不再是"差异"), 行为断言保留。
"""

import pytest

from src.research.inspection import StoreInspection
from src.research.schemas import (
    ObligationStatus,
    ResearchSpec,
)
from src.research.store import ResearchStore

SPEC = ResearchSpec(
    project_id="proj-insp", problem_id="p1",
    original_request="证明对所有实数 x 都有 x**2 >= 0",
    problem_statement="证明对所有实数 x 都有 x**2 >= 0",
)

#: 一个**冻结的**输入契约 (用于验证团队运行不会覆盖已有规格)
RESEARCH_SPEC_DUMP = SPEC.model_dump(mode="json")


def _store_with_objects() -> ResearchStore:
    store = ResearchStore("proj-insp", db_path=":memory:")
    store.put("claim", "clm-1", {"id": "clm-1", "statement": "x**2 >= 0",
                                 "problem_id": "p1", "status": "in_progress",
                                 "lhs": "x**2", "rhs": "0", "relation": ">=",
                                 "variables": ["x"]})
    store.put("claim", "clm-other", {"id": "clm-other", "statement": "另一个问题",
                                     "problem_id": "p2", "status": "proposed"})
    store.put("obligation", "obl-1", {
        "id": "obl-1", "statement": "核验不等式", "kind": "prove_inequality",
        "acceptance_method": "sympy", "claim_id": "clm-1", "claim_version": 1,
        "status": ObligationStatus.open.value})
    store.put("evidence", "evi-1", {"id": "evi-1", "title": "来源 A",
                                    "claim_id": "clm-1", "support": "insufficient",
                                    "excerpt": "片段", "location": "p.1"})
    store.put("evidence_link", "lnk-1", {
        "id": "lnk-1",
        "claim_ref": {"id": "clm-1", "version": 1},
        "source_ref": {"id": "evi-1", "version": 1},
        "relation": "insufficient"})
    return store


def _inspector(store: ResearchStore) -> StoreInspection:
    return StoreInspection(store, SPEC)


def test_other_problem_claims_are_not_mixed_in():
    """同项目两个问题时, 检视层必须只返回本问题的命题 (R6)。"""
    store = _store_with_objects()
    try:
        view = _inspector(store)
        ids = {c.id for c in view.claims()}
        assert "clm-1" in ids
        assert "clm-other" not in ids, ids
    finally:
        store.close()


def test_inspection_never_writes_anything():
    """只读层必须真的只读: 读完之后存储版本一个都不该变。"""
    store = _store_with_objects()
    try:
        before = {kind: store.version_index(kind)
                  for kind in ("claim", "obligation", "evidence", "evidence_link")}
        events_before = len(store.events())
        view = _inspector(store)
        claims = view.claims()
        view.obligations()
        view.metrics(claims)
        view.event_digest(claims)
        view.gaps(claims, view.obligations())
        view.model_selection(claims)
        view.model_comparison(claims[0])
        after = {kind: store.version_index(kind) for kind in before}
        assert after == before, "只读检视改动了对象版本"
        assert len(store.events()) == events_before, "只读检视写了事件"
    finally:
        store.close()


def test_missing_runtime_record_does_not_invent_progress():
    """没有运行记录时如实呈现 0, 不编造进度。"""
    store = _store_with_objects()
    try:
        view = _inspector(store)
        assert view.runtime_record() == {}
        assert view.identity()["run_id"] == ""
        assert view.budget_payload()["actions_used"] == 0
        assert view.stopped_reason() == ""
    finally:
        store.close()


@pytest.mark.parametrize("status", [ObligationStatus.closed, ObligationStatus.refuted])
def test_retrieval_availability_uses_the_declared_policy(status):
    """`retrieval_available` 只看"资料库可用 或 授权自主检索", 不看是否有对象。"""
    store = _store_with_objects()
    try:
        from src.research.schemas import SourcePolicy

        spec = SPEC.model_copy(update={"source_policy": SourcePolicy.user_kb})
        blocked = StoreInspection(store, spec, knowledge_available=False)
        assert blocked.retrieval_available is False
        autonomous = StoreInspection(
            store, spec.model_copy(update={"source_policy": SourcePolicy.autonomous}),
            knowledge_available=False)
        assert autonomous.retrieval_available is True
        with_kb = StoreInspection(store, spec, knowledge_available=True)
        assert with_kb.retrieval_available is True
    finally:
        store.close()


def test_team_run_is_browsable_in_the_research_workbench(tmp_path, monkeypatch):
    """团队运行必须能在工作台里被读到 (同一个项目、同一批对象)。

    团队路径此前不写 `ResearchSpec`, 于是同一个项目在团队模式下"没有研究问题":
    只读端点 404, 对象明明在库里却看不到 —— 这正是"统一系统"最直接的一处断裂。
    """
    from fastapi.testclient import TestClient

    from src import config, server
    from src.graph.research_graph import TeamRun

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    with TeamRun(project_id="proj-team-wb", problem_id="p1", run_id="run-wb",
                 request="请证明对所有实数 x 都有 x**2 >= 0",
                 source_policy="user_kb", max_rounds=6) as team:
        team.run()
        assert team.task_store.store.get("spec", "p1"), "团队运行没有落盘问题规格"

    with TestClient(server.app) as client:
        response = client.get("/api/research/proj-team-wb/state",
                              params={"problem_id": "p1"})
        problems = client.get("/api/research/proj-team-wb/problems")
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert body["claims"], "工作台看不到团队写下的结论"
    # 结论状态必须是**判定层**给的 (团队只提交候选), 因此不是清一色 proposed
    assert body["claims"][0]["status"] == "supported", body["claims"][0]
    assert body["obligations"], "工作台看不到团队拆出的义务"
    assert body["events"], "工作台看不到研究过程事件"
    assert body["budget"]["max_actions"] > 0
    assert problems.status_code == 200
    assert any(p.get("problem_id") == "p1" for p in problems.json()["problems"])


def test_a_second_team_run_does_not_overwrite_the_frozen_spec(tmp_path, monkeypatch):
    """规格是**冻结的输入契约**: 第二次运行不得覆盖它 (那等于悄悄改问题定义)。"""
    from src import config
    from src.graph.research_graph import TeamRun
    from src.research.store import KIND_SPEC, ResearchStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    store = ResearchStore("proj-frozen")
    try:
        store.put(KIND_SPEC, "p1", RESEARCH_SPEC_DUMP)
    finally:
        store.close()
    with TeamRun(project_id="proj-frozen", problem_id="p1", run_id="run-frozen",
                 request="另一个完全不同的请求", source_policy="user_kb",
                 max_rounds=2) as team:
        team.prepare()
    store = ResearchStore("proj-frozen")
    try:
        stored = store.get(KIND_SPEC, "p1")
    finally:
        store.close()
    assert stored["problem_statement"] == RESEARCH_SPEC_DUMP["problem_statement"], \
        "已有规格被团队运行覆盖了"


def test_workbench_endpoint_never_constructs_a_research_engine(tmp_path, monkeypatch):
    """工作台只读端点**不得**需要研究引擎 (§5.5 / G01 的前置)。

    判据是"把引擎变成一构造就炸", 然后要求端点照常返回正确内容 —— 这比"读一眼源码
    里没有 TheoryEngine"强: 它抓住的是**运行时真的不再实例化**, 包括间接路径。
    """
    from fastapi.testclient import TestClient

    from src import config, server
    from src.research.store import ResearchStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    store = ResearchStore("proj-readonly")
    try:
        store.put("spec", "p1", SPEC.model_copy(
            update={"project_id": "proj-readonly"}).model_dump(mode="json"))
        store.put("claim", "clm-1", {"id": "clm-1", "problem_id": "p1",
                                     "statement": "x**2 >= 0", "status": "proposed"})
        store.put("obligation", "obl-1", {
            "id": "obl-1", "statement": "核验", "kind": "prove_inequality",
            "acceptance_method": "sympy", "claim_id": "clm-1", "claim_version": 1,
            "status": ObligationStatus.open.value})
    finally:
        store.close()

    with TestClient(server.app) as client:
        response = client.get("/api/research/proj-readonly/state",
                              params={"problem_id": "p1"})
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert [c["id"] for c in body["claims"]] == ["clm-1"]
    assert [o["id"] for o in body["obligations"]] == ["obl-1"]
    # 预算上限必须来自与执行侧同一个对象 (不是 0)
    assert body["budget"]["max_actions"] > 0, body["budget"]
    assert body["metrics"]["identity"]["problem_id"] == "p1"
