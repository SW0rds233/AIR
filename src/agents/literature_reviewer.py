from __future__ import annotations

"""文献查阅智能体：工具调用闭环 + 确定性检索桥接 + 综合生成综述素材"""

import logging

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from src.config import build_llm, LLM_CONFIG
from src.graph.state import PipelineState
from src.tools.search_tools import (
    search_all_sources,
    arxiv_search,
    semantic_scholar_search,
    openalex_search,
    merge_papers,
)
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import list_to_budgeted

logger = logging.getLogger(__name__)

# agent 循环轮次上限（每轮一次 LLM 调用 + 执行其全部工具调用）
MAX_TOOL_ROUNDS = 3
# 单条 tool 消息最大长度（论文清单格式，防止刷爆上下文）
TOOL_RESULT_BUDGET = 6000
# 综合生成阶段最多喂给 LLM 的论文数
SYNTHESIS_MAX_PAPERS = 40
# 单篇论文摘要长度（喂给综合生成）
ABSTRACT_BUDGET = 400

LITERATURE_REVIEWER_SYSTEM = """你是"文献查阅智能体"，一名严谨的学术文献检索与分析专家。

你的任务：围绕用户指定的研究主题，系统性地检索、筛选和整理学术文献，输出结构化的文献综述素材。

## 执行步骤

1. **分析主题**：从研究主题中提取核心关键词和中英文同义词
2. **多源检索**：使用 search_all_sources 工具搜索 arXiv、Semantic Scholar 和 OpenAlex（OpenAlex 支持中文关键词）
3. **补充检索**：对每个子主题使用 arxiv_search 或 openalex_search（中文主题优先用 openalex_search）单独检索
4. **筛选排序**：按相关性、引用量、时效性筛选，优先近5年论文
5. **结构化输出**：按以下格式输出文献综述素材

## 输出格式

你的最终输出必须包含以下结构（保存为 Markdown）：

```markdown
# 文献综述素材: [研究主题]

## 1. 检索概况
- 检索关键词: ...
- 检索数据库: arXiv, Semantic Scholar
- 初始命中数: XX | 筛选后: XX | 精读: XX

## 2. 核心论文清单
### 2.1 经典/奠基性论文 (5-10篇)
| # | 标题 | 作者 | 年份 | 引用数 | 核心贡献 |
|---|------|------|------|--------|----------|

### 2.2 近期前沿论文 (2019+, 10-20篇)
| # | 标题 | 作者 | 年份 | 引用数 | 核心贡献 |
|---|------|------|------|--------|----------|

## 3. 按主题分类的文献总结
### 3.1 [子主题1]
- 核心论文: ...
- 方法/框架: ...
- 关键发现: ...
- 局限性: ...

## 4. 方法对比分析
| 方法 | 代表论文 | 优点 | 缺点 | 适用场景 |
|------|----------|------|------|----------|

## 5. 研究脉络与趋势
- 发展时间线
- 开放问题与未来方向

## 6. 参考文献列表 (BibTeX)
```

## 纪律

- **只能总结工具返回的论文**；工具未返回的内容用「[缺证据]」标注，不得凭记忆补充
- 检索工具失败时不要放弃，尝试其他工具或换查询词
- 核心贡献/方法总结必须来自论文标题/摘要中的真实信息
"""


def build_literature_reviewer():
    return build_llm("main").bind_tools(
        [search_all_sources, arxiv_search, semantic_scholar_search, openalex_search]
    )


_TOOL_MAP = {
    "search_all_sources": search_all_sources,
    "arxiv_search": arxiv_search,
    "semantic_scholar_search": semantic_scholar_search,
    "openalex_search": openalex_search,
}


def _format_papers_for_tool(papers: list[dict]) -> str:
    """把检索结果格式化为紧凑文本（标题/年份/来源/引用数/摘要摘要）"""
    lines = [f"检索到 {len(papers)} 篇论文：", ""]
    for i, p in enumerate(papers[:25], 1):
        lines.append(
            f"{i}. {p.get('title', '')} | {p.get('year', '')} | "
            f"{p.get('source', '')} | 被引 {p.get('citations', 0)}"
        )
        abstract = (p.get("abstract") or "").strip()
        if abstract:
            lines.append(f"   摘要: {abstract[:ABSTRACT_BUDGET]}")
    return "\n".join(lines)


def _run_agent_loop(llm_with_tools, messages, all_papers, tool_budget=TOOL_RESULT_BUDGET) -> str:
    """agent 循环: invoke → 执行工具 → 工具结果回喂 → 再推理

    工具错误以 [Tool Call Error] 文本回灌，LLM 自我修正。
    """
    notes = ""
    for _round in range(MAX_TOOL_ROUNDS):
        result = llm_with_tools.invoke(messages)
        usage = extract_usage_metadata(result)
        if usage:
            model_name = (
                result.response_metadata.get("model_name")
                if isinstance(result.response_metadata, dict)
                else None
            ) or LLM_CONFIG["model"]
            tracker.add_call(model_name, usage, stage="literature_review")
        messages.append(result)

        tool_calls = getattr(result, "tool_calls", None) or []
        if not tool_calls:
            notes = result.content if hasattr(result, "content") else ""
            break

        for tc in tool_calls:
            name, args = tc.get("name", ""), tc.get("args", {}) or {}
            tool_obj = _TOOL_MAP.get(name)
            raw_result = None
            if tool_obj is not None:
                try:
                    # StructuredTool 通过 .func 访问原始函数 (修复: 直接调用不可用)
                    fn = tool_obj.func if hasattr(tool_obj, "func") else tool_obj
                    raw_result = fn(**args)
                except Exception as e:
                    raw_result = f"[Tool Call Error] 工具 {name} 执行失败: {e}"
            else:
                raw_result = f"[Tool Call Error] 工具 {name} 不存在。"

            if isinstance(raw_result, list):
                merge_papers(all_papers, raw_result)
                content = _format_papers_for_tool(raw_result)
            else:
                content = str(raw_result)

            messages.append(
                ToolMessage(content=content[:tool_budget], tool_call_id=tc.get("id", ""), name=name)
            )

        if len(messages) > 8:  # 防止 tool 结果把对话撑爆
            notes = result.content if hasattr(result, "content") else ""
            break

    return notes


def _synthesize_notes(
    llm,
    topic: str,
    keywords: list[str],
    sub_topics: list[str],
    time_range: str,
    papers: list[dict],
) -> str:
    """把真实论文清单喂给 LLM，产出有数据支撑的综述素材"""
    paper_lines = []
    for i, p in enumerate(papers[:SYNTHESIS_MAX_PAPERS], 1):
        venue = (p.get("venue") or p.get("source") or "").strip()
        doi = (p.get("doi") or "").strip()
        authors = (p.get("authors") or "").strip()
        # 标注正式出处 (有 DOI 的论文优先显示 DOI, 避免 LLM 误判为 arXiv 预印本)
        src = venue or "来源未标注"
        if doi:
            src = f"{src} | DOI: {doi}"
        line = (
            f"{i}. {p.get('title', '')} | 作者: {authors or '未标注'} | "
            f"{p.get('year', '')} | {src} | 被引 {p.get('citations', 0)}"
        )
        abstract = (p.get("abstract") or "").strip()
        if abstract:
            line += f"\n    摘要: {abstract[:ABSTRACT_BUDGET]}"
        paper_lines.append(line)

    paper_block = list_to_budgeted(paper_lines, 24000, label="论文条目")

    prompt = (
        f"以下是围绕「{topic}」检索到的真实论文清单（来自 arXiv/Semantic Scholar/OpenAlex 官方 API）。\n"
        f"关键词: {', '.join(keywords) if keywords else '自动提取'}\n"
        f"子主题: {', '.join(sub_topics) if sub_topics else '自动识别'}\n"
        f"时间范围: {time_range}\n\n"
        f"---真实论文清单---\n{paper_block}\n---清单结束---\n\n"
        f"请基于**清单内的论文**撰写完整的文献综述素材（严格按输出格式的 1-6 部分）：\n"
        f"1. 检索概况\n2. 核心论文清单（经典 + 近期前沿，标注清单中的真实信息）\n"
        f"3. 按主题分类的文献总结（每个子主题一节）\n"
        f"4. 方法对比分析\n5. 研究脉络与趋势\n6. 参考文献列表（BibTeX）\n\n"
        f"纪律:\n"
        f"- 只能描述清单中的论文；清单里没有的论文、数据、结论一律不得编造\n"
        f"- 每篇被提及的论文必须能对应到清单中的真实条目，作者名必须来自清单\n"
        f"- 生成 BibTeX 时，严格以清单信息为准：清单中有 DOI 的条目必须用该 DOI 和"
        f"正式期刊/会议名；清单中有作者的必须写全作者；严禁标注 note={{arXiv preprint}}"
        f"或 howpublished={{arXiv}}，除非清单确实只给了 arXiv 链接且无 DOI/出处\n"
        f"- 清单不足 20 篇时明确说明覆盖有限\n"
    )
    result = llm.invoke(
        [SystemMessage(content=LITERATURE_REVIEWER_SYSTEM), HumanMessage(content=prompt)]
    )
    usage = extract_usage_metadata(result)
    if usage:
        model_name = (
            result.response_metadata.get("model_name")
            if isinstance(result.response_metadata, dict)
            else None
        ) or LLM_CONFIG["model"]
        tracker.add_call(model_name, usage, stage="literature_review")
    return result.content if hasattr(result, "content") else str(result)


def run_retrieval(state: PipelineState) -> dict:
    """模块1·检索阶段: 多源检索 + 相关性过滤 + 出处解析 → 产出论文清单"""
    topic = state["research_topic"]
    keywords = state.get("topic_keywords", [])
    sub_topics = state.get("sub_topics", [])
    time_range = state.get("time_range", "2019-2026")

    llm_with_tools = build_literature_reviewer()
    cheap_llm = build_llm("cheap")

    # 研究 wiki 记忆：检索该主题的历史笔记作为补充上下文
    wiki_context = ""
    try:
        from src.rag.vector_store import search_wiki

        prior = search_wiki(topic, k=2)
        if prior:
            wiki_context = (
                "\n\n### 历史研究笔记（来自研究 wiki，可参考但需验证时效性）\n"
                + "\n\n".join(f"- [{p['topic']}]: {p['text'][:2000]}" for p in prior)
            )
    except Exception:
        pass

    search_query = (
        f"研究主题：{topic}\n"
        f"关键词：{', '.join(keywords) if keywords else '自动提取'}\n"
        f"子主题：{', '.join(sub_topics) if sub_topics else '自动识别'}\n"
        f"时间范围：{time_range}\n{wiki_context}\n\n"
        f"请先用 search_all_sources 搜索主主题，再针对子主题使用 arxiv_search / openalex_search。"
        f"收集至少30篇相关论文。"
    )

    # ===== 阶段 1: agent 循环（工具调用闭环）=====
    all_papers = list(state.get("retrieved_papers", []))
    messages = [SystemMessage(content=LITERATURE_REVIEWER_SYSTEM), HumanMessage(content=search_query)]
    _run_agent_loop(llm_with_tools, messages, all_papers)
    print(f"  [文献检索] agent 循环完成, 已收集 {len(all_papers)} 篇候选论文")

    # ===== 阶段 2: 子查询确定性检索 (借鉴 gpt-researcher, 廉价模型生成) =====
    from src.rag.subquery_generator import generate_sub_queries

    user_kw = ", ".join(keywords) if keywords else ""
    sub_queries = generate_sub_queries(topic, user_kw, llm=cheap_llm)
    print(f"  [子查询] 生成 {len(sub_queries)} 个检索: {sub_queries[:3]}...")

    search_fn = search_all_sources.func if hasattr(search_all_sources, "func") else search_all_sources
    for q in sub_queries:
        try:
            merge_papers(all_papers, search_fn(q, 20))
        except Exception as e:
            print(f"  [warning] 子查询检索失败 ({q}): {e}")

    # ===== 阶段 2.5: 中文文献补充 (官方 API + 人工 PDF) =====
    # 已移除 "LLM 建议中文文献" 路径: LLM 生成的作者名 (如 "李华,张鹏,王磊") 疑似
    # 占位编造, 且 CrossRef 对中文期刊 DOI 覆盖不全 (返回 404), 无法自动补全卷期页码,
    # 导致参考文献维度持续被审稿人扣分。中文文献改由 CNKI/万方官方 API (真实元数据)
    # 与 data/manual_pdfs 人工导入 (人已确认) 提供。
    try:
        from src.tools.chinese_sources import cnki_search, wanfang_search

        # CNKI/万方官方 API (配置 key 时启用, 返回真实作者/卷期页元数据)
        for name, fn in (("CNKI", cnki_search), ("万方", wanfang_search)):
            try:
                results = fn(topic, 10)
                if results:
                    merge_papers(all_papers, results)
                    print(f"  [中文文献] {name} API 检索到 {len(results)} 篇")
            except Exception as e:
                print(f"  [warning] {name} 检索失败: {e}")
    except Exception as e:
        print(f"  [warning] 中文文献补充跳过: {e}")

    # 保存全部未过滤论文（供 PDF 下载节点使用，避免过滤后损失大量 arXiv 全文）
    unfiltered_papers = list(all_papers)
    print(f"  [文献检索] 子查询追加后共 {len(all_papers)} 篇（未过滤）")

    # ===== 阶段 3: 相关性过滤 (规则 + LLM 打分, 廉价模型) =====
    from src.rag.relevance_filter import rule_filter, llm_score_filter

    rule_kept = len(all_papers)
    all_papers = rule_filter(all_papers, topic, user_kw)
    rule_dropped = rule_kept - len(all_papers)
    if all_papers and rule_dropped > 0:
        print(f"  [规则过滤] 剔除 {rule_dropped} 篇, 保留 {len(all_papers)}")
    if all_papers:
        try:
            before_llm = len(all_papers)
            all_papers = llm_score_filter(all_papers, topic, cheap_llm)
            print(f"  [LLM过滤] 剔除 {before_llm - len(all_papers)} 篇, 保留 {len(all_papers)}")
        except Exception as e:
            print(f"  [warning] LLM 相关性打分跳过: {e}")

    # ===== 阶段 4: 0 结果兜底（失败作为信号回喂, 而非静默）=====
    if not all_papers:
        print("  [warning] 检索结果为 0, 回馈 LLM 生成备选检索词...")
        try:
            retry_prompt = (
                f"针对研究主题「{topic}」，先前检索未返回任何相关论文。\n"
                f"请重新生成 5 个更宽泛或不同角度的检索查询（中英文混合，"
                f"避免过于具体导致 0 结果），用逗号分隔输出。"
            )
            retry_result = llm_with_tools.invoke(
                [
                    SystemMessage(
                        content="你是检索策略专家。输出 5 个检索查询，逗号分隔，不要其它文字。"
                    ),
                    HumanMessage(content=retry_prompt),
                ]
            )
            retry_text = retry_result.content if hasattr(retry_result, "content") else str(retry_result)
            for q in [x.strip() for x in retry_text.replace("\n", ",").split(",") if x.strip()][:5]:
                try:
                    merge_papers(all_papers, search_fn(q, 20))
                except Exception:
                    pass
        except Exception as e:
            print(f"  [warning] 0 结果兜底失败: {e}")
        all_papers = rule_filter(all_papers, topic, user_kw)

    print(f"  [文献过滤] 保留 {len(all_papers)} 篇相关论文")

    # ===== 阶段 4.5: 出处解析 (综合生成前) =====
    # arXiv 预印本在检索时无 DOI/正式期刊信息, 若不先解析, LLM 生成素材时会
    # 把大量文献标为 howpublished={arXiv} + 作者缺证据, 导致审稿人误判
    # "预印本占比过高/版本混淆"。此处先解析出处, 让素材用正式版信息。
    try:
        from src.tools.venue_resolver import resolve_venues_fast

        resolved = resolve_venues_fast(all_papers, max_papers=80)
        if resolved:
            print(f"  [文献检索] 出处预解析: {resolved} 篇获得期刊信息")
    except Exception as e:
        print(f"  [warning] 出处预解析跳过: {e}")

    # ===== 阶段 6: 向量入库 (摘要级) =====
    if all_papers:
        try:
            from src.rag.vector_store import add_papers_to_store, embedding_available

            if embedding_available():
                add_papers_to_store(all_papers)
        except Exception:
            pass

    return {
        "messages": messages,
        "retrieved_papers": all_papers,
        "unfiltered_papers": unfiltered_papers,
        "current_phase": "retrieval",
    }


def run_notes_synthesis(state: PipelineState) -> dict:
    """模块2·分析阶段: 基于检索论文清单综合生成文献综述素材"""
    topic = state["research_topic"]
    keywords = state.get("topic_keywords", [])
    sub_topics = state.get("sub_topics", [])
    time_range = state.get("time_range", "2019-2026")
    all_papers = list(state.get("retrieved_papers", []))

    main_llm = build_llm("main")

    # ===== 阶段 5: 综合生成综述素材（真实论文数据驱动）=====
    notes = _synthesize_notes(
        main_llm, topic, keywords, sub_topics, time_range, all_papers
    )
    if not notes and all_papers:
        # 综合生成失败时的兜底: 退化为结构化清单, 保证下游有素材可用
        notes = (
            f"# 文献综述素材: {topic}\n\n"
            f"## 1. 检索概况\n- 检索到 {len(all_papers)} 篇相关论文"
            f"（综合生成不可用，以下为程序化清单）\n\n"
            f"## 2. 核心论文清单\n"
        )
        for i, p in enumerate(all_papers[:30], 1):
            notes += f"{i}. {p.get('title', '')} ({p.get('year', '')}) [{p.get('source', '')}]\n"

    if notes:
        try:
            from src.rag.vector_store import save_to_wiki

            save_to_wiki(topic, notes[:5000])
        except Exception:
            pass

    return {
        "literature_review_notes": notes,
        "current_phase": "notes_synthesis",
    }


def run_literature_review(state: PipelineState) -> dict:
    """完整文献查阅 (兼容旧调用): 检索 + 笔记综合"""
    r = run_retrieval(state)
    merged = dict(state)
    merged.update(r)
    n = run_notes_synthesis(merged)
    merged.update(n)
    return {
        "messages": r.get("messages", []),
        "literature_review_notes": n.get("literature_review_notes", ""),
        "retrieved_papers": r.get("retrieved_papers", []),
        "unfiltered_papers": r.get("unfiltered_papers", []),
        "current_phase": "literature_review",
    }
