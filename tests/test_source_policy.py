from __future__ import annotations

"""P0-1 场景 ②: 只给方向 + 授权自主检索时, 研究循环必须能真的检索、入库并留痕。

不触网: 外部检索函数由测试注入 (引擎的 `search_fn`), 入库走真实 KB 代码路径
(DATA_DIR 已由 conftest 隔离到临时目录)。
"""

from src.research.loop import ResearchBudget, TheoryEngine
from src.research.question_planner import build_spec_from_input
from src.research.schemas import (
    ActionType,
    ResearchAction,
    ResearchSpec,
    SourcePolicy,
    SourceSummary,
    TaskKind,
)
from src.research.store import KIND_SPEC, ResearchStore

PROBLEM = "对所有实数 x、y: x**2 + y**2 >= 2*x*y"


def _engine(spec: ResearchSpec, tmp_path, name: str, **kwargs) -> TheoryEngine:
    store = ResearchStore(name, db_path=tmp_path / f"{name}.sqlite")
    return TheoryEngine(spec, store, budget=ResearchBudget(max_actions=6), **kwargs)


def _autonomous_spec(project_id: str = "auto1") -> ResearchSpec:
    """方向类问题 + 已确认的处理/结果对象 + 授权自主检索。"""
    spec = build_spec_from_input(PROBLEM, project_id=project_id)
    spec.source_policy = SourcePolicy.autonomous
    assert spec.contract is not None
    spec.contract.task_kind = TaskKind.mechanism
    spec.contract.objects = ["可分性", "信道变化"]
    return spec


def _paper(title: str, doi: str) -> dict:
    return {"title": title, "doi": doi, "abstract": "摘要片段", "year": "2022",
            "api_source": "arXiv"}


def test_retrieval_available_follows_policy(tmp_path):
    plain = ResearchSpec(project_id="noauth", problem_statement=PROBLEM)
    assert plain.source_policy is SourcePolicy.user_kb
    assert _engine(plain, tmp_path, "noauth").retrieval_available is False

    allowed = _autonomous_spec("auth")
    assert _engine(allowed, tmp_path, "auth").retrieval_available is True


def test_autonomous_retrieval_ingests_and_records_coverage(tmp_path):
    calls: list[str] = []

    def search_fn(query, limit):
        calls.append(query)
        return [_paper("Channel variation and separability", "10.1/a")]

    terms = {"信道变化": ["channel variation"], "可分性": ["separability"]}
    engine = _engine(_autonomous_spec("auto2"), tmp_path, "auto2",
                     search_fn=search_fn, terminology=terms)
    assert engine.bootstrap()
    claim = engine._claims()[0]
    assert engine._act_retrieve_targeted(ResearchAction(
        action_type=ActionType.retrieve_targeted, object_id=claim.id)) is True

    assert calls, "必须真的发出检索式 (由契约规划, 不是单一查询词)"
    coverage = engine.spec.coverage
    assert coverage is not None and coverage.executed
    assert len(coverage.queries) == len(calls) > 1
    assert coverage.hits == len(calls) and coverage.ingested >= 1
    assert coverage.engines == ["arXiv", "Semantic Scholar", "OpenAlex"]
    assert "策略 autonomous" in coverage.scope_note
    # 入库后同一动作内即可检索到并写成带定位的证据
    items = engine._evidence()
    assert items and items[0].source_id
    # 覆盖记录随规格落盘, 供工作台与人审复核
    stored = engine.store.get(KIND_SPEC, "problem") or {}
    assert stored.get("coverage", {}).get("queries")
    # 入库后知识服务被重新装配, 后续 read_source 能读到内容
    assert engine.knowledge_available


def test_autonomous_retrieval_without_hits_records_boundary(tmp_path):
    engine = _engine(_autonomous_spec("auto3"), tmp_path, "auto3",
                     search_fn=lambda q, n: [])
    assert engine.bootstrap()
    claim = engine._claims()[0]
    assert engine._act_retrieve_targeted(ResearchAction(
        action_type=ActionType.retrieve_targeted, object_id=claim.id)) is False
    coverage = engine.spec.coverage
    assert coverage is not None and coverage.executed
    assert any("未返回任何可用记录" in u for u in coverage.uncovered)
    kinds = {f.kind for f in engine.routes.failures_for(claim.id)}
    assert "no_retrieval_hit" in kinds, kinds


def test_retrieval_failure_is_distinct_from_no_hit(tmp_path):
    def broken(query, limit):
        raise TimeoutError("network down")

    engine = _engine(_autonomous_spec("auto4"), tmp_path, "auto4", search_fn=broken)
    assert engine.bootstrap()
    claim = engine._claims()[0]
    assert engine._act_retrieve_targeted(ResearchAction(
        action_type=ActionType.retrieve_targeted, object_id=claim.id)) is False
    assert any("TimeoutError" in f for f in engine.spec.coverage.failures)
    kinds = {f.kind for f in engine.routes.failures_for(claim.id)}
    assert "retrieval_failed" in kinds, kinds
    assert "no_retrieval_hit" not in kinds, "检索失败不得被记成'无命中'"


def test_user_kb_policy_does_not_search_outside(tmp_path):
    def forbidden(query, limit):  # pragma: no cover - 被调用即失败
        raise AssertionError("user_kb 策略不得外搜")

    spec = build_spec_from_input(PROBLEM, project_id="strict",
                                 source_summary=SourceSummary(source_set_id="",
                                                              documents=0))
    engine = _engine(spec, tmp_path, "strict", search_fn=forbidden)
    assert engine.retrieval_available is False
    assert engine._act_retrieve_targeted(ResearchAction(
        action_type=ActionType.retrieve_targeted, object_id="")) is False
    assert engine.spec.coverage is None, "未授权检索时不得产生覆盖记录"


def test_autonomous_policy_starts_without_any_source_set(tmp_path, monkeypatch):
    """场景 ②: 没有预建资料库也要能启动研究, 并在工作台看到授权策略。"""
    from fastapi.testclient import TestClient

    from src import config, server

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    client = TestClient(server.app)
    started = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "auto-api", "problem_id": "p1",
        "request": PROBLEM, "source_policy": "autonomous",
    })
    assert started.status_code == 200, started.text[:300]
    assert started.json()["contract"], started.json()

    state = client.get("/api/research/auto-api/state", params={"problem_id": "p1"})
    assert state.status_code == 200, state.text[:300]
    spec = state.json()["spec"]
    assert spec["source_policy"] == "autonomous"
    assert "coverage" in spec          # 尚未检索时为 None, 但字段必须在契约里

    client.post(f"/api/sessions/{started.json()['thread_id']}/stop")
    server.shutdown_sessions()


# ----------------------------------------------------------------------
# P0-1: 无本地库不能阻断自主检索 (两处 `knowledge_available` 一票否决)
# ----------------------------------------------------------------------

def test_no_local_kb_still_produces_evidence_gap_and_retrieval_requests(tmp_path):
    """无本地知识库 + 授权自主检索 → 必须产生证据缺口与定向检索请求。

    计划书 P0-1 的落地回归。原始缺陷有**两处**配合才构成"自主检索被整个循环跳过":

    1. `_gaps()` 用 `knowledge_available` 决定是否产生证据缺口 —— 没有本地库就**不产
       缺口**, 于是根本没有东西可转成检索请求;
    2. `_retrieval_requests()` 首行 `if not self.knowledge_available: return []`。

    只修其中一处仍会得到 0 个请求, 因此这条测试同时覆盖两处。
    """
    engine = _engine(_autonomous_spec("nogap"), tmp_path, "nogap")
    assert engine.knowledge_available is False, "本用例前提: 没有本地知识库"
    assert engine.retrieval_available is True, "授权自主检索时检索能力必须可用"

    assert engine.bootstrap()
    claims = engine._claims()
    assert claims
    gaps = engine._gaps(claims, [])
    evidence = [g for g in gaps if g.gap_type.value == "missing_evidence"]
    assert evidence, "无本地库但已授权自主检索时, 必须产生证据缺口"

    requests = engine._retrieval_requests(claims, gaps)
    assert requests, "证据缺口必须能转成定向检索请求 (否则'自主检索'名存实亡)"
    assert all(request.get("claim_id") for request in requests)
    # 缺口必须声明可执行动作, 否则控制器拿不到可派发的动作
    resolvable = {action for gap in evidence for action in (gap.resolving_actions or [])}
    assert ActionType.retrieve_targeted.value in resolvable


def test_user_kb_policy_without_local_kb_produces_no_retrieval_request(tmp_path):
    """反向: 只读本地上传资料的策略下, 无本地库就**不该**产生检索请求。"""
    spec = build_spec_from_input(PROBLEM, project_id="nokb")
    spec.source_policy = SourcePolicy.user_kb
    engine = _engine(spec, tmp_path, "nokb")
    assert engine.retrieval_available is False
    assert engine.bootstrap()
    claims = engine._claims()
    gaps = engine._gaps(claims, [])
    assert not [g for g in gaps if g.gap_type.value == "missing_evidence"]
    assert engine._retrieval_requests(claims, gaps) == []


def test_gaps_are_persisted_so_they_reach_the_package(tmp_path):
    """缺口必须落盘, 否则交付包的 `gaps.json` 为空, 事后无法复核。

    实测缺陷: `_gaps()` 只做内存计算, 而 `_freeze_snapshot()` 从存储读 `KIND_GAP` ——
    真实运行里决策日志有 `missing_evidence` 缺口, `gaps.json` 却是 0 条。
    """
    from src.research.store import KIND_GAP, KIND_SPEC

    engine = _engine(_autonomous_spec("gappack"), tmp_path, "gappack")
    assert engine.bootstrap()
    state = engine._compute_state()
    gaps = state.get("gaps") or []
    assert gaps, "无本地库 + 自主检索时应产生缺口"
    engine._persist_gaps(gaps)

    stored = engine.store.list_latest(KIND_GAP)
    assert stored, "缺口必须落盘"
    persisted_ids = {str(item.get("id")) for item in stored}
    # `_compute_state()` 里 gaps 已是序列化 dict
    assert {str(gap["id"]) for gap in gaps} <= persisted_ids

    # 冻结快照必须带上这些缺口 (交付包即从这里导出)
    snapshot = engine._freeze_snapshot()
    assert snapshot.gaps, "冻结快照必须包含缺口"
    assert {gap.id for gap in snapshot.gaps} <= persisted_ids
    assert engine.store.get(KIND_SPEC, "problem") is not None


def test_persist_gaps_skips_experiment_specs(tmp_path):
    """实验规格同用 `KIND_GAP` 但 id 以 `exp-` 开头, 不得被缺口落盘覆盖。"""
    from src.research.store import KIND_GAP

    engine = _engine(_autonomous_spec("gapkeep"), tmp_path, "gapkeep")
    assert engine.bootstrap()
    engine.store.put(KIND_GAP, "exp-keep", {"id": "exp-keep", "purpose": "不要覆盖我"})
    engine._persist_gaps([engine._gaps(engine._claims(), [])[0]])

    kept = engine.store.get(KIND_GAP, "exp-keep")
    assert kept and kept.get("purpose") == "不要覆盖我"


# ----------------------------------------------------------------------
# P0-2 后半段: 检索到的相反来源必须使结论失效重验
# ----------------------------------------------------------------------

def _verified_claim_engine(tmp_path, name: str):
    """造一个"已有结论 + 有效验证记录 + 已关闭义务"的引擎。"""
    from src.research.schemas import (
        ClaimStatus,
        Coverage,
        ObligationStatus,
        ProofObligation,
        SupportKind,
        ValidationStatus,
        VerificationRecord,
    )

    engine = _engine(_autonomous_spec(name), tmp_path, name)
    assert engine.bootstrap()
    claim = engine._claims()[0]
    claim.status = ClaimStatus.supported
    claim.coverage = Coverage.target
    claim.validation_status = ValidationStatus.verified
    claim.support_kind = SupportKind.theorem_application
    engine._save_claim(claim)

    obligation = ProofObligation(
        id="obl-1", claim_id=claim.id, kind="symbolic_check", required=True,
        status=ObligationStatus.closed, acceptance_method="sympy",
        statement="核验结论", validation_status=ValidationStatus.verified)
    engine._save_obligation(obligation)

    # 一条**有效**验证记录: 回写必须把它标记过期, 而不是删除 (保住审计线索)
    record = VerificationRecord(
        id="ver-1", claim_id=claim.id, obligation_id=obligation.id,
        validation_status=ValidationStatus.verified, stale=False,
        raw_output="sympy 核验通过")
    engine.store.put("verification", record.id, record.model_dump(mode="json"))
    return engine, claim, obligation


def _contradicting_evidence(claim_id: str, evidence_id: str = "ev-contra"):
    from src.research.schemas import ReferenceStatus, SourceEvidence, SupportKindOfEvidence

    return SourceEvidence(
        id=evidence_id, claim_id=claim_id, title="A source that refutes the claim",
        support=SupportKindOfEvidence.contradicts,
        support_reason="该来源给出与结论相反的定理", reference_status=ReferenceStatus.applicable)


def test_contradicting_source_invalidates_conclusion_and_marks_verification_stale(tmp_path):
    """检索到相反来源 -> 命题换代 + 旧验证记录标记过期 + 义务重开。

    这是计划书 P0-2 的落地点: 检索必须在**同一研究链**里纠正先前的推导, 而不是只往
    参考文献表里补一条。原始缺陷是 `engine.finalize()` 先跑、`gather_publication_evidence()`
    后跑且不回写冻结快照, 于是"检索到相反结果"对结论毫无影响。
    """
    from src.research.schemas import ClaimStatus, ObligationStatus

    engine, claim, obligation = _verified_claim_engine(tmp_path, "contra1")
    before_version = claim.version
    engine.store.put("evidence", "ev-contra",
                     _contradicting_evidence(claim.id).model_dump(mode="json"))

    affected = engine._literature_impact_recheck()

    assert affected == [claim.id], affected
    updated = engine._get_claim(claim.id)
    assert updated.status == ClaimStatus.in_progress, updated.status
    assert updated.version > before_version, "命题必须落盘新版本"
    assert "相反来源" in updated.notes

    records = [v for v in engine._verifications() if v.claim_id == claim.id]
    assert records and all(v.stale for v in records), "旧验证记录必须标记过期"
    assert all("相反来源" in (v.raw_output or "") for v in records)

    reopened = [o for o in engine._obligations() if o.id == obligation.id]
    assert reopened and reopened[0].status == ObligationStatus.open, "义务必须重开"
    assert any("文献回写" in note for note in engine._notes)


def test_literature_recheck_is_idempotent(tmp_path):
    """同一相反来源只触发一次回写 —— 否则每一步都会加版本, 审计线索被淹没。"""
    engine, claim, _ = _verified_claim_engine(tmp_path, "contra2")
    engine.store.put("evidence", "ev-contra",
                     _contradicting_evidence(claim.id).model_dump(mode="json"))

    assert engine._literature_impact_recheck() == [claim.id]
    version_after_first = engine._get_claim(claim.id).version
    assert engine._literature_impact_recheck() == [], "第二次不得再次触发"
    assert engine._get_claim(claim.id).version == version_after_first


def test_supporting_evidence_does_not_invalidate_anything(tmp_path):
    """反向: 只有 contradicts 才触发失效; 支持/背景来源不得动结论。"""
    from src.research.schemas import ReferenceStatus, SourceEvidence, SupportKindOfEvidence

    engine, _claim, _obligation = _verified_claim_engine(tmp_path, "contra3")
    version_before = engine._get_claim(_claim.id).version
    for evidence_id, support in (("ev-bg", SupportKindOfEvidence.background),
                                 ("ev-sup", SupportKindOfEvidence.supports)):
        engine.store.put("evidence", evidence_id, SourceEvidence(
            id=evidence_id, claim_id=_claim.id, title=f"{support.value} source",
            support=support, reference_status=ReferenceStatus.applicable
        ).model_dump(mode="json"))

    assert engine._literature_impact_recheck() == []
    assert engine._get_claim(_claim.id).version == version_before
    assert not any(v.stale for v in engine._verifications() if v.claim_id == _claim.id)

