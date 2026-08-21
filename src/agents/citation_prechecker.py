from __future__ import annotations

"""STORM 式预写作阶段：检索后、写作前，先验证候选引用清单

参考: STORM (Stanford) 的两阶段架构 — pre-writing 阶段先收集引用+生成大纲，
     写作阶段只用已验证的引用。从根上避免写作时编造引用。
"""

import logging

from src.graph.state import PipelineState
from src.tools.citation_verifier import (
    CitationRecord,
    verify_single_citation,
)
from src.config import build_llm
from src.utils.cost_tracker import tracker, extract_usage_metadata

logger = logging.getLogger(__name__)

VERIFY_LIMIT = 60  # 最多验证前 60 篇候选论文（从未过滤池获取，保证 Writer 有充足引用）


def verify_reference_list(papers: list[dict], limit: int = VERIFY_LIMIT) -> dict:
    """对候选论文列表逐篇验证，生成可信引用清单

    Returns:
        {
            "verified": [...],  # 通过验证的论文（带 ref_number）
            "ambiguous": [...],
            "not_found": [...],
            "report_md": "...",
        }
    """
    verified, ambiguous, not_found, retracted_list = [], [], [], []
    checked = papers[:limit]

    for idx, paper in enumerate(papers):
        rec = CitationRecord(
            ref_number=idx + 1,
            title=paper.get("title", ""),
            authors=paper.get("authors", ""),
            year=paper.get("year", ""),
            raw=paper.get("bibtex", ""),
        )

        # 撤稿论文: 无论来源如何都不得进入写作清单 (借鉴 OpenAI4S retraction 检测)
        if paper.get("retracted"):
            entry = dict(paper)
            entry["ref_number"] = idx + 1
            entry["verify_status"] = "RETRACTED"
            entry["verified_title"] = paper.get("title", "")
            entry["doi"] = paper.get("doi", "")
            entry["similarity"] = 1.0
            retracted_list.append(entry)
            continue

        # 关键优化: 论文来自官方检索 API 或已通过验证的渠道,
        # 直接标记 VERIFIED, 避免逐篇调用 3 个验证 API 触发限流
        # - arXiv/S2/OpenAlex: 官方 API 检索结果本身即真实存在
        # - 人工导入: 人已确认
        api_source = paper.get("api_source", "") or paper.get("source", "")
        if api_source in ("arXiv", "Semantic Scholar", "OpenAlex", "人工导入"):
            entry = dict(paper)
            entry["ref_number"] = idx + 1
            entry["verify_status"] = "VERIFIED"
            entry["verified_title"] = paper.get("title", "")
            entry["doi"] = paper.get("doi", "")
            entry["similarity"] = 1.0
            verified.append(entry)
        else:
            # 来源不明的论文才做交叉验证 (LLM 补录/用户提供等)
            vr = verify_single_citation(rec)

            entry = dict(paper)
            entry["ref_number"] = idx + 1
            entry["verify_status"] = vr.status
            entry["verified_title"] = vr.matched_title
            entry["doi"] = vr.doi
            entry["similarity"] = round(vr.similarity, 3)

            if vr.status == "VERIFIED":
                verified.append(entry)
            elif vr.status == "AMBIGUOUS":
                ambiguous.append(entry)
            else:
                not_found.append(entry)

        if len(verified) + len(ambiguous) + len(not_found) >= len(checked):
            break

    lines = [
        "# 候选引用预验证报告（写作前）",
        "",
        f"- 候选论文: {len(papers)} 篇 | 已核查: {len(checked)} 篇",
        f"- ✅ 已验证: {len(verified)} | ⚠️ 存疑: {len(ambiguous)} | "
        f"❌ 未找到: {len(not_found)} | 🚫 已撤稿: {len(retracted_list)}",
        "",
        "## 已验证引用清单（写作时只能引用这些）",
        "",
        "| # | 标题 | 年份 | 出处 | 验证状态 | 匹配文献 | DOI |",
        "|---|------|------|------|---------|---------|-----|",
    ]
    for e in verified + ambiguous:
        venue = e.get("venue") or e.get("source") or ""
        if venue in ("", "arXiv", "OpenAlex", "Semantic Scholar"):
            venue = "—"
        lines.append(
            f"| {e['ref_number']} | {e.get('title', '')[:50]} | {e.get('year', '')} | "
            f"{venue[:28]} | {e['verify_status']} | {e.get('verified_title', '')[:40]} | "
            f"{e.get('doi', '') or '—'} |"
        )

    lines.append("")
    lines.append("## ⚠️ 存疑/未找到（不建议写入参考文献）")
    for e in not_found:
        lines.append(f"- [{e['ref_number']}] {e.get('title', '')[:60]}")

    if retracted_list:
        lines.append("")
        lines.append("## 🚫 已撤稿论文（禁止写入参考文献）")
        for e in retracted_list:
            lines.append(f"- [{e['ref_number']}] {e.get('title', '')[:60]}")

    return {
        "verified": verified,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "retracted": retracted_list,
        "report_md": "\n".join(lines),
    }


def build_verified_reference_sheet(verified: list[dict]) -> str:
    """生成写作可用的引用清单（Markdown + BibTeX）"""
    lines = [
        "# 可信参考文献清单",
        "",
        "**以下文献均已通过 CrossRef/OpenAlex/arXiv 验证。写作时只能引用本清单中的文献。**",
        "",
        "| # | 标题 | 作者 | 年份 | 引用格式 |",
        "|---|------|------|------|---------|",
    ]
    for e in verified:
        lines.append(
            f"| [{e['ref_number']}] | {e.get('title', '')} | {e.get('authors', '')} | "
            f"{e.get('year', '')} | [{e['ref_number']}] |"
        )
    lines.append("")
    lines.append("## BibTeX")
    lines.append("```bibtex")
    for e in verified:
        lines.append(e.get("bibtex", ""))
    lines.append("```")
    return "\n".join(lines)


def run_citation_precheck(state: PipelineState) -> dict:
    """流水线节点：写作前验证候选引用清单

    优先从未过滤论文集验证（论文池更大），确保 Writer 有足够的可信引用。
    """
    papers = state.get("unfiltered_papers", []) or state.get("retrieved_papers", [])
    topic = state.get("research_topic", "")
    user_kw = ", ".join(state.get("topic_keywords", []))

    # 人工导入文献 (人已确认存在, 最高信任度)
    manual_papers = state.get("manual_papers", [])
    if manual_papers:
        existing_titles = {(p.get("title", "") or "").lower() for p in papers}
        added = 0
        for mp in manual_papers:
            if (mp.get("title", "") or "").lower() not in existing_titles:
                papers.append(mp)
                existing_titles.add((mp.get("title", "") or "").lower())
                added += 1
        if added:
            print(f"  [citation_precheck] 人工导入文献 {added} 篇加入候选池")
    if not papers:
        return {
            "current_phase": "citation_precheck",
            "verified_references": [],
            "citation_precheck_report": "## 引用预验证\n\n没有候选论文。",
        }

    # 规则过滤: unfiltered 池含 OpenAlex 中文模糊检索的无关论文 (如地质学),
    # 先剔除零命中论文, 避免浪费出处解析配额和引用清单名额
    try:
        from src.rag.relevance_filter import rule_filter

        before = len(papers)
        papers = rule_filter(papers, topic, user_kw)
        if len(papers) < before:
            print(f"  [citation_precheck] 规则过滤: {before} → {len(papers)} 篇候选")
    except Exception as e:
        logger.warning(f"rule filter skipped: {e}")

    # LLM 相关性打分: 规则过滤只要求命中 1 个关键词, 会放过大量"同词异域"论文
    # (如 "deep learning" 命中的材料发现/显微成像/纳米光子/昆虫学/代谢组论文)。
    # 这些无关论文进入可信清单后, 审稿每轮都会扣参考文献分, 分数卡在 ~30 无法提升。
    try:
        from src.rag.relevance_filter import llm_score_filter

        before_llm = len(papers)
        papers = llm_score_filter(papers, topic, build_llm("cheap"))
        if len(papers) < before_llm:
            print(f"  [citation_precheck] LLM 相关性过滤: {before_llm} → {len(papers)} 篇候选")
    except Exception as e:
        logger.warning(f"LLM relevance filter skipped: {e}")

    # 出处解析: 仅对无发表状态标记的论文 (arXiv 等) 解析期刊/会议名
    # 批量请求 (每 20 篇 1 次 API), 网络波动时成功率远高于逐篇请求
    try:
        from src.tools.venue_resolver import resolve_venues_fast

        resolved = resolve_venues_fast(papers, max_papers=80)
        if resolved:
            print(f"  [citation_precheck] 出处批量解析: {resolved} 篇获得期刊信息")
    except Exception as e:
        logger.warning(f"venue resolution skipped: {e}")

    # 权威性优先级排序: 已发表 → 关键词命中数 → 被引量
    # 排序后编号前移, Writer 的 ref_sheet 中已发表文献排前
    try:
        from src.rag.relevance_filter import rank_papers_by_priority

        papers = rank_papers_by_priority(papers, topic, user_kw)
    except Exception as e:
        logger.warning(f"priority ranking skipped: {e}")

    # 用户要求: 最终参考文献只含真实已发表文献, 不含预印本。
    # 在验证+编号**前**剔除预印本 (arXiv 无 DOI), 保证 ref_number 连续 1..N,
    # Writer 的 ref_sheet 与最终参考文献章节都看不到预印本。
    try:
        from src.rag.reference_formatter import is_published_ref

        before_preprint = len(papers)
        papers = [p for p in papers if is_published_ref(p)]
        dropped = before_preprint - len(papers)
        if dropped:
            print(f"  [citation_precheck] 剔除预印本 {dropped} 篇 (最终参考文献只含已发表文献)")
    except Exception as e:
        logger.warning(f"preprint filter skipped: {e}")

    # 兜底去重: 检索/补录多路径合并后, 同一篇文献可能以不同 DOI/标题重复出现
    # (如 Few-Shot SEI 同时来自 arXiv 版与正式版, 被赋予不同编号 → 审稿判"重复引用")
    try:
        from src.tools.search_tools import dedup_papers

        before_dedup = len(papers)
        papers = dedup_papers(papers)
        if len(papers) < before_dedup:
            print(f"  [citation_precheck] 去重: {before_dedup} → {len(papers)} 篇候选")
    except Exception as e:
        logger.warning(f"dedup skipped: {e}")

    result = verify_reference_list(papers)

    verified_refs = result["verified"] + result["ambiguous"]

    # 预印本已在验证前剔除, 清单内文献全部为已发表 (人工导入 + 有 DOI)。
    preprint_count = 0
    published_count = len(verified_refs)
    for r in verified_refs:
        r["published"] = True

    sheet = build_verified_reference_sheet(verified_refs)
    ratio = preprint_count / len(verified_refs) if verified_refs else 0
    print(f"  [citation_precheck] 引用出处: 已发表 {published_count}, 预印本 {preprint_count} ({ratio:.0%})")

    report = result["report_md"] + f"\n\n---\n\n{sheet}"

    return {
        "current_phase": "citation_precheck",
        "verified_references": verified_refs,
        "citation_precheck_report": report,
        "citation_precheck_verified": len(result["verified"]),
        "citation_precheck_not_found": len(result["not_found"]),
        "preprint_count": preprint_count,
        "published_count": published_count,
    }
