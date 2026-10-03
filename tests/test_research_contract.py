from __future__ import annotations

"""P0-2 问题契约: 同一句"影响"在不同材料下必须走不同路径。

计划书 §2 P0-2 验收: 给定理论模型 / 给定观测数据 / 只给文献 三种输入走不同路径;
缺数据不得自称已估计因果效应; 用户要的机理分析不得被缩成"待收集数据的计划"。
"""

from src.research.question_planner import (
    build_spec_from_input,
    formulate,
    generate_candidates,
)
from src.research.schemas import SourceSummary, TaskKind

REQUEST = "分析信道变化对射频指纹可分性的影响, 并给出理论结论与仿真建议"


def test_three_materials_take_three_paths():
    theory = formulate(REQUEST, source_summary=SourceSummary(has_theory_model=True))
    data = formulate(REQUEST, source_summary=SourceSummary(has_observational_data=True))
    literature = formulate(REQUEST, source_summary=SourceSummary(source_set_id="rf-A", documents=4))

    assert theory.task_kind is TaskKind.mechanism
    assert data.task_kind is TaskKind.empirical_causal
    assert literature.task_kind is TaskKind.scenario
    for contract in (theory, data, literature):
        assert contract.paths, "必须给出研究路径"
        assert contract.chosen_path().task_kind is contract.task_kind
        assert all(p.produces and p.requires for p in contract.paths), contract.paths


def test_missing_data_never_yields_causal_candidate():
    spec = build_spec_from_input(
        REQUEST, project_id="no-data", problem_id="p1",
        source_summary=SourceSummary(source_set_id="rf-A", documents=3))
    assert spec.contract.task_kind is TaskKind.scenario
    assert any("缺数据" in s for s in spec.contract.forbidden_substitutions)
    candidates = generate_candidates(spec)
    assert candidates
    assert all(c.claim_type.value != "causal" for c in candidates), candidates


def test_declared_data_yields_causal_candidate_without_fabricating_rows():
    spec = build_spec_from_input(
        "用我提供的观测数据估计信道变化对可分性的影响", project_id="has-data",
        source_summary=SourceSummary(has_observational_data=True))
    candidates = generate_candidates(spec)
    assert candidates and candidates[0].claim_type.value == "causal"
    assert candidates[0].study.data_source_kind == "unspecified"
    assert not candidates[0].study.rows and not candidates[0].study.data_ref


def test_mechanism_question_is_not_downgraded_to_data_collection():
    contract = formulate("分析信道变化影响射频指纹可分性的机理",
                         source_summary=SourceSummary(source_set_id="rf-A", documents=2))
    assert contract.task_kind is TaskKind.mechanism
    assert contract.chosen_path().task_kind is TaskKind.mechanism
    assert any("机理" in s for s in contract.forbidden_substitutions)
    causal_path = next(p for p in contract.paths if p.task_kind is TaskKind.empirical_causal)
    assert not causal_path.recommended


def test_undecidable_materials_ask_exactly_one_question():
    contract = formulate(REQUEST, source_summary=SourceSummary())
    assert contract.needs_clarification
    assert contract.clarification.count("?") == 1
    assert not contract.frozen


def test_explicit_problem_uses_formal_proof_path():
    contract = formulate("对所有实数 x: x**2 >= 0")
    assert contract.task_kind is TaskKind.formal_proof
    assert len(contract.paths) == 1
    assert not contract.needs_clarification


def test_contract_freezes_on_confirmation(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import ResearchStore

    spec = build_spec_from_input("研究在噪声强度影响下误码率的变化", project_id="freeze")
    assert spec.contract is not None and not spec.contract.frozen
    store = ResearchStore("freeze", db_path=tmp_path / "freeze.sqlite")
    engine = TheoryEngine(spec, store, budget=ResearchBudget(max_actions=5))
    try:
        assert engine.plan_candidates()
        assert engine.confirm_candidate(0) is True
        assert engine.spec.contract.frozen
        assert engine.spec.contract.frozen_version == engine.spec.version
    finally:
        store.close()


def test_same_request_with_new_task_kind_conflicts(tmp_path):
    """同一 problem_id 下研究类型变了必须冲突, 不静默沿用旧契约。"""
    from src.research.store import KIND_SPEC, ResearchStore
    from src.server import _existing_spec_conflict

    spec = build_spec_from_input(
        REQUEST, project_id="conflict", problem_id="p1",
        source_summary=SourceSummary(source_set_id="rf-A", documents=2))
    store = ResearchStore("conflict", db_path=tmp_path / "conflict.sqlite")
    try:
        store.put(KIND_SPEC, "p1", spec.model_dump(mode="json"))
        same = _existing_spec_conflict(
            store, "p1", REQUEST,
            source_summary=SourceSummary(source_set_id="rf-A", documents=2))
        assert same is None

        changed = _existing_spec_conflict(
            store, "p1", REQUEST,
            source_summary=SourceSummary(has_observational_data=True))
        assert changed is not None
        assert changed["existing_contract_kind"] == TaskKind.scenario.value
        assert changed["requested_contract_kind"] == TaskKind.empirical_causal.value
        assert set(changed["options"]) == {"resume", "new_problem"}
    finally:
        store.close()


def test_start_response_and_workbench_expose_contract(tmp_path, monkeypatch):
    """P0-2 接线: 启动响应与工作台都要能看到问题契约。"""
    from fastapi.testclient import TestClient

    from src import config, server

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    client = TestClient(server.app)
    started = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "contract-api", "problem_id": "p1",
        "request": "对所有实数 x: x**2 >= 0",
    })
    assert started.status_code == 200, started.text[:300]
    body = started.json()
    assert body["contract"], body
    assert body["contract"]["task_kind"] == TaskKind.formal_proof.value
    assert body["contract"]["paths"] and body["contract"]["frozen_version"] == 0

    state = client.get("/api/research/contract-api/state", params={"problem_id": "p1"})
    assert state.status_code == 200, state.text[:300]
    assert state.json()["spec"]["contract"]["task_kind"] == TaskKind.formal_proof.value

    client.post(f"/api/sessions/{body['thread_id']}/stop")
    server.shutdown_sessions()
