from __future__ import annotations

"""入口分类: 判断一个请求是不是"文献综述型"。"""

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
