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
        observations: list[Any] = []
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
        # 新颖性对照在**检索之前**算: 它不依赖本次检索结果, 而且"没有检索能力/没比过"
        # 本身就是要写进交付物的结论 —— 放在检索之后会让"检索受阻"的运行连一条
        # `unchecked` 记录都没有, 交付物于是看起来没有新颖性问题 (§5.4 / package.py)。
        novelty_changes = self._novelty_records(task, context, [])
        selected_queries, search_cache = _observed_searches(observations, policy)
        collected = self.retrieve(task, context, runtime, topic=topic, query=query,
                                  policy=policy, selected_queries=selected_queries,
                                  search_cache=search_cache)
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
                changes=novelty_changes,
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
                usage=usage,
                changes=novelty_changes)

        locatable = [e for e in evidence_items if e.get("locator")]
        changes = [self._evidence_proposal(task, e, topic) for e in evidence_items]
        # 证据归属 (§6.1): 证据必须绑定到**具体命题版本**, 否则同一批证据会被所有命题
        # 共享 —— "这条来源支持哪个结论、适用条件是什么"就无从回答。这里由检索角色
        # 提交 `evidence_link` 候选 (relation 来自它自己的判定, 不是猜)。
        links = self._evidence_links(task, evidence_items, context)
        changes.extend(links)
        # 新颖性对照 (§5.4): 交付包与出版层按它决定"能不能宣称原创"。**必须留下记录**
        # —— 没有检索能力时也落一条 `unchecked` 并写明原因, 否则交付物看起来像
        # "没有新颖性问题"。判定由 `research/novelty.py` 做 (零 LLM), 这里只提供
        # 对照来源 (本地库优先, 否则外部检索)。
        changes.extend(novelty_changes)
        needs = self._needs_from(evidence_items, locatable, coverage)
        for link in links:
            payload = link.payload
            if payload.get("relation") != "contradicts" or not payload.get("locator"):
                continue
            claim_ref = payload.get("claim_ref") or {}
            claim_id = str(claim_ref.get("id") or "")
            if not claim_id:
                continue
            source_id = str((payload.get("source_ref") or {}).get("id") or "")
            needs.append(ResearchNeed(
                kind=NeedKind.counterexample,
                statement=f"复核文献与命题 {claim_id} 的冲突并修订推导",
                why="可定位的反证材料使既有结论不能继续视为已支持",
                blocked_refs=[claim_ref],
                acceptance=["解释冲突的适用条件，重新核验或明确保留未决状态"],
                hints={"trigger_id": f"contradiction:{claim_id}:{claim_ref.get('version')}:{source_id}"},
                blocking=True,
            ))
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
                 *, topic: str, query: str, policy: str,
                 selected_queries: list[str] | None = None,
                 search_cache: dict[str, list[dict]] | None = None,
                 ) -> tuple[list[dict[str, Any]], Any, list[str]] | None:
        """走 `kb/bridge.gather_sources`: 统一查询规划、入库、去重与覆盖记录。

        返回 `None` 表示"没有可检索的范围, 且未授权外搜" —— 调用方必须如实呈现,
        不能假装检索过。
        """
        from src.kb import bridge
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
            def observed_or_search(query_text: str, limit: int) -> list[dict]:
                if search_cache and query_text in search_cache:
                    return search_cache[query_text][:limit]
                default = bridge._default_search_fn()
                if default is None:
                    raise RuntimeError("外部检索能力不可用")
                return default(query_text, limit)

            items, coverage = bridge.gather_sources(
                service if usable else None, None, topic=topic, policy=parsed_policy,
                claim=claim, k=self.per_query, max_queries=self.max_queries,
                per_query=self.per_query, extra_queries=selected_queries,
                search_fn=(observed_or_search if search_cache else None))
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
            # 归属关系以 source_id 引用这份材料，权威对象必须使用同一个 ID；
            # 否则 link.source_ref 指向外部 ID，而库里只有自动生成的 eviobj-*。
            object_id=str(item.get("source_id") or ""),
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

    def _novelty_records(self, task: AgentTask, context: ContextPack,
                         items: list[dict[str, Any]]) -> list[ChangeProposal]:
        """为上下文里的命题落**新颖性对照记录** (零 LLM 判定)。

        适用范围: 只对**结论类**命题做 (工作台与出版层关心的是"这个结论新不新"),
        不为义务/来源做 —— 那两者的"新"没有意义。
        """
        from src.research.novelty_service import assess_novelty_for

        rows = [row for row in (context.objects.get("claim") or []) if row.get("id")]
        if not rows:
            return []
        service = self._knowledge_service(task, context)
        out: list[ChangeProposal] = []
        for row in rows[:4]:
            claim = _parse_claim(row)
            if claim is None:
                continue
            evidence = [e for e in items if str(e.get("claim_id", "")) == claim.id]
            try:
                record, scope = assess_novelty_for(
                    claim, service=service, evidence=evidence,
                    evidence_scope=[str(s.get("source_set_id", ""))
                                    for s in context.sources
                                    if s.get("source_set_id")],
                    source_policy=task.source_policy or "user_kb")
            except Exception:  # noqa: BLE001 - 对照失败不得吞掉检索成果
                continue
            # **确定性 id 必须写进记录本身**, 不只是存储键: 否则对象库里的键是
            # `nov-<claim>-v<n>` 而载荷里的 `id` 是另一个随机值, 交付包引用哪个都说不清。
            novelty_id = f"nov-{claim.id}-v{claim.version}"
            record = record.model_copy(update={"id": novelty_id})
            out.append(build_proposal(
                "novelty",
                payload=record.model_dump(mode="json"),
                object_id=novelty_id,
                rationale=(f"新颖性对照 ({scope.get('kind') or 'none'}): "
                           f"{record.status.value}"),
                input_versions={claim.id: claim.version},
            ))
        return out

    def _knowledge_service(self, task: AgentTask, context: ContextPack):
        """有可用本地知识底座时返回它 (只探测已存在的库, 不创建空库)。"""
        topic = _topic_of(task, context)
        if not topic:
            return None
        from src.kb.service import KnowledgeService

        try:
            service = KnowledgeService(topic, create_if_missing=False)
        except Exception:  # noqa: BLE001 - 探测失败按"没有本地库"处理
            return None
        return service if getattr(service, "usable", False) else None

    def _evidence_links(self, task: AgentTask, items: list[dict[str, Any]],
                        context: ContextPack) -> list[ChangeProposal]:
        """把命中材料绑定到上下文里的命题 (带版本与适用条件)。

        与 `_evidence_proposal` 的分工: 前者登记**材料本身**, 这里登记**材料与本命题
        的关系**。两者都要有 —— 只登记材料时, 同一批证据会"支持"所有命题; 只登记关系
        时又找不到原文。关系强度按检索角色自己的判定写, 不确定就写 `insufficient`
        (命中不等于支持), 绝不因为"是检索到的"就默认支持。

        条数设上限: 命题 × 材料的笛卡尔积很容易膨胀, 而交付包里的证据链只需要
        "每条命题有哪些可定位依据"。
        """
        from src.research.schemas import (
            EvidenceLink,
            ObjectRef,
            SupportKindOfEvidence,
            ValidationStatus,
        )

        claims = [row for row in (context.objects.get("claim") or [])
                  if row.get("id")]
        if not claims or not items:
            return []
        valid = {kind.value for kind in SupportKindOfEvidence}
        out: list[ChangeProposal] = []
        for claim_row in claims[:8]:
            claim_id = str(claim_row.get("id", ""))
            claim_version = int(claim_row.get("version", 1) or 1)
            for item in items[:12]:
                source_id = str(item.get("source_id") or "")
                if not source_id:
                    continue
                target_id = str(item.get("claim_id") or "")
                if target_id and target_id != claim_id:
                    continue
                relation = str(item.get("relation", "insufficient") or "insufficient")
                if relation not in valid:
                    relation = "insufficient"
                # 一份材料若未明确绑定命题，不能把它对检索问题的关系复制给多个结论。
                if not target_id and len(claims) > 1:
                    relation = "insufficient"
                link = EvidenceLink(
                    claim_ref=ObjectRef(id=claim_id, version=claim_version),
                    source_ref=ObjectRef(id=source_id or str(item.get("id", "")), version=1),
                    relation=SupportKindOfEvidence(relation),
                    excerpt=str(item.get("excerpt", "") or "")[:300],
                    locator=str(item.get("locator", "") or ""),
                    # 只有真的支持本命题时才说"条件匹配"; 其余情况不得默认成立
                    condition_match=relation in ("supports", "partially_supports"),
                    condition_notes=str(item.get("note", "") or ""),
                    review_status=(ValidationStatus.unchecked
                                   if relation == "insufficient"
                                   else ValidationStatus.verified),
                    reviewer="evidence",
                )
                out.append(build_proposal(
                    "evidence_link",
                    payload=link.model_dump(mode="json"),
                    object_id=link.id,
                    rationale=(f"证据 {source_id or '-'} → 命题 {claim_id} "
                               f"({relation})"),
                    input_versions={claim_id: claim_version},
                ))
                if len(out) >= 40:
                    return out
        return out


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


def _parse_claim(row: dict[str, Any]):
    """上下文里的命题行 → `Claim`; 结构不完整时返回 `None` (不猜)。"""
    from src.research.schemas import Claim

    try:
        return Claim.model_validate({k: v for k, v in row.items()
                                     if not k.startswith("_")})
    except Exception:  # noqa: BLE001 - 不完整的行不参与新颖性对照
        return None


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


def _observed_searches(observations: list[Any], policy: str
                       ) -> tuple[list[str], dict[str, list[dict]]]:
    """把模型实际调用过的检索式和原始命中交给统一检索桥。

    只接受成功的检索工具调用；模型正文中的任意引用或失败工具的文本不能冒充来源。
    `user_kb` 只采纳本地查询，绝不把外部观察混进未授权资料范围。
    """
    external = {"search_all_sources", "arxiv_search", "openalex_search",
                "semantic_scholar_search"}
    queries: list[str] = []
    cached: dict[str, list[dict]] = {}
    for observation in observations:
        if getattr(observation, "status", "") != "ok":
            continue
        name = str(getattr(observation, "name", "") or "")
        if name not in external | {"search_local_kb"}:
            continue
        if name in external and policy not in {"autonomous", "both"}:
            continue
        query = str((getattr(observation, "arguments", {}) or {}).get("query") or "").strip()
        if not query or query in queries:
            continue
        queries.append(query[:300])
        if name in external:
            rows = getattr(observation, "result", None)
            if isinstance(rows, list):
                cached[query[:300]] = [row for row in rows if isinstance(row, dict)]
        if len(queries) >= 4:
            break
    return queries, cached


def kinds_note(policy: str) -> str:
    return f" (策略 {policy})"
