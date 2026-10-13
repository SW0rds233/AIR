from __future__ import annotations

"""问题形式化服务。"""

from dataclasses import dataclass, field
from typing import Any

__all__ = ["FormulatedProblem", "formulate_problem"]


@dataclass
class FormulatedProblem:
    """形式化结果 (只描述"有什么", 不含任何状态判定)。"""

    spec: Any = None
    claims: list[Any] = field(default_factory=list)
    obligations: list[Any] = field(default_factory=list)
    assumptions: list[Any] = field(default_factory=list)
    definitions: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    #: 抽不出可判定命题时为 True —— 调用方据此请求澄清, 不编命题。
    needs_clarification: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.claims)


def formulate_problem(*, request: str = "", topic: str = "", project_id: str = "",
                      problem_id: str = "problem", spec: Any = None,
                      available: dict[str, bool] | None = None) -> FormulatedProblem:
    """把题面形式化为命题与义务 (确定性, 零 LLM)。

    `spec` 给出时直接用它 (调用方已有落盘规格, 不再重新解释输入); 否则按
    `build_spec_from_input` 现场构造 —— 这样团队在**没有**预建规格时也能形式化。
    """
    from src.research.problem_formulator import formulate

    text = " ".join(part for part in (str(request or "").strip(),
                                      str(topic or "").strip()) if part).strip()
    if spec is None:
        if not text:
            return FormulatedProblem(needs_clarification=True,
                                     notes=["输入为空, 无法确定研究对象"])
        from src.research.question_planner import build_spec_from_input

        spec = build_spec_from_input(request=request or text, topic=topic,
                                     project_id=project_id or "research",
                                     problem_id=problem_id or "problem")

    source_text = " ".join(filter(None, (
        str(getattr(spec, "problem_statement", "") or ""),
        str(getattr(spec, "original_request", "") or ""),
        str(getattr(spec, "direction", "") or ""),
        text)))

    # 1. 精确计数约束优先 (设计/计数类存在性命题)
    from src.research.design_feasibility import formulate_from_text

    design_form = None
    for candidate in (source_text, str(getattr(spec, "direction", "") or "")):
        if not candidate.strip():
            continue
        design_form = formulate_from_text(candidate)
        if design_form is not None:
            break
    if design_form is not None and design_form.claims:
        out = FormulatedProblem(
            spec=spec, claims=list(design_form.claims),
            obligations=list(design_form.obligations),
            assumptions=list(design_form.assumptions),
            definitions=list(design_form.definitions),
            notes=["题面可被精确形式化 (含计数约束): 直接进入判定, "
                   "不生成研究方向候选", *list(design_form.notes)],
            unknown_fields=list(getattr(design_form, "unknown_fields", []) or []))
        return out

    from src.research.question_planner import requires_intent_clarification

    contract = getattr(spec, "contract", None)
    if contract is not None and hasattr(contract, "paths") \
            and requires_intent_clarification(contract):
        return FormulatedProblem(
            spec=spec, needs_clarification=True,
            notes=[contract.clarification],
            unknown_fields=["research_intent: 形式化证明还是现实对象查询"])

    # 2. 通用形式化
    form = formulate(spec, available or {})
    out = FormulatedProblem(
        spec=spec, claims=list(form.claims), obligations=list(form.obligations),
        assumptions=list(form.assumptions), definitions=list(form.definitions),
        notes=list(form.notes), unknown_fields=list(form.unknown_fields))
    if not out.claims:
        out.needs_clarification = True
        out.notes.append("无法可靠形式化问题 (缺少量词/变量域/关系/显式表达式), 请求澄清")
    return out
