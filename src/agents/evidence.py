from __future__ import annotations

"""EvidenceAgent 检索与证据整理 (合并计划 §3.1 / §7.2)。

职责: 按 `EvidenceNeed` 自主决定查询、资料源、阅读与扩展检索; 覆盖文献/案例/数据。
提交 `EvidenceBundle`: 来源卡、案例卡、数据卡、精确定位、支持/冲突候选、覆盖与缺口。

合并要点 (§7.2):
- 把 `literature_reviewer` 的工具闭环 + `loop._act_retrieve_targeted` +
  `query_planner` 合并成**一套**请求/结果协议 (这里是 `retrieve` 方法);
- 来源事实摘录留在检索; 跨论文解释/分类与冲突综合**迁入 ReasoningAgent** ——
  因此这里不做"综述素材"式的解释性写作, 只交结构化材料;
- 清除固定年份/最低篇数/学科词: 查询式与停止条件按研究缺口定。

离线可用: 没有 LLM 时仍走 `kb/bridge.gather_sources` (它本身就是确定性检索),
覆盖记录与去重都由它保证; LLM 可用时先由工具循环补外部检索, 再统一走同一条入库路径。
"""

from typing import Any

from src.agents.base import (
    AgentBase,
    build_proposal,
    clip,
    context_summary,
    task_intent,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    NeedKind,
    ResearchNeed,
    TaskOutcome,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import evidence_tools

__all__ = ["EvidenceAgent"]


class EvidenceAgent(AgentBase):
    """检索与证据整理角色。"""

    role = "evidence"
    prompt_version = "evidence/v1"
    kinds = ("evidence", "source", "card", "case", "dataset")

    #: 每次检索的最大返回条数 (不是"最低篇数": 命中多少如实记录)
    per_query = 6
    max_queries = 4

    SYSTEM = """你是"检索与证据整理"智能体。

你的任务: 围绕给定的证据需求, 在**已授权**的资料范围内检索、阅读并整理可定位来源。

纪律:
- 只登记你实际读到的内容; 工具没有返回的内容一律不写 (不得凭记忆或常识补充);
- 每条来源必须能给出定位 (页码/章节/定理编号/字符范围); 定位不到就如实标注
  "无定位", 不得为了让引用看起来完整而编造定位;
- 命中不等于支持: 你只输出"候选关系", 支持/反对由判定层判定;
- 检索词按研究缺口设计, 不设固定年份与固定篇数; 命中少就如实说明覆盖有限;
- 工具失败时如实报告失败原因, 不得把失败说成"没有相关文献"。

最终输出 JSON:
{"sources": [{"title": "", "locator": "", "relation": "insufficient|supports|contradicts|partially_supports|background", "note": ""}],
 "coverage_note": "", "uncovered": [""], "needs": [{"kind": "more_sources|source_locator|reader_source|dataset", "statement": "", "why": ""}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        return evidence_tools()

    # ---- 主流程 ----
    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        topic = _topic_of(task, context)
        query = _query_of(task, context)
        policy = task.source_policy or "user_kb"

        # 1. 有 LLM 时: 先让工具循环补外部检索与阅读 (它在授权范围内自行决定查询)
        llm_note = ""
        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"# 证据需求\n{task_intent(task)}\n"
                f"# 预期收益\n{task.expected_gain or '(未声明)'}\n"
                f"# 资料授权\ntopic={topic!r} policy={policy}\n"
                f"# 上下文\n{context_summary(context)}"
            )
            try:
                text, observations = self.tool_loop(
                    task, runtime, tools, usage,
                    [SystemMessage(content=self.SYSTEM), HumanMessage(content=prompt)],
                    grant=context.grant)
                usage.tool_calls += len(observations)
                llm_note = clip(text, 600)
            except Exception as e:  # noqa: BLE001 - 工具循环失败不阻断确定性检索
                runtime.emit("evidence_loop_failed",
                             {"task_id": task.task_id, "reason": str(e)})
                llm_note = f"工具循环不可用 ({e}); 已改用确定性检索路径"

        # 2. 确定性检索: 与 LLM 路径共用同一入库与去重实现 (这就是"合并"的含义)
        collected = self.retrieve(task, context, runtime, topic=topic, query=query,
                                  policy=policy)
        if collected is None:
            return self.blocked(
                task,
                "没有可用的资料源: 未绑定资料库且未授权自主检索",
                needs=[ResearchNeed(
                    kind=NeedKind.more_sources,
                    statement="需要可检索的资料范围 (绑定资料库或授权自主检索)",
                    why="检索不到任何来源就无法支撑后续推理",
                    acceptance=["给出至少一个可用资料源, 或明确以未检索状态交付"],
                    blocking=False)],
                usage=usage,
                summary="资料范围不可用",
            )

        evidence_items, coverage, unresolved = collected
        if not evidence_items and not coverage.executed:
            return self.blocked(
                task, coverage.uncovered[0] if coverage.uncovered else "检索未执行",
                needs=[ResearchNeed(
                    kind=NeedKind.more_sources,
                    statement="检索未执行 (资料库不可用或为空)",
                    why="需要真实检索结果才能给出依据",
                    acceptance=["检索真的执行过, 或明确说明为何无法检索"],
                    blocking=False)],
                usage=usage)

        locatable = [e for e in evidence_items if e.get("locator")]
        changes = [self._evidence_proposal(task, e, topic) for e in evidence_items]
        needs = self._needs_from(evidence_items, locatable, coverage)
        summary = (
            f"检索{kinds_note(policy)}: 命中 {coverage.hits} 条 / 入库 {coverage.ingested}, "
            f"可引用 {len(locatable)} 条 (仅摘要 {coverage.abstract_only}); "
            f"检索式 {len(coverage.queries)} 条"
        )
        if llm_note:
            summary += f" | 工具循环: {llm_note}"
        payload: dict[str, Any] = {
            "schema": "EvidenceBundle/v1",
            "topic": topic,
            "query": query,
            "policy": policy,
            "sources": evidence_items,
            "locatable": len(locatable),
            "coverage": _coverage_dict(coverage),
        }
        outcome = (TaskOutcome.completed if evidence_items
                   else TaskOutcome.partial)
        if outcome == TaskOutcome.completed:
            return self.completed(task, summary, changes=changes, needs=needs,
                                  unresolved=unresolved, usage=usage, payload=payload)
        return self.partial(
            task, summary + " | 没有命中任何来源 (不等于不存在, 只说明本资料范围内没有)",
            changes=changes, unresolved=[*unresolved, "本次没有命中可定位来源"],
            usage=usage, payload=payload)

    # ---- 确定性检索 ----
    def retrieve(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
                 *, topic: str, query: str, policy: str
                 ) -> tuple[list[dict[str, Any]], Any, list[str]] | None:
        """走 `kb/bridge.gather_sources`: 统一查询规划、入库、去重与覆盖记录。

        返回 `None` 表示"没有可检索的范围, 且未授权外搜" —— 调用方必须如实呈现,
        不能假装检索过。
        """
        from src.kb.bridge import gather_sources
        from src.kb.service import KnowledgeService
        from src.research.schemas import Claim, SourcePolicy

        try:
            parsed_policy = SourcePolicy(policy)
        except ValueError:
            parsed_policy = SourcePolicy.user_kb
        service = KnowledgeService(topic, create_if_missing=False) if topic else None
        usable = bool(service and service.usable)
        if not usable and parsed_policy == SourcePolicy.user_kb:
            return None

        claim = Claim(statement=query or task.objective, study=_study_from(context))
        try:
            items, coverage = gather_sources(
                service if usable else None, None, topic=topic, policy=parsed_policy,
                claim=claim, k=self.per_query, max_queries=self.max_queries,
                per_query=self.per_query)
        except Exception as e:  # noqa: BLE001 - 检索失败如实上报, 不静默成功
            runtime.emit("evidence_retrieval_failed",
                         {"task_id": task.task_id, "reason": str(e)})
            from src.research.schemas import RetrievalCoverage

            coverage = RetrievalCoverage(policy=parsed_policy)
            coverage.failures.append(f"检索执行失败: {e}")
            coverage.uncovered.append("检索未成功执行")
            return [], coverage, [f"检索执行失败: {e}"]
        rows = [_evidence_row(item) for item in items]
        unresolved: list[str] = []
        if not rows and coverage.executed:
            unresolved.append("本资料范围内没有命中 (不等于该结论不成立)")
        return rows, coverage, unresolved

    # ---- 需求 ----
    @staticmethod
    def _needs_from(evidence: list[dict[str, Any]], locatable: list[dict[str, Any]],
                    coverage: Any) -> list[ResearchNeed]:
        needs: list[ResearchNeed] = []
        unlocatable = [e for e in evidence if not e.get("locator")]
        if unlocatable:
            needs.append(ResearchNeed(
                kind=NeedKind.source_locator,
                statement=f"{len(unlocatable)} 条命中缺少可回到原文的定位",
                why="无可定位出处不得作为引用依据",
                acceptance=["给出页码/定理编号/字符范围之一, 或标注为不可引用"],
                hints={"count": len(unlocatable)},
            ))
        if not evidence and coverage.executed:
            needs.append(ResearchNeed(
                kind=NeedKind.more_sources,
                statement="检索执行过但没有命中",
                why="当前资料范围可能不含该主题",
                acceptance=["换检索式或扩大资料范围后重新检索, 或如实交付未检索到"],
            ))
        return needs

    # ---- 候选变更 ----
    def _evidence_proposal(self, task: AgentTask, item: dict[str, Any],
                           topic: str) -> ChangeProposal:
        return build_proposal(
            "evidence",
            payload={
                "title": item.get("title", ""),
                "source_id": item.get("source_id", ""),
                "locator": item.get("locator", ""),
                "excerpt": item.get("excerpt", ""),
                "relation": item.get("relation", "insufficient"),
                "topic": topic,
                "subquestion": task.subquestion,
            },
            rationale=item.get("note", "") or "检索候选材料 (支持关系待判定)",
            input_versions={},
        )


def _study_from(context: ContextPack):
    """从上下文里的模型/命题信息还原研究设计 (用于组合检索式)。

    只有在上下文确实给出处理/结果变量时才填 —— 无依据时留空, 不猜因果结构。
    """
    from src.research.schemas import StudyPlan

    for name in ("model", "claim"):
        for row in context.objects.get(name) or []:
            treatment = str(row.get("treatment", "") or "")
            outcome = str(row.get("outcome", "") or row.get("dependent", "") or "")
            if treatment or outcome:
                return StudyPlan(treatment=treatment, outcome=outcome)
    return StudyPlan()


def _evidence_row(item: Any) -> dict[str, Any]:
    relation = getattr(getattr(item, "support", None), "value", "insufficient")
    locator = getattr(item, "location", "") or ""
    return {
        "source_id": getattr(item, "source_id", "") or getattr(item, "id", ""),
        "title": clip(getattr(item, "title", "") or "", 200),
        "locator": locator,
        "page": int(getattr(item, "page", 0) or 0),
        "char_start": int(getattr(item, "char_start", -1) or -1),
        "char_end": int(getattr(item, "char_end", -1) or -1),
        "excerpt": clip(getattr(item, "excerpt", "") or "", 400),
        "relation": relation if isinstance(relation, str) else "insufficient",
        "credibility": str(getattr(getattr(item, "credibility", None), "value", "")
                           or getattr(item, "credibility", "") or ""),
        "source_kind": str(getattr(getattr(item, "source_kind", None), "value", "")
                           or getattr(item, "source_kind", "") or ""),
        "note": clip(getattr(item, "support_reason", "") or getattr(item, "notes", "") or "",
                     200),
    }


def _coverage_dict(coverage: Any) -> dict[str, Any]:
    return {
        "policy": str(getattr(getattr(coverage, "policy", None), "value", "") or ""),
        "executed": bool(getattr(coverage, "executed", False)),
        "queries": list(getattr(coverage, "queries", []) or []),
        "hits": int(getattr(coverage, "hits", 0) or 0),
        "ingested": int(getattr(coverage, "ingested", 0) or 0),
        "fulltext_available": int(getattr(coverage, "fulltext_available", 0) or 0),
        "abstract_only": int(getattr(coverage, "abstract_only", 0) or 0),
        "duplicates": int(getattr(coverage, "duplicates", 0) or 0),
        "uncovered": list(getattr(coverage, "uncovered", []) or []),
        "failures": list(getattr(coverage, "failures", []) or []),
        "scope_note": str(getattr(coverage, "scope_note", "") or ""),
    }


def _topic_of(task: AgentTask, context: ContextPack) -> str:
    if task.source_set_ids:
        return str(task.source_set_ids[0])
    for row in context.sources:
        candidate = str(row.get("source_set_id", "") or "")
        if candidate:
            return candidate
    return ""


def _query_of(task: AgentTask, context: ContextPack) -> str:
    """检索目标: 优先用**研究问题**本身, 而不是子任务措辞。

    为什么必须这样: 资料子问题的措辞是"收集支撑或反驳候选结论的可定位来源" —— 拿它
    当检索式, 在真实资料库里必然零命中 (实测: 命中 0 条, 研究因此卡住)。
    子问题的 `needs`/验收标准才是对检索**结果**的要求, 不是检索词。
    """
    hint = str((task.hints or {}).get("query", "") or "")
    if hint:
        return hint
    for row in context.objects.get("brief") or []:
        question = str(row.get("main_question", "") or "").strip()
        if question:
            return question
    request = str(context.request or "").strip()
    if request:
        return request
    return task_intent(task)


def kinds_note(policy: str) -> str:
    return f" (策略 {policy})"
