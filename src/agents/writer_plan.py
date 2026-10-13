from __future__ import annotations

"""A bounded argument plan and source selection for manuscript drafting."""

import math
import re
from typing import Any

from src.publication.references import research_citation_eligible
from src.publication.schemas import WritingPacket


def build_paper_outline(packet: WritingPacket) -> list[dict[str, str | int]]:
    """Plan the argument from the registered result state, not a claimed result."""
    settled = any(str(row.get("status")) in {"supported", "refuted"}
                  for row in packet.claims)
    finding = ("研究结果与证据链" if settled else
               "候选机制与可检验命题" if packet.claims or packet.models else
               "证据现状与未决问题")
    return [
        {"heading": "引言", "level": 1, "role": "introduction",
         "purpose": "提出研究问题、研究范围、贡献与本文结构；不汇报修订过程。"},
        {"heading": "相关研究与研究缺口", "level": 1, "role": "literature",
         "purpose": "按主题比较文献的方法、适用域和局限；以引用支持具体陈述。"},
        {"heading": "研究设计与资料基础", "level": 1, "role": "method",
         "purpose": "说明研究对象、资料选择、变量、证据等级和研究设计。"},
        {"heading": "模型、假设与识别方法", "level": 2, "role": "method",
         "purpose": "解释已登记模型、条件、公式和识别边界；不填造未知参数。"},
        {"heading": finding, "level": 1, "role": "result",
         "purpose": ("只呈现已确定结论及其证书，并区分反例与支持。" if settled else
                     "提出待核验机制、可证伪含义和现有证据边界，不宣称已测得效应。"
                     if packet.claims or packet.models else
                     "如实汇总已登记证据及当前未决问题；没有模型或命题时不编造机制。")},
        {"heading": "讨论与局限", "level": 1, "role": "discussion",
         "purpose": "比较解释、适用域、反事实与未决项；说明进一步验证所需资料。"},
        {"heading": "结论", "level": 1, "role": "conclusion",
         "purpose": "直接回答研究问题，区分已知、候选与未知；不给出未经验证的方向或数值。"},
    ]


def refine_outline(raw: Any, baseline: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    """Accept model wording only when the argument's required roles survive."""
    rows = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or len(rows) != len(baseline):
        return baseline
    refined = []
    for row, expected in zip(rows, baseline):
        if not isinstance(row, dict) or row.get("role") != expected["role"]:
            return baseline
        heading = str(row.get("heading") or "").strip()
        purpose = str(row.get("purpose") or "").strip()
        if (not heading or len(heading) > 80 or not purpose or len(purpose) > 260
                or any(term in heading + purpose for term in ("上一版", "本版", "审阅意见"))):
            return baseline
        refined.append({**expected, "heading": heading, "purpose": purpose})
    return refined


def linked_source_ids(packet: WritingPacket) -> set[str]:
    """Sources explicitly attached to claims or model assumptions lead the dossier."""
    ids: set[str] = set()
    for claim in packet.claims:
        ids.update(str(value) for value in claim.get("support_refs") or [] if value)
    for model in packet.models:
        for field in ("source_ids", "source_refs"):
            ids.update(str(value) for value in model.get(field) or [] if value)
        for assumption in model.get("assumptions") or []:
            if isinstance(assumption, dict):
                ids.update(str(value) for value in assumption.get("source_ids") or [] if value)
    return ids


def rank_sources(rows: list[dict[str, Any]], limit: int = 20,
                 priority_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Prioritise readable, relevant and recent registered publications."""
    eligible = [row for row in rows if research_citation_eligible(row)]

    def score(row: dict[str, Any]) -> float:
        source_id = str(row.get("source_id") or row.get("id") or "")
        linked = 12 if source_id in (priority_ids or set()) else 0
        level = str(row.get("content_level") or "").lower()
        readable = 5 if level in {"full_text", "fulltext", "full"} else 2 if level == "abstract" else 0
        relation = 3 if row.get("relation") in {"supports", "contradicts"} else 0
        relevance = 2 if row.get("relevance_reason") else 0
        try:
            year = int(row.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        recency = max(0, min(3, (year - 2015) / 4))
        citations = row.get("cited_by_count") or row.get("citation_count") or 0
        try:
            cited = min(3, math.log1p(max(0, int(citations))) / 2)
        except (TypeError, ValueError):
            cited = 0
        return linked + readable + relation + relevance + recency + cited

    return sorted(eligible, key=lambda row: (-score(row), str(row.get("source_id") or row.get("id"))))[:limit]


def revision_section(packet: WritingPacket, plan: dict[str, str | int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Find the corresponding prior section and its actionable review issues."""
    prior_sections = packet.prior_manuscript.get("sections") or []
    if not isinstance(prior_sections, list):
        prior_sections = []
    role = str(plan["role"])
    heading = str(plan["heading"])
    prior = next((row for row in prior_sections if isinstance(row, dict)
                  and re.sub(r"^\d+(?:\.\d+)*\s*", "", str(row.get("heading") or "")) == heading), None)
    if prior is None:
        prior = next((row for row in prior_sections if isinstance(row, dict)
                      and row.get("role") == role), {})
    section_refs = {str(ref.get("id") or "") for block in prior.get("blocks") or []
                    if isinstance(block, dict) for ref in block.get("refs") or []
                    if isinstance(ref, dict)}
    section_blocks = {str(block.get("block_id") or "") for block in prior.get("blocks") or []
                      if isinstance(block, dict) and block.get("block_id")}
    selected = []
    for issue in packet.review_issues:
        if not isinstance(issue, dict):
            continue
        locator = str(issue.get("locator") or "")
        category = str(issue.get("category") or "")
        affected = {str(item.get("id") or "") if isinstance(item, dict) else str(item)
                    for item in (issue.get("affected_refs") or issue.get("affected_ids") or [])}
        if (heading in locator or any(block_id in locator for block_id in section_blocks)
                or section_refs & affected
                or (category == "citation" and role == "literature")
                or (category == "figure" and role == "result")
                or (category == "readability" and role in {"introduction", "discussion"})
                or (category in {"science", "fidelity"} and role in {"method", "result"})
                or (category == "completeness" and role == "conclusion")):
            selected.append(issue)
    return prior, selected[:12]
