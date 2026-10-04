from __future__ import annotations

"""团队形式化闭环 / 唯一提交口 / 交付门槛的回归 (§3.1 G06, §3.2 G08 / G12)。

审计缺口的原文与这里固定的**可失败判据**:

- **G06**: 内核路径只把计划压成 claim 候选, 未提交完整义务/推导/验证对象 →
  团队跑一条形式化题面后, 库里必须真的出现 `obligation` 与 `verification`, 且命题
  状态由判定层按它们算出 (不是角色自报);
- **G08**: 团队走 `finish()` 后逐条 `put()`, 事务能力没用上 → 走唯一提交口:
  依据过期不合入、重放不产生第二个对象、整批写入同一事务;
- **G12**: 角色跑过即被当成交付依据, 导出异常被吞 → 状态 `completed` 必须等于
  交付门槛通过; 导出失败必须抛可见且可恢复的错误, 而不是静默返回空包。
"""

import pytest

from src.agents.protocol import AgentResult, AgentTask, ChangeProposal
from src.research.commit import ResearchCommitService
from src.research.projection import TeamProjection
from src.research.reasoning_kernel import claim_state_for
from src.research.schemas import (
    Claim,
    ClaimStatus,
    ObligationStatus,
    ProofObligation,
    ValidationStatus,
    VerificationRecord,
)
from src.research.store import ResearchStore
from src.research.task_store import TaskStore


# --------------------------------------------------------------------------
# G08: 唯一提交口
# --------------------------------------------------------------------------
def _service(project_id: str = "commit-proj"):
    store = ResearchStore(project_id, db_path=":memory:")
    task_store = TaskStore(project_id, store)
    service = ResearchCommitService(store, project_id=project_id,
                                    problem_id="p1", run_id="run-1",
                                    task_store=task_store)
    return store, task_store, service


def _task(**over) -> AgentTask:
    data = {"agent": "reasoning", "objective": "核验命题", "project_id": "commit-proj",
            "problem_id": "p1", "run_id": "run-1"}
    data.update(over)
    return AgentTask(**data)


def test_claim_candidate_cannot_self_report_a_scientific_grade():
    """角色在候选里写 `status=supported` 必须被**丢弃**, 状态由判定层重算。"""
    store, _, service = _service()
    try:
        task = _task()
        claim = Claim(id="c1", statement="x**2 >= 0")
        payload = claim.model_dump(mode="json")
        # 角色自报最高等级 (只改布尔值挡不住这种做法)
        payload.update({"status": "supported", "assurance": "formally_checked",
                        "support_kind": "formal_proof",
                        "validation_status": "verified", "coverage": "target"})
        result = AgentResult(
            task_id=task.task_id, agent="reasoning", outcome="completed", summary="s",
            proposed_changes=[ChangeProposal(kind="claim", object_id="c1",
                                             payload=payload)])
        outcome = service.commit(task, result, current_versions={})
        assert outcome.accepted, "合规候选应当被接受"
        stored = store.get("claim", "c1")
        assert stored["status"] == ClaimStatus.proposed.value, (
            f"候选自报的科学等级进了权威库: {stored['status']}")
        # 自报的验证维度必须被丢弃: 允许的取值只能来自判定层 (这里没有义务与记录,
        # 因此只能是 unchecked/unknown, 绝不是 verified)
        assert stored["validation_status"] != ValidationStatus.verified.value
        assert stored["support_kind"] == "none"
        assert stored["assurance"] != "formally_checked"
    finally:
        store.close()


def test_closed_obligation_and_aligned_record_are_required_for_supported():
    """义务关闭 + 对齐的有效核验记录齐备时, 状态才升为 supported。"""
    store, _, service = _service("commit-proj2")
    try:
        task = _task(project_id="commit-proj2")
        claim = Claim(id="c2", statement="x**2 >= 0", lhs="x**2", rhs="0",
                      relation=">=", variables=["x"])
        obligation = ProofObligation(id="o2", statement="核验不等式",
                                     kind="prove_inequality",
                                     acceptance_method="sympy",
                                     claim_id="c2", claim_version=1)
        record = VerificationRecord(
            id="v2", tool="sympy", claim_id="c2", claim_version=1,
            arguments={"lhs": "x**2", "rhs": "0", "relation": ">=", "variables": ["x"]},
            status="passed", validation_status=ValidationStatus.verified,
            support_kind="symbolic_check", stale=False)
        result = AgentResult(
            task_id=task.task_id, agent="reasoning", outcome="completed", summary="s",
            proposed_changes=[
                ChangeProposal(kind="claim", object_id="c2",
                               payload={"id": "c2", "statement": claim.statement,
                                        "lhs": "x**2", "rhs": "0", "relation": ">=",
                                        "variables": ["x"]}),
                ChangeProposal(kind="obligation", object_id="o2",
                               payload=obligation.model_dump(mode="json")),
                ChangeProposal(kind="verification", object_id="v2",
                               payload={"record": record.model_dump(mode="json"),
                                        "result": {"tool": "sympy", "status": "passed",
                                                   "detail": "ok"},
                                        "obligation_id": "o2"}),
            ])
        service.commit(task, result, current_versions={})
        stored_obligation = store.get("obligation", "o2")
        assert stored_obligation["status"] == ObligationStatus.closed.value
        stored_claim = store.get("claim", "c2")
        assert stored_claim["status"] == ClaimStatus.supported.value, stored_claim
        # 核验记录必须是可用的 (stale=False), 否则结论没有依据
        assert store.get("verification", "v2")["stale"] is False
    finally:
        store.close()


def test_stale_read_set_is_rejected_and_nothing_is_written():
    """依据的版本在派工之后换代了 → 不合入, 也不留半成品。"""
    store, _, service = _service("commit-proj3")
    try:
        task = _task(project_id="commit-proj3")
        result = AgentResult(
            task_id=task.task_id, agent="reasoning", outcome="completed", summary="s",
            proposed_changes=[ChangeProposal(kind="claim", object_id="c3",
                                             payload={"id": "c3", "statement": "s"})],
            input_versions={"old-obj": 1})
        outcome = service.commit(task, result, current_versions={"old-obj": 4})
        assert outcome.stale == {"old-obj": (1, 4)}
        assert store.get("claim", "c3") is None, "过期依据的成果仍被写进权威库"
    finally:
        store.close()


def test_replaying_the_same_attempt_does_not_create_a_second_object():
    """中断重放: 同一 attempt 再提交一次不得产生第二个对象/第二个版本。"""
    store, _, service = _service("commit-proj4")
    try:
        task = _task(project_id="commit-proj4")
        result = AgentResult(
            task_id=task.task_id, agent="reasoning", outcome="completed", summary="s",
            agent_run_id="agentrun-fixed",
            proposed_changes=[ChangeProposal(kind="claim", object_id="c4",
                                             payload={"id": "c4", "statement": "s"})])
        first = service.commit(task, result, current_versions={})
        assert not first.idempotent
        second = service.commit(task, result, current_versions={})
        assert second.idempotent, "同一 attempt 重放没有被幂等键挡住"
        versions = store.list_versions("claim", "c4")
        assert len(versions) == 1, f"重放产生了 {len(versions)} 个版本"
    finally:
        store.close()


def test_multiple_events_in_one_step_do_not_collide_on_the_idempotency_key():
    """一个步骤的多条事件共用基键会撞唯一索引, 整步回滚 (实测踩过)。"""
    store = ResearchStore("commit-proj5", db_path=":memory:")
    try:
        store.submit_step([("claim", "c5", {"id": "c5", "statement": "s"})],
                          [("a", {"x": 1}), ("b", {"y": 2})],
                          idempotency_key="step-1")
        assert store.get("claim", "c5") is not None
        assert len(store.events()) >= 2
    finally:
        store.close()


def test_unauthorized_candidate_kind_is_rejected_at_the_commit_boundary():
    """提交口是最后一道门: evidence 角色提交 claim 不得落库。"""
    store, _, service = _service("commit-proj6")
    try:
        task = _task(agent="evidence", project_id="commit-proj6")
        result = AgentResult(
            task_id=task.task_id, agent="evidence", outcome="completed", summary="s",
            proposed_changes=[ChangeProposal(kind="claim", object_id="c6",
                                             payload={"id": "c6", "statement": "s"})])
        outcome = service.commit(task, result, current_versions={})
        assert outcome.rejected and "不得提交" in outcome.rejected[0]["reason"]
        assert store.get("claim", "c6") is None
    finally:
        store.close()


# --------------------------------------------------------------------------
# G06: 状态归并的判据 (零 LLM, 唯一写入点)
# --------------------------------------------------------------------------
def test_claim_state_stays_unsupported_without_an_aligned_record():
    """义务全关但没有对齐记录时**不得**升为 supported (防"计划即证明")。"""
    claim = Claim(id="c7", statement="x**2 >= 0")
    obligation = ProofObligation(id="o7", statement="核验", kind="prove_inequality",
                                 acceptance_method="sympy", claim_id="c7",
                                 status=ObligationStatus.closed,
                                 validation_status=ValidationStatus.verified,
                                 support_kind="symbolic_check")
    disposition = claim_state_for(claim, [obligation], [],
                                  aligned=lambda record, claim: False)
    assert disposition.status == ClaimStatus.in_progress
    assert "缺少与原命题对齐的有效验证记录" in disposition.note


def test_claim_state_refuted_takes_precedence_over_closed_obligations():
    claim = Claim(id="c8", statement="x**2 >= 0")
    closed = ProofObligation(id="o8a", statement="核验", kind="prove_inequality",
                             acceptance_method="sympy", claim_id="c8",
                             status=ObligationStatus.closed)
    refuted = ProofObligation(id="o8b", statement="找反例", kind="refute",
                              acceptance_method="sympy", claim_id="c8",
                              status=ObligationStatus.refuted,
                              counterexample={"x": "0.5"},
                              support_kind="symbolic_check")
    disposition = claim_state_for(claim, [closed, refuted], [])
    assert disposition.status == ClaimStatus.refuted
    assert "反例" in disposition.note


def test_required_open_obligation_keeps_the_claim_open():
    claim = Claim(id="c9", statement="x**2 >= 0")
    open_obligation = ProofObligation(id="o9", statement="核验",
                                      kind="prove_inequality",
                                      acceptance_method="sympy", claim_id="c9")
    disposition = claim_state_for(claim, [open_obligation], [])
    assert disposition.status == ClaimStatus.proposed


def test_refutation_is_not_filtered_by_required_flag():
    """找到反例就是命题不成立 —— 与"这条义务是不是必要义务"无关。

    反例搜索义务是 `required=False` (它回答"命题是否根本不成立"), 若状态归并也按
    `required` 过滤反例, 一个被机器找到反例的命题会被判成"未决" —— 最不该的漏判。
    """
    claim = Claim(id="c10", statement="x**2 >= x")
    refute = ProofObligation(id="o10", statement="搜索反例", kind="refute",
                             acceptance_method="refute", claim_id="c10",
                             required=False, status=ObligationStatus.refuted,
                             counterexample={"x": "1/2"},
                             support_kind="symbolic_check")
    disposition = claim_state_for(claim, [refute], [])
    assert disposition.status == ClaimStatus.refuted
    assert "1/2" in disposition.note


def test_unclosed_counterexample_search_does_not_block_a_proven_claim():
    """没找到反例不构成证明, 因此它**不能**挡住已被证明的结论。"""
    claim = Claim(id="c11", statement="x**2 >= 0")
    refute = ProofObligation(id="o11", statement="搜索反例", kind="refute",
                             acceptance_method="refute", claim_id="c11",
                             required=False, status=ObligationStatus.blocked,
                             validation_status=ValidationStatus.unknown)
    proof = ProofObligation(id="o11b", statement="核验不等式",
                            kind="prove_inequality", acceptance_method="sympy",
                            claim_id="c11", status=ObligationStatus.closed,
                            validation_status=ValidationStatus.verified,
                            support_kind="symbolic_check")
    record = VerificationRecord(
        id="v11", tool="sympy", claim_id="c11",
        arguments={"lhs": "x**2", "rhs": "0", "relation": ">=", "variables": ["x"]},
        status="passed", validation_status=ValidationStatus.verified,
        support_kind="symbolic_check", stale=False)
    disposition = claim_state_for(claim, [refute, proof], [record],
                                  aligned=lambda r, c: True)
    assert disposition.status == ClaimStatus.supported


# --------------------------------------------------------------------------
# §10 验收矩阵: 可证明 / 可反驳 各一例 (端到端, 离线)
# --------------------------------------------------------------------------
def _run_claim(tmp_path, monkeypatch, project: str, request: str):
    from src import config
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import TeamSession

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    with TeamRun(project_id=project, problem_id="p1", request=request,
                 source_policy="user_kb", max_rounds=8) as team:
        TeamSession(team).run_to_completion()
        store = team.task_store.store
        claims = store.list_latest("claim") or []
        obligations = store.list_latest("obligation") or []
        records = store.list_latest("verification") or []
        return claims, obligations, records


@pytest.mark.parametrize("statement,expected", [
    ("请证明对所有实数 x 都有 x**2 >= 0", ClaimStatus.supported.value),
    ("请证明对所有实数 x 都有 x**2 >= x", ClaimStatus.refuted.value),
])
def test_team_decides_provable_and_refutable_claims(tmp_path, monkeypatch,
                                                    statement, expected):
    """可证明与可反驳各一例: 必须查到**义务/记录/状态**, 而不是只看文章存在。"""
    claims, obligations, records = _run_claim(
        tmp_path, monkeypatch, f"proj-{expected}", statement)
    assert claims, "没有登记任何结论"
    claim = claims[0]
    assert claim["status"] == expected, (claim, obligations, records)
    if expected == ClaimStatus.supported.value:
        assert any(o.get("status") == "closed" for o in obligations), obligations
        assert any(v.get("status") == "passed" and not v.get("stale")
                   for v in records), records
    else:
        # 被反例否决必须带**可回代的反例**, 不能只写一句"不成立"
        refuted = [o for o in obligations if o.get("status") == "refuted"]
        assert refuted, obligations
        assert any(o.get("counterexample") for o in refuted), refuted
        assert any(v.get("counterexample") for v in records), records


def test_unprovable_claim_is_not_declared_supported(tmp_path, monkeypatch):
    """工具跑不通时保持未决, **不得**升级为 supported (unsupported/缺条件一例)。"""
    claims, obligations, records = _run_claim(
        tmp_path, monkeypatch, "proj-unprovable",
        "请证明对所有复数 z 都有 |z| >= z")
    if not claims:
        pytest.skip("该题面未被形式化 (不属于本用例要固定的路径)")
    claim = claims[0]
    assert claim["status"] != ClaimStatus.supported.value, (claim, obligations, records)


# --------------------------------------------------------------------------
# §6.1 证据归属: 材料必须绑定到**具体命题版本**
# --------------------------------------------------------------------------
def test_evidence_is_bound_to_the_claim_version(tmp_path, monkeypatch):
    """`EvidenceLink` 必须带命题版本与适用条件, 且"命中"不等于"支持"。"""
    from src import config
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import TeamSession

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    with TeamRun(project_id="proj-link", problem_id="p1", run_id="run-link",
                 request="请证明对所有实数 x 都有 x**2 >= 0",
                 source_policy="user_kb", max_rounds=6) as team:
        store = team.task_store.store
        # 归属的可见性受运行身份约束 (G14): 证据必须带本次运行的 _scope,
        # 否则它连推理角色的上下文都进不去 —— 这一点本身就是契约的一部分。
        scope = {"project_id": "proj-link", "problem_id": "p1", "run_id": "run-link"}
        store.put("evidence", "src-support",
                  {"id": "src-support", "title": "支持性来源", "source_id": "src-support",
                   "locator": "p.9", "excerpt": "定理条件", "relation": "supports",
                   "_scope": scope, "_candidate_kind": "source"})
        store.put("evidence", "src-hit-only",
                  {"id": "src-hit-only", "title": "只是命中的材料",
                   "source_id": "src-hit-only", "excerpt": "无关段落",
                   "relation": "insufficient",
                   "_scope": scope, "_candidate_kind": "source"})
        TeamSession(team).run_to_completion()
        links = store.list_latest("evidence_link") or []
        claims = store.list_latest("claim") or []

    assert claims, "没有命题就没有归属可言"
    assert links, "材料没有绑定到任何命题 (交付包讲不出结论依据什么)"
    claim_versions = {c["id"]: int(c.get("version", 1) or 1) for c in claims}
    by_source = {}
    for link in links:
        claim_ref = link.get("claim_ref") or {}
        source_ref = link.get("source_ref") or {}
        assert claim_ref.get("id") in claim_versions, link
        # 归属绑定的是**具体版本**, 不是"那类对象"
        assert int(claim_ref.get("version", 0)) >= 1
        by_source[source_ref.get("id")] = link
    supporting = by_source.get("src-support")
    assert supporting is not None, by_source.keys()
    assert supporting["relation"] == "supports"
    assert supporting["condition_match"] is True
    assert supporting["locator"] == "p.9", "定位在归属里丢了 (回不到原文)"
    hit_only = by_source.get("src-hit-only")
    if hit_only is not None:
        # 命中不等于支持: 关系未判定时不得把适用条件标成"匹配"
        assert hit_only["relation"] == "insufficient"
        assert hit_only["condition_match"] is False


# --------------------------------------------------------------------------
# G12: 交付必须由门槛决定
# --------------------------------------------------------------------------
def test_export_failure_is_a_recoverable_error_not_an_empty_package(monkeypatch):
    """导出异常必须抛出来 (可恢复), 不能静默返回空包让界面以为"没跑过"。"""
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import ExportError, TeamSession

    with TeamRun(project_id="proj-exportfail", problem_id="p1",
                 request="判断参数为 2-(211,15,1) 的设计是否存在",
                 source_policy="user_kb", max_rounds=4) as team:
        session = TeamSession(team)

        def _boom(*args, **kwargs):
            raise RuntimeError("磁盘只读")

        monkeypatch.setattr("src.research.package.export_package", _boom)
        with pytest.raises(ExportError) as excinfo:
            session.export()
        assert "磁盘只读" in str(excinfo.value)
        assert "RuntimeError" in str(excinfo.value), "错误类型必须保留, 不能只留一句话"


def test_run_status_completed_equals_delivery_gate_passed(tmp_path, monkeypatch):
    """`completed` 的唯一依据是交付门槛通过; 未通过必须降级并写出理由。"""
    from src import config
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import TeamSession

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    with TeamRun(project_id="proj-gate", problem_id="p1",
                 request="随便写点什么", source_policy="user_kb", max_rounds=4) as team:
        outcome = team.run()
        # 交付评估必须落在结果里 (界面与复核据此解释等级)
        assert isinstance(outcome.delivery, dict) and "level" in outcome.delivery
        if outcome.status == "completed":
            assert outcome.delivery.get("accepted") is True
        else:
            assert outcome.delivery.get("accepted") is False
            assert outcome.stop_reason, "降级必须写出理由"
        # 等级绝不允许在没有编译过 PDF 时自称完整论文
        assert outcome.delivery.get("level") != "完整论文" or outcome.delivery.get(
            "publication_passed") is True
        session = TeamSession(team)
        assert session.outcome_summary()["run_id"] == outcome.run_id


def test_snapshot_closure_includes_verification_records():
    """快照必须收进核验记录 —— 缺了它, 交付门槛会报"结论缺少有效验证记录"。"""
    from src.research.snapshot import snapshot_from_store

    store = ResearchStore("snap-closure", db_path=":memory:")
    try:
        store.put("claim", "c1", {"id": "c1", "statement": "s"})
        store.put("verification", "v1", {"id": "v1", "claim_id": "c1",
                                         "status": "passed", "stale": False})
        store.put("obligation", "o1", {"id": "o1", "statement": "s",
                                       "claim_id": "c1", "status": "closed"})
        snapshot = snapshot_from_store(store, project_id="snap-closure",
                                       problem_id="p1", run_id="r1")
        assert [v.id for v in snapshot.verifications] == ["v1"]
        assert [c.id for c in snapshot.claims] == ["c1"]
        assert [o.id for o in snapshot.obligations] == ["o1"]
    finally:
        store.close()


def test_writing_map_is_derived_from_the_manuscript_blocks():
    """`writing_map` 必须真的算出来, 否则"结论已写入正文"会被判成未映射。"""
    from src.research.snapshot import snapshot_from_store
    from src.publication.schemas import Block, Manuscript, RefKind, Section
    from src.research.schemas import ObjectRef

    store = ResearchStore("snap-wm", db_path=":memory:")
    try:
        block = Block(role="claim", text="[c1] 结论")
        block.add_ref(RefKind.claim.value, ObjectRef(id="c1", version=1))
        manuscript = Manuscript(title="t", abstract="a",
                                sections=[Section(heading="1", blocks=[block])])
        store.put("manuscript", manuscript.manuscript_id,
                  {**manuscript.to_dict(), "manuscript": manuscript.to_dict()})
        snapshot = snapshot_from_store(store, project_id="snap-wm",
                                       problem_id="p1", run_id="r1")
        assert snapshot.writing_map.get("c1") == f"block:{block.block_id}"
    finally:
        store.close()


def test_projection_still_registers_candidates_without_the_commit_service():
    """投影层作为独立 API 仍然可用 (它只登记, 不判定)。"""
    store = ResearchStore("proj-direct", db_path=":memory:")
    try:
        projection = TeamProjection(store, run_id="run-1")
        task = _task(project_id="proj-direct")
        result = AgentResult(
            task_id=task.task_id, agent="reasoning", outcome="completed", summary="s",
            proposed_changes=[ChangeProposal(kind="claim", object_id="cx",
                                             payload={"id": "cx", "statement": "s"})])
        versions = projection.register(task, result)
        assert versions.get("cx") == 1
        assert store.get("claim", "cx") is not None
    finally:
        store.close()


# --------------------------------------------------------------------------
# G17: 唯一文稿 IR → 多格式同源 (Markdown / LaTeX / PDF)
# --------------------------------------------------------------------------
def _sample_manuscript():
    from src.publication.schemas import Block, Manuscript, RefKind, Section
    from src.research.schemas import ObjectRef

    claim = Block(role="claim", text="[clm-1] 该类设计不存在")
    claim.add_ref(RefKind.claim.value, ObjectRef(id="clm-1", version=1))
    source = Block(role="evidence", text="Bruck–Ryser–Chowla 定理给出必要条件")
    source.add_ref(RefKind.source.value, ObjectRef(id="src-1", version=1))
    manuscript = Manuscript(
        title="2-(211,15,1) 存在性判定", abstract="计数关系自洽但必要条件被违反。",
        sections=[Section(heading="1 引言", blocks=[claim]),
                  Section(heading="2 证据", blocks=[source]),
                  Section(heading="7 参考文献", blocks=[
                      Block(role="evidence", text="[1] Bruck R H. 定理. 1949.")])])
    return manuscript


def test_latex_render_uses_the_same_ir_and_has_no_dangling_references():
    """`.tex` 来自**唯一**文稿 IR: 引用/标签自洽, 不制造悬空引用。"""
    from src.publication.render_latex import render_latex

    manuscript = _sample_manuscript()
    tex = render_latex(manuscript, references={"src-1": "Bruck R H. 定理. 1949."})
    # 摘要/关键词/参考文献/结论锚点都在
    assert r"\begin{abstract}" in tex
    assert "参考文献" in tex
    assert "clm-1" in tex, "结论 id 必须留在正文里 (可反查)"
    # 引用的来源必须在文献表里真的存在 (否则出版门槛判悬空引用)
    import re as _re

    cites = set(_re.findall(r"\\cite\{([^}]*)\}", tex))
    bibitems = set(_re.findall(r"\\bibitem\{([^}]*)\}", tex))
    assert cites, "有来源引用却没有 \\cite"
    assert cites <= bibitems, f"悬空引用: {cites - bibitems}"
    # 没有来源时也必须有参考文献节 (说明检索范围), 而不是省略
    empty = render_latex(_sample_manuscript().model_copy(
        update={"sections": [_sample_manuscript().sections[0]]}))
    assert "参考文献" in empty


def test_team_delivery_writes_markdown_latex_and_pdf_from_one_source(tmp_path,
                                                                     monkeypatch):
    """离线整链: 交付包里 Markdown / `.tex` / `.pdf` 同源, 且等级按编译事实定。"""
    import json
    from pathlib import Path

    from src import config
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import TeamSession

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    with TeamRun(project_id="proj-pub", problem_id="p1",
                 request="判断参数为 2-(211,15,1) 的设计是否存在",
                 source_policy="user_kb", max_rounds=8) as team:
        session = TeamSession(team)
        session.run_to_completion()
        package = Path(session.export())
        level = session.delivery.level
        compile_status = getattr(session, "compile_status", "")
    assert (package / "manuscript.md").is_file()
    if (package / "manuscript.tex").is_file():
        tex = (package / "manuscript.tex").read_text(encoding="utf-8")
        md = (package / "manuscript.md").read_text(encoding="utf-8")
        claims = json.loads((package / "claims.json").read_text(encoding="utf-8"))
        for claim in claims:
            if claim.get("status") in ("supported", "refuted"):
                assert claim["id"] in tex, "结论未进入 .tex"
                assert claim["id"] in md, "结论未进入 Markdown"
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["compilation_status"] in ("ok", "unavailable") or \
            manifest["compilation_status"].startswith("failed")
        if manifest["compilation_status"] == "ok":
            pdf = package / "manuscript.pdf"
            assert pdf.is_file() and pdf.stat().st_size > 1024
            # 编译成功 + 引用一致 + 无悬空 → 才允许称完整论文
            assert level == "完整论文", manifest
        else:
            assert level != "完整论文", "没有 PDF 却自称完整论文"
        assert compile_status == "" or isinstance(compile_status, str)
