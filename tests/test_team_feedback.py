from __future__ import annotations

"""用户反馈 → 对象级修订 (合并计划 §5.1 / G01 的前置能力)。

此前的缺口是: "把自然语言研究意见转成对象级动作"只在 `TheoryEngine.submit_feedback`
里, 团队路径没有这个能力, 于是反馈端点必须建引擎。这里固定团队路径的行为:

1. **不猜对象**: 没给对象 id 时必须返回澄清与候选清单, 且研究状态一个字节都不变;
2. **真的落到对象上**: 指定对象后, 那条对象的**版本**前进 (修订), 而不是另造一个
   新对象把意见挂空;
3. **由主控派工**: 意见变成跨职责需求, 由主控转成任务 (子智能体之间不互相派工);
4. **已收尾的运行要能续**: 用户是在看到交付之后才提意见的, 此时运行已 finished;
   续跑必须真的派工 (给这次意见它自己的轮次额度), 且**已提交的动作不重跑**。
"""

import pytest

from src.agents.protocol import NeedKind
from src.research.feedback import (
    FEEDBACK_ROLE_BY_KIND,
    feedback_need_for,
    find_latest_team_run,
    target_candidates,
)
from src.research.store import ResearchStore

REQUEST = "请证明对所有实数 x 都有 x**2 >= 0"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _run_once(project: str, run_id: str, *, max_rounds: int = 3):
    from src.graph.research_graph import TeamRun

    with TeamRun(project_id=project, problem_id="p1", run_id=run_id,
                 request=REQUEST, source_policy="user_kb",
                 max_rounds=max_rounds) as team:
        outcome = team.run()
    store = ResearchStore(project)
    try:
        claims = store.list_latest("claim") or []
        return outcome, claims, store.version_index("claim")
    finally:
        store.close()


def test_feedback_without_a_target_asks_instead_of_guessing(isolated):
    """没给对象 id → 澄清 + 候选清单, **不改任何对象**。"""
    _run_once("proj-fb-none", "run-fb-none")
    store = ResearchStore("proj-fb-none")
    try:
        before = store.version_index("claim")
        need, info = feedback_need_for(project_id="proj-fb-none", problem_id="p1",
                                       text="结论写得太绝对了", store=store)
        assert need is None
        assert info["needs_clarification"] is True
        assert info["candidates"], "没有给出候选对象, 用户无从确认"
        assert all(c["object_id"] for c in info["candidates"])
        assert store.version_index("claim") == before, "澄清路径改动了对象"
    finally:
        store.close()


def test_targeted_feedback_revises_the_object_version(isolated):
    """指定对象 → 该对象**换版** (修订), 而不是另造一个新对象。"""
    from src.graph.research_graph import TeamRun

    _run_once("proj-fb-rev", "run-fb-rev")
    store = ResearchStore("proj-fb-rev")
    try:
        claims = store.list_latest("claim") or []
        assert claims
        target = claims[0]["id"]
        version_before = store.version_index("claim")[target]
        assert version_before == 1
        need, info = feedback_need_for(project_id="proj-fb-rev", problem_id="p1",
                                       text="请补充等号成立条件",
                                       target_object_id=target, store=store)
        assert need is not None, info
        assert need.owner == FEEDBACK_ROLE_BY_KIND["claim"] == "reasoning"
        assert [ref.id for ref in need.blocked_refs] == [target], "需求没有绑定目标对象"
    finally:
        store.close()

    with TeamRun(project_id="proj-fb-rev", problem_id="p1", run_id="run-fb-rev",
                 request=REQUEST, source_policy="user_kb", max_rounds=3) as team:
        dispatched_before = set(team.loop.results)
        result = team.submit_feedback(
            statement=need.statement, owner=need.owner,
            target_ref=need.blocked_refs[0], why=need.why,
            acceptance=list(need.acceptance), kind=need.kind)
        dispatched = set(team.loop.results) - dispatched_before
        store = team.task_store.store
        versions_after = store.version_index("claim")
        ids_after = {c["id"] for c in store.list_latest("claim")}
        events = [e["kind"] for e in team.runtime.events]
    assert result["ok"] is True
    assert result["rounds_used"] >= 1, "意见没有被派工 (轮次额度没生效)"
    assert dispatched, "主控没有为新需求派任务"
    assert target in ids_after, "反馈把目标对象换成了另一个对象"
    assert versions_after[target] > version_before, "目标对象没有被修订"
    assert "user_feedback" in events, "用户意见必须留痕"


def test_a_finished_run_continues_for_feedback(isolated):
    """已收尾的运行必须能因为用户意见而继续 (而不是原地返回)。"""
    from src.graph.research_graph import TeamRun

    outcome, claims, _ = _run_once("proj-fb-done", "run-fb-done", max_rounds=2)
    assert outcome.status in ("partial", "completed")
    store = ResearchStore("proj-fb-done")
    try:
        state = find_latest_team_run(store, "p1")
        assert state and state["finished"] is True, state
        target = claims[0]["id"]
        need, _info = feedback_need_for(project_id="proj-fb-done", problem_id="p1",
                                        text="请把适用范围写清楚",
                                        target_object_id=target, store=store)
    finally:
        store.close()

    with TeamRun(project_id="proj-fb-done", problem_id="p1", run_id="run-fb-done",
                 request=REQUEST, source_policy="user_kb", max_rounds=2) as team:
        result = team.submit_feedback(statement=need.statement, owner=need.owner,
                                      target_ref=need.blocked_refs[0],
                                      kind=need.kind, max_rounds=2)
    # 轮次上限是"恢复出来的绝对轮次", 因此必须给本次意见单独的额度: 恢复后应当
    # **真的**又跑了若干轮 (而不是因为 rounds >= max_rounds 立刻收尾)。
    assert result["rounds_used"] >= 1, result
    assert result["status"] in ("completed", "partial")
    assert result["stop_reason"], "继续之后必须有停止原因 (不能停在无状态)"


def test_feedback_restores_existing_plan_and_accepts_a_second_revision(isolated):
    """人工连续调整不能重跑旧任务，也不能把同类第二条意见去重掉。"""
    from src.graph.research_graph import TeamRun

    _run_once("proj-fb-twice", "run-fb-twice", max_rounds=2)
    with TeamRun(project_id="proj-fb-twice", problem_id="p1", run_id="run-fb-twice",
                 request=REQUEST, source_policy="user_kb", max_rounds=2) as team:
        persisted = team._load_run_payload()
        old_ids = set(persisted["results"])
        first = team.submit_feedback(statement="给出等号成立的条件", owner="reasoning",
                                     kind=NeedKind.derivation, feedback_id="adjust-1",
                                     max_rounds=2)
        first_ids = set(team.loop.results)
        assert old_ids <= first_ids
        assert len(first_ids - old_ids) >= 1
        assert first["rounds_used"] >= 1

    with TeamRun(project_id="proj-fb-twice", problem_id="p1", run_id="run-fb-twice",
                 request=REQUEST, source_policy="user_kb", max_rounds=2) as team:
        second = team.submit_feedback(statement="再给出严格不等号的条件", owner="reasoning",
                                      kind=NeedKind.derivation, feedback_id="adjust-2",
                                      max_rounds=2)
        assert first_ids <= set(team.loop.results)
        assert len(set(team.loop.results) - first_ids) >= 1
        assert second["rounds_used"] >= 1
        duplicate = team.submit_feedback(statement="再给出严格不等号的条件", owner="reasoning",
                                         kind=NeedKind.derivation, feedback_id="adjust-2")
        assert duplicate["duplicate"] is True
        assert duplicate["rounds_used"] == 0


def test_feedback_on_an_unknown_object_is_refused(isolated):
    """对象不存在时如实拒绝, 不静默挑一个对象改。"""
    _run_once("proj-fb-unknown", "run-fb-unknown")
    store = ResearchStore("proj-fb-unknown")
    try:
        need, info = feedback_need_for(project_id="proj-fb-unknown", problem_id="p1",
                                       text="改一下", target_object_id="不存在",
                                       store=store)
        assert need is None and info["ok"] is False
        assert info["needs_clarification"] is True
    finally:
        store.close()


def test_explicit_workflow_adjustment_does_not_require_an_object(isolated):
    """人工可明确要求补检索/调整方法/修稿；改变研究问题须另开问题。"""
    for scope, owner in (("sources", "evidence"), ("method", "modeling"),
                         ("writing", "writing"), ("review", "review"),
                         ("validation", "validation")):
        need, info = feedback_need_for(project_id="p", problem_id="q",
                                       text="请按这个方向调整", scope=scope)
        assert need is not None, info
        assert need.owner == owner
        assert need.blocked_refs == []
        assert need.hints["intervention_scope"] == scope
    need, info = feedback_need_for(project_id="p", problem_id="q",
                                   text="换成另一道题", scope="question")
    assert need is None
    assert info["needs_clarification"] is True


def test_candidates_only_list_this_problems_objects(isolated):
    """候选对象必须限定在本问题内 —— 不能把别的问题的对象端给用户改。"""
    store = ResearchStore("proj-fb-scope")
    try:
        store.put("claim", "mine", {"id": "mine", "statement": "我的", "problem_id": "p1"})
        store.put("claim", "other", {"id": "other", "statement": "别人的",
                                     "problem_id": "p2"})
        store.put("claim", "mine2", {"id": "mine2", "statement": "我的2", "problem_id": "p1"})
        rows = target_candidates(store, "p1")
        ids = {row["object_id"] for row in rows}
        assert "mine" in ids and "mine2" in ids
        assert "other" not in ids, rows
    finally:
        store.close()


def test_feedback_endpoint_over_http(isolated):
    """端点级: 无对象 → 400+澄清; 有对象 → 200 且目标对象换版。"""
    from fastapi.testclient import TestClient

    from src import server

    _run_once("proj-fb-http", "run-fb-http")
    store = ResearchStore("proj-fb-http")
    try:
        target = (store.list_latest("claim") or [])[0]["id"]
    finally:
        store.close()
    with TestClient(server.app) as client:
        vague = client.post("/api/research/proj-fb-http/feedback",
                            json={"response": "再想想", "problem_id": "p1"})
        assert vague.status_code == 400, vague.text[:200]
        assert vague.json()["needs_clarification"] is True
        pointed = client.post("/api/research/proj-fb-http/feedback",
                              json={"response": "请补充等号成立条件",
                                    "problem_id": "p1", "object_id": target})
        assert pointed.status_code == 200, pointed.text[:300]
        body = pointed.json()
        assert body["ok"] is True and body["owner"] == "reasoning"
        assert body["rounds_used"] >= 1
        assert body["package_dir"], "人工修订后必须形成新版交付包"
        from pathlib import Path

        assert (Path(body["package_dir"]) / "manuscript.md").is_file()
        server.shutdown_sessions()
    store = ResearchStore("proj-fb-http")
    try:
        version = store.version_index("claim")[target]
        assert version > 1, "端点路径没有真的修订对象"
        from src.publication.schemas import Manuscript

        latest = store.list_latest("manuscript")[-1]
        paper = Manuscript.model_validate(latest)
        versions_in_paper = [ref.version for block in paper.all_blocks()
                             for kind, ref in zip(block.ref_kinds, block.refs)
                             if kind == "claim" and ref.id == target]
        assert versions_in_paper and max(versions_in_paper) >= version, (
            "人工修订后不能继续交付引用旧结论版本的正文")
    finally:
        store.close()


def test_need_kind_matches_the_owning_role():
    """需求种类必须与承接角色一致 (主控据此派工)。"""
    cases = {
        "writing": NeedKind.manuscript_revision,
        "reasoning": NeedKind.derivation,
        "evidence": NeedKind.more_sources,
        "validation": NeedKind.empirical_support,
    }
    from src.research.feedback import _NEED_KIND_BY_ROLE

    for role, kind in cases.items():
        assert _NEED_KIND_BY_ROLE[role] == kind
