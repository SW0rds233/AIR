from __future__ import annotations

"""研究交付包 (P3)。

按 project_id/snapshot_id 组织: 问题规格、命题、证明、验证原始输出、决策日志、
新颖性、未决问题、研究稿与清单。过程完整但结果不新颖时标注为复现/理论整理。

计划书 §14: 三种交付级别 (研究备忘录 / 条件性研究报告 / 论文草稿) 必须显式写入
manifest, 不允许把"可导出部分结果"显示为"已完整解决原问题"。
"""

import json
import re
from pathlib import Path

from src.research.acceptance import GateResult
from src.research.schemas import ClaimStatus, ResearchSnapshot, ResearchSpec
from src.publication.schemas import BlockRole, Manuscript, RefKind


def export_package(
    snapshot: ResearchSnapshot,
    spec: ResearchSpec | None,
    decisions: list[dict],
    manuscript_md: str,
    manuscript_tex: str | None = None,
    notes: list[str] | None = None,
    gate: GateResult | None = None,
    base_dir: str | Path | None = None,
    delivery: GateResult | None = None,
    delivery_level: str = "",
    usage: dict | None = None,
    run_id: str = "",
    input_snapshot: dict | None = None,
    manuscript: Manuscript | None = None,
    publication: GateResult | None = None,
) -> Path:
    root = Path(base_dir) if base_dir else _default_root(snapshot)
    root.mkdir(parents=True, exist_ok=True)
    (root / "evidence").mkdir(exist_ok=True)
    (root / "proofs").mkdir(exist_ok=True)
    (root / "verification").mkdir(exist_ok=True)
    (root / "experiment_specs").mkdir(exist_ok=True)

    if spec is not None:
        _write_json(root / "research_spec.json", spec.model_dump(mode="json"))
    _write_json(root / "definitions.json",
                [d.model_dump(mode="json") for d in snapshot.definitions])
    _write_json(root / "models.json", [m.model_dump(mode="json") for m in snapshot.models])
    _write_json(root / "claims.json", [c.model_dump(mode="json") for c in snapshot.claims])
    _write_json(root / "assumptions.json", [a.model_dump(mode="json") for a in snapshot.assumptions])
    _write_json(root / "obligations.json", [o.model_dump(mode="json") for o in snapshot.obligations])
    _write_json(root / "evidence.json", [e.model_dump(mode="json") for e in snapshot.evidence])
    _write_json(root / "evidence_links.json",
                [e.model_dump(mode="json") for e in snapshot.evidence_links])
    _write_json(root / "routes.json", [r.model_dump(mode="json") for r in snapshot.routes])
    _write_json(root / "gaps.json", [g.model_dump(mode="json") for g in snapshot.gaps])
    _write_json(root / "writing_gaps.json", list(manuscript.gaps) if manuscript is not None else [])

    for record in snapshot.verifications:
        _write_json(root / "verification" / f"{record.id}.json", record.model_dump(mode="json"))
    for attempt in snapshot.attempts:
        _write_json(root / "proofs" / f"{attempt.id}.json", attempt.model_dump(mode="json"))
    for spec_item in snapshot.experiment_specs:
        _write_json(root / "experiment_specs" / f"{spec_item.get('id', 'spec')}.json", spec_item)

    with (root / "decisions.jsonl").open("w", encoding="utf-8") as fh:
        for decision in decisions:
            fh.write(json.dumps(decision, ensure_ascii=False) + "\n")

    (root / "novelty_review.md").write_text(_render_novelty(snapshot), encoding="utf-8")
    (root / "unresolved.md").write_text(_render_unresolved(snapshot, notes or []), encoding="utf-8")
    (root / "manuscript.md").write_text(manuscript_md, encoding="utf-8")
    if manuscript_tex:
        (root / "paper.tex").write_text(manuscript_tex, encoding="utf-8")

    manifest = {
        "project_id": snapshot.project_id,
        # R6/F3: 交付物归属到**具体研究问题与运行**, 文件清单据此按当前问题过滤。
        # 身份的权威来源是快照本身; `spec`/`run_id` 只在快照缺该字段时兜底。
        "problem_id": (snapshot.problem_id or (spec.problem_id if spec is not None else "")),
        "run_id": (snapshot.run_id or run_id or root.name),
        "branch_id": snapshot.branch_id,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at,
        "claims": len(snapshot.claims),
        "models": len(snapshot.models),
        "obligations": len(snapshot.obligations),
        "proof_attempts": len(snapshot.attempts),
        "definitions": len(snapshot.definitions),
        "assumptions": len(snapshot.assumptions),
        "gaps": len(snapshot.gaps),
        "writing_gaps": len(manuscript.gaps) if manuscript is not None else 0,
        "routes": len(snapshot.routes),
        "decisions": len(decisions),
        "verifications": len(snapshot.verifications),
        "evidence": len(snapshot.evidence),
        "experiment_specs": len(snapshot.experiment_specs),
        **gate_manifest_fields(gate, delivery, publication),
        "delivery_level": delivery_level or ("论文草稿" if gate and gate.passed else "研究备忘录"),
        "writing_map": snapshot.writing_map,
        # 正文每条核心论断能否回到冻结快照 (只查可反查性, 不判断论证正确性)
        "manuscript_traceability": _traceability(snapshot, manuscript_md, manuscript),
        "limitation": _limitation(snapshot),
        "tool_versions": _tool_versions(snapshot),
        # 审阅者要能凭交付包复核"用了哪版资料、哪组假设、哪次验证"
        "source_set": _source_set(spec, snapshot),
        "model_config": _model_config(),
        "budget_limits": _budget_limits(),
        # 实际花费与预估分开记录
        "usage": dict(usage or {}),
        # R6: 不可变启动输入 (问题附件/资料集/策略/预算) —— 与"证据文献"分开记录,
        # 附件不得冒充证据
        "input_snapshot": dict(input_snapshot or {}),
    }
    _write_json(root / "manifest.json", manifest)
    return root


def _traceability(snapshot: ResearchSnapshot, manuscript_md: str,
                  manuscript: Manuscript | None = None) -> dict:
    """以**真实稿件**的块引用与渲染锚点核对冻结快照。

    不重建一份快照稿件：重建会得到另一套锚点，既可能误报，也可能自证通过。
    没传结构化稿件时仍检查快照登记的正文映射，但不能声称检查过每个块的引用。
    """
    text = manuscript_md or ""
    core = [claim for claim in snapshot.claims
            if claim.status in (ClaimStatus.supported, ClaimStatus.refuted)]
    mapping = snapshot.writing_map or {}
    mapped = [{"claim_id": claim.id, "anchor": mapping[claim.id]}
              for claim in core if mapping.get(claim.id)]
    unmapped = [claim.id for claim in core if not mapping.get(claim.id)]
    missing = [item["claim_id"] for item in mapped
               if text.strip() and item["anchor"] not in text]
    unlabeled: list[str] = []
    orphan_refs: list[str] = []
    if manuscript is not None:
        known = {claim.id for claim in snapshot.claims}
        for block in manuscript.all_blocks():
            refs = [ref.id for kind, ref in zip(block.ref_kinds, block.refs)
                    if kind == RefKind.claim.value]
            if block.role == BlockRole.claim and not refs:
                unlabeled.append(block.block_id)
            orphan_refs.extend(ref for ref in refs if ref not in known)
    checked = "markdown" if text.strip() else "snapshot_only"
    ok = (bool(core) and checked == "markdown" and not unmapped and not missing
          and not unlabeled and not orphan_refs)
    return {
        "ok": ok, "checked": checked, "mapped": mapped,
        "status": "not_applicable" if not core else "passed" if ok else "failed",
        "unmapped_claims": unmapped, "missing_anchors": missing,
        "unlabeled_blocks": unlabeled, "orphan_claim_refs": sorted(set(orphan_refs)),
        "core_claims": len(core),
        "note": ("没有可核查的核心结论映射，不能据此认定论文结论已通过核验" if not core else
                 "真实正文的核心结论已反查到冻结快照对象" if ok else
                 "正文与快照的映射不完整，或缺少可核对的正文"),
    }


def gate_manifest_fields(theory=None, delivery=None, publication=None) -> dict:
    """One serialized gate contract, shared by initial and post-compilation export."""
    stages = {"theory": theory, "delivery": delivery, "publication": publication}
    fields = {f"{name}_gate_passed": bool(gate.passed) if gate is not None else None
              for name, gate in stages.items()}
    for name, gate in stages.items():
        fields[f"{name}_gate_reasons"] = list(dict.fromkeys(
            [*gate.reasons, *(gate.unresolved if not gate.passed else [])])) if gate is not None else []
        fields[f"{name}_gate_unresolved"] = list(gate.unresolved) if gate is not None else []
    fields["blocking"] = list(dict.fromkeys(reason for name in stages
                                            for reason in fields[f"{name}_gate_reasons"]))
    fields["unresolved"] = list(dict.fromkeys(reason for gate in stages.values() if gate is not None
                                              for reason in gate.unresolved))
    fields["gate_passed"] = all(gate is not None and gate.passed for gate in stages.values())
    return fields


def _source_set(spec: ResearchSpec | None, snapshot: ResearchSnapshot) -> dict:
    """资料集合版本与文件 hash + 查询/原文定位 (P1-3 可复核清单)。

    只放 hash 与定位, 不外发原始数据; 没有资料源时如实写空, 不编造版本号。
    """
    files: list[dict] = []
    seen: set[str] = set()
    for item in snapshot.evidence:
        key = item.source_id or item.doi or item.title
        if not key or key in seen:
            continue
        seen.add(key)
        files.append({"source_id": item.source_id, "title": item.title, "doi": item.doi,
                      "authors": item.authors, "year": item.year, "url": item.url,
                      "content_level": item.content_level, "source_set_id": item.source_set_id,
                      "file_hash": item.file_hash, "locator": item.location,
                      "retrieved_at": item.retrieved_at,
                      **{key: getattr(item, key, "") for key in (
                          "publication_status", "publication_type", "publication_verified_by",
                          "publication_note", "preprint_url", "volume", "issue", "pages", "venue")}})
    coverage = getattr(spec, "coverage", None) if spec is not None else None
    return {
        "source_set_id": str(getattr(spec, "source_set_id", "") or ""),
        "source_set_kind": str(getattr(spec, "source_set_kind", "") or ""),
        "source_policy": (spec.source_policy.value
                          if spec is not None and getattr(spec, "source_policy", None) else ""),
        "queries": list(dict.fromkeys(
            (list(coverage.queries) if coverage is not None else [])
            + [query for item in snapshot.evidence for query in item.retrieval_queries])),
        "source_set_ids": list(dict.fromkeys(item.source_set_id for item in snapshot.evidence
                                             if item.source_set_id)),
        "uncovered": list(coverage.uncovered) if coverage is not None else [],
        "documents": files,
        "note": "只记录版本/hash 与原文定位, 不外发原始数据",
    }


def _prompt_digest() -> str:
    """提示词版本 = 承载提示词的模块内容摘要 (可复核, 不再写"未登记")。"""
    import hashlib

    files = ("src/agents/supervisor.py", "src/agents/evidence.py",
             "src/agents/modeling.py", "src/agents/reasoning.py",
             "src/agents/validation_planning.py", "src/agents/writing.py",
             "src/agents/figures.py", "src/agents/review.py")
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode("utf-8"))
        try:
            digest.update(Path(name).read_bytes())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()[:12]


def _model_config() -> dict:
    """本次运行使用的模型配置 (供复核"哪版模型/提示")。"""
    try:
        from src.config import (
            CHEAP_CONFIG,
            COORDINATOR_CONFIG,
            LLM_CONFIG,
            REVIEWER_CONFIG,
            THEORIST_CONFIG,
            VERIFIER_CONFIG,
        )

        digest = _prompt_digest()
        return {
            "main": LLM_CONFIG.get("model", ""), "reviewer": REVIEWER_CONFIG.get("model", ""),
            "cheap": CHEAP_CONFIG.get("model", ""),
            "coordinator": COORDINATOR_CONFIG.get("model", ""),
            "theorist": THEORIST_CONFIG.get("model", ""),
            "verifier": VERIFIER_CONFIG.get("model", ""),
            # R6: 提示词以内容摘要登记 (改了提示词摘要就变, 便于复核用了哪版提示)
            "prompt_digest": digest,
            "prompt_version": f"digest:{digest}",
        }
    except Exception as e:  # noqa: BLE001 - 配置不可读不影响交付包导出
        return {"error": type(e).__name__}


def _budget_limits() -> dict:
    """本次运行生效的资源上限 (与 usage 对照看出是否因预算停止)。"""
    try:
        from src.config import RESEARCH_MAX_COST_USD, RESEARCH_MAX_TOKENS, RESEARCH_MAX_WALL_SECONDS

        return {"max_tokens": RESEARCH_MAX_TOKENS, "max_cost_usd": RESEARCH_MAX_COST_USD,
                "max_wall_seconds": RESEARCH_MAX_WALL_SECONDS}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


def _tool_versions(snapshot: ResearchSnapshot) -> dict:
    versions: dict[str, str] = {}
    for record in snapshot.verifications:
        if record.tool and record.tool_version:
            versions.setdefault(record.tool, record.tool_version)
    try:
        from src.verification.runner import available_tools

        versions["available"] = ",".join(sorted(k for k, v in available_tools().items() if v))
    except Exception as e:  # noqa: BLE001 - 工具清单不可用不影响交付包导出
        versions["available_error"] = type(e).__name__
    return versions


def _default_root(snapshot: ResearchSnapshot) -> Path:
    from src.config import OUTPUT_DIR

    return OUTPUT_DIR / "research" / snapshot.project_id / snapshot.snapshot_id


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _render_novelty(snapshot: ResearchSnapshot) -> str:
    lines = ["# 新颖性审查", ""]
    if not snapshot.novelty:
        lines.append("未开展新颖性检索; 不得宣称原创。")
        return "\n".join(lines) + "\n"
    for record in snapshot.novelty:
        lines.append(f"## {record.claim_id}")
        lines.append(f"- 状态: {record.status.value}")
        lines.append(f"- 检索日期: {record.search_date}")
        if record.queries:
            lines.append("- 查询: " + "; ".join(record.queries))
        lines.append(f"- 结论: {record.conclusion}")
        lines.append(f"- （{record.bounded_statement}）")
        lines.append("")
    return "\n".join(lines)


def _render_unresolved(snapshot: ResearchSnapshot, notes: list[str]) -> str:
    lines = ["# 未决问题与后续建议", ""]
    open_items = [o for o in snapshot.obligations if o.status.value in ("open", "blocked")]
    required = [o for o in open_items if o.required]
    optional = [o for o in open_items if not o.required]
    pending = [c for c in snapshot.claims if c.status.value in ("proposed", "in_progress", "blocked")]
    lines += [f"必要义务 {len(required)} 条；可选审查 {len(optional)} 条；未核验/受阻命题 {len(pending)} 条。",
              "必要义务关闭仍需有效核验记录；可选审查不等于科学证明。", ""]
    if open_items:
        for heading, items in (("必要义务", required), ("可选审查", optional)):
            if items:
                lines.extend([f"## {heading}", ""])
                for o in items:
                    lines.append(f"- {o.id} → {o.claim_id} v{o.claim_version} ({o.status.value}): {o.statement} — {o.detail}")
                lines.append("")
    else:
        lines.append("- 未发现未关闭的形式化证明义务")
    blocked_claims = [c for c in snapshot.claims if c.status.value == "blocked"]
    for c in blocked_claims:
        lines.append(f"- 受阻结论 {c.id}: {c.statement}")
    if notes:
        lines.append("")
        lines.append("## 交付缺口与过程说明")
        groups: dict[str, list[str]] = {}
        seen: set[str] = set()
        for note in notes:
            # Strip only transport wrappers, not object IDs or substantive conditions.
            value = re.sub(r"^(?:(?:need|gap|需求|缺口)\s*:\s*)+", "", str(note).strip())
            key = re.sub(r"\s+", " ", value)
            if not key or key in seen:
                continue
            seen.add(key)
            category = value.split(":", 1)[0] if ":" in value else "其他"
            if len(category) > 40:
                category = "其他"
            groups.setdefault(category, []).append(value)
        for category, items in groups.items():
            lines += ["", f"### {category}（{len(items)}）", ""]
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines) + "\n"


def write_unresolved_report(base_dir: str | Path, snapshot: ResearchSnapshot,
                            notes: list[str] | None = None) -> Path:
    """写入与最终交付评估一致的未决项报告。"""
    target = Path(base_dir) / "unresolved.md"
    target.write_text(_render_unresolved(snapshot, list(notes or [])), encoding="utf-8")
    return target


def _limitation(snapshot: ResearchSnapshot) -> str:
    if any(record.status.value == "known_equivalent" for record in snapshot.novelty):
        return "复现/理论整理 (存在已知等价结果), 不得包装为原创突破"
    if any(record.status.value == "unchecked" for record in snapshot.novelty) or not snapshot.novelty:
        return "在受限检索范围内整理, 新颖性未确认"
    return "初步判定可能不同, 尚待专家评审"
