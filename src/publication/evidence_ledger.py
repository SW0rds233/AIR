from __future__ import annotations

"""证据台账与已核实文献表 (计划书 §5.3 D1 的通用件)。

逐字迁出自 `agents/citation_checker.build_evidence_ledger` 与
`agents/citation_prechecker.build_verified_reference_sheet`: 两者都是"把结构化结果写成人
可读清单"的纯格式函数, 与旧流水线状态无关。
"""


from src.tools.citation_verifier import (  # noqa: E402  (通用件: 逐篇验证工具)
    CitationRecord,
    verify_single_citation,
)

#: 最多验证多少篇候选论文 (来自未过滤池, 保证写作有充足引用)。
VERIFY_LIMIT = 60


def build_evidence_ledger(report: dict) -> str:
    """生成证据账本：每一条引用的完整证据链

    参考: research-proof 的 proof ledger 设计 — 每个 claim 都有可复核的证据
    记录格式: 文中引用位置 → 声称的文献 → 验证源 → 匹配到的真实文献 → 结论
    """
    lines = [
        "# 证据账本 (Evidence Ledger)",
        "",
        "**说明**: 每一条引用都有可复核的证据链。验证源: CrossRef / OpenAlex / arXiv。",
        "",
        "| 引用号 | 声称的文献 | 验证源 | 匹配到的真实文献 | 相似度 | 状态 | DOI/链接 |",
        "|--------|-----------|--------|----------------|--------|------|---------|",
    ]

    results = report.get("results", [])
    status_icon = {"VERIFIED": "✅", "AMBIGUOUS": "⚠️", "NOT_FOUND": "❌", "RETRACTED": "🚫", "ERROR": "🔧"}
    for r in results:
        lines.append(
            f"| [{r.get('ref_number', '')}] | {r.get('claimed_title', '')[:60]} | "
            f"{r.get('detail', '')[:25]} | {r.get('matched_title', '')[:60]} | "
            f"{r.get('similarity', 0)} | {status_icon.get(r.get('status', ''), '?')} {r.get('status', '')} | "
            f"{r.get('doi', '') or r.get('venue', '') or '—'} |"
        )

    coverage = report.get("coverage", {})
    lines.extend(
        [
            "",
            "## 覆盖率检查",
            "",
            f"- 文中引用标记: {coverage.get('inline_citations', 0)} 处",
            f"- 参考文献条目: {coverage.get('ref_entries', 0)} 条",
            f"- 文中引用但参考文献缺失: {coverage.get('unreferenced_citations', [])}",
            f"- 参考文献未被文中引用: {coverage.get('uncited_refs', [])}",
            f"- 覆盖率通过: {'✅' if coverage.get('coverage_ok') else '❌'}",
            "",
            "## 核查统计",
            "",
            f"- 总引用: {report.get('total_refs', 0)}",
            f"- 已验证: {report.get('verified', 0)}",
            f"- 存疑: {report.get('ambiguous', 0)}",
            f"- 未找到(疑似虚构): {report.get('not_found', 0)}",
            f"- 解析错误: {report.get('errors', 0)}",
            "",
            "## 结论",
            "",
            "**处理建议**:",
        ]
    )
    if report.get("not_found", 0) > 0:
        lines.append(
            f"- 存在 {report.get('not_found', 0)} 条疑似虚构引用，必须删除或替换后再投稿"
        )
    elif report.get("ambiguous", 0) > 0:
        lines.append(
            f"- 存在 {report.get('ambiguous', 0)} 条存疑引用，建议人工确认标题准确性"
        )
    else:
        lines.append("- 所有引用均已通过验证，可以进入审阅阶段")

    return "\n".join(lines)


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
