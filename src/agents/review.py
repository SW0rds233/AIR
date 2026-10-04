from __future__ import annotations

"""ReviewAgent 独立审阅 (合并计划 §3.1 / §7.3)。

职责: 从**原始任务、当前稿件/图表、研究快照与原文**独立审阅 —— 检查科学逻辑、
问题忠实度、引文、可读性、图文一致性。提交 `ReviewReport` 与稳定 `ReviewIssue`。

合并要点 (§7.3):
- `paper_reviewer` + `research/critic/adversarial` + `citation_checker` 的语义意见
  合并到本角色, 保留科学/引用/可读性/图表分项检查;
- **不把文本总分作为科学结论的证明**;
- `pipeline.increment_revision/_build_revision_contract/_resolve_review_suggestions`
  拆分: 问题账本与修改验收归这里, 新增文献需求归 EvidenceAgent, 派谁修与何时停归主控。

硬约束 (§14.3, 不得让渡): 审阅**只可降级、不可升级**。它可以质疑推导、要求补证据
或建议否定命题, 但不直接修改命题真值 —— 运行时按 `downgrade_only_gate` 与对象种类
闸门实际拦住越界提交。
"""

from typing import Any

from src.agents.base import (
    AgentBase,
    as_list_of_str,
    clip,
    context_summary,
    extract_json,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    IssueSeverity,
    NeedKind,
    ResearchNeed,
    ReviewIssueRef,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec

__all__ = ["REVIEW_CATEGORIES", "ReviewAgent", "deterministic_issues"]

#: 检查项分类 (分项检查, 不给单一总分)。
REVIEW_CATEGORIES: tuple[str, ...] = (
    "science",       # 科学逻辑: 推论是否成立、前提是否交代
    "fidelity",      # 问题忠实度: 是否偷换题意/域/量词
    "citation",      # 引文: 是否可定位、是否支持当前论断
    "readability",   # 可读性: 结构、术语、定义
    "figure",        # 图文一致性: 图注与正文是否相符
    "completeness",  # 完整性: 未决项是否如实交代
)

_SEVERITY_ORDER = {
    IssueSeverity.blocking.value: 0,
    IssueSeverity.major.value: 1,
    IssueSeverity.minor.value: 2,
    IssueSeverity.advisory.value: 3,
}


class ReviewAgent(AgentBase):
    """独立审阅角色。"""

    role = "review"
    prompt_version = "review/v1"
    kinds = ("review_issue", "revision_task")

    SYSTEM = """你是"独立审阅"智能体, 一名严格但公正的学术审阅人。

审阅依据: 原始任务、当前稿件、研究快照与可定位原文。你**不接收作者的自评结论**作为
判断依据。

逐项检查并给出问题:
- science: 推论是否成立、前提是否交代、结论强度是否与证书一致;
- fidelity: 是否偷换了题目的对象/域/量词/约束;
- citation: 每处引用是否能定位、是否真的支持该论断 (命中不等于支持);
- readability: 结构、术语、符号是否一致可读;
- figure: 图注与正文说法是否一致、单位与数据来源是否交代;
- completeness: 未决项与局限是否如实交代。

纪律:
- 你只能**提出意见**与建议降级; 你不得宣称某条结论成立 (不得升级);
- 每个问题必须给出: 严重度 (blocking/major/minor/advisory)、类别、受影响对象 id、
  建议由谁返工、以及**可核验的验收标准**;
- 引用问题时给出原文片段作为定位;
- 不要用总分代替分项判断; 不要为了显得严格而编造问题。

最终输出 JSON:
{"issues": [{"severity": "blocking|major|minor|advisory", "category": "science|fidelity|citation|readability|figure|completeness",
             "summary": "", "detail": "", "locator": "", "affected_ids": [""],
             "suggested_owner": "evidence|modeling|reasoning|validation|writing|figures",
             "acceptance": [""]}],
 "resolved": [""], "unresolved": [""],
 "needs": [{"kind": "more_sources|derivation|model_condition|manuscript_revision", "statement": "", "why": ""}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        # 审阅可以回原文核对引文 (只读), 但没有写研究状态的工具
        from src.agents.tools import evidence_tools

        return [t for t in evidence_tools()
                if t.name in ("search_local_kb", "read_source")]

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        issues: list[ReviewIssueRef] = []
        parse_note = ""
        unresolved: list[str] = []

        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            draft = _draft_text(context)
            prompt = (
                f"# 原始任务\n{context.request}\n"
                f"# 待审稿件\n{draft or '(没有稿件正文)'}\n"
                f"# 上下文\n{context_summary(context)}"
            )
            llm = runtime.llm(task, usage, stage="review")
            try:
                result = llm.invoke([SystemMessage(content=self.SYSTEM),
                                     HumanMessage(content=prompt)])
                payload, parse_note = extract_json(getattr(result, "content", ""))
            except Exception as e:  # noqa: BLE001
                payload, parse_note = None, f"审阅调用失败: {e}"
            if isinstance(payload, dict):
                issues = _issues_from_payload(payload.get("issues"))
                unresolved.extend(as_list_of_str(payload.get("unresolved")))

        deterministic = deterministic_issues(context)
        issues = _merge_issues(issues, deterministic)
        if parse_note:
            unresolved.append(parse_note)

        if not issues:
            return self.completed(
                task, "未发现可报告的问题 (逐项检查通过)",
                unresolved=unresolved, usage=usage,
                payload={"schema": "ReviewReport/v1", "issues": [],
                         "by_severity": {}, "by_category": {}})

        blocking = [i for i in issues if i.blocking]
        summary = (f"发现 {len(issues)} 个问题 "
                   f"(阻断 {len(blocking)}); 分项: "
                   + ", ".join(f"{k} {v}" for k, v in _tally(issues).items()))
        needs = _needs_from(issues)
        changes = [ChangeProposal(
            kind="review_issue",
            payload=issue.to_dict(),
            rationale=f"审阅问题 ({issue.category}, {issue.severity.value})",
            input_versions={},
            may_change_conclusion=False,      # 审阅从不直接改结论真值
        ) for issue in issues]
        return self.completed(
            task, summary, changes=changes, needs=needs,
            unresolved=unresolved[:6], usage=usage,
            issues=issues, replan=bool(blocking),
            payload={"schema": "ReviewReport/v1",
                     "issues": [i.to_dict() for i in issues],
                     "by_severity": _tally(issues, "severity"),
                     "by_category": _tally(issues, "category"),
                     "blocking": len(blocking)})


# ----------------------------------------------------------------------
# 确定性检查 (零 LLM; 与 LLM 意见合并去重)
# ----------------------------------------------------------------------
def deterministic_issues(context: ContextPack) -> list[ReviewIssueRef]:
    """可判定的审阅项: 无定位引用、越权措辞、结论无依据、图文不一致、未决未交代。

    这些是**程序能查清**的部分, 因此不依赖模型; 模型负责语义层面的判断。两者合并
    后按 (类别 + 摘要) 去重, 保证同一问题在多次审阅之间保持稳定 id。
    """
    issues: list[ReviewIssueRef] = []
    manuscript_rows = context.objects.get("manuscript") or []
    evidence = context.objects.get("evidence") or []
    claims = context.objects.get("claim") or []
    figures = context.objects.get("figure") or []
    brief = (context.objects.get("brief") or [{}])[0]

    # 1. 引用不可定位
    for source in evidence:
        if source.get("locator"):
            continue
        issues.append(_issue(
            category="citation", severity="major",
            summary=f"来源缺少可回到原文的定位: {clip(str(source.get('title', '')), 100)}",
            affected=[str(source.get("source_id") or source.get("id") or "")],
            owner="evidence",
            acceptance=["给出页码/定理编号/字符范围之一, 或标注为不可引用"],
            locator=str(source.get("excerpt", "") or "")[:200],
        ))

    # 2. 命题无依据却已定论 / 含越权措辞
    for claim in claims:
        support_refs = as_list_of_str(claim.get("support_refs"))
        status = str(claim.get("status", "") or "")
        if status in ("supported", "verified") and not support_refs:
            issues.append(_issue(
                category="science", severity="blocking",
                summary=f"命题已标为 {status} 但没有任何来源依据",
                affected=[str(claim.get("id", "") or "")],
                owner="reasoning",
                acceptance=["补齐可定位来源, 或把状态降为未决/条件性"],
            ))
    for block in _manuscript_blocks(manuscript_rows):
        text = str(block.get("text", "") or "")
        if _overclaims(text) and not _has_certificate(block):
            issues.append(_issue(
                category="science", severity="major",
                summary=f"段落含强结论措辞但未给出证书: {clip(text, 120)}",
                affected=[str(block.get("block_id", "") or "")],
                owner="reasoning",
                acceptance=["给出可复核证书, 或把表述降为条件性结论"],
                locator=clip(text, 200),
            ))

    # 3. 图文一致性: 图缺图注/单位, 或图未声明数据来源
    for figure in figures:
        caption = str(figure.get("caption", "") or "")
        if not caption.strip():
            issues.append(_issue(
                category="figure", severity="minor",
                summary=f"图缺少图注: {clip(str(figure.get('purpose', '')), 80)}",
                affected=[str(figure.get("id", "") or "")],
                owner="figures",
                acceptance=["补图注, 并说明数据来源与单位"],
            ))
        elif _unit_conflict(caption, figure):
            issues.append(_issue(
                category="figure", severity="minor",
                summary="图注与数据源的量纲/单位说法不一致",
                affected=[str(figure.get("id", "") or "")],
                owner="figures",
                acceptance=["统一图注与数据源的单位表述"],
            ))

    # 4. 完整性问题: 有未决项但稿件没有交代
    unresolved = as_list_of_str(brief.get("unknown_fields"))
    gaps = context.objects.get("gap") or []
    if (unresolved or gaps) and manuscript_rows:
        mentions = any("未决" in str(b.get("text", "")) or "局限" in str(b.get("text", ""))
                       for b in _manuscript_blocks(manuscript_rows))
        if not mentions:
            issues.append(_issue(
                category="completeness", severity="major",
                summary=f"存在 {len(unresolved) + len(gaps)} 项未决/缺口, 但稿件没有交代",
                affected=[str(m.get("id", "") or "") for m in manuscript_rows],
                owner="writing",
                acceptance=["稿件明确写出未决项与局限"],
            ))
    return issues


def _manuscript_blocks(manuscript_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for manuscript in manuscript_rows:
        for section in manuscript.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for block in section.get("blocks") or []:
                if isinstance(block, dict):
                    out.append(block)
    return out


def _has_certificate(block: dict[str, Any]) -> bool:
    kinds = block.get("ref_kinds") or []
    if "verification" in kinds:
        return True
    return str(block.get("role", "")) == "certificate"


_STRONG_MARKERS = ("已证明", "已经证明", "严格证明", "证明了", "必定成立", "必然成立",
                   "QED", "qed")


def _overclaims(text: str) -> bool:
    return any(marker in str(text or "") for marker in _STRONG_MARKERS)


def _unit_conflict(caption: str, figure: dict[str, Any]) -> bool:
    declared = str(figure.get("y_unit", "") or "")
    if not declared:
        return False
    return declared not in caption and "单位" not in caption


# ----------------------------------------------------------------------
# 合并与辅助
# ----------------------------------------------------------------------
def _issues_from_payload(value: Any) -> list[ReviewIssueRef]:
    out: list[ReviewIssueRef] = []
    for item in value or []:
        issue = _issue_from(item)
        if issue is not None:
            out.append(issue)
    return out


def _issue_from(item: Any) -> ReviewIssueRef | None:
    if not isinstance(item, dict):
        return None
    try:
        return ReviewIssueRef(
            issue_id=_issue_id(str(item.get("category") or ""),
                               str(item.get("summary") or "")),
            severity=str(item.get("severity") or "minor"),
            category=str(item.get("category") or ""),
            summary=str(item.get("summary") or ""),
            locator=clip(str(item.get("locator") or ""), 400),
            affected_refs=[],
            suggested_owner=str(item.get("suggested_owner") or ""),
            acceptance=as_list_of_str(item.get("acceptance")),
        )
    except Exception:  # noqa: BLE001 - 未登记的严重度/角色跳过该条
        return None


def _issue(*, category: str, severity: str, summary: str,
           affected: list[str], owner: str, acceptance: list[str],
           locator: str = "") -> ReviewIssueRef:
    from src.research.schemas import ObjectRef

    return ReviewIssueRef(
        issue_id=_issue_id(category, summary),
        severity=severity,
        category=category,
        summary=summary,
        affected_refs=[ObjectRef(id=i) for i in affected if i],
        suggested_owner=owner,
        acceptance=acceptance,
        locator=locator,
    )


def _issue_id(category: str, summary: str) -> str:
    """稳定问题 id: 同一类同一问题在多次审阅之间保持同一编号。

    计划要求"问题账本可关闭且有修复证据" —— 如果每次审阅都换新 id, 账本永远关不掉。
    """
    from src.research.schemas import stable_id

    return stable_id("issue", category, " ".join(str(summary).split())[:200])


def _merge_issues(primary: list[ReviewIssueRef],
                  extra: list[ReviewIssueRef]) -> list[ReviewIssueRef]:
    by_id: dict[str, ReviewIssueRef] = {i.issue_id: i for i in primary}
    for issue in extra:
        existing = by_id.get(issue.issue_id)
        if existing is None:
            by_id[issue.issue_id] = issue
            continue
        # 同一问题: 取更严重的严重度, 并保留双方给出的验收标准
        if _SEVERITY_ORDER.get(issue.severity.value, 9) < \
                _SEVERITY_ORDER.get(existing.severity.value, 9):
            existing.severity = issue.severity
        for item in issue.acceptance:
            if item not in existing.acceptance:
                existing.acceptance.append(item)
    return sorted(by_id.values(),
                  key=lambda i: _SEVERITY_ORDER.get(i.severity.value, 9))


def _tally(issues: list[ReviewIssueRef], field: str = "category") -> dict[str, int]:
    out: dict[str, int] = {}
    for issue in issues:
        key = issue.category if field == "category" else issue.severity.value
        key = key or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


def _draft_text(context: ContextPack) -> str:
    parts: list[str] = []
    for manuscript in context.objects.get("manuscript") or []:
        parts.append(f"# {manuscript.get('title', '')}")
        if manuscript.get("abstract"):
            parts.append(str(manuscript["abstract"]))
        for section in manuscript.get("sections") or []:
            if not isinstance(section, dict):
                continue
            parts.append(f"## {section.get('heading', '')}")
            for block in section.get("blocks") or []:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))
    return "\n\n".join(parts)[:12000]


def _needs_from(issues: list[ReviewIssueRef]) -> list[ResearchNeed]:
    needs: list[ResearchNeed] = []
    mapping = {"citation": NeedKind.more_sources, "science": NeedKind.derivation,
               "fidelity": NeedKind.model_condition,
               "readability": NeedKind.manuscript_revision,
               "figure": NeedKind.figure_data,
               "completeness": NeedKind.manuscript_revision}
    for issue in issues:
        kind = mapping.get(issue.category)
        if kind is None or not issue.blocking:
            continue
        needs.append(ResearchNeed(
            kind=kind,
            statement=f"审阅问题 {issue.issue_id}: {issue.summary}",
            why="阻断交付的问题需要先处置",
            acceptance=list(issue.acceptance),
            blocking=True,
        ))
    return needs
