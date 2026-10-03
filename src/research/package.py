from __future__ import annotations

"""研究交付包 (P3)。

按 project_id/snapshot_id 组织: 问题规格、命题、证明、验证原始输出、决策日志、
新颖性、未决问题、研究稿与清单。过程完整但结果不新颖时标注为复现/理论整理。

计划书 §14: 三种交付级别 (研究备忘录 / 条件性研究报告 / 论文草稿) 必须显式写入
manifest, 不允许把"可导出部分结果"显示为"已完整解决原问题"。
"""

import json
from pathlib import Path

from src.research.acceptance import GateResult
from src.research.schemas import ResearchSnapshot, ResearchSpec


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
        "verifications": len(snapshot.verifications),
        "evidence": len(snapshot.evidence),
        "experiment_specs": len(snapshot.experiment_specs),
        "gate_passed": bool(gate.passed) if gate else None,
        "theory_gate_passed": bool(gate.passed) if gate else None,
        "delivery_gate_passed": bool(delivery.passed) if delivery else None,
        "delivery_level": delivery_level or ("论文草稿" if gate and gate.passed else "研究备忘录"),
        "writing_map": snapshot.writing_map,
        # P1-3: 正文每条核心论断能否回到冻结快照 (只查可反查性, 不判断论证正确性)
        "manuscript_traceability": _traceability(snapshot, manuscript_md),
        "limitation": _limitation(snapshot),
        "tool_versions": _tool_versions(snapshot),
        # P1-3: 审阅者要能凭交付包复核"用了哪版资料、哪组假设、哪次验证"
        "source_set": _source_set(spec, snapshot),
        "model_config": _model_config(),
        "budget_limits": _budget_limits(),
        # 计划书 §9.3: 实际花费与预估分开记录
        "usage": dict(usage or {}),
        # R6: 不可变启动输入 (问题附件/资料集/策略/预算) —— 与"证据文献"分开记录,
        # 附件不得冒充证据
        "input_snapshot": dict(input_snapshot or {}),
    }
    _write_json(root / "manifest.json", manifest)
    return root


def _traceability(snapshot: ResearchSnapshot, manuscript_md: str) -> dict:
    """核对交付包里的正文与冻结快照是否一致 (可反查性); 失败不影响导出。

    用快照**重建**一份正文对象, 再与落盘的 markdown 对照: 若写作阶段自行补足了
    快照里没有的结论, 映射与正文就会对不上, 这里如实报出来。
    """
    try:
        from src.agents.theory_writer import build_manuscript, trace_manuscript

        manuscript = build_manuscript(snapshot, topic=snapshot.project_id)
        return trace_manuscript(snapshot, manuscript, manuscript_md)
    except Exception as e:  # noqa: BLE001 - 追踪失败时如实记录, 不假装通过
        return {"ok": False, "error": type(e).__name__, "note": "可反查性检查未能执行"}


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
                      "file_hash": item.file_hash, "locator": item.location,
                      "retrieved_at": item.retrieved_at})
    coverage = getattr(spec, "coverage", None) if spec is not None else None
    return {
        "source_set_id": str(getattr(spec, "source_set_id", "") or ""),
        "source_set_kind": str(getattr(spec, "source_set_kind", "") or ""),
        "source_policy": (spec.source_policy.value
                          if spec is not None and getattr(spec, "source_policy", None) else ""),
        "queries": list(coverage.queries) if coverage is not None else [],
        "uncovered": list(coverage.uncovered) if coverage is not None else [],
        "documents": files,
        "note": "只记录版本/hash 与原文定位, 不外发原始数据",
    }


def _prompt_digest() -> str:
    """提示词版本 = 承载提示词的模块内容摘要 (可复核, 不再写"未登记")。"""
    import hashlib

    files = ("src/research/proposal.py", "src/research/coordinator.py",
             "src/research/question_planner.py", "src/agents/theory_writer.py")
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
    if open_items:
        for o in open_items:
            lines.append(f"- {o.id} ({o.status.value}): {o.statement} — {o.detail}")
    else:
        lines.append("- 无未关闭义务")
    blocked_claims = [c for c in snapshot.claims if c.status.value == "blocked"]
    for c in blocked_claims:
        lines.append(f"- 受阻结论 {c.id}: {c.statement}")
    if notes:
        lines.append("")
        lines.append("## 过程说明")
        lines.extend(f"- {n}" for n in notes)
    return "\n".join(lines) + "\n"


def _limitation(snapshot: ResearchSnapshot) -> str:
    if any(record.status.value == "known_equivalent" for record in snapshot.novelty):
        return "复现/理论整理 (存在已知等价结果), 不得包装为原创突破"
    if any(record.status.value == "unchecked" for record in snapshot.novelty) or not snapshot.novelty:
        return "在受限检索范围内整理, 新颖性未确认"
    return "初步判定可能不同, 尚待专家评审"
