from __future__ import annotations

"""入口分类: 判断一个请求是不是"文献综述型" (合并计划 §3 / §15.2)。

为什么需要它
-----------
界面已经只留一个入口, 但服务端仍有两类研究形态:

- **形式化研究** (定理/设计/机理推导): 需要命题、义务、核验与交付门槛 —— 由
  `TheoryEngine` 承担;
- **文献综述**: 需要"检索 → 笔记综合 → 成文 → 配图", 不产生可核验的形式化义务 ——
  由团队会话 (EvidenceAgent/ReasoningAgent/WritingAgent/FigureAgent) 承担。

把"这是哪一类"交给用户选, 正是本次合并要消除的二选一入口; 但**也不能**靠猜。
因此这里给一个**可单测、可解释**的分类函数, 只在信号明确时判为综述, 其余一律按
形式化研究处理 (宁可让形式化引擎去请求澄清, 也不要拿综述流程糊弄一个证明题)。

判据刻意保守:
- 出现"综述/文献回顾/研究进展/现状/梳理/盘点/survey/review of/state of the art/
  research progress"等**综述意图**词 → 综述型;
- 出现"证明/不存在/构造/定理/充要条件/反例/是否成立"等**形式化意图**词 → 不是综述
  (形式化优先: "综述一下 XX 是否存在" 仍应按形式化问题处理);
- 两者都没有 → 不是综述。
"""

import re

__all__ = ["SURVEY_MARKERS", "FORMAL_MARKERS", "is_survey_request"]

#: 综述意图词 (中英并集)。命中即倾向"文献综述"。
SURVEY_MARKERS: tuple[str, ...] = (
    "综述", "文献回顾", "文献综述", "研究进展", "研究现状", "进展综述", "现状综述",
    "梳理", "盘点", "研究概况", "国内外研究", "系统性回顾", "文献调研",
    "survey", "literature review", "review of the literature", "state of the art",
    "research progress", "systematic review", "overview of",
)

#: 形式化意图词: 命中就**不**按综述处理 (形式化优先)。
FORMAL_MARKERS: tuple[str, ...] = (
    "证明", "不存在", "存在性", "构造", "定理", "引理", "推论", "充要条件",
    "必要条件", "充分条件", "反例", "是否成立", "能否存在", "判定", "求解",
    "prove", "proof", "nonexistence", "does there exist", "counterexample",
    "necessary and sufficient",
)

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub("", str(text or "")).lower()


def is_survey_request(*texts: str) -> bool:
    """请求是否是"文献综述型"。

    多个文本片段 (原始请求、主题、附件正文) 会被合并判断: 只看其中一个会漏掉
    "题面在附件里"这种真实入口 (CD-004 的形状)。
    """
    blob = _normalize(" ".join(str(t or "") for t in texts))
    if not blob:
        return False
    if any(_normalize(marker) in blob for marker in FORMAL_MARKERS):
        return False
    return any(_normalize(marker) in blob for marker in SURVEY_MARKERS)
