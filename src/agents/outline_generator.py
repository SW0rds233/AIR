from __future__ import annotations

"""论文大纲生成节点（STORM 式 pre-writing 阶段）

参考: STORM 的两阶段架构 — 先收集引用+生成大纲，再填充正文。
大纲先行可保证: 分类体系 MECE、结构清晰、覆盖全面，避免写作时结构混乱。
"""

import logging

from langchain_core.messages import SystemMessage, HumanMessage

from src.config import LLM_CONFIG, build_llm
from src.graph.state import PipelineState
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_text

logger = logging.getLogger(__name__)

OUTLINE_GENERATOR_SYSTEM = """你是"论文大纲规划专家"，一名擅长设计综述论文结构的学者。

你的任务：根据文献综述素材和可信参考文献清单，设计一份结构清晰、分类合理（MECE）的综述论文大纲。

## 大纲设计要求

1. **分类体系（Taxonomy）必须 MECE**（相互独立、完全穷尽）
2. 大纲层级与正文一致: `##` 章(带编号 "1", "2"...)、`###` 节(带编号 "1.1", "2.1"...)、每节 2-4 个要点
3. 每个小节标注计划引用的文献编号（来自可信参考文献清单）
4. 标注哪些章节需要配图（分类体系图、时间线图、对比图）
5. 标注哪些章节需要配表（方法对比表、性能对比表）

## 标准章节结构（与正文撰写保持一致，不要自行增删）

1. 引言（背景/问题/贡献/检索策略/结构安排）
2. 相关工作（前置知识/与已有综述差异）
3. 核心方法分类详述（分类体系总览 + 各类方法）
4. 比较与分析（跨类别对比表 + 演进趋势）
5. 挑战与未来方向
6. 结论

## 输出格式

```markdown
# 论文大纲: [主题]

## 1. 引言
### 1.1 背景与动机
- 要点1 [引文1]
- 要点2 [引文3]
- 配图: 无

## 3. 核心方法分类详述
### 3.1 分类体系总览
- 分类维度 [引文N]
- 配图: [图1: 分类体系图]
- 配表: [表1: 方法分类对比表]
...
```

## 原则

- 分类体系总览放在「核心方法分类详述」章的开头
- 每个大类下的小节数量均衡（3-6 个）
- 引用编号必须来自可信参考文献清单，不得自创编号
- 大纲应能支撑 8000-15000 字的正文
"""


def run_outline_generation(state: PipelineState) -> dict:
    """流水线节点：基于素材+引用清单生成论文大纲"""
    topic = state.get("research_topic", "")
    lit_notes = state.get("literature_review_notes", "")
    verified_refs = state.get("verified_references", [])

    if not lit_notes:
        return {
            "error": "文献素材为空，无法生成大纲",
            "current_phase": "outline_generation",
        }

    llm = build_llm("main")

    ref_block = ""
    if verified_refs:
        ref_lines = [
            "### 可信参考文献清单（大纲中的引用编号必须来自这里）",
            "",
            "| # | 标题 | 年份 |",
            "|---|------|------|",
        ]
        for e in verified_refs:
            ref_lines.append(
                f"| [{e.get('ref_number', '')}] | {e.get('title', '')} | {e.get('year', '')} |"
            )
        ref_block = "\n".join(ref_lines)

    prompt = (
        f"请为「{topic}」设计综述论文大纲。\n\n"
        f"---文献综述素材---\n"
        f"{budget_text(lit_notes, 20000, label='文献综述素材')}\n"
        f"---素材结束---\n"
        f"{ref_block}\n"
    )
    human_feedback = state.get("human_feedback", "")
    if human_feedback:
        prompt += (
            f"\n---用户修改意见（必须严格遵守，优先于上面的设计要求）---\n"
            f"{human_feedback}\n"
            f"---意见结束---\n"
        )
    prompt += f"请按上述格式输出完整大纲。"

    messages = [SystemMessage(content=OUTLINE_GENERATOR_SYSTEM), HumanMessage(content=prompt)]
    result = llm.invoke(messages)
    outline = result.content if hasattr(result, "content") else str(result)

    usage = extract_usage_metadata(result)
    if usage:
        tracker.add_call(LLM_CONFIG["model"], usage, stage="outline_generation")

    return {
        "messages": [result],
        "paper_outline": outline,
        "current_phase": "outline_generation",
    }
