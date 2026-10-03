from __future__ import annotations

"""结构化研究动作提议器 (计划书 §3 R1)。

计划书的原始判断: 引擎会调用 `_propose()`, 但正式图装配 `TheoryEngine` 时**没有
注入 proposer**, 因此真实路径仍按缺口规则和固定打分选动作。本模块提供
"给正式入口用的"提议器, 并把它要求的结构化字段与校验写清楚:

输入 (由引擎的 ContextPack 提供)
--------------------------------
当前问题、候选/已选领域模型、证据冲突、未决义务、已失败路线、预算余量、
**当前可派发动作清单**。

输出 (必须齐全, 缺一项即视为无效提议)
------------------------------------
```json
{"action_type": "...", "object_id": "clm-xxxx@v2", "target_gap": "...",
 "why_now": "...", "uncertainty_reduced": "...", "on_failure": "..."}
```

约束 (与计划书一致)
------------------
- 只能从"当前可派发动作"里选; 未注册/前置条件不满足的提议由协调者拒绝并记录;
- `object_id` 必须带版本 (`id@vN`); 版本与当前不一致的提议被拒绝
  (旧版本上做动作等于在过期的命题上研究);
- 提议**不改变任何结论状态**: 它只决定"下一步做什么";
- 停用 LLM 时返回 None, 由协调者的确定性排序接管 (稳定降级)。
"""

import re
from dataclasses import dataclass, field

from src.utils.external_data import wrap_external_with_scan

# 提议必须给出的结构化字段
REQUIRED_FIELDS = ("action_type", "object_id", "why_now", "uncertainty_reduced",
                   "on_failure")

_SYSTEM_PROMPT = (
    "你是研究流程的动作规划助手。只输出一个 JSON 对象, 不要任何解释或 Markdown。\n"
    "硬约束:\n"
    "1. action_type 只能取给定清单中的值; 不要发明动作;\n"
    "2. 结论状态不由你决定: 你只提议下一步做什么, 不得声称任何命题已成立/已证明;\n"
    "3. object_id 必须写成 `<对象ID>@v<版本>` 且只能引用给定上下文里出现过的对象;\n"
    "4. 四个说明字段必须具体: why_now (为什么现在做这件事)、"
    "uncertainty_reduced (这一步将减少哪一项不确定性)、"
    "on_failure (如果失败/无法判定, 下一步怎么办)。\n"
    "JSON: {\"action_type\": str, \"object_id\": str, \"target_gap\": str, "
    "\"why_now\": str, \"uncertainty_reduced\": str, \"on_failure\": str}"
)

_VERIFICATION_WORDS = re.compile(
    r"已证明|已被证明|已验证|已成立|证明完毕|证毕|\bQED\b|\bproved\b|\bverified\b",
    re.IGNORECASE)


@dataclass
class Proposal:
    """一次提议的解析结果 (含被拒原因, 供审计)。"""

    action_type: str = ""
    object_id: str = ""
    object_version: int = 0
    target_gap: str = ""
    why_now: str = ""
    uncertainty_reduced: str = ""
    on_failure: str = ""
    ok: bool = False
    rejected: list[str] = field(default_factory=list)
    model: str = ""

    def describe(self) -> str:
        if not self.ok:
            return "提议无效: " + "; ".join(self.rejected)
        return (f"提议 {self.action_type} → {self.object_id} "
                f"(减少不确定性: {self.uncertainty_reduced})")

    def to_payload(self) -> dict:
        """转成协调者 `_from_proposal` 认识的形状。"""
        return {
            "action_type": self.action_type,
            "object_id": self.object_id,
            "target_gap": self.target_gap,
            "reason": self.why_now or "模型提议",
            "why_now": self.why_now,
            "uncertainty_reduced": self.uncertainty_reduced,
            "on_failure": self.on_failure,
        }


# 不接受对象绑定的动作: 这些动作作用于"整个研究"而不是某个对象,
# 因此允许 object_id 为空 (要求所有提议都带对象会拒掉合法的全局动作)。
GLOBAL_ACTIONS = frozenset({
    "retrieve_targeted", "design_experiment", "synthesize_results",
    "deliver_partial", "stop_with_report", "clarify_problem", "compare_prior_work",
})


def parse_proposal(raw: str, *, allowed_actions: list[str] | None = None,
                   known_objects: dict[str, int] | None = None,
                   prefer_object_id: bool = True) -> Proposal:
    """解析并校验模型提议。

    `known_objects`: {对象ID: 当前版本}; 提供的对象的 `@vN` 必须与之一致,
    不一致即拒绝 (不能在旧版本上提案)。`allowed_actions` 为空表示不限制动作集合
    (由协调者的前置条件检查兜底)。
    """
    proposal = Proposal()
    text = (raw or "").strip()
    if not text:
        proposal.rejected.append("空输出")
        return proposal
    data = _loads_json_object(text)
    if data is None:
        proposal.rejected.append("输出不是合法 JSON 对象")
        return proposal

    allowed = {a.strip() for a in (allowed_actions or []) if str(a).strip()}
    action = str(data.get("action_type") or "").strip()
    if not action:
        proposal.rejected.append("缺少 action_type")
    elif allowed and action not in allowed:
        proposal.rejected.append(
            f"动作 {action} 不在当前可派发清单内 (只能从 {sorted(allowed)} 中选)")

    raw_object = str(data.get("object_id") or "").strip()
    obj_id, version = _split_ref(raw_object)
    needs_object = bool(action) and action not in GLOBAL_ACTIONS
    if not obj_id and needs_object:
        proposal.rejected.append("缺少 object_id")
    elif obj_id and known_objects:
        if obj_id not in known_objects:
            proposal.rejected.append(f"对象 {obj_id} 不在当前研究范围内")
        else:
            current = known_objects[obj_id]
            if version and version != current:
                proposal.rejected.append(
                    f"对象版本过期: 提议基于 v{version}, 当前为 v{current}")
            version = current

    for key in ("why_now", "uncertainty_reduced", "on_failure"):
        value = str(data.get(key) or "").strip()
        if not value:
            proposal.rejected.append(f"缺少结构化字段 {key}")
        elif _VERIFICATION_WORDS.search(value):
            # 提议不得替验证器宣布结论
            proposal.rejected.append(f"{key} 出现结论性断言 (状态只能由验证器写入)")

    proposal.action_type = action
    proposal.object_id = obj_id
    proposal.object_version = version or 0
    proposal.target_gap = str(data.get("target_gap") or "").strip()
    proposal.why_now = str(data.get("why_now") or "").strip()[:400]
    proposal.uncertainty_reduced = str(data.get("uncertainty_reduced") or "").strip()[:300]
    proposal.on_failure = str(data.get("on_failure") or "").strip()[:300]
    proposal.ok = not proposal.rejected
    return proposal


def _split_ref(ref: str) -> tuple[str, int]:
    """把 `id@vN` 拆成 (id, 版本); 没有版本后缀时版本为 0。"""
    text = (ref or "").strip()
    if not text:
        return "", 0
    match = re.match(r"^(?P<id>.+?)(?:@v(?P<ver>\d+))?$", text)
    if not match:
        return text, 0
    version = int(match.group("ver")) if match.group("ver") else 0
    return match.group("id"), version


# JSON 容错解析统一在 `src/utils/json_text.py` (此处与 derivation.py 曾是逐字重复的两份)
from src.utils.json_text import loads_json_object as _loads_json_object  # noqa: E402


def build_proposal_prompt(context_text: str, *, available_actions: list[str],
                          known_objects: dict[str, int]) -> str:
    """把上下文与"可派发动作/可选对象"拼成提示词。

    上下文按外部资料处理 (它包含检索到的外部文本), 定界后交给模型。
    """
    wrapped, scan = wrap_external_with_scan(context_text, source="当前研究状态")
    objects = "\n".join(f"- {obj_id}@v{ver}" for obj_id, ver in
                        sorted(known_objects.items())) or "- (暂无可引用对象)"
    actions = ", ".join(available_actions) or "(无可用动作)"
    prompt = (
        "根据当前研究状态, 决定**下一步**做一个动作。\n"
        f"可派发动作: {actions}\n"
        f"可引用对象 (必须带版本):\n{objects}\n"
        "请给出 action_type / object_id / target_gap / why_now / "
        "uncertainty_reduced / on_failure。\n\n" + wrapped
    )
    return prompt, scan.describe()


def make_proposer(llm, *, event_sink=None, max_calls: int = 0):
    """构造可注入 `TheoryEngine(proposer=...)` 的提议函数。

    - `llm`: 已包好记账的 LLM (`engine.metered_llm(...)`); None 时返回的函数恒返回 None;
    - `event_sink`: 可选 `callable(dict)`, 用于把"提议/拒绝理由"写进研究日志;
    - `max_calls`: >0 时限制提议次数, 超出后返回 None (退回确定性排序)。
    """
    calls = {"n": 0}

    def _proposer(payload: dict) -> dict | None:
        # 兼容两种调用约定: 引擎传 {"context","state"}, 协调者直接传 state
        if "context" in payload or "state" in payload:
            state = payload.get("state") or {}
            context_text = payload.get("context") or state.get("context_text") or ""
        else:
            state = payload or {}
            context_text = state.get("context_text") or ""
        available = list(state.get("available_actions") or [])
        known = _known_objects(state)
        if llm is None:
            return None
        if max_calls and calls["n"] >= max_calls:
            _emit(event_sink, {"type": "proposal_skipped",
                               "reason": f"已达提议次数上限 {max_calls}"})
            return None
        calls["n"] += 1
        prompt, scan_note = build_proposal_prompt(context_text,
                                                  available_actions=available,
                                                  known_objects=known)
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            result = llm.invoke([SystemMessage(content=_SYSTEM_PROMPT),
                                 HumanMessage(content=prompt)])
        except Exception as e:  # noqa: BLE001 - 提议失败不得中断研究
            _emit(event_sink, {"type": "proposal_failed",
                               "reason": f"{type(e).__name__}: {e}"})
            return None
        text = result.content if hasattr(result, "content") else str(result)
        proposal = parse_proposal(text, allowed_actions=available, known_objects=known)
        if scan_note:
            proposal.rejected_scan = scan_note  # type: ignore[attr-defined]
        if not proposal.ok:
            _emit(event_sink, {"type": "proposal_rejected",
                               "reasons": list(proposal.rejected),
                               "raw_head": (text or "")[:200]})
            return None
        _emit(event_sink, {"type": "proposal_used", "action_type": proposal.action_type,
                           "object_id": f"{proposal.object_id}@v{proposal.object_version}",
                           "why_now": proposal.why_now,
                           "uncertainty_reduced": proposal.uncertainty_reduced,
                           "on_failure": proposal.on_failure,
                           "allowed_actions": available})
        return proposal.to_payload()

    return _proposer


def _known_objects(state: dict) -> dict[str, int]:
    """从状态里收集"可引用的对象版本" (结论/义务/模型/假设/证据)。"""
    out: dict[str, int] = {}
    for key in ("unresolved_claims", "undetermined_claims", "retryable_claims",
                "pending_novelty", "unplanned_claims"):
        for item in state.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = int(item.get("version", 1) or 1)
    for item in state.get("open_obligations") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = int(item.get("version", 1) or 1)
    return out


def _emit(sink, event: dict) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 - 日志失败不得影响研究
        pass


__all__ = [
    "REQUIRED_FIELDS",
    "Proposal",
    "build_proposal_prompt",
    "make_proposer",
    "parse_proposal",
]
