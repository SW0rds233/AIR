from __future__ import annotations

"""问题类型能力矩阵与领域模型选择的失败用例 (计划书 §5.2 / §7.2)。

反向安全测试:
- 未登记的问题类型不得被当作"可符号核验"的命题 (fail-closed);
- 需要数据/设计的类型在缺前置条件时必须请求澄清, 而不是给出结论;
- 规范性结论不得被系统自动判定成立;
- 选中模型是**显式动作**: 必须写 `selected` 与具体版本, 且不改变结论状态。
"""


import pytest


# --------------------------------------------------------------------------
# 能力声明
# --------------------------------------------------------------------------
def test_unknown_category_is_not_downgraded_to_definitional():
    from src.research.capability import claim_type_for, declare_capability
    from src.research.schemas import ClaimType

    decl = declare_capability("quantum-vibes")
    assert decl.known is False
    assert decl.action == "clarify"
    assert decl.questions, "未知类型必须给出澄清问题"
    # 未登记类型按"需独立审查"处理, 绝不退化成可符号核验
    assert claim_type_for("quantum-vibes") == ClaimType.normative
    assert claim_type_for("") == ClaimType.normative


def test_causal_and_predictive_require_data_and_design():
    from src.research.capability import declare_capability

    for category in ("causal", "associational", "predictive", "descriptive", "scenario"):
        missing = declare_capability(category, available={"stats": True})
        assert missing.action == "clarify", category
        assert missing.questions, category

        ready = declare_capability(category, available={"stats": True},
                                   has_data=True, has_design=True)
        assert ready.action == "proceed", category

    # 只有数据没有设计仍然不够 (无法区分识别条件)
    partial = declare_capability("causal", available={"stats": True}, has_data=True)
    assert partial.action == "clarify"


def test_missing_backend_is_reported_not_ignored():
    from src.research.capability import declare_capability

    decl = declare_capability("inequality", available={})
    # inequality 需要符号/求解器后端; 全部不可用时必须显式说明
    assert decl.required_backends
    assert decl.missing_backends
    assert any("后端" in r for r in decl.reasons)


def test_normative_claim_never_self_certified():
    from src.research.capability import declare_capability
    from src.research.schemas import ClaimType

    decl = declare_capability("normative", available={"stats": True},
                              has_data=True, has_design=True)
    assert decl.claim_type == ClaimType.normative
    assert decl.action == "clarify", "规范性结论必须由独立审查确认"
    assert decl.questions


def test_category_mapping_matches_claim_types():
    from src.research.capability import CATEGORY_TO_CLAIM_TYPE, claim_type_for
    from src.research.schemas import ClaimType

    assert claim_type_for("causal") == ClaimType.causal
    assert claim_type_for("applied") == ClaimType.causal
    assert claim_type_for("predictive") == ClaimType.predictive
    assert claim_type_for("descriptive") == ClaimType.descriptive
    assert claim_type_for("monotonicity") == ClaimType.definitional
    assert CATEGORY_TO_CLAIM_TYPE["associational"] == ClaimType.associational


def test_non_definitional_claim_gets_data_and_scope_obligations():
    """描述/预测/规范类命题不得"没有义务" (那等于无需证据即可交付)。"""
    from src.research.problem_formulator import _obligations_for
    from src.research.schemas import Claim, ClaimType

    for claim_type in (ClaimType.descriptive, ClaimType.predictive,
                       ClaimType.scenario, ClaimType.normative,
                       ClaimType.associational):
        claim = Claim(statement="某类结论", claim_type=claim_type)
        obligations = _obligations_for(claim, False, {"stats": True, "sympy": True})
        kinds = {o.kind for o in obligations}
        assert obligations, claim_type
        assert "scope_check" in kinds, claim_type
        assert "evidence_support" in kinds, claim_type
        # 非定义型命题不得只给出"核验不等式"的义务
        assert "prove_inequality" not in kinds, claim_type


# --------------------------------------------------------------------------
# 领域模型选择
# --------------------------------------------------------------------------
def _fake_model(model_id="mdl-1", *, version=1, refs=2, complete=True):
    from src.research.schemas import ResearchModel

    return ResearchModel(
        id=model_id, version=version, name=f"模型 {model_id}",
        natural_language="自然语言描述" if complete else "",
        formal_encoding="x >= 0" if complete else "",
        fidelity="忠实度说明" if complete else "",
        boundaries="适用边界" if complete else "",
        source_refs=[f"ev-{i}" for i in range(refs)],
    )


def test_select_model_writes_selected_and_version_only():
    from src.research.capability import select_model
    from src.research.schemas import Claim, ClaimStatus

    claim = Claim(statement="x^2 >= 0")
    weak = _fake_model("mdl-weak", refs=1)
    strong = _fake_model("mdl-strong", refs=3)
    before = claim.status
    outcome = select_model(None, claim, [weak, strong])

    assert outcome["ok"] is True
    assert outcome["selected"] == "mdl-strong", "应选来源最完整的候选"
    assert outcome["version"] == 1
    assert strong.selected is True
    assert weak.selected is False, "其余候选必须取消选中"
    assert claim.model_ref is not None and claim.model_ref.id == "mdl-strong"
    # 选中模型不得改变结论状态
    assert claim.status == before == ClaimStatus.proposed


def test_select_model_without_candidates_fails_loudly():
    from src.research.capability import select_model
    from src.research.schemas import Claim

    outcome = select_model(None, Claim(statement="p"), [])
    assert outcome["ok"] is False
    assert "reason" in outcome
    assert outcome.get("selected", "") == ""


@pytest.mark.parametrize("category,expected", [
    ("causal", "因果识别模型"), ("predictive", "预测模型"),
    ("scenario", "情景/仿真模型"), ("normative", "规范论证模型"),
    ("descriptive", "描述模型"), ("associational", "关联模型"),
])
def test_candidate_scheme_per_claim_type(category, expected):
    from src.research.capability import candidate_scheme, claim_type_for
    from src.research.schemas import Claim

    claim = Claim(statement="p", claim_type=claim_type_for(category))
    name, mechanism, fidelity = candidate_scheme(claim)
    assert name == expected
    assert mechanism and fidelity
