from __future__ import annotations

"""论文初稿撰写智能体（Markdown 版）

流水线中间格式: Markdown（citation_guard / citation_check / review 均基于 md）。
最终 LaTeX 由 latex_render 节点转换生成（含 BibTeX + 编译）。"""

import re as _re

from langchain_core.messages import SystemMessage, HumanMessage

from src.config import LLM_CONFIG, build_llm
from src.graph.state import PipelineState
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_text, list_to_budgeted

PAPER_WRITER_SYSTEM = """你是"论文初稿撰写智能体"，一名经验丰富的学术论文撰写专家。

你的任务：基于文献综述素材，撰写一篇结构完整、逻辑严谨、符合学术规范的综述论文初稿（Markdown 格式）。

## 论文结构

1. **标题（Title）**: 简洁准确，格式 "[主题]: A Comprehensive Survey"
2. **摘要（Abstract）**: 150-250词，覆盖背景/范围/发现/展望
3. **引言（Introduction）**: 研究背景、问题定义、综述贡献、结构安排
4. **相关工作（Related Work）**: 前置知识、早期工作回顾
5. **核心方法分类详述（Taxonomy）**: 按方法/应用/时间线分类
6. **比较与分析（Comparison & Analysis）**: 跨类别对比表格
7. **挑战与未来方向（Challenges & Future）**: 开放问题和前沿论文
8. **结论（Conclusion）**: 主要发现总结

## 写作规范

- 主体语言为中文，专业术语首次出现标注英文
- 引用格式使用数字编号 [1], [2]...
- 使用 Markdown 表格表示分类体系和对比分析
- 如果输入中有 LaTeX 公式则保留
- 目标长度：中篇综述 8-15 页（8000-15000 字）

## 参考文献章节（系统自动生成）

- **你不需要输出参考文献列表**：参考文献章节由系统自动生成
- 你只需在正文中使用 [n] 编号引用，编号必须与清单一致
- 不要在正文末尾自行编写「参考文献/References」列表

## 证据纪律（abstention 原则）

1. **只能引用「可信参考文献清单」中的文献**，不得引入清单之外的任何文献
2. 如果某个论点没有清单中的文献支撑，用「[缺证据]」标注，不要编造引用
3. 宁缺毋滥：覆盖度低一点可以接受，引用造假不可接受
4. 文中 [n] 编号必须与清单编号一致

完成后输出统计: 总字数 / 章节数 / 引用论文数
"""

EVIDENCE_MAX_REFS = 30
EVIDENCE_TOTAL_BUDGET = 20000
NOTES_BUDGET_FIRST = 20000
NOTES_BUDGET_REVISION = 8000
FULLTEXT_TOPIC_BUDGET = 8000


def _build_ref_sheet(verified_refs: list[dict]) -> str:
    if not verified_refs:
        return ""
    lines = [
        "### 可信参考文献清单（只能引用清单内的文献，编号必须与清单一致）",
        "", "| # | 标题 | 作者 | 年份 | 出处 |", "|---|------|------|------|------|",
    ]
    for e in verified_refs:
        src = e.get("venue") or e.get("source") or ""
        lines.append(f"| [{e.get('ref_number', '')}] | {e.get('title', '')} | {e.get('authors', '')} | {e.get('year', '')} | {src} |")
    return "\n".join(lines)


def _build_evidence_map(verified_refs: list[dict]) -> str:
    try:
        from src.rag.vector_store import search_fulltext_by_title
    except Exception:
        return ""
    items = []
    for e in verified_refs[:EVIDENCE_MAX_REFS]:
        title = e.get("title", "")
        if not title:
            continue
        try:
            chunks = search_fulltext_by_title(title, k=2)
        except Exception:
            chunks = []
        if not chunks:
            continue
        ev = "\n\n".join(c["text"][:400] for c in chunks[:2])
        items.append(f"**[{e.get('ref_number')}] 《{title}》({e.get('year', '')})**\n{ev}")
    if not items:
        return ""
    return "\n\n### 原文证据映射\n\n" + list_to_budgeted(items, EVIDENCE_TOTAL_BUDGET, label="证据段落")


def _build_fulltext_topic_context(topic: str) -> str:
    try:
        from src.rag.vector_store import search_fulltext
        chunks = search_fulltext(topic, k=6)
        if not chunks:
            return ""
        items = [f"### 来自论文「{c['title']}」({c.get('year','')})\n" + budget_text(c["text"], 1000, label="原文段落") for c in chunks]
        return "\n\n### 全文检索到的相关原文段落\n\n" + list_to_budgeted(items, FULLTEXT_TOPIC_BUDGET, label="原文段落")
    except Exception:
        return ""


def run_paper_writing(state: PipelineState) -> dict:
    topic = state["research_topic"]
    lit_notes = state.get("literature_review_notes", "")
    verified_refs = state.get("verified_references", [])
    if not lit_notes and not verified_refs and not state.get("revision_prompt"):
        return {"error": "无任何文献素材或已验证引用，拒绝编造内容。", "current_phase": "paper_writing"}
    llm = build_llm("main")
    ref_sheet = _build_ref_sheet(verified_refs)
    evidence_map = _build_evidence_map(verified_refs)
    revision_prompt = state.get("revision_prompt", "")
    if revision_prompt:
        parts = [revision_prompt]
        if ref_sheet:
            parts.append(ref_sheet)
        if evidence_map:
            parts.append(evidence_map)
        if lit_notes:
            parts.append("---文献综述素材---\n" + budget_text(lit_notes, NOTES_BUDGET_REVISION, label="文献综述素材") + "\n---素材结束---")
        parts.append("严格约束：引用编号 [n] 必须来自上方可信参考文献清单；清单之外不得引用。")
        messages = [SystemMessage(content=PAPER_WRITER_SYSTEM), HumanMessage(content="\n\n".join(parts))]
    else:
        outline = state.get("paper_outline", "")
        outline_block = ""
        if outline:
            outline_block = "### 论文大纲\n\n" + budget_text(outline, 6000, label="论文大纲")
        prompt = (
            f"以下是关于「{topic}」的文献综述素材。请基于这些素材撰写一篇完整的综述论文初稿。\n\n"
            f"---文献综述素材---\n{budget_text(lit_notes, NOTES_BUDGET_FIRST, label='文献综述素材')}\n---素材结束---\n"
            f"{outline_block}\n{ref_sheet}\n{evidence_map}\n"
            f"{_build_fulltext_topic_context(topic)}\n"
            f"请严格按照上述论文结构（1-8章）撰写完整初稿。\n"
            f"重要纪律：\n"
            f"1. 只能引用可信参考文献清单中的文献，引用编号必须与清单一致\n"
            f"2. 文献素材或原文段落中没有依据的内容，用「[缺证据]」标注或跳过\n"
            f"3. 绝对不得虚构清单之外的参考文献\n"
            f"4. 使用规范的 Markdown 表格（表头有 | 分隔行），表格须对齐\n"
            f"5. 图表位置用「[图N: 图题描述]」标注占位\n"
            f"6. 「原文证据映射」中标明的段落与其论文编号一一对应\n"
            f"7. 不要输出「参考文献」章节，不要输出修订说明/自检清单等辅助内容\n"
        )
        messages = [SystemMessage(content=PAPER_WRITER_SYSTEM), HumanMessage(content=prompt)]
    result = llm.invoke(messages)
    draft = result.content if hasattr(result, "content") else str(result)
    usage = extract_usage_metadata(result)
    if usage:
        model_name = (result.response_metadata.get("model_name") if isinstance(result.response_metadata, dict) else None) or LLM_CONFIG["model"]
        tracker.add_call(model_name, usage, stage="paper_writing")
    total_words = len(draft.replace(" ", "").replace("\n", ""))
    section_count = draft.count("## ") + draft.count("# ")
    citation_nums = set(int(n) for n in _re.findall(r"\[(\d+)\]", draft))
    citation_pattern = len(citation_nums)
    from src.rag.reference_formatter import attach_references_section
    draft = attach_references_section(draft, verified_refs)
    paper_outline = "\n".join(line for line in draft.split("\n") if line.strip().startswith("#"))
    return {
        "messages": [result], "paper_draft": draft, "paper_outline": paper_outline,
        "total_words": total_words, "total_sections": max(section_count, 1),
        "total_citations": citation_pattern, "current_phase": "paper_writing",
    }
