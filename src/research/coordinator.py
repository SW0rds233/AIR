from __future__ import annotations

"""研究协调者 (P1/P3): 从**有效动作集合**中选择下一步, 并解释选择理由。

计划书 §5.3 的七步决策:
1. 读取当前目标与相关对象; 2. 构造 ContextPack; 3. 模型提出少量候选动作;
4. 规则层过滤不存在对象/版本过期/前置条件不满足/越权的动作;
5. 从有效动作中选择下一项; 没有有效动作时澄清、换路或部分交付;
6. 执行并记录观察; 7. 比较缺口是否减少后再规划下一步。

修复 (计划书 §2 P0-A02): 旧实现按固定 if 顺序取第一个对象, 前置条件失败只写入
字段而不拦截。现在:
- 候选动作必须同时"有执行器"且"前置条件成立"才进入有效集合;
- 模型提议只作为**候选**, 越界/未注册/前置条件不满足的一律丢弃并记录理由;
- 协调者不写任何研究对象状态。
"""

from collections.abc import Callable

from src.research.action_registry import available_actions, check_preconditions
from src.research.schemas import ActionType, ResearchAction

# 动作 -> (目标缺口类型, 可解释的基础价值 0-10)。
# 价值 = 基础价值 × 缺口紧迫度 ÷ 成本; 仅用于可解释排序, 不训练决策模型。
_ACTION_VALUE = {
    ActionType.retrieve_targeted: (8, "补齐缺失的证据/定理条件"),
    ActionType.read_source: (7, "回到原文核对完整条件"),
    ActionType.interpret_evidence: (7, "判定证据与命题的支持关系"),
    ActionType.propose_model: (6, "建立可检验的领域模型"),
    ActionType.plan_proof: (6, "为目标命题制定证明路线"),
    ActionType.derive_step: (5, "生成可审查的推导步骤并做反方审查"),
    ActionType.check_step: (6, "关闭未决的证明义务"),
    ActionType.seek_counterexample: (5, "判定命题是否为假 (允许否定性结果)"),
    ActionType.compare_prior_work: (4, "判定新颖性/是否已有等价结果"),
    ActionType.extract_result: (4, "从原文抽取可用定理与条件"),
    ActionType.switch_strategy: (7, "当前路线无进展, 更换研究路线"),
    ActionType.design_experiment: (3, "把解析困难部分转为实验规格"),
    ActionType.synthesize_results: (2, "汇总并冻结研究快照"),
    ActionType.revise_hypothesis: (2, "按反馈修订假设/范围"),
}

# 义务受阻 (验证后端无法判定) 时能**产生新信息**的动作 (计划书 §5.3-2)。
# 反复执行同一个 check_step 不会改变结果, 只是在烧预算 —— 必须换一类动作。
_INFO_ACTIONS = {
    ActionType.derive_step: "生成推导步骤与反方审查, 可能补出缺失条件",
    ActionType.propose_model: "建立/更换领域模型, 补充形式化依据",
    ActionType.retrieve_targeted: "补充外部材料以取得缺失条件",
    ActionType.switch_strategy: "更换研究路线以免在同一条死路上重复",
    ActionType.design_experiment: "把解析困难转为可执行的实验规格",
}


def _stuck_resolving(state: dict) -> dict[ActionType, str]:
    """当前主缺口的义务已受阻时, 哪些动作仍能产生新信息。

    触发条件看**义务的验证结果**而不是"试了几次": 只要目标义务的判定是
    unsupported / unknown / encoding_mismatch (非科学性失败), 继续跑同一工具
    不会得到不同结论。
    """
    primary = state.get("primary_gap") or {}
    if primary.get("gap_type") != "open_obligation":
        return {}
    target = primary.get("object_id", "")
    obligation = next((o for o in (state.get("open_obligations") or [])
                       if o.get("id") == target), None)
    if obligation is None:
        return {}
    if obligation.get("validation_status") not in ("unsupported", "unknown",
                                                   "encoding_mismatch"):
        return {}
    return dict(_INFO_ACTIONS)


def _needs_review_first(state: dict) -> bool:
    """该义务所属命题**尚未做过**推导步骤+反方审查。

    未做审查时直接跑验证工具, 只会在缺条件的情况下得到一堆"无法判定",
    而审查才是能补出条件的动作 —— 因此此时 `derive_step` 优先于 `check_step`。
    """
    primary = state.get("primary_gap") or {}
    if primary.get("gap_type") != "open_obligation":
        return False
    claim_id = primary.get("claim_id", "")
    if not claim_id or claim_id in set(state.get("reviewed_claims") or []):
        return False
    target = primary.get("object_id", "")
    obligation = next((o for o in (state.get("open_obligations") or [])
                       if o.get("id") == target), None)
    return bool(obligation) and obligation.get("validation_status") == "unchecked"
# 缺口越靠前越紧迫 (与 loop._compute_state 的 obligations 排序配套)。
_GAP_URGENCY = {
    "evidence": 5,
    "obligation": 4,
    "obligation_unresolved": 4,
    "model": 4,
    "plan": 3,
    "counterexample": 3,
    "novelty": 2,
    "experiment": 2,
    "none": 1,
}


_EXPENSIVE_COST = 3
# 每个动作"打算减少什么不确定性"的简明说明 (P1-2: 昂贵动作必须说清预期信息增益)
_GAIN_HINT = {
    ActionType.retrieve_targeted: "补上缺失的原文或资料覆盖",
    ActionType.read_source: "把候选证据变成可定位的原文条件",
    ActionType.interpret_evidence: "把候选证据判成支持/反对/待审",
    ActionType.propose_model: "得到可比较的候选机制与条件",
    ActionType.plan_proof: "把结论拆成可核验的义务",
    ActionType.derive_step: "得到可逐步复核的推导步骤",
    ActionType.check_step: "用受限工具核验关键一步",
    ActionType.seek_counterexample: "找出会推翻过强结论的构造",
    ActionType.compare_prior_work: "弄清已有工作与本研究的差异",
    ActionType.design_experiment: "给出能区分竞争解释的建议",
    ActionType.switch_strategy: "换掉已失败的研究方法",
}


def _make(action_type: ActionType, state: dict, **kwargs) -> ResearchAction:
    ok, reason = check_preconditions(action_type, state)
    reason_text = kwargs.pop("reason", "")
    budget = max(1, int(state.get("budget_remaining", 1) or 1))
    cost = int(kwargs.get("estimated_cost", 1) or 1)
    if cost >= _EXPENSIVE_COST or budget <= cost:
        gain = _GAIN_HINT.get(action_type, "推进当前缺口")
        cap = min(cost, budget)
        reason_text = (f"{reason_text} | 预期信息增益: {gain}; "
                       f"成本上限: {cap} (剩余预算 {budget})").strip(" |")
        kwargs.setdefault("termination_condition",
                          kwargs.get("termination_condition") or f"最多消耗 {cap} 次动作")
    return ResearchAction(
        action_type=action_type,
        preconditions=[] if ok else [reason],
        reason=reason_text,
        **kwargs,
    )


def _gap_urgency(state: dict, action_type: ActionType) -> int:
    """动作对当前主缺口的紧迫度 (兼容保留)。"""
    primary = state.get("primary_gap") or {}
    if action_type in {ActionType(a) for a in (primary.get("resolving_actions") or [])
                       if _is_action(a)}:
        return 4
    return 1


def rank_actions(state: dict, actions: list[ActionType] | None = None) -> list[tuple[float, ActionType, str]]:
    """对有效动作做可解释排序, 返回 [(分值, 动作, 理由)]。

    排序以**当前缺口**为主, 而不是各动作的固有分值:
    1. 能消除最紧迫缺口的动作优先 (动作 × 缺口相关性);
    2. 其次是与该缺口同类动作 (兜底路线);
    3. 没有缺口时优先冻结快照 (研究已有结论, 不应继续改动假设);
    4. 剩余动作按基础价值与预算压力排序 (部分交付/停止最后考虑)。
    """
    actions = actions if actions is not None else available_actions(state)
    gaps = state.get("gaps") or []
    primary = state.get("primary_gap") or {}
    resolving = {ActionType(a) for a in (primary.get("resolving_actions") or [])
                 if _is_action(a)}
    primary_gap_type = primary.get("gap_type", "")
    cost = max(1, int(state.get("budget_remaining", 1) or 1))
    stuck_actions = _stuck_resolving(state)
    review_first = _needs_review_first(state)
    # P1-2: 最近一次失败的**类型**决定下一动作候选, 而不是笼统重试
    failure_actions = {str(a) for a in (state.get("failure_next_actions") or [])}
    failure_note = str(state.get("failure_judgement", "") or "")

    ranked: list[tuple[float, ActionType, str]] = []
    # 已对当前目标对象执行过 (同一输入版本) 的动作不再作为候选: 重复同一输入
    # 不会带来新信息。避让集合由引擎按"对象 + 版本 + 路线"精确计算。
    dismissed = set(state.get("dismissed_actions") or [])
    for action_type in actions:
        if action_type.value in dismissed:
            continue
        base, why = _ACTION_VALUE.get(action_type, (1, "兜底动作"))
        tier = 0
        urgency = 1
        if failure_actions and action_type.value in failure_actions:
            # 失败类型映射的下一动作**优先于其它一切**: 例如"资料没找到"→扩大检索/读原文,
            # "模型被反例否定"→换机制/新子命题, "工具未知"→换后端。
            tier = 5
            urgency = 6
            why = f"按失败类型选择下一动作 ({failure_note or '失败后改换方法'}): {why}"
        elif review_first and action_type == ActionType.derive_step:
            # 尚未做过推导步骤+反方审查: 先审查再验证 (存在性检查救不了缺条件)
            tier = 4
            urgency = 5
            why = "先做推导步骤与反方审查, 再关闭义务"
        elif not gaps and action_type == ActionType.synthesize_results:
            tier = 3
            urgency = 4
            why = "所有义务已关闭: 冻结研究快照"
        elif action_type in resolving:
            tier = 3
            urgency = 4
            why = f"消除当前缺口({primary_gap_type}): {why}"
        elif action_type in stuck_actions:
            # 义务已受阻 (非科学性失败): 换能产生新信息的动作, 而不是重跑同一工具
            tier = 2
            urgency = 3
            why = f"义务受阻, 改换信息动作: {stuck_actions[action_type]}"
        elif _resolves_gap_type(action_type, primary_gap_type):
            tier = 2
            urgency = 2
            why = f"同类缺口兜底路线: {why}"
        elif action_type in (ActionType.deliver_partial, ActionType.stop_with_report,
                             ActionType.synthesize_results, ActionType.clarify_problem):
            tier = 0
            urgency = 0 if gaps else 1
            why = f"收尾/澄清: {why}"
        # 没有缺口时不应再改动假设/命题: revise_hypothesis 只在有缺口时才可用
        if action_type == ActionType.revise_hypothesis and not gaps:
            tier = -1
            why = "无缺口时不做假设修订"
        score = (tier * 100 + base * 10) * max(1, urgency) / (1.0 + (1.0 / cost))
        ranked.append((score, action_type, why))
    ranked.sort(key=lambda x: (-x[0], x[1].value))
    return ranked


def _is_action(value) -> bool:
    try:
        ActionType(str(value))
        return True
    except ValueError:
        return False


# 缺口类型 → 可消除它的动作类别 (用于同类兜底)
_GAP_ACTIONS = {
    "missing_evidence": {ActionType.retrieve_targeted, ActionType.read_source,
                         ActionType.interpret_evidence, ActionType.extract_result},
    "missing_condition": {ActionType.retrieve_targeted, ActionType.switch_strategy,
                          ActionType.revise_hypothesis},
    "missing_model": {ActionType.propose_model, ActionType.retrieve_targeted,
                      ActionType.read_source},
    "missing_definition": {ActionType.propose_model, ActionType.retrieve_targeted},
    "open_obligation": {ActionType.check_step, ActionType.plan_proof},
    "novelty_unchecked": {ActionType.compare_prior_work},
    "encoding_mismatch": {ActionType.switch_strategy, ActionType.revise_hypothesis},
    "route_exhausted": {ActionType.switch_strategy, ActionType.design_experiment,
                        ActionType.deliver_partial},
    "budget_exhausted": {ActionType.deliver_partial, ActionType.stop_with_report},
}


def _resolves_gap_type(action_type: ActionType, gap_type: str) -> bool:
    return action_type in _GAP_ACTIONS.get(gap_type, set())


def _known_object_versions(state: dict) -> dict[str, int]:
    """当前可引用对象的版本表 (提议里的 `id@vN` 必须与之一致)。"""
    out: dict[str, int] = {}
    for key in ("unresolved_claims", "undetermined_claims", "retryable_claims",
                "pending_novelty", "unplanned_claims", "claims_without_route",
                "open_obligations"):
        for item in state.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = int(item.get("version", 1) or 1)
    return out


# 不绑定具体对象的动作: 提议省略 object_id 是合法的
_GLOBAL_ACTIONS = frozenset({
    ActionType.retrieve_targeted, ActionType.design_experiment,
    ActionType.synthesize_results, ActionType.deliver_partial,
    ActionType.stop_with_report, ActionType.clarify_problem,
    ActionType.compare_prior_work,
})


def _record_rejection(state: dict, reason: str, proposal: dict | None = None) -> None:
    """记录拒绝理由 (内存态 + 审计事件)。"""
    state.setdefault("rejected_proposals", []).append(reason)
    entry = {"reason": reason}
    if proposal:
        entry["proposal"] = {k: proposal.get(k) for k in
                             ("action_type", "object_id", "why_now",
                              "uncertainty_reduced", "on_failure")}
    state.setdefault("proposal_audit", []).append(entry)


def _from_proposal(proposal: dict | None, state: dict) -> ResearchAction | None:
    """把模型提议转成动作; 越界/未注册/前置条件不满足的一律拒绝并记录理由。

    R1: 除动作是否注册、前置条件是否满足之外, 还要校验**对象与版本**:
    提议引用不在当前研究范围内的对象、或基于过期版本 (`@vN` 与当前不一致) 时拒绝。
    """
    if not proposal:
        return None
    try:
        action_type = ActionType(str(proposal.get("action_type", "")))
    except ValueError:
        _record_rejection(state, f"未注册的动作提议: {proposal.get('action_type')!r}",
                          proposal)
        return None
    ok, reason = check_preconditions(action_type, state)
    if not ok:
        _record_rejection(state, f"提议 {action_type.value} 被拒绝: {reason}", proposal)
        return None

    object_id = str(proposal.get("object_id", "") or "")
    obj_id, version = _split_object_ref(object_id)
    known = _known_object_versions(state)
    if obj_id and obj_id in known:
        current = known[obj_id]
        if version and version != current:
            _record_rejection(
                state,
                f"提议对象版本过期: {obj_id} 提议基于 v{version}, 当前为 v{current}",
                proposal)
            return None
        version = current
    elif obj_id and obj_id not in known:
        _record_rejection(state, f"提议对象 {obj_id} 不在当前研究范围内", proposal)
        return None
    elif not obj_id and action_type not in _GLOBAL_ACTIONS:
        _record_rejection(
            state, f"提议动作 {action_type.value} 必须绑定具体对象 (object_id 为空)",
            proposal)
        return None

    kwargs = {
        "object_id": obj_id,
        "target_gap": str(proposal.get("target_gap", "") or ""),
        "estimated_cost": int(proposal.get("estimated_cost", 1) or 1),
        "termination_condition": str(proposal.get("termination_condition", "") or ""),
        "reason": _proposal_reason(proposal),
    }
    input_versions = dict(proposal.get("input_versions") or {})
    if obj_id and version:
        input_versions.setdefault(obj_id, version)
    if input_versions:
        kwargs["input_versions"] = input_versions
    state.setdefault("proposal_audit", []).append({
        "accepted": True, "action_type": action_type.value, "object_id": obj_id,
        "version": version,
        "why_now": str(proposal.get("why_now", "") or ""),
        "uncertainty_reduced": str(proposal.get("uncertainty_reduced", "") or ""),
        "on_failure": str(proposal.get("on_failure", "") or ""),
    })
    return ResearchAction(action_type=action_type, **kwargs)


def _split_object_ref(ref: str) -> tuple[str, int]:
    """把 `id@vN` 拆成 (id, 版本); 无版本后缀时版本为 0。"""
    text = (ref or "").strip()
    if not text:
        return "", 0
    if "@v" not in text:
        return text, 0
    head, _, tail = text.rpartition("@v")
    return head, (int(tail) if tail.isdigit() else 0)


def _proposal_reason(proposal: dict) -> str:
    """把结构化提议的理由拼成可读原因 (含"为什么现在做/减少什么不确定性/失败怎么办")。"""
    bits = [str(proposal.get("why_now") or proposal.get("reason") or "模型提议")]
    if proposal.get("uncertainty_reduced"):
        bits.append(f"减少不确定性: {proposal['uncertainty_reduced']}")
    if proposal.get("on_failure"):
        bits.append(f"失败后: {proposal['on_failure']}")
    return "; ".join(bits)[:500]


def decide(
    state: dict,
    proposal: dict | None = None,
    proposer: Callable[[dict], dict | None] | None = None,
) -> ResearchAction:
    """选择下一步动作。state 由引擎计算 (见 loop._compute_state)。"""
    if proposal is None and proposer is not None:
        try:
            proposal = proposer(state)
        except Exception as e:  # noqa: BLE001 - 提议失败必须回退到确定性排序
            state.setdefault("rejected_proposals", []).append(f"提议失败: {e}")
            proposal = None

    from_proposal = _from_proposal(proposal, state)
    if from_proposal is not None:
        return from_proposal

    if state.get("budget_remaining", 0) <= 0:
        return _make(ActionType.stop_with_report, state, target_gap="预算耗尽",
                     reason="动作/工具预算耗尽, 输出部分结果与未决问题")

    ranked = rank_actions(state)
    if not ranked:
        # 当前目标对象的动作都已执行过, 或没有满足前置条件的动作:
        # 不得空转 —— 只能澄清、换路或部分交付
        reason = state.get("no_action_reason", "当前没有满足前置条件的新动作")
        return _make(ActionType.deliver_partial, state,
                     target_gap="no_valid_action",
                     reason=f"{reason}; 输出部分结果与未决项")

    score, action_type, why = ranked[0]
    target = state.get("primary_gap") or {}
    return _make(
        action_type, state,
        object_id=str(target.get("object_id", "") or state.get("action_object_id", "")),
        target_gap=str(target.get("gap_type", "") or ""),
        estimated_cost=1,
        reason=f"{why} (评分 {score:.1f}, 可用动作 {len(ranked)} 个)",
    )
