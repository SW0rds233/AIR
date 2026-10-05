from __future__ import annotations

"""理论研究模式 (theory mode) 测试。

覆盖《下一步执行方案》第 13 节要求的验收情形: 真命题、假命题、缺失前提、
循环证明、矛盾假设、工具未决、陈述偷换、Lean 留洞、重复已有结果、
论文遗漏前提、修改上游假设、恢复/重试。
"""


import pytest

ACCEPTANCE_TEXT = (
    "判断对所有实数 x、y，是否有 x² + y² ≥ 2xy；给出证明和等号条件。"
    "再判断是否可以把 ≥ 换成 >。"
)

_CACHE: dict = {}


def test_dependency_graph_rejects_cycle_and_propagates_stale():
    from src.research.dependency_graph import CyclicDependencyError, DependencyGraph

    graph = DependencyGraph([("asm", "L1"), ("L1", "L2"), ("L2", "T")])
    assert graph.find_cycle() is None
    assert graph.stale_closure("asm") == {"L1", "L2", "T"}
    assert graph.dependents_of("L1", transitive=True) == {"L2", "T"}
    with pytest.raises(CyclicDependencyError):
        graph.add_edge("T", "asm")
    with pytest.raises(CyclicDependencyError):
        graph.add_edge("L2", "L2")


def test_store_versioning_idempotency_and_revision(tmp_path):
    from src.research.store import KIND_CLAIM, ResearchStore, RevisionConflict, VersionConflict

    store = ResearchStore("s1", db_path=tmp_path / "s1.sqlite")
    assert store.put(KIND_CLAIM, "c1", {"v": 1}) == 1
    assert store.put(KIND_CLAIM, "c1", {"v": 2}) == 2
    assert store.get(KIND_CLAIM, "c1")["v"] == 2
    assert store.get(KIND_CLAIM, "c1", version=1)["v"] == 1
    assert len(store.list_versions(KIND_CLAIM, "c1")) == 2
    with pytest.raises(RevisionConflict):
        store.put(KIND_CLAIM, "c1", {"v": 3}, expected_revision=1)
    # 研究历史不可覆盖: 显式指定已存在的历史版本会被拒绝
    with pytest.raises(VersionConflict):
        store.put(KIND_CLAIM, "c1", {"v": 9}, version=1)
    # 同内容重复写入幂等
    assert store.put(KIND_CLAIM, "c1", {"v": 2}) == 2
    assert store.append_event("run_start", {"project_id": "s1", "problem_id": "p1"},
                              idempotency_key="k1") is True
    assert store.append_event("run_start", {"project_id": "s1", "problem_id": "p1"},
                              idempotency_key="k1") is False
    assert len(store.events()) == 1
    store.close()


def test_store_atomic_step_and_tool_ledger(tmp_path):
    """原子研究步骤提交 + 工具运行账本 (计划书 §9.2)。"""
    from src.research.store import (
        KIND_CLAIM,
        KIND_OBLIGATION,
        ResearchStore,
        StepAlreadyApplied,
    )

    store = ResearchStore("s2", db_path=tmp_path / "s2.sqlite")
    versions = store.submit_step(
        writes=[(KIND_CLAIM, "c1", {"v": 1}), (KIND_OBLIGATION, "o1", {"v": 1})],
        events=[("formulated", {"claims": 1, "obligations": 1, "problem_id": "p1"})],
        idempotency_key="step-1")
    assert versions == {"c1": 1, "o1": 1}
    assert len(store.events()) == 1
    # 幂等: 同一步骤不重复执行
    with pytest.raises(StepAlreadyApplied):
        store.submit_step(writes=[(KIND_CLAIM, "c1", {"v": 2})],
                          events=[("formulated", {"claims": 2, "obligations": 2,
                                                  "problem_id": "p1"})],
                          idempotency_key="step-1")
    assert store.get(KIND_CLAIM, "c1")["v"] == 1

    # 工具账本: 待执行登记 → 幂等键命中不再执行 → 结果未知可被识别
    assert store.begin_tool_run("r1", "sympy", "simplify", {"expr": "x"},
                                idempotency_key="t1") is True
    assert store.begin_tool_run("r1", "sympy", "simplify", {"expr": "x"},
                                idempotency_key="t1") is False
    assert [r["run_id"] for r in store.unresolved_tool_runs()] == ["r1"]
    store.finish_tool_run("r1", "passed", {"ok": True})
    assert store.unresolved_tool_runs() == []
    assert store.get_tool_run("r1")["status"] == "passed"
    store.close()


def test_list_latest_exposes_object_ids(tmp_path):
    """`list_latest` 默认只返回业务数据; 取主键必须用 version_index / strip_meta。

    回归: 早期实现两者都拿不到 obj_id, 导致按对象查询 (反馈/派生接口) 永远 404。
    """
    from src.research.store import KIND_OBLIGATION, KIND_SPEC, ResearchStore

    store = ResearchStore("ids", db_path=tmp_path / "ids.sqlite")
    store.put(KIND_SPEC, "prob-1", {"problem_id": "prob-1", "statement": "x"})
    store.put(KIND_OBLIGATION, "obl-1", {"statement": "y"})

    assert store.version_index(KIND_SPEC) == {"prob-1": 1}
    assert "obj_id" not in store.list_latest(KIND_SPEC)[0]
    with_meta = store.list_latest(KIND_OBLIGATION, strip_meta=False)[0]
    assert with_meta["__obj_id__"] == "obl-1"
    assert with_meta["__version__"] == 1
    # 用 version_index 取主键 → 能按 id 取回对象
    for obj_id in store.version_index(KIND_SPEC):
        assert store.get(KIND_SPEC, obj_id)["problem_id"] == "prob-1"
    store.close()


def test_claim_step_scope_cannot_claim_full_assurance():
    from src.research.schemas import Assurance, Claim, ClaimStatus, VerificationScope

    with pytest.raises(ValueError):
        Claim(
            statement="local only",
            status=ClaimStatus.supported,
            assurance=Assurance.symbolic_checked,
            verification_scope=VerificationScope.step,
        )


# --------------------------------------------------------------------------
# 验证适配器
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def runner():
    from src.verification.runner import VerificationRunner

    return VerificationRunner(inproc=True)


def test_sympy_acceptance_behaviour(runner):
    base = dict(lhs="x**2 + y**2", rhs="2*x*y", variables=["x", "y"],
                assumptions={"x": "real", "y": "real"})
    ge = runner.sympy("prove_inequality", {**base, "relation": ">="})
    assert ge.status.value == "passed"
    gt = runner.sympy("prove_inequality", {**base, "relation": ">"})
    assert gt.status.value == "failed"
    assert gt.counterexample == {"x": 0, "y": 0}
    eq = runner.sympy("equality_condition", base)
    assert eq.status.value == "passed"
    assert "x = y" in eq.certificate


def test_sympy_complex_domain_unsupported(runner):
    result = runner.sympy("prove_inequality", dict(
        lhs="x**2 + y**2", rhs="2*x*y", relation=">=", variables=["x", "y"],
        assumptions={"x": "complex", "y": "complex"}))
    assert result.status.value == "unsupported"


def test_sympy_rejects_unsafe_expression(runner):
    result = runner.sympy("simplify", {"expr": "__import__('os').system('echo hi')", "variables": []})
    assert result.status.value == "unsupported"


def test_z3_implication_and_inconsistency(runner):
    ok = runner.z3("check_implication", dict(
        premises=["x >= 1"], conclusion="x >= 0", variables={"x": "Real"}))
    assert ok.status.value == "passed"
    bad = runner.z3("check_implication", dict(
        premises=["x >= 1"], conclusion="x >= 2", variables={"x": "Real"}))
    assert bad.status.value == "failed"
    assert bad.counterexample.get("x") == 1
    incon = runner.z3("check_implication", dict(
        premises=["x >= 1", "x <= 0"], conclusion="x == 5", variables={"x": "Real"}))
    assert incon.status.value == "unknown"
    assert "unsatisfiable" in incon.detail
    sat = runner.z3("check_satisfiable", dict(
        constraints=["x >= 1", "x <= 0"], variables={"x": "Real"}))
    assert sat.status.value == "failed"


def test_lean_rejects_sorry_without_toolchain(runner):
    result = runner.run("lean", "check_declaration", {"source": "theorem t : True := by sorry"})
    assert result.status.value == "failed"
    assert "sorry" in result.detail


# --------------------------------------------------------------------------
# 问题形式化 / 意图 / 新颖性 / 抽取 / 排版
# --------------------------------------------------------------------------
def test_problem_formulator_parses_acceptance():
    from src.research.problem_formulator import parse_questions

    questions = parse_questions(ACCEPTANCE_TEXT)
    assert len(questions) == 2
    assert questions[0].relation.value == ">="
    assert questions[0].lhs == "x**2 + y**2"
    assert questions[0].rhs == "2*x*y"
    assert questions[0].variables == ["x", "y"]
    assert questions[0].variable_domains == {"x": "real", "y": "real"}
    assert questions[0].wants_equality_condition is True
    assert questions[1].relation.value == ">"


def test_problem_formulator_unknown_returns_clarification():
    from src.research.problem_formulator import formulate
    from src.research.schemas import ResearchSpec

    form = formulate(ResearchSpec(project_id="x", problem_statement="帮我研究一下这个东西"))
    assert not form.claims
    assert "questions" in form.unknown_fields


def test_intent_parsing_maps_to_objects():
    from src.research.intent import parse_intent

    context = {"assumptions": {"asm-1": "矩阵 M 可逆"}, "claims": {"clm-1": "上界"}}
    actions = parse_intent("不要假设矩阵可逆", context)
    assert actions[0]["action_type"] == "revise_hypothesis"
    assert actions[0]["object_id"] == "asm-1"
    assert parse_intent("停止当前路线，但保留引理", context)[0]["action_type"] == "stop_with_report"
    assert parse_intent("如果这个命题不成立，给反例", context)[0]["action_type"] == "seek_counterexample"
    assert parse_intent("", context) == []


def test_novelty_known_equivalent_and_unchecked():
    from src.research.novelty import assess
    from src.research.schemas import Claim, NoveltyComparisonRow

    claim = Claim(statement="x^2 + y^2 >= 2*x*y")
    unchecked = assess(claim)
    assert unchecked.status.value == "unchecked"
    assert "尚未发现" in unchecked.bounded_statement
    record = assess(claim, lambda c: [NoveltyComparisonRow(result="AM-GM", difference="等价")])
    assert record.status.value == "known_equivalent"


def test_theorem_extractor_cards():
    from src.rag.theorem_extractor import extract_source_evidence

    text = (
        "Section 2\n"
        "Theorem 2.1: For all real x, x^2 >= 0.\n"
        "Proof. Squares are nonnegative.\n"
        "Lemma 2.2: The sum of nonnegative numbers is nonnegative.\n"
    )
    cards = extract_source_evidence(text, literature_id="lit-1", title="T")
    assert len(cards) == 2
    assert "x^2 >= 0" in cards[0].excerpt
    assert cards[0].location.startswith("line")


# --------------------------------------------------------------------------
# 交付门槛
# --------------------------------------------------------------------------
def test_acceptance_alignment_blocks_statement_swap():
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        SupportKind,
        ValidationStatus,
        VerificationRecord,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="x >= 0", lhs="x", rhs="0", relation=">=",
                  variables=["x"], status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    swapped = VerificationRecord(claim_id="c1", tool="sympy", status="passed",
                                 scope=VerificationScope.target,
                                 validation_status=ValidationStatus.verified,
                                 arguments={"lhs": "x**2", "rhs": "0", "relation": ">=",
                                            "variables": ["x"]})
    gate = theory_validity_gate([claim], [], [swapped], [])
    assert not gate.passed
    assert any("偷换" in r for r in gate.reasons)


def test_acceptance_cycle_and_missing_proof():
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        SupportKind,
        ValidationStatus,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    gate = theory_validity_gate([claim], [], [], [("c1", "c1")])
    assert not gate.passed
    assert any("循环" in r or "非法依赖" in r for r in gate.reasons)


def test_delivery_gate_requires_claim_mapping():
    from src.research.acceptance import delivery_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        ResearchSnapshot,
        SupportKind,
        ValidationStatus,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    snapshot = ResearchSnapshot(project_id="p", claims=[claim], writing_map={})
    gate = delivery_gate("x" * 300, snapshot)
    assert not gate.passed
    assert any("未映射" in r for r in gate.reasons)
    snapshot.writing_map = {"c1": "theorem-1"}
    assert delivery_gate("x" * 300, snapshot).passed


# --------------------------------------------------------------------------
# 研究循环端到端
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 方向输入 → 候选问题 → 确认 → 单调性研究
# --------------------------------------------------------------------------
def test_parse_direction_extracts_variables():
    from src.research.question_planner import parse_direction

    for text, x, y in [
        ("我想要研究在温度影响下电阻的变化", "温度", "电阻"),
        ("随着温度增大，电阻如何变化", "温度", "电阻"),
        ("温度对电阻的影响", "温度", "电阻"),
        ("how does temperature affect resistance", "temperature", "resistance"),
    ]:
        analysis = parse_direction(text)
        assert analysis.matched
        assert analysis.independent == x
        assert analysis.dependent == y


def test_build_spec_distinguishes_direction_and_problem():
    from src.research.question_planner import build_spec_from_input

    direction_spec = build_spec_from_input("研究在温度影响下电阻如何变化", project_id="d1")
    assert direction_spec.direction
    assert not direction_spec.problem_statement

    problem_spec = build_spec_from_input("对所有实数 x、y: x^2 + y^2 >= 2xy", project_id="d2")
    assert problem_spec.problem_statement
    assert not problem_spec.direction


# --------------------------------------------------------------------------
# 因果影响分析 (应用型) 垂直切片
# --------------------------------------------------------------------------
def _did_rows(true_effect: float):
    import random

    random.seed(7)
    rows = []
    for _ in range(25):
        rows.append({"g": "C", "t": "pre", "y": 10 + random.gauss(0, 0.5)})
        rows.append({"g": "C", "t": "post", "y": 11 + random.gauss(0, 0.5)})
        rows.append({"g": "T", "t": "pre", "y": 10 + random.gauss(0, 0.5)})
        rows.append({"g": "T", "t": "post", "y": 11 + true_effect + random.gauss(0, 0.5)})
    return rows


def _did_study(rows, **overrides):
    from src.research.schemas import StudyDesign, StudyPlan

    base = dict(
        design=StudyDesign.did, treatment="技术A", outcome="行业产出",
        population="制造业", region="华东", period="2015-2023",
        counterfactual="未采用技术A", confounders=["企业规模", "地区经济"],
        confounder_handling="DiD 平行趋势 + 地区固定效应",
        # 计划书 §7.4: 因果结论需独立声明识别假设/设计可行性/测量与缺失/误差结构
        identification_assumptions=["平行趋势", "无预期效应", "SUTVA 无干扰"],
        design_feasibility="存在受处理组与对照组、政策前后各 4 期观测",
        measurement_notes="产出按不变价增加值计量, 单位: 亿元",
        missing_data_handling="缺失率 <1%, 按企业-年度插值 (近似 MAR)",
        error_structure="按地区聚类稳健标准误",
        data_source_kind="real",
        group_col="g", time_col="t", outcome_col="y",
        treated_label="T", control_label="C", pre_label="pre", post_label="post",
        rows=rows,
    )
    base.update(overrides)
    return StudyPlan(**base)


def test_stats_did_and_mean_difference(runner):
    did = runner.run("stats", "difference_in_differences", {
        "rows": _did_rows(2.0), "group_col": "g", "time_col": "t", "outcome_col": "y",
        "treated_label": "T", "control_label": "C", "pre_label": "pre", "post_label": "post"})
    assert did.status.value == "passed"
    assert abs(did.values["estimate"] - 2.0) < 0.5
    assert did.values["ci_low"] < 2.0 < did.values["ci_high"]

    md = runner.run("stats", "mean_difference", {
        "rows": _did_rows(2.0), "group_col": "t", "outcome_col": "y",
        "treated_label": "post", "control_label": "pre"})
    assert md.status.value == "passed"
    assert "estimate" in md.values

    no_data = runner.run("stats", "difference_in_differences", {"group_col": "g"})
    assert no_data.status.value == "unsupported"




def test_effect_direction_and_contract_decide_candidates():
    from src.research.question_planner import parse_effect_direction

    for text, t, o in [
        ("研究某AI技术应用导致制造业就业的变化", "某AI技术应用", "制造业就业"),
        ("某技术对金融行业的影响", "某技术", "金融行业"),
        ("impact of automation on employment", "automation", "employment"),
    ]:
        analysis = parse_effect_direction(text)
        assert analysis.matched
        assert analysis.treatment == t
        assert analysis.outcome == o

    from src.research.question_planner import build_spec_from_input, generate_candidates
    from src.research.schemas import SourceSummary

    # P0-2: 没声明数据 → 不做经验因果估计, 只给条件性情景候选
    without_data = build_spec_from_input("研究某AI技术应用导致制造业就业的变化",
                                        project_id="ap1")
    assert without_data.contract.task_kind.value == "scenario"
    scenario = generate_candidates(without_data)
    assert scenario and scenario[0].claim_type.value == "scenario"

    # 声明了观测数据 → 才产出因果候选, 且不编造数据行
    with_data = build_spec_from_input("研究某AI技术应用导致制造业就业的变化",
                                      project_id="ap2",
                                      source_summary=SourceSummary(has_observational_data=True))
    causal = generate_candidates(with_data)
    assert causal and causal[0].claim_type.value == "causal"
    assert causal[0].category == "applied"
    assert causal[0].study.treatment
    assert not causal[0].study.rows and not causal[0].study.data_ref


def test_evidence_tiering_contradiction_and_grade():
    from src.research.evidence import (
        classify_source,
        detect_contradictions,
        grade_evidence,
        independent_supporters,
    )
    from src.research.schemas import EvidenceGrade, SourceKind, SupportKindOfEvidence

    journal = classify_source({"title": "AI and manufacturing output",
                               "doi": "10.1/x", "venue": "Journal of Economics",
                               "abstract": "We find a 5% increase in output."})
    assert journal.source_kind == SourceKind.journal
    assert journal.peer_reviewed and journal.credibility.value == "high"
    # 命中只代表"来源存在", 不建立支持关系 (计划书 §6.2)
    assert journal.content_supports is False
    assert journal.support == SupportKindOfEvidence.insufficient

    preprint = classify_source({"title": "AI and jobs", "venue": "arXiv preprint",
                                "abstract": "effects unclear"})
    assert preprint.source_kind == SourceKind.preprint
    assert not preprint.peer_reviewed

    pos = classify_source({"title": "AI raises output", "doi": "10.2/a",
                           "abstract": "increase growth"})
    neg = classify_source({"title": "AI lowers output", "doi": "10.3/b",
                           "abstract": "we find a decrease in output"})
    # 支持关系必须显式判定, 且方向由命题决定
    pos.support = SupportKindOfEvidence.supports
    neg.support = SupportKindOfEvidence.supports
    detect_contradictions([pos, neg])
    assert pos.contradicts and neg.contradicts

    assert grade_evidence([]) == EvidenceGrade.unsupported
    # 未判定支持关系时不得升级
    unjudged = [classify_source({"title": "AI raises output", "doi": "10.9/z"})]
    assert grade_evidence(unjudged) == EvidenceGrade.anecdotal
    assert grade_evidence([pos]) == EvidenceGrade.single_source
    assert grade_evidence([pos, neg]) == EvidenceGrade.converging
    # 多篇转引同一原始记录只算一个独立来源
    dup = classify_source({"title": "AI raises output (preprint)",
                           "original_record_hash": pos.independence_key})
    dup.support = SupportKindOfEvidence.supports
    assert len(independent_supporters([pos, dup])) == 1
    assert grade_evidence([pos, dup]) == EvidenceGrade.single_source


def test_server_startrequest_has_no_engine_fields():
    """`StartRequest` 不再有引擎/预算字段: 只有一张图, 预算由团队运行决定 (G01)。

    这条用例过去断言"留空 mode 时按默认引擎启动" —— 引擎选择已删除, 现在断言的是
    字段**不存在**, 且身份仍由统一入口规范化产出 (团队与工作台读同一份)。
    """
    from src.server import StartRequest, _normalized_input

    req = StartRequest(request="对所有实数 x: x**2 >= 0", project_id="pj",
                       problem_id="p1")
    assert not hasattr(req, "mode"), "StartRequest.mode 又回来了"
    assert not hasattr(req, "max_actions"), "理论引擎专属预算字段又回来了"
    snapshot = _normalized_input(req)
    assert snapshot["project_id"] == "pj"
    assert snapshot["problem_id"] == "p1"
    assert snapshot["run_id"], "统一入口必须产出运行身份"
    assert snapshot["request"] == "对所有实数 x: x**2 >= 0"
