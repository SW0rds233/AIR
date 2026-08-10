from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.config import LLM_CONFIG, REVIEWER_CONFIG, build_llm
from src.graph.state import PipelineState
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_text

PAPER_REVIEWER_SYSTEM = """你是"论文审阅智能体"，一名严格的学术同行评审专家。

你的任务：对论文初稿进行全面的质量审阅，提供结构化的审稿意见和修改建议。

## 评分维度（每项 1-5 分）

1. **标题**: 准确反映内容，简洁有力
2. **摘要**: 覆盖背景/范围/发现/展望，150-250词
3. **引言**: 背景清晰，问题定义明确，贡献明确
4. **相关工作**: 前置知识充分，与已有综述的区分
5. **核心内容（分类体系）**: 分类 MECE，每类≥3篇论文，非简单罗列
6. **比较与分析**: 跨类别对比，关键趋势提炼
7. **挑战与未来方向**: 挑战有深度，有具体建议
8. **结论**: 总结发现，与摘要呼应
9. **参考文献**: 数量≥30，覆盖经典+前沿，**每条必须标明出版出处**（期刊/会议名或 arXiv 编号，禁止只写"网络来源"），格式符合 GB/T 7714 规范，编号与正文一致
10. **整体写作质量**: 学术规范，逻辑连贯，图表合理

## 总分: /50

## 决策建议
- ≥40: 小修后接受
- 30-39: 大修后重审
- <30: 需要大幅重写

## 输出格式

```markdown
# 论文审稿报告

**论文标题**: [标题]
**审阅日期**: [日期]

## 1. 总体评价
- 推荐: [接受/小修/大修/重写]
- 总分: XX/50

## 2. 逐项评分
| 维度 | 评分 (1-5) | 说明 |
|------|------------|------|
| 标题 | X | ... |
| ... | ... | ... |
| **总分** | **XX/50** | |

## 3. 主要问题 (Major Issues)
1. [问题1 - 具体位置 + 修改建议]
2. [问题2]

## 4. 细节问题 (Minor Issues)
1. [问题1 - 行X: 原文 → 建议]
2. [问题2]

## 5. 缺失内容
1. [缺失1 - 应补充的论文/内容]
2. [缺失2]

## 6. 修改优先级
### 高优先级（必须修改）
1. ...
### 中优先级（建议修改）
1. ...
### 低优先级（可选）
1. ...

## 7. 补充推荐论文
| # | 论文 | 建议引用位置 | 理由 |
|---|------|-------------|------|
```

完成后保存到 outputs/paper_review_{topic}.md
"""


def build_paper_reviewer():
    """跨模型审阅：使用独立的 reviewer LLM（如已配置），否则回退到主模型

    参考: ARIS cross-model review loops — 审阅者与撰写者不同模型可避免
    共享认知框架导致的审查盲区（frame-lock 问题）
    """
    return build_llm("reviewer")


def run_paper_review(state: PipelineState) -> dict:
    topic = state["research_topic"]
    draft = state.get("paper_draft", "")
    lit_notes = state.get("literature_review_notes", "")

    if not draft:
        return {
            "error": "论文草稿为空，请先完成论文初稿撰写阶段",
            "current_phase": "paper_review",
        }

    llm = build_paper_reviewer()

    prompt = (
        f"请审阅以下关于「{topic}」的综述论文初稿。\n\n"
        f"---论文初稿---\n{budget_text(draft, 50000, label='论文初稿')}\n---草稿结束---\n\n"
        f"---原始文献素材（用于验证引用准确性）---\n"
        f"{budget_text(lit_notes, 10000, label='文献综述素材')}\n---素材结束---\n\n"
        f"请严格按照上述 10 个维度进行评分，按输出格式生成完整的审稿报告。\n"
        f"注意：评分必须严格、客观，不要过度宽容。"
    )

    messages = [SystemMessage(content=PAPER_REVIEWER_SYSTEM), HumanMessage(content=prompt)]
    result = llm.invoke(messages)
    report = result.content if hasattr(result, "content") else str(result)

    # 用量追踪
    usage = extract_usage_metadata(result)
    if usage:
        model = (
            REVIEWER_CONFIG["model"] if REVIEWER_CONFIG["enabled"] else LLM_CONFIG["model"]
        )
        tracker.add_call(model, usage, stage="paper_review")

    score = _extract_score(report)

    return {
        "messages": [result],
        "review_report": report,
        "review_score": score,
        "review_recommendation": _score_to_recommendation(score),
        "current_phase": "paper_review",
    }


def _extract_score(report: str) -> int:
    import re
    patterns = [
        r"\*\*总分\*\*:\s*[\*\s]*(\d+)/\d+",
        r"\*\*总分\*\*\s*\|\s*[\*\s]*(\d+)/\d+",
        r"总分:\s*(\d+)/\d+",
        r"总分:\s*\*\*(\d+)\*\*",
    ]
    for p in patterns:
        m = re.search(p, report)
        if m:
            return int(m.group(1))
    return 35


def _score_to_recommendation(score: int) -> str:
    if score >= 40:
        return "小修后接受"
    elif score >= 30:
        return "大修后重审"
    else:
        return "需要大幅重写"
