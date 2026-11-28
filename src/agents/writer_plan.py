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
         "purpose": "从需要解释的现象或两种不同预测入手，指出已有解释缺少哪一步，再提出本文能够回答的问题。"},
        {"heading": "相关研究与研究缺口", "level": 1, "role": "literature",
         "purpose": "按主题比较文献的方法、适用域和局限；以引用支持具体陈述。"},
        {"heading": "研究设计与资料基础", "level": 1, "role": "method",
         "purpose": "明确研究对象入排标准、分析单位、地区/行业/时期、变量和资料；全篇口径须与模型前提一致。"},
        {"heading": "模型、假设与识别方法", "level": 2, "role": "method",
         "purpose": "解释已登记模型、条件、公式和识别边界；不填造未知参数。"},
        {"heading": finding, "level": 1, "role": "result",
         "purpose": ("呈现回答研究问题的已确定结论、条件与证据；恒等式、定义和计数一致性放方法说明，不充作原创贡献。" if settled else
                     "提出待核验机制、可证伪含义和现有证据边界，不宣称已测得效应。"
                     if packet.claims or packet.models else
                     "如实汇总已登记证据及当前未决问题；没有模型或命题时不编造机制。")},
        {"heading": "讨论与局限", "level": 1, "role": "discussion",
         "purpose": "选取会改变核心结论的一个前提，比较它成立和失效时的预测，给出能够区分两者的观测或实验。"},
        {"heading": "结论", "level": 1, "role": "conclusion",
         "purpose": "用读者关心的量回答开篇问题，说清推导止于哪一环，以及这对解释结果有什么影响。"},
    ]


def refine_outline(raw: Any, baseline: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    """Keep the inferential roles while allowing a shorter paper or distinct results."""
    rows = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not 4 <= len(rows) <= len(baseline):
        return baseline
    order = ("introduction", "literature", "method", "result", "discussion", "conclusion")
    roles = [row.get("role") if isinstance(row, dict) else None for row in rows]
    if (any(role not in order for role in roles)
            or not {"introduction", "method", "result", "conclusion"}.issubset(roles)
            or roles.count("introduction") != 1 or roles.count("conclusion") != 1
            or sorted(roles, key=order.index) != roles):
        return baseline
    refined = []
    for index, row in enumerate(rows):
        heading = str(row.get("heading") or "").strip()
        purpose = str(row.get("purpose") or "").strip()
        if (not heading or len(heading) > 80 or not purpose or len(purpose) > 260
                or any(term in heading + purpose for term in ("上一版", "本版", "审阅意见"))):
            return baseline
        if any(section["heading"] == heading for section in refined):
            return baseline
        level = 2 if index and roles[index] == roles[index - 1] else 1
        refined.append({"role": row["role"], "heading": heading, "purpose": purpose, "level": level})
    return refined


def claim_basis(packet: WritingPacket) -> list[dict[str, Any]]:
    """Pair each current statement with the actual checked expression and open conditions."""
    basis = []
    for claim in packet.claims[:20]:
        claim_id = claim.get("id")
        if not claim_id:
            continue
        version = claim.get("version") or 1

        def current(row):
            return (row.get("claim_id") == claim_id and str(row.get("claim_version") or 1) == str(version)
                    and not row.get("stale"))

        checked = []
        for record in packet.verifications:
            if (not current(record) or record.get("validation_status") != "verified"
                    or record.get("usable") is False):
                continue
            arguments = record.get("arguments") or {}
            target = arguments.get("conclusion") or record.get("checked_formula")
            if not target:
                continue
            proof = {"id": record.get("id"), "scope": record.get("scope"),
                     "premises": arguments.get("premises") or record.get("premises") or [],
                     "target": target}
            for key in ("variables", "assumptions"):
                if arguments.get(key):
                    proof[key] = arguments[key]
            if record.get("assumption_ids"):
                proof["assumption_ids"] = record["assumption_ids"]
            checked.append(proof)
        checked.sort(key=lambda proof: proof["scope"] != "target")
        basis.append({"id": claim_id, "version": version, "status": claim.get("status", "proposed"),
                      "statement": claim.get("statement"), "checked": checked[:4],
                      "open_conditions": [o.get("statement") for o in packet.obligations
                                          if current(o) and o.get("status", "open") in {"open", "blocked"}
                                          and o.get("statement")]})
    return basis


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

    ranked = sorted(eligible, key=lambda row: (-score(row), -len(str(row.get('excerpt') or '')),
                                             str(row.get("source_id") or row.get("id"))))
    unique = {}
    for row in ranked:
        key = str(row.get('source_id') or row.get('id') or '')
        if key and key not in unique:
            unique[key] = dict(row)
            unique[key]['_support_assessments'] = [{field: other.get(field) for field in (
                'claim_id','claim_version','relation','support_scope','supported_target','support_reason')}
                for other in rows if str(other.get('source_id') or other.get('id') or '') == key]
    return list(unique.values())[:limit]


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
