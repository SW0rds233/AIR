from __future__ import annotations

"""调度与执行链路的失败用例 (计划书 §5.3 / §7.2)。

这些用例针对**集成层**缺陷: 单个动作的实现正确, 但协调者从不选中它,
或者动作拿到的对象类型不对 —— 早期实现因此让"推导步骤+反方审查"和
"LLM 推导"在真实运行中从未执行过 (只有直接调用处理器才跑得通)。
"""

from src.research.schemas import (
    ClaimQuestion,
    ResearchSpec,
    StudyDesign,
    StudyPlan,
)


def _causal_spec(pid: str) -> ResearchSpec:
    question = ClaimQuestion(
        statement="技术A提高了行业产出", category="causal", claim_type="causal",
        study=StudyPlan(design=StudyDesign.observational, treatment="技术A",
                        outcome="行业产出"),
    )
    return ResearchSpec(project_id=pid, questions=[question], confirmed=True)


def test_stuck_obligation_promotes_information_actions():
    """义务因能力/数据受阻时, 产生新信息的动作必须获得与 check_step 同级的优先级。

    生产链路上还会由执行账本去重拦住重复的 check_step; 这里只保证排序本身
    不再把同一工具排在唯一首选位置 (否则会一直空转)。
    """
    from src.research.coordinator import rank_actions
    from src.research.schemas import ActionType, ValidationStatus

    state = {
        "primary_gap": {"gap_type": "open_obligation", "object_id": "obl-1",
                        "claim_id": "clm-1", "resolving_actions": ["check_step",
                                                                   "plan_proof"]},
        "open_obligations": [{"id": "obl-1", "claim_id": "clm-1",
                              # model_dump 出来的枚举就是枚举成员, 两种写法都必须识别
                              "validation_status": ValidationStatus.unsupported,
                              "status": "blocked", "kind": "estimate_effect"}],
        "reviewed_claims": ["clm-1"],
        "gaps": [{"gap_type": "open_obligation"}],
        "dismissed_actions": [],
        "budget_remaining": 10,
    }
    ranked = [(a.value, why) for _, a, why in rank_actions(
        state, [ActionType.check_step, ActionType.derive_step, ActionType.propose_model,
                ActionType.switch_strategy, ActionType.deliver_partial])]
    info = [name for name, _ in ranked if name in ("derive_step", "propose_model",
                                                  "switch_strategy")]
    assert info, ranked
    # 受阻义务必须让"信息动作"获得专门的优先级理由, 而不是落在兜底档
    assert any("受阻" in why or "信息" in why for name, why in ranked if name in info), ranked


def test_unreviewed_obligation_prefers_derive_step():
    """尚未审查过的义务: 先用推导步骤补条件, 而不是直接跑验证工具。"""
    from src.research.coordinator import rank_actions
    from src.research.schemas import ActionType

    state = {
        "primary_gap": {"gap_type": "open_obligation", "object_id": "obl-1",
                        "claim_id": "clm-1", "resolving_actions": ["derive_step",
                                                                   "check_step"]},
        "open_obligations": [{"id": "obl-1", "claim_id": "clm-1",
                              "validation_status": "unchecked",
                              "status": "open", "kind": "prove_inequality"}],
        "reviewed_claims": [],
        "gaps": [{"gap_type": "open_obligation"}],
        "dismissed_actions": [],
        "budget_remaining": 10,
    }
    ranked = [a.value for _, a, _ in rank_actions(
        state, [ActionType.check_step, ActionType.derive_step])]
    assert ranked[0] == "derive_step", ranked
