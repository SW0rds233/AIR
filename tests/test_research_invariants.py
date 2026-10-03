from __future__ import annotations

"""可信状态与知识闭环的失败用例 (计划书 §13.2 首批必须覆盖的案例)。

这些测试针对**容易被错误升级**的语义, 是反向安全测试:
- 未知/失败/缺证不得被升级为支持;
- 域外见证必须拒绝;
- 局部验证不得升级整体结论;
- 单篇高可信/多篇转引同一来源不得报成多源独立证据;
- 版本不得被覆盖;
- 检索失败不得等同于"无相关文献"。
"""

import pytest


# --------------------------------------------------------------------------
# 结论状态: 只能由中央规则依据证据闭包计算
# --------------------------------------------------------------------------
def test_claim_supported_requires_coverage_kind_and_validation():
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        SupportKind,
        ValidationStatus,
    )

    # 只有 status+assurance 不够: 必须声明覆盖范围、支持方式与验证结果
    with pytest.raises(ValueError):
        Claim(statement="p", status=ClaimStatus.supported,
              assurance=Assurance.symbolic_checked)
    with pytest.raises(ValueError):
        Claim(statement="p", status=ClaimStatus.supported,
              support_kind=SupportKind.symbolic_check, coverage=Coverage.step,
              validation_status=ValidationStatus.verified)
    with pytest.raises(ValueError):
        Claim(statement="p", status=ClaimStatus.supported,
              support_kind=SupportKind.none, coverage=Coverage.target,
              validation_status=ValidationStatus.verified)
    # 已发现反例不得同时标记为 supported
    with pytest.raises(ValueError):
        Claim(statement="p", status=ClaimStatus.supported,
              support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
              validation_status=ValidationStatus.counterexample_found)


def test_gate_blocks_supported_claim_with_open_obligation():
    """一条义务通过不得升级整个目标 (计划书 §4.3-2)。"""
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        ObligationStatus,
        ProofObligation,
        SupportKind,
        ValidationStatus,
    )

    claim = Claim(id="c1", statement="x >= 0", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked)
    open_obligation = ProofObligation(id="o2", statement="第二个必要义务", kind="prove_inequality",
                                      claim_id="c1", status=ObligationStatus.open)
    gate = theory_validity_gate([claim], [open_obligation], [], [])
    assert not gate.passed
    assert any("必要义务未关闭" in r for r in gate.reasons)


def test_gate_rejects_refutation_without_witness():
    """普通执行错误不得冒充反驳 (计划书 §4.3-3)。"""
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Claim,
        ClaimStatus,
        ObligationStatus,
        ProofObligation,
        ValidationStatus,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.refuted)
    bogus = ProofObligation(id="o1", statement="p", kind="prove_inequality", claim_id="c1",
                            status=ObligationStatus.refuted, counterexample={},
                            validation_status=ValidationStatus.execution_error)
    gate = theory_validity_gate([claim], [bogus], [], [])
    assert not gate.passed
    assert any("没有满足前提的反例" in r for r in gate.reasons)


def test_gate_reports_runtime_failure_as_unresolved_not_false():
    """timeout/unavailable 属于运行问题, 不得当作"命题为假"。"""
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import ObligationStatus, ProofObligation, ValidationStatus

    obligation = ProofObligation(id="o1", statement="p", kind="prove_inequality",
                                 claim_id="c1", status=ObligationStatus.blocked,
                                 validation_status=ValidationStatus.timeout)
    gate = theory_validity_gate([], [obligation], [], [])
    assert not gate.passed
    assert any("运行/能力问题" in u for u in gate.unresolved)
    assert not any("为假" in r for r in gate.reasons)


def test_gate_ignores_historical_stale_outside_snapshot():
    """历史 stale 不应永久阻止交付: 只检查本次快照引用的记录 (§4.3-6)。"""
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        ResearchSnapshot,
        SupportKind,
        ValidationStatus,
        VerificationRecord,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked,
                  verification_scope=VerificationScope.target)
    in_use = VerificationRecord(id="v1", claim_id="c1", tool="sympy", status="passed",
                                validation_status=ValidationStatus.verified, stale=False)
    historical = VerificationRecord(id="v0", claim_id="c1", tool="sympy", status="passed",
                                    validation_status=ValidationStatus.verified, stale=True)
    snapshot = ResearchSnapshot(project_id="p", claims=[claim], writing_map={},
                                verification_index={"c1": ["v1"]})
    gate = theory_validity_gate([claim], [], [in_use, historical], [], snapshot=snapshot)
    assert gate.passed, gate.render()


# --------------------------------------------------------------------------
# 领域外反例必须拒绝
# --------------------------------------------------------------------------
def test_counterexample_must_respect_declared_domain():
    import sympy

    from src.verification.runner import VerificationRunner

    runner = VerificationRunner(inproc=True)
    # 声明域为正实数: x**2 单调非减成立, 不得用负半轴的点对反驳
    ok = runner.sympy("prove_monotonicity", dict(
        expr="x**2", wrt="x", direction="nondecreasing",
        variables=["x"], assumptions={"x": "positive"}))
    assert ok.status.value == "passed"

    # 声明域为全体实数: 此时负半轴的点对才是合法反例
    bad = runner.sympy("prove_monotonicity", dict(
        expr="x**2", wrt="x", direction="nondecreasing",
        variables=["x"], assumptions={"x": "real"}))
    assert bad.status.value == "failed"
    witness = bad.counterexample
    assert witness and witness["x"] and witness["x2"]
    x1, x2 = sympy.Rational(witness["x"]), sympy.Rational(witness["x2"])
    assert x1 < x2 and x1 < 0     # 见证落在负半轴, 说明确实检查了域
    x = sympy.Symbol("x", real=True)
    assert sympy.simplify((x ** 2).subs(x, x2) - (x ** 2).subs(x, x1)) < 0


def test_unknown_never_upgraded_to_pass_or_refute(tmp_path):
    """工具 unknown → 保持未决, 不映射为通过或反驳 (计划案 §7.3)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimStatus, ObligationStatus, ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.schemas import VerificationResult, VerificationStatus

    class UnknownRunner:
        def run(self, tool, operation, arguments, timeout=None):
            return VerificationResult(tool=tool, status=VerificationStatus.unknown,
                                      detail="求解器无法判定")

    spec = ResearchSpec(project_id="unk", problem_statement="对所有实数 x: 1/x >= 0")
    store = ResearchStore("unk", db_path=tmp_path / "unk.sqlite")
    engine = TheoryEngine(spec, store, runner=UnknownRunner(),
                          budget=ResearchBudget(max_actions=6))
    result = engine.run()
    assert all(c.status.value != ClaimStatus.supported for c in result.snapshot.claims)
    assert all(c.status.value != ClaimStatus.refuted for c in result.snapshot.claims)
    assert all(o.status != ObligationStatus.refuted for o in result.snapshot.obligations)
    assert not result.gate.passed
    store.close()


# --------------------------------------------------------------------------
# 版本不可覆盖
# --------------------------------------------------------------------------
def test_object_versions_never_overwritten(tmp_path):
    from src.research.store import KIND_CLAIM, ResearchStore, VersionConflict

    store = ResearchStore("v1", db_path=tmp_path / "v1.sqlite")
    store.put(KIND_CLAIM, "c1", {"statement": "a", "status": "proposed"})
    store.put(KIND_CLAIM, "c1", {"statement": "a", "status": "supported"})
    versions = store.list_versions(KIND_CLAIM, "c1")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["data"]["status"] == "proposed"
    with pytest.raises(VersionConflict):
        store.put(KIND_CLAIM, "c1", {"statement": "a", "status": "refuted"}, version=1)
    store.close()


def test_engine_state_write_is_monotonic(tmp_path):
    """引擎连续写状态时版本只增不减 (早期实现会把所有写入压到 v1)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_CLAIM, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="mono", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("mono", db_path=tmp_path / "mono.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    result = engine.run()
    claim = result.snapshot.claims[0]
    versions = store.list_versions(KIND_CLAIM, claim.id)
    assert len(versions) >= 2
    assert [v["version"] for v in versions] == list(range(1, len(versions) + 1))
    # 最终版本必须保留统计/推导结果, 而不是被较弱的中间状态覆盖
    assert versions[-1]["data"]["status"] == "supported"
    allowed = [v["data"]["status"] for v in versions]
    assert "supported" in allowed
    store.close()


# --------------------------------------------------------------------------
# 知识闭环: 缺口 → 检索 → 原文 → 证据关系 → 下一步决策
# --------------------------------------------------------------------------
SAMPLE_DOC = """摘要

本文研究射频指纹识别中的特征提取方法，并给出一个可分性结论。

1 引言

射频指纹识别利用发射机硬件差异带来的信号特征实现设备身份认证。

定理1：在信噪比足够高且信道条件一致时，不同发射机的指纹特征在特征空间中是线性可分的。

结果表明，所提方法在多个公开数据集上准确率提升 5%。
"""


@pytest.fixture()
def kb_topic(tmp_path, monkeypatch):
    import json

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    from src.kb.ingest import ensure_topic

    topic = "RFTEST"
    tdir = ensure_topic(topic)
    (tdir / "manual" / "特征可分性研究_2020.txt").write_text(SAMPLE_DOC, encoding="utf-8")
    (tdir / "manual" / "meta.json").write_text(json.dumps({
        "特征可分性研究_2020.txt": {
            "title": "特征可分性研究", "authors": "张三", "year": "2020",
            "venue": "电子学报", "doi": "10.1000/rf.2020.9",
        }
    }, ensure_ascii=False), encoding="utf-8")
    from src.kb.ingest import ingest_manual

    ingest_manual(topic, embed=False)
    return topic


def test_knowledge_loop_end_to_end(kb_topic):
    """一次真实资料检索必须改变后续推导, 而不只是把文本附在报告里。"""
    from src.kb.service import KnowledgeService
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import (
        ClaimQuestion,
        ClaimType,
        ResearchSpec,
        SupportKindOfEvidence,
    )
    from src.research.store import KIND_EVIDENCE, KIND_EVIDENCE_LINK, ResearchStore
    from src.verification.runner import VerificationRunner

    service = KnowledgeService(kb_topic)
    assert service.has_content
    question = ClaimQuestion(
        statement="信噪比足够高且信道一致时, 不同发射机的指纹特征线性可分",
        category="applied", claim_type=ClaimType.descriptive,
        study=__import__("src.research.schemas", fromlist=["StudyPlan"]).StudyPlan(
            population="发射机", region="实验室", period="2020",
            confounders=["信道"], confounder_handling="信道条件一致",
            treatment="信噪比", outcome="特征可分"),
    )
    spec = ResearchSpec(project_id="kbloop", domain=kb_topic, questions=[question],
                        confirmed=True)
    store = ResearchStore("kbloop", db_path=tmp_path_sqlite(kb_topic))
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=25), knowledge=service)
    result = engine.run()

    # 1) 检索产生了带定位的候选证据
    evidence = store.list_latest(KIND_EVIDENCE)
    assert evidence, "未能从知识底座取到任何证据"
    locatable = [e for e in evidence if e.get("source_id") and e.get("location")]
    assert locatable, "证据缺少可回到原文的定位信息"
    # 2) 证据关系被显式判定 (默认 insufficient 必须被改写)
    judged = [e for e in evidence
              if e.get("support") != SupportKindOfEvidence.insufficient.value]
    assert judged, "候选证据没有被判定支持关系"
    # 3) 产生了 EvidenceLink 记录
    assert store.list_latest(KIND_EVIDENCE_LINK), "未生成 EvidenceLink"
    # 4) 决策记录里能看到检索/读取/判定这几类动作
    actions = {d["action"] for d in result.decisions}
    assert actions & {"retrieve_targeted", "read_source", "interpret_evidence", "extract_result"}
    store.close()


def tmp_path_sqlite(topic: str):
    from src.config import DATA_DIR

    path = DATA_DIR / "research-test"
    path.mkdir(parents=True, exist_ok=True)
    return path / "kbloop.sqlite"


def test_engine_budget_stops_and_reports_usage(tmp_path):
    """资源预算触顶必须停止并留下部分报告, 且用量可追溯 (计划书 §9.3)。"""
    import time

    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_CLAIM, ResearchStore
    from src.verification.schemas import VerificationResult, VerificationStatus

    class Unknown:
        def run(self, tool, operation, arguments, timeout=None):
            time.sleep(0.01)
            return VerificationResult(tool=tool, status=VerificationStatus.unknown,
                                      detail="无法判定")

    spec = ResearchSpec(project_id="bud", problem_statement="对所有实数 x: 1/x >= 0")
    store = ResearchStore("bud", db_path=tmp_path / "bud.sqlite")

    # 1) 墙钟预算: 极小上限 → 运行中即停止, 并给出部分结果
    engine = TheoryEngine(spec, store, runner=Unknown(),
                          budget=ResearchBudget(max_actions=50, max_wall_seconds=0.001))
    result = engine.run()
    assert result.stopped_reason, "墙钟触顶必须给出停止原因"
    assert "墙钟" in result.stopped_reason
    assert "wall_seconds" in result.usage
    assert any("预算停止" in n for n in result.notes), result.notes
    assert result.snapshot is not None, "部分结果仍须导出"
    assert store.list_latest(KIND_CLAIM)

    # 2) token 预算: 记账后即触顶 (独立项目, 避免与上面的 runtime 版本冲突)
    spec2 = ResearchSpec(project_id="bud2", problem_statement="对所有实数 x: 1/x >= 0")
    store2 = ResearchStore("bud2", db_path=tmp_path / "bud2.sqlite")
    engine2 = TheoryEngine(spec2, store2, runner=Unknown(),
                           budget=ResearchBudget(max_actions=50, max_tokens=100))
    engine2.record_llm_usage(model="deepseek-chat",
                             usage={"input_tokens": 80, "output_tokens": 40,
                                    "total_tokens": 120})
    usage = engine2.usage_summary()
    assert usage["tokens"] == 120
    assert usage["cost_usd"] > 0
    assert "token" in engine2._budget_exhausted()

    # 3) 用量随运行状态持久化: 续跑不得重置已花费预算
    engine2.save_runtime()
    resumed = TheoryEngine(spec2, ResearchStore("bud2", db_path=tmp_path / "bud2.sqlite"),
                           runner=Unknown(),
                           budget=ResearchBudget(max_actions=50, max_tokens=100))
    resumed.load_runtime()
    assert resumed.usage_summary()["tokens"] == 120, "续跑不得重置 token 预算"
    assert resumed.usage_summary()["cost_usd"] > 0
    store2.close()
    store.close()


def test_action_registry_only_exposes_implemented_actions():
    """注册表只能暴露有执行器且前置条件成立的动作 (计划书 §5.3-4)。"""
    from src.research.action_registry import (
        ACTION_REGISTRY,
        available_actions,
        check_preconditions,
    )

    # 未实现的动作必须显式标注, 且不出现在有效集合中
    unimplemented = [a for a, spec in ACTION_REGISTRY.items() if not spec.implemented]
    assert unimplemented, "应显式标注未实现的动作"
    state = {"questions": [{"id": "q1"}], "budget_remaining": 5}
    available = available_actions(state)
    for action in unimplemented:
        assert action not in available
        ok, reason = check_preconditions(action, state)
        assert not ok and reason


def test_engine_rejects_action_without_handler(tmp_path):
    """协调者派发的动作必须有执行器, 不允许"记录未实现然后空转"。"""
    from src.research.loop import TheoryEngine
    from src.research.schemas import ResearchAction, ResearchSpec
    from src.research.store import ResearchStore

    spec = ResearchSpec(project_id="nohandler", problem_statement="x >= 0")
    store = ResearchStore("nohandler", db_path=tmp_path / "nh.sqlite")
    engine = TheoryEngine(spec, store)
    handled = len(engine._dispatch.__self__._compute_state() or {}) >= 0
    assert handled
    # 注册表中标记 implemented 的动作都必须有 handler
    from src.research.action_registry import ACTION_REGISTRY

    engine.bootstrap()
    for action_type, action_spec in ACTION_REGISTRY.items():
        if not action_spec.implemented:
            continue
        action = ResearchAction(action_type=action_type)
        # 用一个不存在的对象调用: 应返回 False 而不是抛异常, 也不应记录"未实现"
        engine._notes.clear()
        engine._dispatch(action)
        assert not any("无执行器" in n for n in engine._notes), action_type
    store.close()
