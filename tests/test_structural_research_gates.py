"""Transfer checks for research gates that previously depended on subject words."""

from src.research.adversarial import _is_applied, review_claim
from src.research.classification import is_formal_question
from src.research.constraint_fidelity import conflicting_code_parameters
from src.research.formulation import formulate_problem
from src.research.problem_formulator import _obligations_for
from src.research.question_planner import (build_spec_from_input, formulate,
                                           requires_intent_clarification)
from src.research.schemas import Claim, ProofObligation
from src.research.verification_service import _rule_verdict


def test_formal_intent_is_stable_under_domain_and_wording_changes():
    questions = (
        "211 个节点、211 轮，每轮 15 个，是否存在这样的设计？",
        "211 个节点、211 轮，每轮 15 个，是否存在这样的安排？",
        "211 specimens, 211 groups, 15 in each group: does such an arrangement exist?",
    )
    assert all(is_formal_question(question) for question in questions)
    assert not is_formal_question("是否存在湿度导致性能下降的证据？")
    assert not is_formal_question("证明湿度导致性能下降")
    assert not is_formal_question("211 名患者每人服用 15 毫克，是否存在副作用？")
    assert not is_formal_question("RH=40、t=24 时是否存在性能下降？")


def test_compact_constrained_existence_is_not_silently_routed_as_scenario():
    questions = (
        "是否存在一个 668 阶 Hadamard 矩阵？",
        "是否存在一条长度 333 的 Legendre 对？",
        "是否存在一个有限射影平面，其每条线上有 15 个点？",
        "does there exist a regular graph on 100 vertices where every two vertices "
        "have exactly one common neighbour?",
    )
    for question in questions:
        contract = formulate(question)
        assert requires_intent_clarification(contract) or contract.task_kind.value == "formal_proof"
        assert "形式化" in contract.clarification or contract.task_kind.value == "formal_proof"
        if contract.clarification:
            assert any(path.task_kind.value == "formal_proof" for path in contract.paths)
            assert not any(path.recommended for path in contract.paths)
    physical = formulate("是否存在一条长度 333 的金属导线？")
    assert physical.task_kind.value != "formal_proof"
    assert requires_intent_clarification(physical)
    assert requires_intent_clarification(formulate("请证明是否存在一条长度 333 的金属导线？"))
    explicit = formulate("请形式化证明是否存在一个 668 阶 Hadamard 矩阵？")
    assert explicit.task_kind.value == "formal_proof"
    assert not requires_intent_clarification(explicit)
    clarified = formulate("是否存在一个 668 阶 Hadamard 矩阵？补充说明：这是抽象数学对象。")
    assert clarified.task_kind.value == "formal_proof"
    assert not requires_intent_clarification(clarified)
    unresolved = build_spec_from_input("是否存在一个 668 阶 Hadamard 矩阵？")
    result = formulate_problem(spec=unresolved)
    assert result.needs_clarification
    assert not result.claims


def test_named_parameter_conflicts_do_not_require_code_vocabulary():
    problem = "研究等距集合 (n,M,d)=(12,24,6)"
    assert conflicting_code_parameters(problem, "候选 (n,M,d)=(12,25,6)")
    assert conflicting_code_parameters("n=12, M=24, d=6", "M=25")
    assert not conflicting_code_parameters(problem, "候选 (n,M,d)=(12,24,6)")
    assert not conflicting_code_parameters(problem, "若改用 (n,M,d)=(12,25,6)，则另需证明")
    assert not conflicting_code_parameters("有 12 个对象", "候选有 25 个对象")
    assert conflicting_code_parameters("RH=40, t=24", "在 RH=50, t=24 下")
    assert not conflicting_code_parameters("RH=40, t=24", "若改用 RH=50，则另行研究")
    assert not conflicting_code_parameters("RH=40%", "RH=0.4")
    assert not conflicting_code_parameters("RH=40 或 RH=50", "RH=40")


def test_adversarial_route_uses_registered_study_and_strategy():
    for statement in ("给定关系的存在性", "给定矩阵的存在性"):
        formal = Claim(statement=statement, claim_type="descriptive", strategy="proof")
        assert not _is_applied(formal)
        empirical = Claim(statement=statement, claim_type="descriptive",
                          strategy="proof", study={"design": "observational"})
        assert _is_applied(empirical)
    undecided = Claim(statement="矩阵性能随温度变化", claim_type="descriptive")
    assert _is_applied(undecided)


def test_scope_and_predictive_validation_have_distinct_obligations():
    material = Claim(statement="湿度影响材料性能", claim_type="descriptive",
                     study={"design": "observational", "measurement_notes": "记录湿度和温度"})
    obligations = _obligations_for(material, False, {})
    scope = next(item for item in obligations if item.kind == "scope_check")
    assert "人群" not in scope.statement
    assert "研究对象" in scope.statement
    assert not _rule_verdict(material, scope, [])[0]
    material.scope_conditions = "薄膜样品；20–40°C；RH 30–70%；暴露 24 小时"
    assert _rule_verdict(material, scope, [])[0]
    finding = next(f for f in review_claim(material).findings if f.check_id == "external_validity")
    assert finding.status != "hit"

    predictive = Claim(statement="预测材料寿命", claim_type="predictive")
    kinds = {item.kind for item in _obligations_for(predictive, False, {})}
    assert "predictive_validation" in kinds
    validation = ProofObligation(statement="样本外方案", kind="predictive_validation")
    predictive.study.design_feasibility = "可采集数据"
    assert not _rule_verdict(predictive, validation, [])[0]
    predictive.study.predictive_validation = "按时间划分训练/测试；MAE；与常数基线比较"
    assert _rule_verdict(predictive, validation, [])[0]


def test_scenario_parameters_do_not_reuse_causal_design_feasibility():
    scenario = Claim(statement="在给定条件下分析输出", claim_type="scenario")
    obligations = _obligations_for(scenario, False, {})
    parameter_check = next(item for item in obligations if "情景参数" in item.statement)
    assert parameter_check.kind == "scenario_parameters"
    scenario.study.design_feasibility = "已有样本可供回归"
    assert not _rule_verdict(scenario, parameter_check, [])[0]
    scenario.study.scenario_parameters = "温度 20–40°C，湿度 30–70%，暴露 24 小时"
    assert _rule_verdict(scenario, parameter_check, [])[0]
    old = ProofObligation(statement="旧版情景参数", kind="design_feasibility")
    assert _rule_verdict(scenario, old, [])[0]
