from __future__ import annotations

"""引文核查智能体：对论文初稿的每条引用进行真实性核查

实现参考:
- opendraft: 所有引用提交 CrossRef/OpenAlex/arXiv 验证后才进参考文献
- sisyphus-academica: 引用验证 + 对抗性审查
- research-paper-lifecycle-skills: citation verification skill
"""

import logging

from langchain_core.messages import SystemMessage, HumanMessage

from src.config import build_llm
from src.graph.state import PipelineState
from src.tools.citation_verifier import verify_draft_citations, extract_references_from_draft
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_text

logger = logging.getLogger(__name__)

CITATION_CHECKER_SYSTEM = """你是"引文核查智能体"，一名严谨的学术引用验证专家。

你的任务：对论文初稿中的每条引用进行真实性核查，输出结构化核查报告。

## 核查流程

1. 从论文初稿中提取所有参考文献条目（数字编号 [1], [2], ...）
2. 对每条引用，调用 verify_citation 工具验证：
   - CrossRef API（首选）
   - OpenAlex API（备选）
   - arXiv API（针对预印本）
3. 检查文中引用标记 [n] 与参考文献列表的对应关系
4. 输出核查报告

## 核查标准

- **VERIFIED**: 标题相似度 ≥ 0.6，可在数据库中查到真实文献
- **AMBIGUOUS**: 标题相似度 < 0.6 但找到相近文献，需要人工确认
- **NOT_FOUND**: 三个数据库都查不到，高度疑似虚构引用

## 输出格式

输出完整的核查报告 Markdown，包含：
1. 核查概况（总数/已验证/存疑/未找到）
2. 逐条核查结果表格
3. 疑似虚构引用清单
4. 修改建议

**重要原则**：对于 NOT_FOUND 的引用，必须明确标注并要求删除或替换，不得保留在论文中。
"""


def run_citation_check(state: PipelineState) -> dict:
    """流水线节点：核查初稿中的所有引用"""
    draft = state.get("paper_draft", "")
    verified_refs = state.get("verified_references", [])

    if not draft:
        return {
            "error": "论文初稿为空，无法进行引文核查",
            "current_phase": "citation_check",
        }

    # 1. 程序化核查（确定性，不依赖 LLM）
    # 提供可信清单: 清单内文献直接标记 VERIFIED, 仅未命中的才走外部 API
    report = verify_draft_citations(draft, verified_refs)

    # 2. LLM 深度核查（针对模糊引用给出建议）— 轻量任务, 用廉价模型摊薄成本
    llm = build_llm("cheap")

    messages = [
        SystemMessage(content=CITATION_CHECKER_SYSTEM),
        HumanMessage(
            content=(
                f"以下是引文核查工具输出的程序化核查结果，请审阅并给出最终意见。\n\n"
                f"---程序化核查结果---\n"
                f"{budget_text(report['report_md'], 8000, label='程序化核查结果')}\n---结束---\n\n"
                f"请重点审查 'AMBIGUOUS' 和 'NOT_FOUND' 的条目，给出具体修改建议"
                f"（删除/替换为哪篇真实文献）。\n"
                f"输出格式：\n"
                f"1. 总体结论（引用真实性评级）\n"
                f"2. 需要处理的引用清单（含建议）\n"
                f"3. 修改优先级（高/中/低）"
            )
        ),
    ]
    try:
        result = llm.invoke(messages)
        llm_opinion = result.content if hasattr(result, "content") else str(result)
        usage = extract_usage_metadata(result)
        if usage:
            from src.config import CHEAP_CONFIG

            model_name = (
                result.response_metadata.get("model_name")
                if isinstance(result.response_metadata, dict)
                else None
            ) or CHEAP_CONFIG["model"]
            tracker.add_call(model_name, usage, stage="citation_check")
    except Exception as e:
        logger.warning(f"LLM citation review failed: {e}")
        llm_opinion = "（LLM 深度审查不可用，仅保留程序化核查结果）"

    report["llm_opinion"] = llm_opinion
    report["report_md"] += f"\n\n---\n\n## LLM 深度审查意见\n\n{llm_opinion}"

    # 证据账本：为每条引用生成证据链（claim → 验证源 → 匹配文献 → 结论）
    ledger = build_evidence_ledger(report)
    report["evidence_ledger"] = ledger

    # 参考文献章节缺失 = 必须修订的结构性问题 (触发修订循环)
    ref_section_missing = bool(report.get("ref_section_missing", False))
    not_found_count = report.get("not_found", 0)
    if ref_section_missing:
        not_found_count = max(not_found_count, 1)

    return {
        "current_phase": "citation_check",
        "citation_report": report,
        "evidence_ledger": ledger,
        "ref_section_missing": ref_section_missing,
        "hallucinated_refs": report.get("hallucinated_refs", []),
        "citation_verified_count": report.get("verified", 0),
        "citation_not_found_count": not_found_count,
        "citation_total": report.get("total_refs", 0),
    }


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
