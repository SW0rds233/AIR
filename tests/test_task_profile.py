from __future__ import annotations

"""任务画像与领域措辞闸门的用例 (合并计划 §7.3 / P0-3)。

固定的是**现场缺陷的回归**: 通用入口曾经输出组合设计专用内容 —— 一篇关于信道
可分性的报告会在关键词里写"组合设计存在性"、引言以"组合设计的存在性判定是组合数学中的
经典问题"开头、摘要声称"本文结论为纯数学判定, 不含实验或仿真数据"。

判据不是"字符串里没有某个词", 而是:
- 设计论措辞只在快照里**真的有声明的设计参数**时出现;
- 形式化措辞只在有形式化子问题时出现;
- "纯数学判定/不含实验数据"只在**纯形式化**研究里出现;
- 章节骨架按交付形态决定, 一份验证建议不长成期刊六章。
"""


from src.research.schemas import (
    Claim,
    ClaimStatus,
    ClaimType,
    Coverage,
    ResearchSnapshot,
    ResearchSpec,
    SupportKind,
    ValidationStatus,
)
from src.research.task_profile import (
    DELIVERABLE_PROFILES,
    TaskProfile,
    profile_for_deliverables,
    task_profile_from_brief,
)


# --------------------------------------------------------------------------
# 画像本身
# --------------------------------------------------------------------------
def test_design_vocabulary_needs_declared_parameters():
    formal = profile_for_deliverables(["theoretical_conclusion"], ["existence_proof"])
    assert formal.allows_formal_vocabulary() is True
    assert formal.allows_design_vocabulary() is False, \
        "没有声明 (v,k,λ) 就不许出现设计论措辞"

    design = profile_for_deliverables(["theoretical_conclusion"], ["existence_proof"],
                                      has_design_parameters=True)
    assert design.allows_design_vocabulary() is True


def test_empirical_scope_forbids_pure_math_claim():
    empirical = profile_for_deliverables(["full_paper"], ["mechanism", "case_comparison"])
    assert empirical.has_empirical_scope is True
    assert empirical.is_purely_formal() is False, \
        "涉及机理/案例时不得声称'纯数学判定、不含实验数据'"

    formal = profile_for_deliverables(["theoretical_conclusion"], ["existence_proof"])
    assert formal.is_purely_formal() is True


def test_sections_follow_deliverables_not_a_fixed_template():
    proposal = profile_for_deliverables(["validation_proposal"], ["validation_plan"])
    assert "待区分的解释" in proposal.sections()
    # 验证建议不该长成期刊论文: 没有"与已有工作比较"这种论文章
    assert "与已有工作比较" not in proposal.sections()

    paper = profile_for_deliverables(["full_paper"], ["literature_synthesis"])
    assert "与已有工作比较" in paper.sections()


def test_sections_union_preserves_order_for_multiple_deliverables():
    both = profile_for_deliverables(["theoretical_conclusion", "figures"], [])
    sections = both.sections()
    # 结论骨架的章仍在, 图表的章也加上, 且顺序按声明顺序
    assert sections[0] == DELIVERABLE_PROFILES["theoretical_conclusion"][0]
    assert any(s in sections for s in DELIVERABLE_PROFILES["figures"])
    assert len(sections) == len(set(sections)), "章节不得重复"


def test_profile_accepts_brief_object_and_dict():
    from src.agents.supervisor import SupervisorAgent

    brief = SupervisorAgent().brief("解释某机理并写成论文", project_id="p",
                                    problem_id="q")
    from_object = task_profile_from_brief(brief)
    from_dict = task_profile_from_brief(brief.to_dict())
    assert from_object.deliverables == from_dict.deliverables
    assert from_object.subquestion_kinds == from_dict.subquestion_kinds
    assert from_object.has_empirical_scope is True


def test_profile_without_brief_falls_back_conservatively():
    profile = task_profile_from_brief(None)
    assert profile.deliverables == []
    assert profile.allows_design_vocabulary() is False
    assert profile.sections(), "必须有可用的默认骨架"


# --------------------------------------------------------------------------
# 现场缺陷回归: 非设计类研究不得出现设计论内容
# --------------------------------------------------------------------------
def _snapshot(claims: list, verifications: list | None = None) -> ResearchSnapshot:
    """构造快照: 绕开**状态一致性**校验, 只固定文稿措辞所需的事实。

    `ResearchSnapshot` 校验 "supported 需 coverage=target + validation_status=verified +
    声明 support_kind" 等组合 —— 那些是判定层的产物, 本文件不重复验证它 (判定层有
    自己的用例)。这里用 `model_construct` 直接给出"已经判定完成"的事实。
    """
    return ResearchSnapshot.model_construct(
        project_id="proj-1", problem_id="prob-1", topic="", run_id="run-1",
        claims=list(claims), evidence=[], assumptions=[], definitions=[],
        models=[], obligations=[], verifications=list(verifications or []),
        evidence_links=[], routes=[], gaps=[], dependency_edges=[], novelty=[],
        experiment_specs=[], writing_map={}, verification_index={})


def _non_design_snapshot() -> ResearchSnapshot:
    """非设计类研究: 一条被统计估计支持的经验结论。"""
    claim = Claim.model_construct(
        id="clm-signal", statement="信道变化会降低射频指纹的可分性",
        claim_type=ClaimType.causal, status=ClaimStatus.supported,
        support_kind=SupportKind.statistical_estimate, coverage=Coverage.target,
        validation_status=ValidationStatus.verified, version=1)
    return _snapshot([claim])


def _spec() -> ResearchSpec:
    return ResearchSpec(project_id="proj-1", problem_id="prob-1",
                        problem_statement="分析信道变化对射频指纹可分性的影响")


# --------------------------------------------------------------------------
# 引擎/图侧: 画像真的被用上
# --------------------------------------------------------------------------
def test_task_profile_describe_is_human_readable():
    profile = profile_for_deliverables(["validation_proposal"], ["validation_plan"])
    described = profile.describe()
    assert "交付" in described
    assert "设计参数: 无" in described


def test_profile_round_trips_through_dict():
    profile = TaskProfile(deliverables=["full_paper"],
                          subquestion_kinds=["mechanism"],
                          has_empirical_scope=True)
    payload = profile.to_dict()
    rebuilt = TaskProfile(deliverables=payload["deliverables"],
                          subquestion_kinds=payload["subquestion_kinds"],
                          has_empirical_scope=payload["has_empirical_scope"])
    assert rebuilt.sections() == profile.sections()
    assert rebuilt.is_purely_formal() == profile.is_purely_formal()
