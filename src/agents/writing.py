from __future__ import annotations

"""WritingAgent 写作 (合并计划 §3.1 / §7.3)。

职责: 按 `WritingPacket` 设计大纲、写正文、按审阅意见局部修订。
提交: 版本化 `Manuscript`、块级论断映射、`WritingGap`、图表需求、修订说明。

合并要点 (§7.3):
- `outline_generator.run_outline_generation` + `paper_writer.run_paper_writing` 合并为
  这里的 plan/draft/revise 三种任务; 章节按**任务画像与论证需要**设计, 不套固定六章;
- `theory_writer.build_manuscript/trace_manuscript` + `writing_bridge.build_writing_inputs`
  的可复用部分是"事实与追溯映射", 收敛到 `publication/inputs.py`;
- **退役"双正文"主路径**: 主文由本角色形成, 确定性证书保留为附录块 (role=certificate)。

硬约束: 写作不研究。发现缺文献/缺依据时向主控提 `ResearchNeed`, 不自行补条件或
编造引用 (§3.1: "它们都向主控返回请求, 不越过任务权限改研究结论")。
"""

import re
from typing import Any

from src.agents.base import (
    AgentBase,
    as_list_of_str,
    clip,
    context_summary,
    extract_json,
    record_artifact,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    NeedKind,
    ObjectRef,
    ResearchNeed,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import latex_tool
from src.publication.schemas import (
    Block,
    BlockRole,
    Manuscript,
    ManuscriptStatus,
    RefKind,
    Section,
    WritingPacket,
)

__all__ = [
    "WritingAgent",
    "build_packet_from_context",
    "manuscript_from_snapshot",
    "render_markdown",
    "to_publication_manuscript",
    "write_main_manuscript",
]


class WritingAgent(AgentBase):
    """写作角色。"""

    role = "writing"
    prompt_version = "writing/v1"
    kinds = ("manuscript", "block")

    SYSTEM = """你是"写作"智能体。

你的任务: 把已经确定的研究结果写成**连贯的学术正文**, 并让每一段都能回到它的依据。

必须做到:
1. 先按论证需要设计章节 (不是套固定模板): 每个章节说明它承担哪一步论证;
2. 每个段落给出: 段落角色 (结论/论证/证据/证书/方法/局限/背景)、正文、以及它依据的
   对象 id (来源 / 命题 / 模型 / 图表 / 核验记录);
3. 引用只使用**给定的来源清单**; 清单里没有的来源一律不得出现。
   **正文里不要写 `[1]`、`[2]` 这类编号** —— 编号由渲染器按来源清单分配;
   来源清单为空时, 正文一个引用标记都不要写 (没有来源就不可能有引用);
4. 每一处结论都要在正文里写出它的对象 id (形如 `clm-…`), 否则无法与快照反查;
5. 未决项与局限必须如实写出来, 不得用模糊措辞掩盖;
6. 发现缺少依据时, 在 "needs" 里提出需求, 不要自己补数据或条件。

不得: 编造数值/引用/定理; 把非形式化论证写成"已证明"; 把案例类比写成普适结论。

最终输出 JSON:
{"title": "", "abstract": "",
 "sections": [{"heading": "", "role": "introduction|method|result|discussion|conclusion",
   "blocks": [{"role": "claim|reasoning|evidence|certificate|method|limitation|background|transition",
               "heading": "", "text": "", "ref_ids": [""], "math": "", "needs_check": false}]}],
 "unresolved": [""],
 "needs": [{"kind": "more_sources|manuscript_revision|figure_data", "statement": "", "why": "", "acceptance": [""]}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        return [latex_tool()]

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        packet = build_packet_from_context(task, context)
        if packet.is_revision() or _is_revision_task(task):
            packet.revision_of = packet.revision_of or str(
                (task.hints or {}).get("revision_of", "") or "")
        manuscript: Manuscript | None = None
        parse_note = ""

        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            manuscript, parse_note = self._draft_with_llm(task, context, runtime,
                                                         usage, packet)
        if manuscript is None:
            manuscript, fallback_note = self.deterministic_manuscript(task, packet)
            if parse_note:
                manuscript.gaps.append({
                    "kind": "writing_llm_unavailable", "detail": parse_note,
                    "blocking": False,
                })
            if fallback_note:
                manuscript.gaps.append({
                    "kind": "deterministic_draft", "detail": fallback_note,
                    "blocking": False,
                })

        # 出版必需结构在**稿件 IR 层**统一补齐, 而不是各起草路径各写一遍 (合并计划
        # §3.3 G17): 模型起草的稿件与确定性起草的稿件必须在同一套结构契约下,
        # 否则"预览/Markdown/PDF 同源同版"就只是对其中一条路径成立。
        finalise_manuscript(manuscript, packet)

        # 追溯与缺口检查 (零 LLM)
        problems = self.audit_manuscript(manuscript, packet)
        needs = self._needs_from(manuscript, packet, problems)
        changes = [ChangeProposal(
            kind="manuscript",
            object_id=packet.revision_of,
            payload=manuscript.to_dict(),
            rationale=f"第 {manuscript.version} 版稿件 ({len(manuscript.sections)} 节)",
            input_versions=dict(packet.input_versions),
            may_change_conclusion=False,
        )]
        artifact = record_artifact(
            kind="markdown", uri=f"research/{packet.project_id or 'proj'}/"
                                 f"{packet.problem_id or 'prob'}/manuscript-v{manuscript.version}.md",
            label=f"稿件 v{manuscript.version}",
            summary=clip(manuscript.abstract or manuscript.title, 200),
        )
        summary = (f"形成稿件 v{manuscript.version}: {len(manuscript.sections)} 节, "
                   f"{len(manuscript.all_blocks())} 段, {manuscript.word_count()} 字")
        if problems:
            summary += f"; {len(problems)} 处待核查"
        outcome = self.completed if not problems else self.partial
        result = outcome(task, summary, changes=changes, artifacts=[artifact],
                         needs=needs, unresolved=[*_gap_texts(manuscript), *problems[:5]],
                         usage=usage,
                         payload={"schema": "Manuscript/v1",
                                  "manuscript": manuscript.to_dict(),
                                  "trace": manuscript.trace()})
        return result

    # ---- LLM 起草 ----
    def _draft_with_llm(self, task: AgentTask, context: ContextPack,
                        runtime: AgentRuntime, usage: UsageRecord,
                        packet: WritingPacket) -> tuple[Manuscript | None, str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        source_lines = "\n".join(
            f"- id={s.get('source_id') or s.get('id')} | {clip(str(s.get('title', '')), 120)}"
            f" | 定位 {s.get('locator') or '(无)'}"
            for s in packet.sources[:40])
        claim_lines = "\n".join(
            f"- id={c.get('id')}@{c.get('version')} [{c.get('status', '')}] "
            f"{clip(str(c.get('statement', '')), 200)}"
            for c in packet.claims[:30])
        prompt = (
            f"# 研究任务\n{packet.main_question or packet.original_request}\n"
            f"# 子问题类型\n{', '.join(packet.subquestion_kinds) or '(未识别)'}\n"
            f"# 交付形态\n{', '.join(packet.deliverables) or '(未指定)'}\n"
            f"# 可用来源 (只能引用这些 id)\n{source_lines or '(没有可引用来源)'}\n"
            f"# 结论与版本\n{claim_lines or '(还没有结论)'}\n"
            f"# 未决项\n" + "\n".join(f"- {u}" for u in packet.unresolved[:15])
            + f"\n# 上下文\n{context_summary(context)}"
        )
        llm = runtime.llm(task, usage, stage="writing")
        try:
            result = llm.invoke([SystemMessage(content=self.SYSTEM),
                                 HumanMessage(content=prompt)])
            payload, note = extract_json(getattr(result, "content", ""))
        except Exception as e:  # noqa: BLE001 - 调用失败退到确定性起草
            return None, f"写作调用失败: {e}"
        if not isinstance(payload, dict):
            return None, note
        manuscript = _manuscript_from_payload(payload, packet)
        if manuscript is None:
            return None, "写作输出缺少可用的章节结构"
        return manuscript, ""

    # ---- 确定性起草 ----
    def deterministic_manuscript(self, task: AgentTask,
                                 packet: WritingPacket) -> tuple[Manuscript, str]:
        """无 LLM 时的**结构化**起草: 只渲染已登记的结果, 不生成新论断。

        它保证的是"可交付的骨架 + 完整追溯", 而不是"读起来像论文": 每一段都来自
        一条已登记的对象, 段末带上来源引用。这样 M1 在离线状态下也能跑通
        "检索→综合→写作→审阅"的闭环, 且交付物不自称有未经验证的内容。
        """
        manuscript = Manuscript(
            title=_title_of(packet),
            version=1,
            snapshot_id=packet.snapshot_id,
            input_versions=dict(packet.input_versions),
        )
        # 摘要必须是**如实的一句话**: 只说已判定的结论与范围, 不润色、不外推。
        # 没有明文摘要的稿件过不了出版门槛 ("缺少摘要"), 而用模型润色出来的摘要又会把
        # "候选"说成"结论" —— 因此这里由已登记对象直接生成 (零 LLM)。
        manuscript.abstract = _abstract_of(packet)
        intro = Section(heading="1 引言", role="introduction")
        intro.blocks.append(Block(
            role=BlockRole.background,
            text=f"本文研究以下问题: {clip(packet.main_question or packet.original_request, 800)}",
            input_versions=dict(packet.input_versions),
        ))
        if packet.subquestion_kinds:
            intro.blocks.append(Block(
                role=BlockRole.transition,
                text="本研究涉及的问题类型: " + "、".join(packet.subquestion_kinds)
                     + ("; 交付形态: " + "、".join(packet.deliverables)
                        if packet.deliverables else ""),
            ))
        # 关键词: 由任务画像的显式字段组成 (类型 + 交付形态), 不从正文里"提炼"——
        # 提炼就是生成叙事, 而这一路径的纪律是"只渲染已登记的东西"。
        keywords = [*packet.subquestion_kinds, *packet.deliverables]
        if keywords:
            intro.blocks.append(Block(
                role=BlockRole.transition,
                text="关键词: " + "; ".join(dict.fromkeys(keywords)),
            ))
        manuscript.sections.append(intro)

        evidence_section = Section(heading="2 资料与证据", role="method")
        if packet.sources:
            for index, source in enumerate(packet.sources[:20], 1):
                source_id = str(source.get("source_id") or source.get("id") or "")
                block = Block(
                    role=BlockRole.evidence,
                    text=(f"[{index}] {clip(str(source.get('title', '')), 200)}"
                          f" (定位: {source.get('locator') or '无定位'}; "
                          f"关系: {source.get('relation', 'insufficient')})"),
                    input_versions=dict(packet.input_versions),
                )
                if source_id:
                    block.add_ref(RefKind.source.value,
                                  ObjectRef(id=source_id,
                                            version=int(source.get("version", 1) or 1)))
                if not source.get("locator"):
                    block.needs_check = True
                evidence_section.blocks.append(block)
        else:
            evidence_section.blocks.append(Block(
                role=BlockRole.limitation,
                text="本次没有登记到可定位来源; 该状态如实记录, 不表示相关文献不存在。",
                needs_check=True,
            ))
        manuscript.sections.append(evidence_section)

        result_section = Section(heading="3 结果与论证", role="result")
        if packet.claims:
            for claim in packet.claims[:20]:
                claim_id = str(claim.get("id", "") or "")
                block = Block(
                    role=BlockRole.claim,
                    # 论断 id 必须出现在正文里: 出版门槛据此判断"核心结论能否反查",
                    # 只写块 id 是不够的 (块 id 回到快照, 但读者要按结论编号核对)。
                    text=(f"[{claim_id}] " if claim_id else "")
                         + clip(str(claim.get("statement", "")), 600),
                    input_versions=dict(packet.input_versions),
                    needs_check=str(claim.get("status", "")) not in ("supported",),
                )
                if claim_id:
                    block.add_ref(RefKind.claim.value,
                                  ObjectRef(id=claim_id,
                                            version=int(claim.get("version", 1) or 1)))
                for ref_id in as_list_of_str(claim.get("support_refs")):
                    block.add_ref(RefKind.source.value, ObjectRef(id=ref_id))
                reasoning = as_list_of_str(claim.get("reasoning"))
                result_section.blocks.append(block)
                if reasoning:
                    result_section.blocks.append(Block(
                        role=BlockRole.reasoning,
                        text="依据: " + "; ".join(clip(r, 200) for r in reasoning[:6]),
                        input_versions=dict(packet.input_versions),
                    ))
        else:
            result_section.blocks.append(Block(
                role=BlockRole.limitation,
                text="本次运行没有形成可陈述的结论; 这是未决状态, 不是零结果。",
                needs_check=True,
            ))
        manuscript.sections.append(result_section)

        if packet.models:
            model_section = Section(heading="4 模型与假设", role="method")
            for model in packet.models[:10]:
                model_section.blocks.append(Block(
                    role=BlockRole.method,
                    text=f"{clip(str(model.get('name', '')), 120)}: "
                         f"{clip(str(model.get('mechanism', '')), 400)}",
                    input_versions=dict(packet.input_versions),
                ))
            manuscript.sections.append(model_section)

        if packet.validations:
            validation_section = Section(heading="5 验证建议 (未执行)", role="discussion")
            for plan in packet.validations[:5]:
                validation_section.blocks.append(Block(
                    role=BlockRole.limitation,
                    text="验证方案建议: " + clip(str(plan.get("summary") or plan), 800)
                         + " (本轮未执行)",
                    needs_check=True,
                ))
            manuscript.sections.append(validation_section)

        if packet.unresolved:
            limit_section = Section(heading="6 未决项与局限", role="limitation")
            for item in packet.unresolved[:20]:
                limit_section.blocks.append(Block(role=BlockRole.limitation,
                                                  text=clip(item, 400)))
            manuscript.sections.append(limit_section)

        # 参考文献章节必须在位 (§5 阶段 1): 列表本身就来自已登记来源, 没有来源时
        # 也要**说明检索范围** —— 缺这一节时出版门槛判"不完整", 交付等级因此降级。
        ref_section = Section(heading="7 参考文献", role="references")
        if packet.sources:
            for index, source in enumerate(packet.sources[:20], 1):
                ref_section.blocks.append(Block(
                    role=BlockRole.evidence,
                    text=(f"[{index}] {clip(str(source.get('title', '')), 200)}. "
                          f"{clip(str(source.get('authors', '')), 120)} "
                          f"{str(source.get('year', '') or '')}. "
                          f"定位: {source.get('locator') or '无定位'} "
                          f"(来源 id: {source.get('source_id') or source.get('id') or '-'})"),
                    needs_check=not source.get("locator"),
                ))
        else:
            ref_section.blocks.append(Block(
                role=BlockRole.limitation,
                text=("本次运行没有可引用的可定位来源。检索范围与授权情况已记录在"
                      "交付清单中; 未命中不等于相关文献不存在。"),
                needs_check=True,
            ))
        manuscript.sections.append(ref_section)

        note = ("确定性起草: 只渲染已登记的结果与来源, 不生成新论断; "
                "正文未经过写作模型润色")
        manuscript.status = ManuscriptStatus.draft
        return manuscript, note

    # ---- 一致性审计 ----
    def audit_manuscript(self, manuscript: Manuscript,
                         packet: WritingPacket) -> list[str]:
        """块级追溯检查 (零 LLM)。

        查三件可判定的事: 引用是否都在给定来源清单里、结论块是否交代依据、
        是否出现了"已证明"这类越权措辞 (文本风险保留为**提示**, 不据此丢弃内容)。
        """
        known_sources = {str(s.get("source_id") or s.get("id") or "")
                         for s in packet.sources}
        known_claims = {str(c.get("id", "") or "") for c in packet.claims}
        problems: list[str] = []
        for block in manuscript.all_blocks():
            for kind, ref in zip(block.ref_kinds, block.refs):
                if kind == RefKind.source.value and ref.id and ref.id not in known_sources:
                    problems.append(f"块 {block.block_id} 引用了不在来源清单里的 {ref.id}")
                if kind == RefKind.claim.value and ref.id and ref.id not in known_claims:
                    problems.append(f"块 {block.block_id} 引用了不存在的命题 {ref.id}")
            if block.role == BlockRole.claim and not block.refs \
                    and not block.needs_check:
                problems.append(f"结论块 {block.block_id} 没有依据也未标为待核查")
            if _overclaims(block.text):
                problems.append(f"块 {block.block_id} 含越权措辞 (需改为条件性表述或补证书)")
        return problems

    # ---- 需求 ----
    def _needs_from(self, manuscript: Manuscript, packet: WritingPacket,
                    problems: list[str]) -> list[ResearchNeed]:
        needs: list[ResearchNeed] = []
        if not packet.sources:
            needs.append(ResearchNeed(
                kind=NeedKind.more_sources,
                statement="写作时没有可引用来源",
                why="没有依据的正文不能作为研究交付",
                acceptance=["给出可定位来源清单, 或明确交付为无依据的草稿"],
            ))
        if any("不在来源清单里" in p for p in problems):
            needs.append(ResearchNeed(
                kind=NeedKind.source_locator,
                statement="稿件引用了未登记来源",
                why="引用必须可定位到已登记来源",
                acceptance=["补齐来源登记或删除该引用"],
            ))
        if any("越权措辞" in p for p in problems):
            needs.append(ResearchNeed(
                kind=NeedKind.derivation,
                statement="正文含未被证书支撑的强结论",
                why="结论强度必须与证书一致",
                acceptance=["给出证书或把表述降为条件性"],
            ))
        return needs


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------
def _abstract_of(packet: WritingPacket) -> str:
    """由已登记对象生成**如实**的摘要 (零 LLM)。

    它只陈述两件事: 研究了什么, 以及当前登记到的结论状态。绝不把候选说成结论 ——
    这个函数是降级路径的一部分, 而"没有模型就把候选写成定论"正是最危险的那种失败。
    """
    settled = [c for c in packet.claims
               if str(c.get("status", "")) in ("supported", "refuted")]
    pending = [c for c in packet.claims
               if str(c.get("status", "")) not in ("supported", "refuted")]
    parts = [f"本文研究: {clip(packet.main_question or packet.original_request, 400)}。"]
    if settled:
        for claim in settled[:3]:
            status = "已确证" if str(claim.get("status")) == "supported" else "已被反例否决"
            parts.append(f"当前登记到 {len(settled)} 条{status}的结论, 例如: "
                         f"{clip(str(claim.get('statement', '')), 200)}。")
    if pending:
        parts.append(f"另有 {len(pending)} 条结论尚未定论, 正文按候选状态如实标注。")
    if not settled and not pending:
        parts.append("本次运行未登记到可陈述的结论; 该状态如实记录, 不是零结果。")
    if packet.unresolved:
        parts.append(f"存在 {len(packet.unresolved)} 项未决事宜, 见末节。")
    return "".join(parts)


def _references_section(packet: WritingPacket) -> Section:
    """参考文献章节 (列表来自已登记来源; 没有来源时**说明检索范围**)。

    为什么这一节不能省 (§5 阶段 1): 缺它时出版门槛判"不完整", 交付等级因此降级;
    而没有来源时更不能凭空省掉 —— 必须让读者看到"这次没有可引用来源"而不是"作者忘了"。
    """
    section = Section(heading="7 参考文献", role="references")
    if packet.sources:
        for index, source in enumerate(packet.sources[:20], 1):
            section.blocks.append(Block(
                role=BlockRole.evidence,
                text=(f"[{index}] {clip(str(source.get('title', '')), 200)}. "
                      f"{clip(str(source.get('authors', '')), 120)} "
                      f"{str(source.get('year', '') or '')}. "
                      f"定位: {source.get('locator') or '无定位'} "
                      f"(来源 id: {source.get('source_id') or source.get('id') or '-'})"),
                needs_check=not source.get("locator"),
            ))
    else:
        section.blocks.append(Block(
            role=BlockRole.limitation,
            text=("本次运行没有可引用的可定位来源。检索范围与授权情况已记录在"
                  "交付清单中; 未命中不等于相关文献不存在。"),
            needs_check=True,
        ))
    return section


def finalise_manuscript(manuscript: Manuscript, packet: WritingPacket,
                        *, max_citations: int = 40) -> list[str]:
    """把稿件补齐到**出版必需结构** (幂等, 零 LLM)。返回补充说明。

    补的四件事都来自真实存在的对象或正文, 不生成新论断:
    1. **摘要** (缺失时由已登记结论如实生成);
    2. **关键词** (由任务画像的显式字段组成);
    3. **参考文献章节** (来自已登记来源; 无来源时说明检索范围);
    4. **论断锚点**: 带 claim 引用的块, 正文里必须出现该 claim id —— 出版门槛与
       追溯视图都据此判断"核心结论能否反查"。

    另外**如实标出**正文里指向文献表之外的引用编号: 模型写出的 `[1][2]` 如果文献表里
    没有对应条目, 那不是格式问题而是"编造引用", 必须留在未决项里让审阅与人工看到。
    """
    notes: list[str] = []
    if not (manuscript.abstract or "").strip():
        manuscript.abstract = _abstract_of(packet)
        notes.append("补写摘要 (由已登记结论如实生成)")

    blocks = manuscript.all_blocks()
    has_keywords = any("关键词" in (b.text or "") for b in blocks)
    if not has_keywords:
        keywords = [*packet.subquestion_kinds, *packet.deliverables]
        if keywords:
            target = manuscript.sections[0] if manuscript.sections else None
            if target is None:
                target = Section(heading="1 引言", role="introduction")
                manuscript.sections.append(target)
            target.blocks.append(Block(
                role=BlockRole.transition,
                text="关键词: " + "; ".join(dict.fromkeys(keywords))))
            notes.append("补关键词 (来自任务画像)")

    has_references = any("参考文献" in (s.heading or "")
                         for s in manuscript.sections)
    if not has_references:
        manuscript.sections.append(_references_section(packet))
        notes.append("补参考文献章节")

    # 论断锚点: 正文里出现 claim id 才能被反查 (块 id 只回到快照, 不是结论编号)。
    # 两种情况都要处理:
    #   1. 块已有 claim 引用 → 正文里补上 id;
    #   2. 正文里**已经写了** id 但块没有引用记录 → 补上引用 (这是从正文里读出来的
    #      关联, 不是新造的): 模型起草时常把 [clm-…] 写进正文却忘了登记引用,
    #      于是"结论已写入正文"在 `writing_map` 里看不见, 交付门槛判为未映射。
    known_claims = {str(c.get("id", "")) for c in packet.claims if c.get("id")}
    for block in manuscript.all_blocks():
        text = block.text or ""
        existing = {ref.id for kind, ref in zip(block.ref_kinds, block.refs)
                    if kind == RefKind.claim.value}
        for claim_id in sorted(known_claims):
            if claim_id in text and claim_id not in existing:
                block.add_ref(RefKind.claim.value, ObjectRef(id=claim_id))
                existing.add(claim_id)
        for claim_id in sorted(existing):
            if claim_id and claim_id not in text:
                block.text = f"[{claim_id}] " + text
                text = block.text

    # 编造引用检测: 正文里的 [n] 必须在文献表里存在
    listed = _reference_numbers(manuscript)
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", _manuscript_text(manuscript))}
    dangling = sorted(n for n in cited if n not in listed and n <= max_citations)
    if dangling and packet.sources:
        notes.append(f"正文引用了文献表之外的编号 {dangling}: 按未决项处理, 不当作已核实引用")
        manuscript.gaps.append({
            "kind": "unresolved_citation",
            "detail": f"正文出现文献表之外的引用编号 {dangling}",
            "blocking": False,
        })
    return notes


def _manuscript_text(manuscript: Manuscript) -> str:
    return "\n".join(b.text or "" for b in manuscript.all_blocks())


def _reference_numbers(manuscript: Manuscript) -> set[int]:
    """文献表里实际存在的编号 (按 `[n]` 行首计; 我们的文献表就是这种形态)。"""
    numbers: set[int] = set()
    in_references = False
    for section in manuscript.sections:
        if "参考文献" in (section.heading or ""):
            in_references = True
            continue
        if not in_references:
            continue
        for block in section.blocks:
            for match in re.finditer(r"(?:^|\s)\[(\d+)\]", block.text or ""):
                numbers.add(int(match.group(1)))
    return numbers


def _is_revision_task(task: AgentTask) -> bool:
    return "修订" in task.objective or "revise" in task.objective.lower()


def _gap_texts(manuscript: Manuscript) -> list[str]:
    """把写作缺口渲染成可展示的未决项 (回流给主控)。"""
    return [clip(str(g.get("detail") or g.get("kind") or ""), 300)
            for g in manuscript.gaps if isinstance(g, dict)]


def _title_of(packet: WritingPacket) -> str:
    """稿件标题: 用主问题的一句话, 并**去掉 Markdown 标记**。

    题面常常以 Markdown 标题给出 (`# Problem 2：…`), 直接当标题会渲染成
    `# # Problem 2…` —— 用户看到的是两个井号。这里只做清洗, 不重写内容。
    """
    raw = (packet.main_question or packet.original_request or "研究报告").strip()
    first = raw.splitlines()[0] if raw else "研究报告"
    cleaned = first.lstrip("#").strip().strip("*_`").strip()
    return clip(cleaned or "研究报告", 200)
    question = packet.main_question or packet.original_request or "研究报告"
    first = re.split(r"[。\n]", question.strip())[0]
    return clip(first, 80) or "研究报告"


_OVERCLAIM_MARKERS = ("已证明", "已经证明", "严格证明", "证明了", "qed", "QED",
                      "必定成立", "必然成立")


def _overclaims(text: str) -> bool:
    body = str(text or "")
    return any(marker in body for marker in _OVERCLAIM_MARKERS)


def _manuscript_from_payload(payload: dict[str, Any],
                             packet: WritingPacket) -> Manuscript | None:
    sections_payload = payload.get("sections")
    if not isinstance(sections_payload, list) or not sections_payload:
        return None
    manuscript = Manuscript(title=str(payload.get("title") or _title_of(packet)),
                            abstract=str(payload.get("abstract") or ""),
                            snapshot_id=packet.snapshot_id,
                            input_versions=dict(packet.input_versions))
    for index, section_payload in enumerate(sections_payload, 1):
        if not isinstance(section_payload, dict):
            continue
        section = Section(heading=str(section_payload.get("heading")
                                      or f"{index} 未命名章节"),
                          role=str(section_payload.get("role") or ""), level=1)
        blocks = section_payload.get("blocks")
        for block_payload in (blocks if isinstance(blocks, list) else []):
            if not isinstance(block_payload, dict):
                continue
            role = str(block_payload.get("role") or "reasoning")
            if role not in {r.value for r in BlockRole}:
                role = "reasoning"
            block = Block(role=BlockRole(role),
                          heading=str(block_payload.get("heading") or ""),
                          text=str(block_payload.get("text") or ""),
                          math=str(block_payload.get("math") or ""),
                          needs_check=bool(block_payload.get("needs_check", False)),
                          input_versions=dict(packet.input_versions))
            for ref_id in as_list_of_str(block_payload.get("ref_ids")):
                kind = (RefKind.claim.value
                        if ref_id in {str(c.get("id", "")) for c in packet.claims}
                        else RefKind.source.value)
                block.add_ref(kind, ObjectRef(id=ref_id))
            section.blocks.append(block)
        if section.blocks:
            manuscript.sections.append(section)
    if not manuscript.sections:
        return None
    for item in as_list_of_str(payload.get("unresolved")):
        manuscript.gaps.append({"kind": "unresolved", "detail": clip(item, 300),
                                "blocking": False})
    return manuscript


def build_packet_from_context(task: AgentTask, context: ContextPack) -> WritingPacket:
    """从上下文组装 `WritingPacket` (合并计划 §3.1)。

    结果快照只带**引用与摘要**: 正文留在产物文件里, 不复制一份可写状态 (§6.1)。
    """
    brief = (context.objects.get("brief") or [{}])[0]
    evidence = context.objects.get("evidence") or []
    claims = context.objects.get("claim") or []
    models = context.objects.get("model") or []
    validations = context.objects.get("validation_plan") or []
    verification = context.objects.get("verification") or []
    unresolved: list[str] = list(brief.get("unknown_fields") or [])
    for row in context.objects.get("gap") or []:
        unresolved.append(clip(str(row.get("statement", "")), 300))
    return WritingPacket(
        project_id=task.project_id, problem_id=task.problem_id, run_id=task.run_id,
        original_request=context.request,
        main_question=str(brief.get("main_question") or context.request),
        subquestion_kinds=[str(s.get("kind", "")) for s in (brief.get("subquestions") or [])
                           if isinstance(s, dict)],
        deliverables=as_list_of_str(brief.get("deliverables")),
        output_language=str(brief.get("output_language") or ""),
        claims=claims, models=models, validations=validations, verifications=verification,
        sources=evidence,
        figures=context.objects.get("figure") or [],
        review_issues=[i for r in context.upstream for i in (r.get("issues") or [])],
        unresolved=[u for u in unresolved if u],
        snapshot_id=str(brief.get("snapshot_id") or ""),
        input_versions={str(row.get("id", "")): int(row.get("version", 1) or 1)
                        for row in [*claims, *models] if row.get("id")},
    )


def render_markdown(manuscript: Manuscript) -> str:
    """把稿件渲染为 Markdown (保守: 不执行任何来源文本, 编号在渲染时分配)。"""
    from src.publication.schemas import assign_render_numbers

    numbers = assign_render_numbers(manuscript)
    lines: list[str] = [f"# {manuscript.title}", ""]
    if manuscript.abstract:
        lines += ["## 摘要", "", manuscript.abstract, ""]
    for section in manuscript.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        for block in section.blocks:
            text = block.text
            if block.math:
                lines += ["", f"$$ {block.math} $$", ""]
            if text:
                # 追溯锚点: 块 id 必须出现在正文里, 否则"从论断回到快照对象"只能靠猜
                # (§14.3 可反查性判据: 锚点出现在正文中)。放在 HTML 注释里, 不干扰阅读。
                lines.append(text)
                lines.append("")
                lines.append(f"<!-- block:{block.block_id} -->")
                lines.append("")
            marks = [f"[{numbers['citations'][ref.id]}]"
                     for kind, ref in zip(block.ref_kinds, block.refs)
                     if kind == RefKind.source.value and ref.id in numbers["citations"]]
            if marks:
                lines.append("依据: " + " ".join(marks))
                lines.append("")
    return "\n".join(lines).strip() + "\n"


# ----------------------------------------------------------------------
# 主文统一入口 (合并计划 §7.3: 退役"双正文"主路径)
# ----------------------------------------------------------------------
_ROLE_BY_KIND: dict[str, BlockRole] = {
    "heading": BlockRole.transition,
    "prose": BlockRole.reasoning,
    "equation": BlockRole.certificate,
    "proof": BlockRole.certificate,
    "theorem": BlockRole.claim,
    "lemma": BlockRole.claim,
    "proposition": BlockRole.claim,
    "corollary": BlockRole.claim,
    "citation": BlockRole.evidence,
    "figure": BlockRole.figure_ref,
    "table": BlockRole.table,
}

#: 这些块类型承载"结论文本", 其 label 就是快照里的命题 id。
CLAIM_KINDS = frozenset({"theorem", "lemma", "proposition", "corollary"})


def manuscript_from_snapshot(snapshot: Any, topic: str = "",
                             delivery_level: str = "") -> tuple[Any, dict[str, str]]:
    """由冻结快照确定性起草正文 (零 LLM), 返回 (快照稿件, 写作映射)。

    这是 WritingAgent 的**降级路径**: 无 LLM、调用失败或输出不可解析时走它。
    它保证"可交付骨架 + 完整追溯": 每段都来自一条已登记对象, 不生成新论断。

    返回的是快照渲染器的稿件对象 (`rag.theory_render.Manuscript`): 它与现有交付链
    (Markdown/LaTeX 渲染、可反查性检查) 直接兼容, 因此离线交付**逐字不变**。
    需要出版块表示时用 `to_publication_manuscript()` 转换。
    """
    from src.agents.theory_writer import build_manuscript as _snapshot_render

    legacy = _snapshot_render(snapshot, topic, delivery_level=delivery_level)
    return legacy, dict(getattr(legacy, "writing_map", {}) or {})


def to_publication_manuscript(snapshot_manuscript: Any, snapshot: Any = None
                              ) -> Manuscript:
    """把快照渲染的稿件转成出版块表示 (供团队/追溯视图与产物级校对使用)。

    转换只改变表示, 不改变文本: 每个块的 `text` 逐字保留, 结论块的 label (命题 id)
    变成 `claim` 引用, 于是"从段落回到冻结快照对象"在两种表示下都成立。
    """
    manuscript = Manuscript(
        title=str(getattr(snapshot_manuscript, "title", "") or ""),
        version=1,
        snapshot_id=str(getattr(snapshot, "snapshot_id", "") or ""),
        input_versions={
            str(getattr(c, "id", "")): int(getattr(c, "version", 1) or 1)
            for c in getattr(snapshot, "claims", []) if getattr(c, "id", "")},
    )
    section = Section(heading="正文", role="result")
    # **块锚点必须确定性**: `Block.block_id` 默认是随机 id, 于是"同一份冻结快照渲染两次"
    # 会得到不同的正文锚点 —— 离线交付不再是可复现的 (同一输入两次产物不同), 版本之间
    # 也无法按锚点对齐。这里按**位置**给稳定 id (文档序), 只对"由快照确定性渲染"这条
    # 路径负责; 模型起草的稿件保留它自己的 id (内容本来就不同)。
    position = 0
    for block in getattr(snapshot_manuscript, "blocks", []):
        kind = str(getattr(block, "kind", "") or "prose")
        text = str(getattr(block, "text", "") or "")
        label = str(getattr(block, "label", "") or "")
        title = str(getattr(block, "title", "") or "")
        if kind == "heading":
            if section.blocks:
                manuscript.sections.append(section)
            section = Section(heading=text or "正文", role="")
            continue
        chunk = Block(role=_ROLE_BY_KIND.get(kind, BlockRole.reasoning),
                      heading=title, text=text,
                      block_id=f"blk-{position}",
                      input_versions=dict(manuscript.input_versions))
        position += 1
        if kind == "equation":
            chunk.math = text
            chunk.text = ""
        if label and kind in CLAIM_KINDS:
            chunk.add_ref(RefKind.claim.value, ObjectRef(id=label))
        section.blocks.append(chunk)
    if section.blocks:
        manuscript.sections.append(section)
    return manuscript


def write_main_manuscript(
    snapshot: Any,
    topic: str = "",
    delivery_level: str = "",
    *,
    task: AgentTask | None = None,
    context: ContextPack | None = None,
    runtime: AgentRuntime | None = None,
    usage: UsageRecord | None = None,
    agent: WritingAgent | None = None,
) -> tuple[Any, str, dict[str, str], str]:
    """**唯一的正文生产入口** (合并计划 §7.3: 退役"双正文"主路径)。

    返回 `(稿件, Markdown, writing_map, note)`, 其中稿件是
    `rag.theory_render.Manuscript` —— 交付链 (LaTeX 渲染、可反查性检查) 只认这一种。

    两种执行方式, 同一个生产者:
    - 有 LLM 与运行时 → WritingAgent 起草 (提示词要求逐段给出依据对象 id),
      再把出版块稿件**复原成**快照稿件表示;
    - 无 LLM (离线/未授权) 或模型输出不可解析/调用失败 → 由冻结快照确定性起草,
      输出与旧渲染器**逐字一致**, 降级原因写进 `note`。

    这样"离线复现"与"模型起草"不会退化成两套实现: 前者就是后者的降级路径。
    """
    writer = agent or WritingAgent()
    reasons: list[str] = []
    if runtime is not None and task is not None and context is not None:
        try:
            if runtime.llm_available():
                packet = build_packet_from_context(task, context)
                drafted, parse_note = writer._draft_with_llm(  # noqa: SLF001 - 同一模块内复用
                    task, context, runtime, usage or UsageRecord(), packet)
                if drafted is not None:
                    legacy = _snapshot_manuscript_from_blocks(drafted)
                    unified = to_publication_manuscript(legacy, snapshot)
                    # **Markdown 与 LaTeX 从同一份稿件出来** (§3.3 G17): 此前 Markdown
                    # 走旧快照 IR, 而交付的 .tex/.pdf 走唯一 IR —— 同一次运行的两份产物
                    # 来自两种表示, "同源同版"只能靠人工比。现在两者都是 `Manuscript`。
                    return (unified, render_markdown(unified),
                            _writing_map_of(drafted), "由写作智能体起草 (LLM)")
                if parse_note:
                    reasons.append(parse_note)
        except Exception as e:  # noqa: BLE001 - 写作失败必须降级而不是中断交付
            reasons.append(f"写作智能体调用失败: {type(e).__name__}: {e}")

    manuscript, writing_map = manuscript_from_snapshot(snapshot, topic, delivery_level)
    unified = to_publication_manuscript(manuscript, snapshot)
    note = "确定性起草 (由冻结快照渲染, 未经过写作模型润色)"
    if reasons:
        note += "; 降级原因: " + "; ".join(reasons)
    return unified, render_markdown(unified), writing_map, note


def _snapshot_manuscript_from_blocks(manuscript: Manuscript) -> Any:
    """把 WritingAgent 的出版块稿件**复原**为快照稿件表示 (交付链的唯一输入)。"""
    from src.rag.theory_render import Block as SnapshotBlock
    from src.rag.theory_render import Manuscript as SnapshotManuscript

    _kind_by_role = {
        BlockRole.claim.value: "proposition",
        BlockRole.certificate.value: "prose",
        BlockRole.evidence.value: "prose",
        BlockRole.definition.value: "prose",
        BlockRole.method.value: "prose",
        BlockRole.limitation.value: "prose",
        BlockRole.background.value: "prose",
        BlockRole.transition.value: "prose",
        BlockRole.figure_ref.value: "prose",
        BlockRole.table.value: "prose",
        BlockRole.reasoning.value: "prose",
    }
    out = SnapshotManuscript(title=manuscript.title or "",
                             writing_map=_writing_map_of(manuscript))
    for section in manuscript.sections:
        out.blocks.append(SnapshotBlock("heading", section.heading or "正文"))
        for block in section.blocks:
            kind = _kind_by_role.get(block.role.value, "prose")
            label = ""
            for ref_kind, ref in zip(block.ref_kinds, block.refs):
                if ref_kind == RefKind.claim.value:
                    label = ref.id
                    break
            text = block.text or (f"$$ {block.math} $$" if block.math else "")
            out.blocks.append(SnapshotBlock(kind, text, label=label,
                                            title=block.heading or ""))
    return out


def _writing_map_of(manuscript: Manuscript) -> dict[str, str]:
    """从**已生成的稿件**反推 writing_map (claim id → 正文锚点)。

    锚点取块的 `block_id`: 渲染器把它写进正文, 因此"锚点必须出现在正文里"
    这条可反查判据对模型起草同样成立。
    """
    mapping: dict[str, str] = {}
    for block in manuscript.all_blocks():
        for kind, ref in zip(block.ref_kinds, block.refs):
            if kind == RefKind.claim.value and ref.id not in mapping:
                mapping[ref.id] = block.block_id
    return mapping
