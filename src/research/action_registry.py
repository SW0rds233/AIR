from __future__ import annotations

"""研究动作契约与注册表 (P1)。

计划书 §5.3 / §2 P0-A02/A05 的两条硬约束:
1. **注册表只向控制器暴露当前确实有执行器且满足条件的动作** ——
   `available_actions(state)` 同时检查"有 handler"与"前置条件成立";
   没有有效动作时控制器必须澄清、换路或部分交付, 不能空转。
2. 前置条件失败不是只写一个字段, 而是形成的**执行门禁**:
   `check_preconditions` 的结果决定动作是否可派发。

动作声明输入 schema、前置条件、可修改对象与输出 schema。
验证结果由执行器与规则层写入, 协调者不得直接把命题状态改为"已证明"。
"""

from collections.abc import Callable

from pydantic import BaseModel, Field

from src.research.schemas import ActionType


class ActionSpec(BaseModel):
    action_type: ActionType
    description: str
    inputs: dict[str, str] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    writable: list[str] = Field(default_factory=list)
    output: str = ""
    estimated_cost: int = 1
    # 是否已有真正的执行器实现 (未实现的动作不暴露给控制器)
    implemented: bool = True


# 前置条件谓词: 名称 -> (状态 dict -> bool)
_PRECONDITIONS: dict[str, Callable[[dict], bool]] = {
    "has_open_obligations": lambda s: bool(s.get("open_obligations")),
    "has_unplanned_claim": lambda s: bool(s.get("unplanned_claims")),
    "has_pending_novelty": lambda s: bool(s.get("pending_novelty")),
    "has_targeted_request": lambda s: bool(s.get("retrieval_requests")),
    "has_unresolved_claims": lambda s: bool(s.get("unresolved_claims")),
    # 冻结快照要求**全部必要义务关闭**, 而不是"没有被标记为 open 的义务":
    # blocked/refuted 的义务同样不得被当作已完成研究。
    "all_obligations_closed": lambda s: bool(s.get("all_obligations_closed")),
    "has_questions": lambda s: bool(s.get("questions")),
    "budget_available": lambda s: s.get("budget_remaining", 0) > 0,
    "has_knowledge": lambda s: bool(s.get("retrieval_available", s.get("knowledge_available"))),
    "has_evidence_gap": lambda s: bool(s.get("evidence_gaps")),
    "has_pending_evidence": lambda s: bool(s.get("pending_evidence")),
    "has_retryable_claim": lambda s: bool(s.get("retryable_claims")),
    "has_failed_route": lambda s: bool(s.get("failed_routes")),
    "has_undetermined_claim": lambda s: bool(s.get("undetermined_claims")),
    # 换路/修订面向**所有未定结论**, 包括 blocked (受阻正是需要换路的情形)
    "has_undetermined_or_unresolved": lambda s: bool(s.get("undetermined_claims")
                                                     or s.get("unresolved_claims")),
    "no_route_for_claim": lambda s: bool(s.get("claims_without_route")),
    # 模型/实验规格缺口: 已有对应对象时不再重复提议 (避免同一动作空转)
    "has_model_gap": lambda s: bool(s.get("model_gaps")),
    "has_experiment_gap": lambda s: bool(s.get("experiment_gaps")),
    # 新颖性比较需要真实的检索能力 (否则只能得到"未接入检索"的空结论)
    "has_novelty_lookup": lambda s: bool(s.get("novelty_lookup_available")),
}


ACTION_REGISTRY: dict[ActionType, ActionSpec] = {}


def register(spec: ActionSpec) -> None:
    ACTION_REGISTRY[spec.action_type] = spec


def get(action_type: ActionType) -> ActionSpec:
    return ACTION_REGISTRY[action_type]


def check_preconditions(action_type: ActionType, state: dict) -> tuple[bool, str]:
    """执行门禁: (是否可派发, 原因)。"""
    spec = ACTION_REGISTRY.get(action_type)
    if spec is None:
        return False, "未注册的动作"
    if not spec.implemented:
        return False, "该动作尚无执行器"
    for name in spec.preconditions:
        pred = _PRECONDITIONS.get(name)
        if pred is None:
            return False, f"未知前置条件 {name}"
        if not pred(state):
            return False, f"前置条件不满足: {name}"
    return True, ""


def available_actions(state: dict) -> list[ActionType]:
    """当前真正可执行的动作集合 (有执行器 + 前置条件满足)。"""
    return [at for at in ACTION_REGISTRY
            if check_preconditions(at, state)[0]]



def _bootstrap() -> None:
    register(ActionSpec(
        action_type=ActionType.propose_claim,
        description="提出候选猜想/更强或更弱的命题版本",
        inputs={"object_id": "str"},
        preconditions=["has_questions"],
        writable=["claim"],
        output="Claim",
        implemented=False,   # 结构性变更由 revise_hypothesis 承接, 不在此重复
    ))
    register(ActionSpec(
        action_type=ActionType.propose_model,
        description="提出候选领域模型 (自然语言定义 + 形式化编码 + 适用域)",
        inputs={"claim_id": "str"},
        preconditions=["has_questions", "has_model_gap"],
        writable=["model"],
        output="ResearchModel",
    ))
    register(ActionSpec(
        action_type=ActionType.derive_step,
        description="从已有定义/假设/引理推导一个证明步骤",
        inputs={"claim_id": "str"},
        preconditions=["has_unresolved_claims"],
        writable=["attempt"],
        output="ProofStep",
    ))
    register(ActionSpec(
        action_type=ActionType.extract_result,
        description="从文献证据中抽取定理卡/条件",
        inputs={"evidence_id": "str"},
        preconditions=["has_knowledge"],
        writable=["evidence"],
        output="SourceEvidence",
    ))
    register(ActionSpec(
        action_type=ActionType.interpret_evidence,
        description="判定候选证据与命题的支持/反对关系 (生成 EvidenceLink)",
        inputs={"claim_id": "str"},
        preconditions=["has_pending_evidence"],
        writable=["evidence", "evidence_link"],
        output="list[EvidenceLink]",
    ))
    register(ActionSpec(
        action_type=ActionType.plan_proof,
        description="为目标命题制定证明路线与子目标",
        inputs={"claim_id": "str"},
        preconditions=["has_unplanned_claim", "budget_available"],
        writable=["attempt"],
        output="ProofAttempt",
    ))
    register(ActionSpec(
        action_type=ActionType.check_step,
        description="对具体证明义务执行受限工具核验",
        inputs={"obligation_id": "str", "tool": "str"},
        preconditions=["has_open_obligations", "budget_available"],
        writable=["verification", "obligation"],
        output="VerificationRecord",
    ))
    register(ActionSpec(
        action_type=ActionType.seek_counterexample,
        description="对命题或严格版本寻找精确反例",
        inputs={"claim_id": "str"},
        preconditions=["has_undetermined_claim", "budget_available"],
        writable=["verification", "obligation"],
        output="VerificationRecord",
    ))
    register(ActionSpec(
        action_type=ActionType.compare_prior_work,
        description="与已有工作比较, 判定新颖性",
        inputs={"claim_id": "str"},
        preconditions=["has_pending_novelty", "has_novelty_lookup"],
        writable=["novelty"],
        output="NoveltyRecord",
    ))
    register(ActionSpec(
        action_type=ActionType.retrieve_targeted,
        description="按当前缺口定向检索定理/反例/已有结果",
        inputs={"request": "RetrievalRequest"},
        preconditions=["has_targeted_request", "has_knowledge", "budget_available"],
        writable=["evidence"],
        output="list[SourceEvidence]",
    ))
    register(ActionSpec(
        action_type=ActionType.read_source,
        description="回到原文读取完整上下文 (摘要不能代替完整条件)",
        inputs={"source_ref": "SourceRef"},
        preconditions=["has_knowledge", "budget_available"],
        writable=["evidence"],
        output="dict",
    ))
    register(ActionSpec(
        action_type=ActionType.switch_strategy,
        description="连续无进展时真实换路: 新建路线并保留失败原因",
        inputs={"claim_id": "str"},
        preconditions=["has_undetermined_or_unresolved", "has_retryable_claim"],
        writable=["route", "attempt"],
        output="ResearchRoute",
    ))
    register(ActionSpec(
        action_type=ActionType.design_experiment,
        description="为具体未决问题生成实验/仿真规格 (不执行)",
        inputs={"claim_id": "str"},
        preconditions=["has_undetermined_or_unresolved", "has_experiment_gap"],
        writable=["experiment_spec"],
        output="ExperimentSpec",
    ))
    register(ActionSpec(
        action_type=ActionType.synthesize_results,
        description="汇总结果并冻结研究快照",
        inputs={},
        preconditions=["all_obligations_closed"],
        writable=["snapshot"],
        output="ResearchSnapshot",
    ))
    register(ActionSpec(
        action_type=ActionType.deliver_partial,
        description="存在未决项时输出部分结果与未决报告",
        inputs={},
        preconditions=[],
        writable=["snapshot"],
        output="ResearchSnapshot",
    ))
    register(ActionSpec(
        action_type=ActionType.stop_with_report,
        description="预算耗尽或路线耗尽时输出部分结果与未决问题",
        inputs={},
        preconditions=[],
        writable=["snapshot"],
        output="ResearchSnapshot",
    ))
    register(ActionSpec(
        action_type=ActionType.clarify_problem,
        description="问题无法形式化或歧义会改变数学含义时请求澄清",
        inputs={"question": "str"},
        preconditions=[],
        writable=[],
        output="str",
    ))
    register(ActionSpec(
        action_type=ActionType.revise_hypothesis,
        description="按用户反馈增删假设/限定范围, 生成新版本并传播失效",
        inputs={"assumption_id": "str", "claim_id": "str"},
        preconditions=[],
        writable=["spec", "assumption", "claim", "verification", "route"],
        output="ResearchSpec",
    ))
    register(ActionSpec(
        action_type=ActionType.write_paper,
        description="从冻结快照生成研究稿 (出口由交付门槛把关)",
        inputs={},
        preconditions=[],
        writable=["manuscript"],
        output="str",
        implemented=False,   # 写作不属于研究内核, 由 reporting 层承接
    ))


_bootstrap()
