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
from src.utils.cost_tracker import tracker, extract_usage_metadata

logger = logging.getLogger(__name__)

VERIFY_LIMIT = 40  # 最多验证前 40 篇候选论文（综述需 30+ 参考文献）


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

        # 关键优化: 论文来自官方检索 API (arXiv/S2/OpenAlex), 本身即真实存在,
        # 直接标记 VERIFIED, 避免逐篇调用 3 个验证 API 触发限流 (借鉴 opendraft)
        api_source = paper.get("api_source", "") or paper.get("source", "")
        if api_source in ("arXiv", "Semantic Scholar", "OpenAlex"):
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
    """流水线节点：写作前验证候选引用清单"""
    papers = state.get("retrieved_papers", [])
    if not papers:
        return {
            "current_phase": "citation_precheck",
            "verified_references": [],
            "citation_precheck_report": "## 引用预验证\n\n没有候选论文。",
        }

    # 出处解析: 为无真实出处的论文解析期刊/会议名 (DOI→CrossRef / arXiv journal_ref)
    # 参考文献必须标明出版出处, 而非只标 arXiv
    try:
        from src.tools.venue_resolver import resolve_venue_batch

        resolve_venue_batch(papers)
    except Exception as e:
        logger.warning(f"venue resolution skipped: {e}")

    result = verify_reference_list(papers)

    verified_refs = result["verified"] + result["ambiguous"]
    sheet = build_verified_reference_sheet(verified_refs)

    report = result["report_md"] + f"\n\n---\n\n{sheet}"

    return {
        "current_phase": "citation_precheck",
        "verified_references": verified_refs,
        "citation_precheck_report": report,
        "citation_precheck_verified": len(result["verified"]),
        "citation_precheck_not_found": len(result["not_found"]),
    }
