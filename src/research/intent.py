from __future__ import annotations

"""研究意图解析 (P2)。

把用户反馈解析为**研究对象上的动作** (绑定 assumption_id / claim_id / step_id),
而不是只返回 scope/revise。控制指令 (停止/暂停/导出) 保留确定性处理。

修复 (计划书 §2 P1-A11、§6.1):
- 旧实现"匹配不到就取第一个对象", 会把用户的局部修正施加到错误的对象上;
- 现在解析不出唯一对象时**不猜**: 返回歧义标记, 由调用方请求澄清;
- 输出带对象 ID、版本与依据原句, 可直接转为变更提案;
- 语义解析优先用模型 (可选), 规则只做保守兜底与校验。
"""

import json
import re

from src.research.action_registry import ACTION_REGISTRY
from src.research.schemas import ActionType

_CONTROL = {
    "stop": ("停止", "终止", "quit", "exit", "stop"),
    "pause": ("暂停", "先停", "pause"),
    "export": ("导出", "保存", "下载", "export", "save"),
}

# 语义解析提示 (可选 LLM 路径)
_SEMANTIC_SYSTEM = (
    "你是研究意图解析器。把用户的反馈转成对研究对象的动作列表。"
    "只输出 JSON: {\"actions\":[{\"action_type\":str,\"object_id\":str,\"reason\":str,"
    "\"evidence\":str}],\"ambiguous\":bool,\"clarify\":str}。"
    "object_id 必须是给定对象清单中的 ID; 无法确定时 ambiguous=true 并在 clarify 中说明。"
    "不许臆造不存在的对象 ID。"
)


def classify_control(feedback: str) -> str | None:
    text = (feedback or "").strip().lower()
    if not text:
        return None
    for kind, words in _CONTROL.items():
        if any(w in text for w in words):
            # "不要停止" 之类否定避免误判
            if kind == "stop" and re.search(r"(不要|别|无需)停", feedback):
                continue
            return kind
    return None


def _tokens(text: str) -> list[str]:
    """切词: 保留长度 ≥1 的字母/数字/汉字串 (单字母符号如 M 也是有效指称)。"""
    return [t for t in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text or "") if t]


def _overlap_score(feedback_tokens: set[str], description: str) -> float:
    desc_tokens = [t for t in _tokens(description) if len(t) >= 2]
    if not desc_tokens:
        return 0.0
    hits = sum(1 for t in desc_tokens if any(t in f or f in t for f in feedback_tokens))
    return hits / len(desc_tokens)


def match_objects(feedback: str, objects: dict[str, str]) -> list[str]:
    """按反馈与对象描述的**重叠度**挑选候选, 返回全部并列最高的对象 ID。

    - 用户直接写了对象 ID (如 `clm-xxxx`) → 直接命中, 不算歧义;
    - 否则按描述覆盖率排序, 只有并列最高才算歧义。
    """
    explicit = [obj_id for obj_id in (objects or {}) if obj_id and obj_id in (feedback or "")]
    if len(explicit) == 1:
        return explicit
    if len(explicit) > 1:
        return explicit
    feedback_tokens = set(_tokens(feedback))
    if not feedback_tokens:
        return []
    scores = {obj_id: _overlap_score(feedback_tokens, desc or "")
              for obj_id, desc in (objects or {}).items()}
    best = max(scores.values(), default=0.0)
    if best <= 0:
        return []
    return [obj_id for obj_id, score in scores.items() if score == best]


def _match_object(feedback: str, objects: dict[str, str]) -> tuple[str, bool]:
    """返回 (object_id, ambiguous)。命中多个并列最高或无命中都算歧义, 不猜第一个。"""
    hits = match_objects(feedback, objects)
    if len(hits) == 1:
        return hits[0], False
    return "", True


def _first(ids) -> str:
    ids = list(ids or [])
    return ids[0] if ids else ""


def parse_intent(feedback: str, context: dict | None = None, llm=None) -> list[dict]:
    """返回动作列表 [{"action_type", "object_id", "reason", "evidence", ...}]。

    无法唯一确定对象时会附带 ``ambiguous`` 与 ``clarify`` 字段, 调用方必须请求澄清,
    不得默认对第一个对象执行。
    """
    context = context or {}
    feedback = (feedback or "").strip()
    if not feedback:
        return []
    control = classify_control(feedback)
    if control in ("stop", "pause", "export"):
        return [{"action_type": "stop_with_report", "object_id": "",
                 "reason": f"用户控制指令: {control}", "evidence": feedback}]

    assumptions = context.get("assumptions", {}) or {}
    claims = context.get("claims", {}) or {}
    steps = context.get("steps", {}) or {}

    semantic = _semantic_parse(feedback, context, llm)
    if semantic is not None:
        return semantic

    actions: list[dict] = []

    if re.search(r"(不要|别|无需|取消|删除|去掉|移除).{0,6}假设|假设.{0,4}(删除|取消|不要)", feedback):
        object_id, ambiguous = _match_object(feedback, assumptions)
        actions.append(_action("revise_hypothesis", object_id, assumptions,
                               "用户要求删除/修改假设", feedback, ambiguous))

    if re.search(r"(更紧|更严格|加强|改进|能不能更|更强|弱化|放宽)", feedback):
        object_id, ambiguous = _match_object(feedback, claims)
        actions.append(_action("revise_hypothesis" if "弱化" in feedback or "放宽" in feedback
                               else "propose_claim",
                               object_id, claims, "用户要求调整结论强度", feedback, ambiguous))

    if re.search(r"(特例|特殊情况|先证|退一步)", feedback):
        object_id, ambiguous = _match_object(feedback, claims)
        actions.append(_action("propose_claim", object_id, claims,
                               "用户要求先处理特例", feedback, ambiguous))

    m_step = re.search(r"第\s*(\d+)\s*步", feedback)
    if m_step and re.search(r"(解释|说明|为什么|怎么|核查|检查)", feedback):
        index = m_step.group(1)
        object_id = next((sid for sid, desc in steps.items()
                          if str(desc).startswith(f"{index}:") or f"第{index}步" in str(desc)), "")
        ambiguous = not object_id
        actions.append(_action("check_step", object_id, steps,
                               f"用户要求解释/核查第 {index} 步", feedback, ambiguous))

    if re.search(r"(反例|不成立|证伪|推翻|是否为假)", feedback):
        object_id, ambiguous = _match_object(feedback, claims)
        actions.append(_action("seek_counterexample", object_id, claims,
                               "用户允许证明或严格反例两种结果", feedback, ambiguous))

    if re.search(r"(停止|放弃|终止).{0,10}路线", feedback):
        actions.append({"action_type": "stop_with_report", "object_id": "",
                        "reason": "用户要求停止当前路线 (保留已验证引理)",
                        "evidence": feedback})

    if re.search(r"(检索|查找|搜索|调研|找.{0,4}定理|查.{0,4}资料)", feedback):
        object_id, ambiguous = _match_object(feedback, claims)
        actions.append(_action("retrieve_targeted", object_id, claims,
                               "用户要求定向检索", feedback, ambiguous))

    # 校验动作类型与对象存在性; 未注册动作丢弃并保留原文
    valid: list[dict] = []
    for action in actions:
        try:
            atype = ActionType(action["action_type"])
        except ValueError:
            continue
        if atype not in ACTION_REGISTRY:
            continue
        valid.append(action)
    return valid


def _action(action_type: str, object_id: str, objects: dict[str, str], reason: str,
            feedback: str, ambiguous: bool) -> dict:
    action = {
        "action_type": action_type, "object_id": object_id, "reason": reason,
        "evidence": feedback,
        # 用可序列化的字典表示对象引用 (动作会被写入事件日志)
        "object_ref": {"id": object_id, "version": 1} if object_id else None,
    }
    if ambiguous or not object_id:
        action["ambiguous"] = True
        action["clarify"] = (
            f"无法唯一确定「{reason}」作用的对象"
            + (f"; 候选: {', '.join(list(objects)[:5])}" if objects else "; 当前没有可选对象")
        )
    return action


def _semantic_parse(feedback: str, context: dict, llm) -> list[dict] | None:
    """可选 LLM 语义解析: 失败或输出不合规时返回 None (回退到规则层)。"""
    if llm is None:
        return None
    objects = []
    for kind in ("assumptions", "claims", "steps"):
        for obj_id, desc in (context.get(kind) or {}).items():
            objects.append(f"{obj_id} [{kind}] {desc}")
    if not objects:
        return None
    prompt = (
        "可用动作: " + ", ".join(a.value for a in ACTION_REGISTRY) + "\n"
        "研究对象:\n- " + "\n- ".join(objects) + f"\n\n用户反馈: {feedback}"
    )
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        result = llm.invoke([SystemMessage(content=_SEMANTIC_SYSTEM),
                             HumanMessage(content=prompt)])
        text = result.content if hasattr(result, "content") else str(result)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or "actions" not in data:
        return None

    known = {oid for kind in ("assumptions", "claims", "steps")
             for oid in (context.get(kind) or {})}
    out: list[dict] = []
    for item in data.get("actions") or []:
        action_type = str(item.get("action_type", ""))
        object_id = str(item.get("object_id", ""))
        try:
            atype = ActionType(action_type)
        except ValueError:
            continue
        if atype not in ACTION_REGISTRY:
            continue
        # 对象存在性校验: 模型臆造的 ID 一律拒绝
        if object_id and object_id not in known:
            continue
        action = {
            "action_type": action_type, "object_id": object_id,
            "reason": str(item.get("reason", "") or "模型语义解析"),
            "evidence": str(item.get("evidence", "") or feedback),
            "parser": "llm",
        }
        if not object_id or data.get("ambiguous"):
            action["ambiguous"] = True
            action["clarify"] = str(data.get("clarify", "") or "请指明该意见作用的具体对象")
        out.append(action)
    return out or None
