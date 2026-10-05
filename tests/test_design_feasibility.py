from __future__ import annotations

"""2-(v,k,λ) 可行性判定: 通用必要条件, 不含任何具体题目的特例。

同时覆盖 S1 命题化: 判定必须变成**命题 + 必要性义务**, 由规则证书验收关闭,
未决 (necessary_met) 时不得关闭、不得升级交付等级。
"""

import json
from pathlib import Path


from src.research.design_feasibility import (
    DesignParams,
    check,
    is_sum_of_two_squares,
)

CASE = (Path(__file__).resolve().parents[1] / "evals" / "cases"
        / "combinatorial-design" / "case.md")


def test_sum_of_two_squares_rule():
    for n in (0, 1, 2, 4, 5, 8, 9, 10, 13, 25):
        assert is_sum_of_two_squares(n), n
    for n in (3, 6, 7, 11, 12, 14, 15, 21, 28):
        assert not is_sum_of_two_squares(n), n


def test_projective_plane_of_order_14_is_excluded():
    """211 点/每块 15/每对一次: 计数自洽, 但 14 阶射影平面不存在。"""
    report = check(DesignParams(v=211, k=15, lam=1))
    assert report.r == 15 and report.b == 211
    assert report.symmetric and report.order == 14
    assert report.verdict == "nonexistent"
    assert any("Bruck–Ryser–Chowla" in v for v in report.violations)
    assert "14" in report.describe()


def test_order_6_plane_excluded_and_order_2_3_planes_not_excluded():
    assert check(DesignParams(v=43, k=7, lam=1)).verdict == "nonexistent"   # n=6
     # n=2 (Fano) 与 n=3: BRC 不排除
    fano = check(DesignParams(v=7, k=3, lam=1))
    assert fano.verdict == "necessary_met" and fano.order == 2
    order3 = check(DesignParams(v=13, k=4, lam=1))
    assert order3.verdict == "necessary_met" and order3.order == 3
    assert any("不适用" in item for item in order3.limitations)


def test_divisibility_and_fisher_violations():
    bad_r = check(DesignParams(v=8, k=4, lam=1))          # r=7/3
    assert bad_r.verdict == "nonexistent"
    assert any("非整数" in v for v in bad_r.violations)

    bad_b = check(DesignParams(v=16, k=6, lam=1))         # r=3, b=8 < 16
    assert bad_b.verdict == "nonexistent"
    assert any("Fisher" in v for v in bad_b.violations)

    inconsistent = check(DesignParams(v=211, k=15, lam=1, b=200))
    assert inconsistent.verdict == "nonexistent"


def test_even_v_symmetric_design_needs_square_k_minus_lambda():
    # 2-(16,6,2): v=16 偶, k-λ=4 是平方数 → 必要条件通过
    ok = check(DesignParams(v=16, k=6, lam=2))
    assert ok.symmetric and ok.verdict == "necessary_met", ok.describe()
    # 2-(22,7,2): 计数与对称性都成立, 但 k-λ=5 非平方数 → BRC 排除
    bad = check(DesignParams(v=22, k=7, lam=2))
    assert bad.symmetric and bad.r == 7 and bad.b == 22
    assert bad.verdict == "nonexistent"
    assert any("平方数" in item for item in bad.violations), bad.describe()


def test_general_case_reports_limitations_instead_of_overclaiming():
    """非射影平面的一般参数: 不得声称"必要条件全部检查过"。"""
    report = check(DesignParams(v=211, k=15, lam=2))
    assert report.symmetric is False and report.verdict == "necessary_met"
    assert any("需要显式构造" in item for item in report.limitations)
    # 奇 v 且 λ>1 的非对称设计: BRC 不适用这一点必须明说, 不得冒充已检查
    odd = check(DesignParams(v=11, k=5, lam=2))
    assert odd.verdict == "necessary_met"
    assert any("不适用" in item or "未实现" in item for item in odd.limitations)


def test_invalid_parameters_are_insufficient_not_guessed():
    assert check(DesignParams(v=15, k=15, lam=1)).verdict == "nonexistent"
    assert check(DesignParams(v=0, k=0, lam=0)).verdict == "insufficient"


def test_extraction_is_generic_not_tuned_to_one_phrasing():
    """同一套词表要能吃下多种说法与不同参数 (含英文), 不针对某一题。"""
    from src.research.design_feasibility import extract_design_params, feasibility_from_text

    chinese = ("某实验有 211 个节点。管理员希望安排恰好 211 轮联合测试: 每轮恰好选择 15 个节点;"
               " 每个节点恰好参加 15 轮; 任意两个不同节点恰好共同参加一轮测试。")
    params = extract_design_params(chinese)
    assert params is not None
    assert (params.v, params.k, params.lam) == (211, 15, 1)
    assert feasibility_from_text(chinese).verdict == "nonexistent"

    # 不同参数、不同措辞 (Fano: 7 点 / 每块 3 个 / 每点 3 块 / 每对一次)
    other = ("设有 7 个对象, 每个区组包含 3 个对象; 每个对象出现在 3 组中;"
             " 任意两个对象恰好共同出现在一组。")
    second = extract_design_params(other)
    assert second is not None and second.v == 7 and second.k == 3
    assert feasibility_from_text(other).verdict == "necessary_met"

    english = ("There are 43 points. Each line contains 7 points. Each point occurs in 7 lines."
               " Any two points occur together in exactly 1 line.")
    third = extract_design_params(english)
    assert third is not None and third.v == 43 and third.k == 7

    # 抽不出计数约束时不得编造参数
    assert extract_design_params("请分析信道变化对可分性的影响") is None
    assert feasibility_from_text("讨论一下这个方向值不值得做") is None


# ---------------------------------------------------------------------------
# 出处要求: 判定必须记录"引用了哪条经典必要性定理", 不得空口断言
# ---------------------------------------------------------------------------


def test_every_check_carries_theorem_provenance():
    report = check(DesignParams(v=211, k=15, lam=1))
    assert report.sources(), "必须记录引用了哪些必要性定理"
    assert any("Bruck–Ryser–Chowla" in s for s in report.sources())
    for item in report.checks:
        assert item.theorem_cn and item.statement and item.citation, item
        assert item.inputs, "每条判定都必须记录输入数值"
        assert item.conclusion, "每条判定都必须给出本条件下的结论"
    violated = [c for c in report.checks if not c.result]
    assert violated and "Bruck–Ryser–Chowla" in violated[0].theorem_cn
    # 未判定的必要条件必须如实报出, 不得冒充"已全部检查"
    assert all(item.condition and item.reason for item in report.unchecked)


def test_certificate_is_deterministic_and_recomputable():
    first = check(DesignParams(v=211, k=15, lam=1))
    second = check(DesignParams(v=211, k=15, lam=1))
    assert first.certificate_digest() == second.certificate_digest()
    assert first.certificate_dict()["design"]["v"] == 211
    # 换参数必须换证书 (否则"换参数沿用旧证书"可以伪造结论)
    assert (check(DesignParams(v=43, k=7, lam=1)).certificate_digest()
            != first.certificate_digest())
    payload = json.loads(first.certificate_json())
    assert payload["verdict"] == "nonexistent"
    assert payload["counts"] == {"r": 15, "b": 211, "symmetric": True, "order": 14}


def test_certificate_records_why_the_theorem_applies():
    """BRC 的适用前提 (等价于射影平面) 必须自己也是一条可核验判定。

    否则"因为这是射影平面问题, 所以用 BRC"只停留在自然语言里, 复核者无法逐项确认
    (v = k(k-1)+1、λ=1、r=k=b 三个前提)。
    """
    report = check(DesignParams(v=211, k=15, lam=1))
    keys = [c.key for c in report.checks]
    assert "projective_plane_equivalence" in keys, keys
    plane = next(c for c in report.checks if c.key == "projective_plane_equivalence")
    assert plane.result is True
    assert plane.inputs["v"] == plane.inputs["k(k-1)+1"] == 211
    assert "射影平面" in plane.conclusion and "14 阶" in plane.conclusion
    # 非射影平面参数不得声称已套用射影平面形式的 BRC (2-(16,6,2): b=v 但 λ≠1)
    other = check(DesignParams(v=16, k=6, lam=2))
    assert "projective_plane_equivalence" not in [c.key for c in other.checks]
    assert "brc_projective_plane" not in [c.key for c in other.checks]
    assert "brc_even_order" in [c.key for c in other.checks]


# ---------------------------------------------------------------------------
# S1: 判定 → 命题 + 义务 (通用逻辑, 不含题目常量)
# ---------------------------------------------------------------------------


def test_formulation_from_text_produces_claim_and_obligation():
    from src.research.design_feasibility import (
        ACCEPTANCE_METHOD,
        formulate_from_text,
    )

    text = ("某大型实验有 211 个节点。管理员希望安排恰好 211 轮联合测试: "
            "每轮恰好选择 15 个节点; 每个节点恰好参加 15 轮; "
            "任意两个不同节点恰好共同参加一轮测试。")
    form = formulate_from_text(text)
    assert form is not None
    claim = form.claims[0]
    assert claim.design_v == 211 and claim.design_k == 15 and claim.design_lambda == 1
    assert claim.design_verdict == "nonexistent"
    assert "不存在" in claim.statement
    # 命题陈述必须与证书结论一致, 且记录引用的定理
    assert "定义" in claim.notes or "定理" in claim.notes
    obligation = form.obligations[0]
    assert obligation.acceptance_method == ACCEPTANCE_METHOD
    assert obligation.claim_id == claim.id
    assert obligation.local_context, "义务必须带上可复核证书"


def test_formulation_is_absent_when_counts_cannot_be_extracted():
    from src.research.design_feasibility import formulate_from_text

    assert formulate_from_text("请分析信道变化对可分性的影响") is None


# ---------------------------------------------------------------------------
# 端到端 (离线): 交付等级与门槛由证书决定
# ---------------------------------------------------------------------------


def _run_case(tmp_path, monkeypatch, request: str, project_id: str) -> dict:
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    from src.graph import theory_pipeline

    return theory_pipeline.run_theory_pipeline(
        request=request, topic=request[:20], project_id=project_id,
        problem_id="design", max_actions=20, max_tool_calls=20)


def test_statement_swap_across_parameters_is_rejected():
    """对齐检查: 换一组设计参数后不得沿用旧证书 (陈述偷换)。"""
    from src.research.acceptance import _aligned
    from src.research.schemas import Claim, VerificationRecord

    record = VerificationRecord(
        tool="design_necessity", claim_id="c1", validation_status="verified",
        arguments={"design_v": 211, "design_k": 15, "design_lambda": 1,
                   "design_b": 211, "design_r": 15, "design_verdict": "nonexistent"})
    same = Claim(statement="s", design_v=211, design_k=15, design_lambda=1,
                 design_b=211, design_r=15, design_verdict="nonexistent")
    assert _aligned(record, same) is True
    swapped = same.model_copy(update={"design_v": 43, "design_k": 7, "design_b": 43,
                                      "design_r": 7, "design_lambda": 1})
    assert _aligned(record, swapped) is False
    # 参数相同但结论被改动 → 同样拒绝
    flipped = same.model_copy(update={"design_verdict": "necessary_met"})
    assert _aligned(record, flipped) is False
    # 命题带设计参数但记录里没有对应输入 → 拒绝 (不得用别的记录充当证书)
    bare = VerificationRecord(tool="sympy", claim_id="c1", arguments={})
    assert _aligned(bare, same) is False

