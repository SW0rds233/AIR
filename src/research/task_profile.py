from __future__ import annotations

"""任务画像: 决定文稿结构与领域措辞的唯一依据。"""

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DELIVERABLE_PROFILES",
    "TaskProfile",
    "profile_for_deliverables",
    "task_profile_from_brief",
]


#: 交付形态 -> 文稿章节骨架。键与 `agents.supervisor.DELIVERABLES` 一致。
#: 为什么不用"六章模板": 一份验证建议不该长成期刊论文, 一份文献清单也不该有讨论章。
DELIVERABLE_PROFILES: dict[str, tuple[str, ...]] = {
    "source_list": ("范围与检索式", "来源清单", "覆盖与缺口"),
    "problem_report": ("问题与形式化", "判定依据", "结论与未决项"),
    "theoretical_conclusion": ("问题与形式化", "主要结果", "判定链", "适用边界与未决项"),
    "full_paper": ("引言", "问题与形式化", "主要结果", "与已有工作比较",
                   "讨论与局限", "结论"),
    "figures": ("图表用途", "数据来源与单位", "图注"),
    "review_report": ("审阅范围", "分项问题", "验收标准"),
    "validation_proposal": ("待区分的解释", "设计要点", "指标与判据",
                            "参数与预算", "局限 (**未执行**)"),
}


@dataclass
class TaskProfile:
    """一份文稿的任务画像。

    `subquestion_kinds` 与 `deliverables` 直接来自主控的 `ResearchBrief`
, 因此文稿结构与"主控怎么理解任务"
    是同一份事实, 不再各写一套。
    """

    deliverables: list[str] = field(default_factory=list)
    subquestion_kinds: list[str] = field(default_factory=list)
    #: 有设计/计数参数 (v,k,λ) 的命题才允许出现设计论措辞。
    has_design_parameters: bool = False
    #: 有经验/数据类子问题 —— 摘要与局限不得声称"纯数学判定"。
    has_empirical_scope: bool = False
    #: 有形式化/证明类子问题 —— 才允许"判定链/证书"这类表述。
    has_formal_scope: bool = False
    #: 语言偏好 (来自画像; 空表示不指定)。
    output_language: str = ""

    # ---- 章节骨架 ----
    def sections(self) -> list[str]:
        """章节骨架: 取**交付形态**决定的骨架, 并集去重后保序。

        多个交付形态时取并集而不是选一个: 用户要"结论 + 图"就该都得有, 不能只按
        第一个交付形态裁剪。
        """
        out: list[str] = []
        for deliverable in self.deliverables or ["problem_report"]:
            for section in DELIVERABLE_PROFILES.get(deliverable, ()):
                if section not in out:
                    out.append(section)
        return out or list(DELIVERABLE_PROFILES["problem_report"])

    # ---- 领域措辞闸门 ----
    def allows_design_vocabulary(self) -> bool:
        """是否允许出现"组合设计/2-设计/必要条件的判定链"这类领域措辞。

        判据是**快照里真的有声明的设计参数**, 不是"命题类型像数学题"。
        """
        return bool(self.has_design_parameters)

    def allows_formal_vocabulary(self) -> bool:
        """"判定链""证书""机器可复核"这类形式化措辞是否适用。"""
        return bool(self.has_formal_scope)

    def is_purely_formal(self) -> bool:
        """是否**纯**形式化研究 (决定了能否写"不含实验或仿真数据")。

        只要涉及经验/数据子问题, 这句话就是假陈述 —— 宁可不说。
        """
        return self.allows_formal_vocabulary() and not self.has_empirical_scope

    def describe(self) -> str:
        bits = ["交付: " + "、".join(self.deliverables or ["(未指定)"])]
        if self.subquestion_kinds:
            bits.append("子问题: " + "、".join(self.subquestion_kinds))
        bits.append("设计参数: " + ("有" if self.has_design_parameters else "无"))
        bits.append("经验范围: " + ("有" if self.has_empirical_scope else "无"))
        return "; ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deliverables": list(self.deliverables),
            "subquestion_kinds": list(self.subquestion_kinds),
            "has_design_parameters": self.has_design_parameters,
            "has_empirical_scope": self.has_empirical_scope,
            "has_formal_scope": self.has_formal_scope,
            "output_language": self.output_language,
            "sections": self.sections(),
        }


#: 经验/数据类子问题 (决定"不得声称纯数学判定")。
EMPIRICAL_SUBQUESTIONS: frozenset[str] = frozenset({
    "case_comparison", "data_availability", "validation_plan", "mechanism",
})

#: 形式化/证明类子问题。
FORMAL_SUBQUESTIONS: frozenset[str] = frozenset({"existence_proof"})


def profile_for_deliverables(deliverables: list[str] | None = None,
                             subquestion_kinds: list[str] | None = None,
                             *, has_design_parameters: bool = False,
                             output_language: str = "") -> TaskProfile:
    """按交付形态与子问题类型构造画像 (供没有 `ResearchBrief` 的调用方使用)。"""
    kinds = list(subquestion_kinds or [])
    return TaskProfile(
        deliverables=list(deliverables or []),
        subquestion_kinds=kinds,
        has_design_parameters=bool(has_design_parameters),
        has_empirical_scope=any(k in EMPIRICAL_SUBQUESTIONS for k in kinds),
        has_formal_scope=any(k in FORMAL_SUBQUESTIONS for k in kinds),
        output_language=output_language,
    )


def task_profile_from_brief(brief: Any,
                            *, has_design_parameters: bool = False) -> TaskProfile:
    """从主控的 `ResearchBrief` 推导文稿画像。

    `brief` 可以是 `ResearchBrief` 或它的 `to_dict()` —— 图状态里只存引用与摘要,
    因此两种形态都必须能读 (否则"文稿按画像生成"在真实路径上就断了)。
    """
    if brief is None:
        return profile_for_deliverables(has_design_parameters=has_design_parameters)

    def _pick(name: str) -> list[str]:
        if isinstance(brief, dict):
            value = brief.get(name)
        else:
            value = getattr(brief, name, None)
        return [str(v) for v in (value or []) if str(v).strip()]

    kinds = _pick("subquestion_kinds")
    if not kinds:
        # 画像里子问题以对象列表存放时按 `kind` 取
        subquestions = (brief.get("subquestions")
                        if isinstance(brief, dict) else getattr(brief, "subquestions", None))
        for item in subquestions or []:
            kind = item.get("kind") if isinstance(item, dict) else getattr(item, "kind", "")
            if kind and str(kind) not in kinds:
                kinds.append(str(kind))
    language = ""
    if isinstance(brief, dict):
        language = str(brief.get("output_language", "") or "")
    else:
        language = str(getattr(brief, "output_language", "") or "")
    return profile_for_deliverables(
        _pick("deliverables"), kinds,
        has_design_parameters=has_design_parameters,
        output_language=language)
