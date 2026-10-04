from __future__ import annotations

import json
import os

"""理论研究循环图 (P0-P3)。

外层使用 LangGraph + 检查点; 内部为 研究循环 (formulate → decide → act → verify → update)。
每个节点从版本化存储重建研究状态 (可断点恢复), 预算与决策记录持久化在 runtime 对象中。
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from src.agents.theory_writer import build_manuscript, render_research_report, run_theory_writing
from src.graph.theory_state import TheoryState
from src.rag.theory_render import render_latex
from src.research.acceptance import classify_deliverable, delivery_gate, theory_validity_gate
from src.research.input_snapshot import (
    InputSnapshotRejected,
    SnapshotCheck,
    merge_verification,
    verify_input_snapshot,
)
from src.research.loop import ResearchBudget, TheoryEngine
from src.research.package import export_package
from src.research.question_planner import recommended_index
from src.research.schemas import ResearchSpec
from src.research.store import KIND_SPEC, ResearchStore
from src.verification.runner import available_tools


def _role_llm():
    """协调者/理论研究者所用的模型 (未配置时回退主模型); 失败则返回 None。

    `THEORY_LLM=0` 强制离线 (只走规则层与工具核验): 离线复现与自动化测试必须
    不依赖外部模型 —— 否则同一条验收题会因为模型每次给出的推导步骤不同而时通时不通,
    并且真的产生费用。
    """
    if os.getenv("THEORY_LLM", "").strip() == "0":
        return None
    try:
        from src.config import build_llm

        return build_llm("coordinator")
    except Exception:  # noqa: BLE001
        return None


def _writing_agent_inputs(snapshot, topic: str):
    """为 WritingAgent 组装 (task, context, runtime, usage)。

    为什么必须走 AgentRuntime: 写作角色通过运行时拿 LLM 与预算 (token/调用次数上限
    与取消都在那里生效), 而不是自己 `build_llm()` —— 否则"预算与取消"在写作这一步
    会失效。`THEORY_LLM=0` 时 `_role_llm()` 返回 None, 运行时的 `llm_available()`
    为 False, 写作自动走确定性起草 (离线路径)。

    任何组装失败都返回 None 组合: 主文必须能降级交付, 不能因为写作智能体不可用而中断。
    """
    try:
        from src.agents.protocol import AgentTask, ContextPack, TaskBudget
        from src.agents.runtime import AgentRuntime

        run_id = str(getattr(snapshot, "run_id", "") or "")
        project_id = str(getattr(snapshot, "project_id", "") or "")
        problem_id = str(getattr(snapshot, "problem_id", "") or "")
        budget = TaskBudget()
        task = AgentTask(
            task_id=f"writing-{run_id or project_id or 'run'}",
            agent="writing",
            objective=f"撰写研究正文: {topic}",
            project_id=project_id, problem_id=problem_id, run_id=run_id,
            acceptance_criteria=["每个段落给出它依据的对象 id", "未决项如实写出"],
            budget=budget,
            idempotency_key=f"writing:{run_id}:{getattr(snapshot, 'snapshot_id', '')}",
        )
        claims = [c.model_dump(mode="json") for c in getattr(snapshot, "claims", [])]
        sources = [e.model_dump(mode="json") for e in getattr(snapshot, "evidence", [])]
        context = ContextPack(
            request=topic,
            objects={"claim": claims, "evidence": sources,
                     "brief": [{"main_question": topic, "deliverables": ["theoretical_conclusion"],
                                "snapshot_id": str(getattr(snapshot, "snapshot_id", "") or "")}]},
        )
        runtime = AgentRuntime(llm_factory=lambda stage: _role_llm())  # type: ignore[arg-type,return-value]
        from src.agents.protocol import UsageRecord

        return task, context, runtime, UsageRecord()
    except Exception:  # noqa: BLE001
        return None, None, None, None


def _knowledge_for(spec: ResearchSpec):
    """按**绑定资料源**组装知识底座; 无主题或不可用时返回 None (相关动作不暴露)。

    R0: 显式绑定 (`spec.source_set_id`) 优先于"按主题名找同名底座"。绑定声明了
    却不可用时**不静默降级**到别的底座 —— 那会让用户以为在用他选定的资料。
    只探测已存在的底座 (`create_if_missing=False`), 不在 data/kb/ 下建空库。
    """
    from src.kb.service import KnowledgeService
    from src.kb.sources import resolve_topic_with_binding

    topic, binding_note = resolve_topic_with_binding(spec)
    if not topic:
        if binding_note:
            # 绑定不可用: 记录原因, 由调用方/工作台展示, 而不是当作"没有资料"
            print(f"  [warning] {binding_note}")
        return None
    try:
        service = KnowledgeService(topic, create_if_missing=False)
        if not service.available:
            return None
        # 把绑定说明挂在服务上, 供研究过程与交付物展示"实际用了哪版资料"
        service.binding_note = binding_note  # type: ignore[attr-defined]
        return service
    except Exception:  # noqa: BLE001
        return None


def _novelty_lookup_for(service):
    """新颖性对照: 优先本地知识底座; 底座不可用时退回**外部文献检索**。

    计划书 §2 P1-A12: 未检索到相同结论 ≠ 世界首次。没有可用检索能力时
    `novelty.assess` 会保持 unchecked, 因此这里提供外部检索作为后备,
    并把实际覆盖的资料源写入 NoveltyRecord。
    """
    if service is not None and getattr(service, "usable", False):
        from src.kb.bridge import novelty_lookup

        return novelty_lookup(service)
    from src.research.retrieval import build_lookup

    return build_lookup()


def engine_spec_coverage(engine):
    """研究循环累积的检索覆盖记录 (没有则 None)。

    检索现在发生在研究阶段 (计划书 P0-1), 覆盖记录累加在问题规格上
    (`engine.spec.coverage`, 多轮 `absorb`)。导出交付包时必须优先用它, 否则出版层的
    空覆盖会把真实检索记录覆盖掉 —— 实测: 入库 7 条证据, 交付包却报
    `executed=false / 命中 0`, 事后无法复核"研究阶段检索了什么"。
    """
    spec = getattr(engine, "spec", None)
    coverage = getattr(spec, "coverage", None)
    if coverage is None:
        return None
    # 只有真的执行过检索才算"研究阶段的覆盖记录"
    if not getattr(coverage, "executed", False):
        return None
    # 标注来源: 覆盖记录必须能自证"这是研究阶段检索的, 不是出版层补检索的"
    if not getattr(coverage, "origin", ""):
        coverage.origin = "research_loop"
    return coverage


def _engine_from_state(state: TheoryState) -> TheoryEngine:
    store = ResearchStore(state["project_id"])
    data = store.get(KIND_SPEC, state["problem_id"])
    spec = ResearchSpec.model_validate(data) if data else ResearchSpec(
        project_id=state["project_id"], problem_id=state["problem_id"],
        original_request=state.get("request", ""), problem_statement=state.get("request", ""),
    )
    budget = ResearchBudget(
        max_actions=state.get("budget_max_actions", 40),
        max_tool_calls=state.get("budget_max_tool_calls", 60),
    )
    knowledge = _knowledge_for(spec)
    # 问题说明附件: 引擎必须能读到附件正文, 否则"把题面作为附件交上来"的输入
    # 会被判成"缺少研究问题"并请求澄清 (附件文本此前只在 state 里, 无人读取)。
    # 两条来源都取: 顶层 attachment_ids, 以及输入快照里记录的附件 (续跑时以它为准)。
    attachment_ids = [str(i) for i in (state.get("attachment_ids") or []) if i]
    snapshot_ids = [str(i) for i in
                    ((state.get("input_snapshot") or {}).get("attachment_ids") or []) if i]
    for attachment_id in snapshot_ids:
        if attachment_id not in attachment_ids:
            attachment_ids.append(attachment_id)
    if os.getenv("AIR_DEBUG_ATTACH"):
        print(f"  [debug] 附件 id: {attachment_ids}")
    engine = TheoryEngine(spec, store=store, budget=budget, available=available_tools(),
                          llm=_role_llm(), knowledge=knowledge,
                          novelty_lookup=_novelty_lookup_for(knowledge),
                          run_id=str(state.get("run_id", "") or ""),
                          attachment_ids=attachment_ids)
    _attach_proposer(engine, store)
    engine.load_runtime()
    return engine


def engine_log_sink(store):
    """把提议/拒绝理由写进研究事件流 (计划书 §3 R1)。

    提议曾经只落在内存态里, 端到端跑完后无法回答"模型提了什么、为什么被拒"。
    """
    def _sink(event: dict) -> None:
        try:
            store.append_event(event.get("type", "proposal"), event)
        except Exception:  # noqa: BLE001 - 日志失败不得影响研究
            pass

    return _sink


class _MeteredProposerLLM:
    """把提议用的模型调用计入引擎的 token/费用预算 (计划书 §9.3)。

    提议器是独立于研究循环的调用点; 不记账就会让"预算"在真实路径上失真。
    """

    def __init__(self, inner, engine):
        self._inner = inner
        self._engine = engine

    def invoke(self, *args, **kwargs):
        result = self._inner.invoke(*args, **kwargs)
        try:
            from src.utils.cost_tracker import usage_metadata_of

            usage = usage_metadata_of(result)
            model = getattr(self._inner, "model_name", "") or getattr(self._inner, "model", "")
            if usage:
                self._engine.record_llm_usage(model=str(model or ""), usage=usage,
                                              stage="proposal")
        except Exception:  # noqa: BLE001 - 记账失败不得影响研究
            pass
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _attach_proposer(engine, store) -> None:
    """给引擎装结构化动作提议器 (计划书 §3 R1)。

    **默认开启** —— 计划书的要求就是"正式图入口真实使用结构化提议", 关闭它会让
    研究退回纯规则打分。可用 `THEORY_PROPOSER=0` 显式停用 (离线运行、测试或
    成本敏感场景); 显式传入 `llm=None` 的测试引擎不会走到这里。

    - 提议用的模型调用计入引擎的 token/费用预算;
    - `THEORY_PROPOSER_MAX_CALLS` 限制提议次数 (默认 20);
    - 任何构造失败都退回确定性排序 (稳定降级)。
    """
    import os

    if os.getenv("THEORY_PROPOSER", "1").strip() in ("0", "false", "False", "no"):
        return
    try:
        from src.research.proposal import make_proposer

        llm = getattr(engine, "llm", None)
        if llm is None:
            return
        max_calls = int(os.getenv("THEORY_PROPOSER_MAX_CALLS", "20") or 20)
        engine.proposer = make_proposer(_MeteredProposerLLM(llm, engine),
                                        event_sink=engine_log_sink(store),
                                        max_calls=max_calls)
    except Exception:  # noqa: BLE001
        engine.proposer = None


def theory_bootstrap_node(state: TheoryState) -> dict:
    engine = _engine_from_state(state)
    ok = engine.bootstrap()
    engine.save_runtime()
    return {
        "bootstrapped": ok,
        "done": engine.done,
        "needs_clarification": engine.needs_clarification,
        "needs_confirmation": engine.needs_confirmation,
        "candidates": [c.model_dump(mode="json") for c in engine.spec.candidates],
        "notes": engine.notes,
        "current_phase": "theory_bootstrap",
    }


def _resolve_candidate_choice(candidates: list, answer) -> tuple[int, str]:
    """把用户回答解析成 (候选序号, 稳定候选 ID) (计划书 F1-4)。

    按数组序号确认在历史恢复后会错位, 因此优先按 `candidate_id` 匹配;
    只有回答确实是纯序号时才退回序号语义。
    """
    text = str(answer).strip()
    if not text:
        idx = recommended_index(candidates)
        return idx, (candidates[idx].candidate_id if candidates else "")
    match = next((i for i, c in enumerate(candidates)
                  if c.candidate_id and c.candidate_id == text), None)
    if match is not None:
        return match, candidates[match].candidate_id
    if text.isdigit():
        idx = int(text)
        if 0 <= idx < len(candidates):
            return idx, candidates[idx].candidate_id
    idx = recommended_index(candidates)
    return idx, (candidates[idx].candidate_id if candidates else "")


def theory_confirm_node(state: TheoryState) -> dict:
    """候选路线确认: 交互模式暂停等待用户选择, 否则自动选推荐路线。"""
    engine = _engine_from_state(state)
    selected = state.get("selected_candidate")
    candidate_id = ""
    if selected is None:
        if state.get("interactive"):
            answer = interrupt({
                "type": "theory_candidates",
                "title": "请选择要研究的主路线",
                "candidates": [c.model_dump(mode="json") for c in engine.spec.candidates],
                "hint": "点选候选（按稳定 ID 确认）, 或输入候选 ID；直接回车采用推荐路线",
            })
            if isinstance(answer, int):
                index = answer
                candidate_id = (engine.spec.candidates[index].candidate_id
                                if 0 <= index < len(engine.spec.candidates) else "")
            else:
                index, candidate_id = _resolve_candidate_choice(engine.spec.candidates, answer)
        else:
            index = recommended_index(engine.spec.candidates)
            candidate_id = (engine.spec.candidates[index].candidate_id
                            if engine.spec.candidates else "")
    else:
        index = int(selected)
    engine.confirm_candidate(int(index), candidate_id=candidate_id)
    engine.save_runtime()
    return {
        "selected_candidate": int(index),
        "selected_candidate_id": candidate_id or engine.spec.selected_candidate_id,
        "needs_confirmation": False,
        "candidates": [c.model_dump(mode="json") for c in engine.spec.candidates],
        "notes": engine.notes,
        "current_phase": "theory_confirm",
    }


def theory_step_node(state: TheoryState) -> dict:
    engine = _engine_from_state(state)
    # 写作缺口回流后图会回到这里 (见 `_route_after_finalize`), 但**引擎的 `done`
    # 标志是持久化的**: 上一次收尾已把它置 True, 于是 `step()` 会立刻返回、什么也不做,
    # 回流出的义务永远无人处理。回到本节点时先解除完成态, 让研究循环真的继续。
    if state.get("feedback_reopened"):
        engine.resume_after_reflow()
    info = engine.step()
    return {
        "action": info,
        "step_index": int(state.get("step_index", 0)) + 1,
        "done": engine.done,
        # 注意: 这里**不**把 `needs_clarification` 回写图状态。它在引擎里是"粘住"的
        # (一旦置位就保持到用户回答), 图状态又会被每个节点从版本化存储重建 ——
        # 回写会让一次中途的澄清请求永久降级最终交付等级。
        # 澄清的收尾由引擎自己完成: `needs_clarification` 会让 run() 停止推进,
        # 之后若缺口被解开并产出结论, 门槛仍应按最终状态判定。
        "notes": engine.notes,
        "current_phase": "theory_step",
    }


def theory_feedback_node(state: TheoryState) -> dict:
    """用户反馈节点 (计划书 §5.1): 把自然语言意见转成对象级动作。

    - 交互模式: interrupt 等待反馈;
    - 无法唯一确定作用对象时**不猜**, 把澄清问题回传, 研究状态保持不变。
    """
    engine = _engine_from_state(state)
    feedback = state.get("human_feedback", "")
    if not feedback:
        if not state.get("interactive"):
            return {"current_phase": "theory_feedback"}
        context = engine.feedback_context()
        feedback = interrupt({
            "type": "theory_feedback",
            "title": "可以补充意见 (修订假设/质疑结论/要求检索), 回车表示无意见",
            "objects": {
                "assumptions": list(context["assumptions"].items())[:10],
                "claims": list(context["claims"].items())[:10],
            },
        })
    outcome = engine.submit_feedback(str(feedback))
    notes = engine.notes
    if outcome.get("clarify"):
        notes = notes + [f"需要澄清: {outcome['clarify']}"]
    return {
        "human_feedback": "",
        "feedback_result": outcome,
        "notes": notes,
        "needs_clarification": bool(outcome.get("needs_clarification")),
        "current_phase": "theory_feedback",
    }


def _degraded_input_snapshot(state: TheoryState) -> dict:
    """没有启动输入快照时写出的退化快照 (显式标注不可复现)。

    直接 CLI/测试调用没有附件与资料集上下文; 留一个空 {} 会看起来"快照正常",
    因此这里如实写上已有字段 + `reproducible: False` + 原因。
    """
    return {
        "request": state.get("request", ""),
        "topic": state.get("topic", ""),
        "source_set_id": state.get("source_set_id", ""),
        "source_set_kind": state.get("source_set_kind", "kb"),
        "source_policy": state.get("source_policy", "user_kb"),
        "attachment_ids": list(state.get("attachment_ids") or []),
        "attachments": [],
        "budget": {"max_actions": state.get("budget_max_actions"),
                   "max_tool_calls": state.get("budget_max_tool_calls")},
        "reproducible": False,
        "note": "直接调用未提供附件/资料集上下文: 该快照不足以完整复现",
    }


def _record_design_success_conditions(spec, store) -> None:
    """把判定类问题的**成功判据**写进规格 (计划书 §1 验收/停止判据字段)。

    为什么需要: 交付门槛只看义务是否关闭, 不看"结论的适用范围是否写清"。验收口径
    要求"不得把结论推广到其他参数", 这条要求必须以**可核查的判据**出现, 由写作与
    人工评审逐条对照, 而不是只留在评审者的记忆里。
    """
    from src.research.design_feasibility import feasibility_from_text, success_conditions
    from src.research.store import KIND_SPEC

    if spec.success_conditions:
        return
    text = " ".join(filter(None, (spec.problem_statement, spec.direction,
                                  spec.original_request)))
    report = feasibility_from_text(text)
    if report is None:
        return
    spec.success_conditions = success_conditions(report)
    try:
        store.put(KIND_SPEC, spec.problem_id, spec.model_dump(mode="json"))
    except Exception as e:  # noqa: BLE001 - 判据写不进规格不得中断研究
        print(f"  [warning] 成功判据写入规格失败: {type(e).__name__}: {e}")


def _entry_input_snapshot(spec, *, request: str = "", topic: str = "",
                          source_set_id: str = "", source_set_kind: str = "kb",
                          source_policy: str = "user_kb",
                          max_actions: int | None = None,
                          max_tool_calls: int | None = None) -> dict:
    """按实际拿到的参数构造启动输入快照 (与入口无关)。

    为什么需要: Web 启动路径会带附件 hash 与资料集上下文, 而 CLI/直接调用只带
    请求文本 —— 于是"这次输入是否可复现"会取决于从哪个入口进来。快照应当只记录
    **这次实际给了什么**: 没给附件就是"没有附件"(可复现), 而不是"输入不完整"。
    资料库内容本身未做 hash 记录, 这一点在 note 里如实写明, 不冒充完整复现。
    """
    return {
        "request": (request or spec.original_request or spec.problem_statement
                    or spec.direction or ""),
        "topic": topic or spec.problem_statement or spec.direction or "",
        "source_set_id": (source_set_id or spec.source_set_id or ""),
        "source_set_kind": source_set_kind or spec.source_set_kind or "kb",
        "source_policy": str(source_policy or spec.source_policy.value or "user_kb"),
        "attachment_ids": [],
        "attachments": [],
        "budget": {"max_actions": max_actions, "max_tool_calls": max_tool_calls},
        "note": "本次入口未携带附件; 资料库内容未做 hash 记录, 因此快照只覆盖请求/策略/预算",
    }


def _snapshot_verification(state: TheoryState) -> tuple[dict, SnapshotCheck]:
    """取本次运行生效的输入快照, 并给出**实际校验**过的可用性结论 (R6)。

    - 有输入快照 (Web 启动或续跑): 逐份附件重算 sha256 并与快照记录比对;
    - 没有输入快照 (直接 CLI/测试调用): 写出一份带 `reproducible: False` 与原因的
      退化快照, 让 manifest 明确"这次输入不足以完整复现", 而不是留一个空 {} 看起来正常。

    `reproducible` 一律由这里返回的校验结论决定: 一致 → True; 任一附件不可用,
    或快照自身已声明不可复现 → False。
    """
    provided = state.get("input_snapshot") or _degraded_input_snapshot(state)
    check = verify_input_snapshot(provided)
    return merge_verification(provided, check), check


def theory_finalize_node(state: TheoryState) -> dict:
    """收尾: 冻结快照 → 写作 → 两道门槛 → 研究交付包。

    顺序很关键: writing_map 必须在导出前写回快照并落盘, 否则交付门槛看不到
    结论与正文的映射 (计划书 §9.1 表达门槛)。
    """
    engine = _engine_from_state(state)
    result = engine.finalize()
    snapshot = result.snapshot
    topic = state.get("topic", "") or snapshot.project_id
    # R6: 交付前再确认一次输入快照仍可用 (续跑路径已在入口拒绝不一致的快照;
    # 这里是 manifest 的事实来源 —— 包里的 reproducible 必须是校验结论, 不是声明)
    input_snapshot, snapshot_check = _snapshot_verification(state)
    if not snapshot_check.reproducible:
        result.notes.append("输入快照校验: " + snapshot_check.reason)
    # 页面上也要能看到"哪份附件坏了": 逐份原因进 notes (reasons 已含具体原因)
    for item in snapshot_check.attachments:
        if item.get("status") != "ok":
            result.notes.append(
                f"附件不可用 [{item.get('status')}] {item.get('filename') or item.get('attachment_id')}: "
                f"{item.get('detail')}")

    # ---- 主文: 统一交给 WritingAgent (合并计划 §7.3 "退役双正文主路径") ----
    # 有模型时由写作智能体起草 (逐段带依据对象 id); 离线/未配置时它由冻结快照
    # 确定性起草 —— 输出与旧渲染器逐字一致, 因此离线复现不因合并而下降。
    writing_task, writing_context, writing_runtime, writing_usage = _writing_agent_inputs(
        snapshot, topic)
    manuscript_md, writing_map = run_theory_writing(
        snapshot, topic, task=writing_task, context=writing_context,
        runtime=writing_runtime, usage=writing_usage)
    snapshot.writing_map = writing_map
    # 排版映射写回冻结快照 (仅补充映射, 不改变任何研究结论)
    try:
        engine.store.save_snapshot(snapshot, allow_replace=True)
    except Exception as e:  # noqa: BLE001
        result.notes.append(f"writing_map 回写快照失败: {e}")

    # 写作缺口回流: 若回流出**阻塞义务**, 本次收尾不导出交付包 —— 图会回到研究循环
    # 去消解它, 下一轮再收尾 (见 `_route_after_finalize`)。导出必须只做一次且是最新
    # 结论, 否则交付包里会留下"义务还没关闭"时的旧稿。
    rounds = int(state.get("finalize_rounds", 0) or 0) + 1
    # 上限语义: `rounds` 是"本次是第几次收尾"。允许在前 `limit - 1` 次收尾后回到研究
    # 循环, 第 `limit` 次收尾无论缺口是否消解都**必须导出** —— 否则缺口永远消不掉时
    # 会一直重入而不交付 (实测: 用 <= 判定时 limit=1 仍会重入一次, 然后卡在 pending)。
    if result.feedback_reopened and rounds < _refinalize_limit() \
            and not result.needs_clarification:
        result.notes.append(
            f"写作缺口已回流为阻塞义务: 回到研究循环消解 (第 {rounds} 次收尾, "
            f"上限 {_refinalize_limit()})")
        return {
            "feedback_reopened": True,
            "finalize_rounds": rounds,
            "notes": result.notes,
            "current_phase": "theory_finalize_pending",
        }

    # ---- 出版层 (方案 v2 §5 阶段 3): 期刊式稿件 + 参考文献表 ----
    # 引用只能改变表述、不能改变结论: 结论文本取自冻结快照, 本步骤不触碰命题/义务/验证。
    from src.rag import reference_list as reference_list_mod
    from src.rag.publication_render import render_publication_latex, render_publication_markdown
    from src.research.acceptance import publication_complete, publication_gate
    from src.research.publication_paper import build_publication_paper

    references = reference_list_mod.build_reference_list(snapshot.evidence)
    # 阶段 2 (方案 v2 M3): 检索**只在策略允许时执行**, 命中结果按支持关系筛成参考文献表。
    # 离线 (THEORY_LLM=0) 时不联网, 覆盖记录如实写"未执行" ——
    # 缺记录不等于"检索过且无命中"。
    from src.research.publication_evidence import gather_publication_evidence

    evidence_bundle = gather_publication_evidence(snapshot, engine, references)
    references = evidence_bundle.references
    if evidence_bundle.notes:
        result.notes.extend(evidence_bundle.notes)
    # 覆盖记录以**研究循环累积的那份**为准: 检索现在发生在研究阶段 (P0-1), 出版层
    # 会因"研究循环已产生证据"而跳过检索并返回一份空覆盖 —— 若直接采用它,
    # 交付包会报 `executed=false / 命中 0`, 而实际入库了 7 条证据 (实测 livefinal),
    # 事后无法复核"研究阶段检索了什么"。仅当研究循环确实没检索过时才用出版层记录。
    coverage = engine_spec_coverage(engine) or evidence_bundle.coverage
    # 任务画像 (合并计划 §7.3 / P0-3): 文稿的章节骨架与领域措辞由它决定。理论图这条
    # 旧路径没有 `ResearchBrief`, 因此按快照里**是否真的声明了设计参数**降级推断 ——
    # 这正是"通用入口曾经输出组合设计专用内容"的修复点。
    from src.research.task_profile import profile_for_deliverables

    task_profile = profile_for_deliverables(
        ["theoretical_conclusion"],
        has_design_parameters=any(c.design_v and c.design_k and c.design_lambda
                                  for c in snapshot.claims))
    publication_doc, dangling = build_publication_paper(
        snapshot, topic, references, coverage=evidence_bundle.coverage,
        spec=engine.spec, related_evidence=evidence_bundle.evidence,
        profile=task_profile)
    # 正文里的引用占位 (`[[REF:key]]`) 由**两种渲染器各自解析**: Markdown 出 `[n]`,
    # LaTeX 出 `\upcite{key}`。在这里解析 Markdown 版本, 漏掉的键如实记进 notes
    # (占位符直接印进正文是明显的格式缺陷)。
    publication_md = render_publication_markdown(publication_doc)
    unresolved_refs: list[str] = []
    publication_md = reference_list_mod.render_markdown_citations(
        publication_md, references, unresolved_refs)
    if unresolved_refs:
        result.notes.append(
            "出版层引用未收录: " + "、".join(sorted(set(unresolved_refs))))
    # 附录里的证明块需要**结构化证书**才能排成公式与表格 (逐字排版只会是符号堆叠)
    from src.research.design_feasibility import certificate_of as _certificate_of

    certificates = {record.claim_id: _certificate_of(record)
                    for record in snapshot.verifications if not record.stale}
    publication_tex, render_warnings = render_publication_latex(
        publication_doc, references, certificates={k: v for k, v in certificates.items() if v})
    if dangling:
        result.notes.append("出版层发现未收录引用: " + "、".join(sorted(set(dangling))))
    for warning in render_warnings:
        result.notes.append("出版层渲染警告: " + warning)

    # 长文附录路径已删除 (2026-10-04, 合并计划 §15.2 清理)。
    # 它受 `THEORY_LONG_FORM` 控制且**默认关闭**, 关闭时唯一效果是往 notes 里写一句
    # "长文附录未生成" —— 也就是一条"从未启用"的路径。正文已由 WritingAgent 统一承担
    # (见 `agents/writing.write_main_manuscript`), 因此删掉它不减少任何实际能力:
    # 交付物里从来没有出现过经由这条路径生成的附录。
    #
    # 删除的模块: `src/research/writing_bridge.py` 与 `src/agents/paper_writer.py`
    # (后者只被前者调用; 静态可达性检查确认它们只能从这条路径到达)。

    # 预算触顶时明确告知: 这是部分结果, 不是研究已完成 (§9.3)
    if result.stopped_reason:
        result.notes.append(f"本次运行因预算停止: {result.stopped_reason}")

    # 两道门槛分别执行: 研究有效性 + 论文表达 (再加上出版完备门槛)
    theory_gate = result.gate or theory_validity_gate(
        snapshot.claims, snapshot.obligations, snapshot.verifications,
        snapshot.dependency_edges, snapshot=snapshot)
    delivery = delivery_gate(manuscript_md, snapshot, theory_gate=theory_gate)
    # 先算等级 (出版门槛此时还不知道能否编译), 再真编译, 最后按编译结论复核等级。
    publication = publication_gate(
        publication_md, snapshot,
        references=[item.to_dict() for item in references.references],
        compile_status=COMPILE_DEFERRED, blocks=publication_doc.blocks)
    level = classify_deliverable(theory_gate, delivery, publication)
    # 未澄清/无命题的运行不得拿到高交付等级: 澄清请求不是研究结论 (计划书 §1/发布门槛 4)
    # 两种情形都要拦:
    #   1) 全程只有澄清请求 (没有任何命题);
    #   2) 后来虽然解开了部分命题, 但本次运行是**以澄清请求收尾**的 —— 引擎已停止推进,
    #      此时把门槛报成"通过"等于用已解决的部分掩盖未解决的前置问题。
    clarification_only = bool(state.get("needs_clarification")) and not snapshot.claims
    if clarification_only:
        level = "研究备忘录"
        if not any("澄清" in r for r in theory_gate.reasons):
            theory_gate.reasons.append("本次运行只提出澄清请求, 尚未产生任何结论")
        theory_gate.passed = False
        result.notes.append("结论: 仅提出澄清请求 (无命题/无义务), 交付等级降为研究备忘录")
    elif state.get("needs_clarification") and theory_gate.passed:
        level = "条件性研究报告"
        reason = "本次运行以澄清请求收尾: 前置问题未解决, 结论只能条件性交付"
        theory_gate.reasons.append(reason)
        theory_gate.passed = False
        result.notes.append("结论: " + reason)

    manuscript = build_manuscript(snapshot, topic, delivery_level=level)
    tex = render_latex(manuscript)
    report = render_research_report(snapshot, theory_gate, result.notes,
                                    delivery_gate_result=delivery)
    # 先建包目录 (manifest 由 export_package 写), 出版层产物随后落进去, 编译之后再
    # **刷新 manifest 的交付等级与门槛报告** —— 否则 manifest 会停在编译前的等级上
    # (实测: 交付包里有 PDF、CLI 报"完整论文", manifest 却写"论文草稿")。
    package_dir = export_package(
        snapshot, engine.spec, result.decisions, manuscript_md, tex,
        notes=result.notes, gate=theory_gate,
        base_dir=None, delivery=delivery, delivery_level=level,
        usage=result.usage,
        # R6: 交付包目录名与 manifest 使用引擎的运行身份, 而不是另生成一个
        run_id=engine.run_id,
        # R6: 输入快照与入口无关 —— Web 启动路径带完整快照 (附件/策略/资料集),
        # 直接调用 (CLI/测试) 没有该上下文时写退化快照并**显式标注不可复现**;
        # 两种情况都附上逐份附件的校验结论 (manifest.input_snapshot.verification)。
        input_snapshot=input_snapshot,
    )
    (package_dir / "research_report.md").write_text(report, encoding="utf-8")
    # 出版层产物 (方案 v2 阶段 3): 期刊式稿件、参考文献表与检索覆盖记录。
    # 与 `manuscript.md` 的分工: 后者是研究稿 (结论如何验证), 前者是论文 (如何成篇)。
    (package_dir / "publication.md").write_text(publication_md, encoding="utf-8")
    (package_dir / "publication.tex").write_text(publication_tex, encoding="utf-8")
    # 出版级 PDF (方案 v2 阶段 5): 能编译就交付 .pdf, 不能就如实降级。
    # 顺序讲究: **先落盘 .tex, 再编译, 再用编译结论复核门槛与等级** ——
    # 编译结论是"完整论文"成立的必要条件, 不能先定等级再补编译。
    # 传**路径**而不是内容: 内容是整篇 LaTeX, 当路径用会报"目录名称无效"。
    compile_status, compile_note = compile_publication(
        str(package_dir / "publication.tex"))
    if compile_note:
        result.notes.append(compile_note)
    publication = publication_gate(
        publication_md, snapshot,
        references=[item.to_dict() for item in references.references],
        compile_status=compile_status, blocks=publication_doc.blocks)
    revisited = classify_deliverable(theory_gate, delivery, publication)
    if revisited != level:
        result.notes.append(f"交付等级按出版结论复核: {level} → {revisited}")
        level = revisited
    # 编译结论会改变等级, 因此必须刷新 manifest 与门槛报告 (见上面的顺序说明)
    _refresh_manifest(package_dir, level, publication, compile_status,
                      theory_passed=bool(theory_gate.passed),
                      delivery_passed=bool(delivery.passed))
    # 人审清单 (方案 v2 §10): 出版级交付必须能被逐条核对, 而不是只看一个级别名
    (package_dir / "publication_checklist.md").write_text(
        _publication_checklist(package_dir, publication_doc, publication, references,
                               compile_status),
        encoding="utf-8")
    _write_json(package_dir / "references.json", references.to_dict())
    _write_json(package_dir / "retrieval_coverage.json", {
        "executed": bool(getattr(coverage, "executed", False)),
        "policy": str(getattr(getattr(coverage, "policy", ""), "value",
                              getattr(coverage, "policy", "")) or ""),
        "queries": list(getattr(coverage, "queries", []) or []),
        "engines": list(getattr(coverage, "engines", []) or []),
        "hits": int(getattr(coverage, "hits", 0) or 0),
        "ingested": int(getattr(coverage, "ingested", 0) or 0),
        # 取得全文的篇数: 决定"能否核到具体定理陈述"的关键指标, 必须出现在覆盖记录里,
        # 否则无法从交付包判断"检索到的是元数据还是全文"
        "fulltext_available": int(getattr(coverage, "fulltext_available", 0) or 0),
        "uncovered": list(getattr(coverage, "uncovered", []) or []),
        "failures": list(getattr(coverage, "failures", []) or []),
        "scope_note": str(getattr(coverage, "scope_note", "") or ""),
        "origin": str(getattr(coverage, "origin", "") or ""),
        # 没有覆盖记录时如实说明: 缺记录不等于"检索过且无命中"
        "note": ("本次运行没有检索覆盖记录 (离线或未接入检索源)"
                 if coverage is None else ""),
    })
    (package_dir / "delivery_gate.md").write_text(
        f"# 交付级别: {level}\n\n## 研究有效性门槛\n{theory_gate.render()}\n\n"
        f"## 论文表达门槛\n{delivery.render()}\n\n"
        f"## 出版完备门槛\n{publication.render()}\n\n"
        f"## 资源用量\n"
        f"- 动作 {result.usage.get('actions', 0)}/{result.usage.get('max_actions', '-')}"
        f"; 工具调用 {result.usage.get('tool_calls', 0)}/{result.usage.get('max_tool_calls', '-')}\n"
        f"- tokens {result.usage.get('tokens', 0)}"
        f"; 费用 ${result.usage.get('cost_usd', 0)}\n"
        f"- 墙钟 {result.usage.get('wall_seconds', 0)}s\n"
        + (f"\n停止原因: {result.stopped_reason}\n" if result.stopped_reason else ""),
        encoding="utf-8")
    return {
        "snapshot_id": snapshot.snapshot_id,
        # R6: 身份回传给调用方 (Web 会话据此显示/落盘同一个 run, 不再各自拼)
        "run_id": snapshot.run_id or engine.run_id,
        "branch_id": snapshot.branch_id,
        "project_id": snapshot.project_id,
        "problem_id": snapshot.problem_id,
        "gate_passed": bool(theory_gate.passed and delivery.passed),
        "delivery_passed": publication_complete(publication),
        "gate_report": (f"交付级别: {level}\n\n{theory_gate.render()}\n\n"
                        f"{delivery.render()}\n\n{publication.render()}"),
        "delivery_level": level,
        "publication_report": publication.render(),
        "usage": result.usage,
        "stopped_reason": result.stopped_reason,
        "package_dir": str(package_dir),
        "manuscript_path": str(package_dir / "manuscript.md"),
        "tex_path": str(package_dir / "publication.tex"),
        "publication_path": str(package_dir / "publication.md"),
        "publication_tex_path": str(package_dir / "publication.tex"),
        "needs_clarification": result.needs_clarification,
        "feedback_reopened": False,
        "finalize_rounds": rounds,
        "notes": result.notes,
        "current_phase": "theory_finalize",
    }


# 出版门槛的"编译尚未执行"标记: 等级先在落盘前算一次, 编译完成后复核。
# 它不是"编译失败", 因此只记未决不记缺项 (见 `publication_gate`)。
COMPILE_DEFERRED = "deferred: 编译将在交付包落盘后执行"


def _publication_checklist(package_dir, manuscript, publication, references,
                           compile_status: str) -> str:
    """生成 `publication_checklist.md` (方案 v2 §10): 逐条可核对, 不是一句"完整论文"。

    每项写"通过/需修改 + 依据文件"。作者/单位占位一律标为"待作者补全",
    避免把模板占位当成已完成的交付物。
    """
    kinds = {getattr(b, "kind", "") for b in manuscript.blocks}
    has_pdf = (package_dir / "publication.pdf").is_file()
    ref_count = len(references.references)

    def mark(ok: bool, pending: str = "需修改") -> str:
        return "通过" if ok else pending

    rows = [
        ("摘要与关键词覆盖问题/方法/结论/边界",
         mark("abstract" in kinds and "keywords" in kinds), "publication.md"),
        ("英文题名与摘要 (与中文一致, 不引入新论断)",
         mark("Title:" in (package_dir / "publication.md").read_text(encoding="utf-8")),
         "publication.md"),
        ("主要结果与冻结快照逐条对应 (定理编号 ↔ claim_id)",
         mark(bool(manuscript.writing_map)), "manuscript.md / claims.json"),
        ("判定链与证书可在附录逐条反查",
         mark("附录" in (package_dir / "publication.md").read_text(encoding="utf-8")),
         "publication.md / verification/"),
        (f"参考文献通过核查且正文双向一致 (共 {ref_count} 条)",
         mark(not any("参考文献表" in r or "引用" in r for r in publication.reasons)),
         "references.json / delivery_gate.md"),
        ("第 4 节写明与已有工作的关系判定 (无文献时如实说明)",
         mark(True, "需作者复核"), "publication.md"),
        ("检索覆盖与未覆盖范围如实记录",
         mark((package_dir / "retrieval_coverage.json").is_file(), "需补充"),
         "retrieval_coverage.json"),
        ("未决义务/未关闭项在局限一节如实列出",
         mark(True, "需作者复核"), "publication.md / obligations.json"),
        ("作者与单位占位已标注为待补 (不得作为已完成交付)",
         "待作者补全", "publication.md"),
        ("PDF 可读 (无缺字、图表完整)", mark(has_pdf, "需编译"), "publication.pdf"),
        ("编译状态", compile_status, "compile_log.txt"),
    ]
    lines = ["# 出版完备自检清单", "",
             "> 逐项核对交付物；标记为需修改/需作者复核的项目不得对外称已出版。", "",
             "| 检查项 | 结论 | 依据 |", "|---|---|---|"]
    lines.extend(f"| {item} | {result} | `{source}` |" for item, result, source in rows)
    lines.append("")
    lines.append(f"- 出版完备门槛: {'通过' if not publication.reasons and not publication.unresolved else '未通过'}")
    for reason in publication.reasons:
        lines.append(f"  - 缺项: {reason}")
    for item in publication.unresolved:
        lines.append(f"  - 未决: {item}")
    return "\n".join(lines) + "\n"


def _refresh_manifest(package_dir, level: str, publication, compile_status: str,
                      theory_passed: bool = True, delivery_passed: bool = True) -> None:
    """把编译后的交付等级与三道门槛写回 manifest。

    为什么需要刷新: 包目录由 `export_package` 建立 (它写 manifest), 而"能否交付 PDF"
    只有在包目录里真编译过才知道。不刷新就会出现"包里有 PDF、CLI 报完整论文、
    manifest 却写论文草稿"的三方不一致 —— 交付物自身必须自洽。

    三个门槛字段各自独立, 不得互相顶替 (现场实测过: 研究门槛未过但出版门槛通过时,
    manifest 曾把 `delivery_gate_passed` 写成 `true`, 与 `gate_passed=false` 自相矛盾):
    - `gate_passed`: 研究有效性门槛;
    - `delivery_gate_passed`: 论文表达门槛;
    - `publication_gate`: 出版完备门槛 (含编译结论)。
    """
    path = package_dir / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - manifest 缺失不应中断收尾
        return
    payload["delivery_level"] = level
    payload["gate_passed"] = bool(theory_passed)
    payload["delivery_gate_passed"] = bool(delivery_passed)
    payload["publication_gate"] = {
        "passed": bool(publication.passed),
        "complete": bool(not publication.reasons and not publication.unresolved),
        "reasons": list(publication.reasons),
        "unresolved": list(publication.unresolved),
        "compile_status": compile_status,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def compile_publication(publication_tex: str) -> tuple[str, str]:
    """编译出版级 `.tex` 为 PDF。返回 `(compile_status, note)`。

    `compile_status` 取值与 `publication_gate` 约定一致:
    - `ok`: 编译成功且 PDF 存在;
    - `unavailable`: 本机没有 LaTeX 引擎 (环境性缺项, 记未决而非失败);
    - 其他字符串: 失败原因 (会作为**缺项**挡住"完整论文")。

    PDF 文本层可读性自检也在这里做: 只检查"有没有生成文件、页数是否合理、
    有没有缺字警告" —— 缺字会让 PDF 静默留空, 那种"编译成功"不算交付。
    """
    from pathlib import Path

    from src.rag.latex_compiler import compile_latex

    # 必须 `resolve()`: `package_dir` 可能是相对路径, 而编译器的 workdir 相对
    # **当前工作目录**解析, 于是产物落进 `out/research/<pid>/<snap>/out/research/...`
    # 这样的套娃目录, `tex_path.with_suffix(".pdf")` 又找不到它 —— 结果是
    # "编译其实成功、出版门槛却报没有 PDF"(实测)。
    tex_path = Path(publication_tex).resolve()
    package_dir = tex_path.parent
    try:
        ok, log = compile_latex(str(tex_path), workdir=str(package_dir))
    except Exception as e:  # noqa: BLE001 - 编译异常不得影响研究结论
        return f"编译异常: {type(e).__name__}: {e}", f"出版级 PDF 编译异常: {e}"
    (package_dir / "compile_log.txt").write_text(log or "", encoding="utf-8")
    pdf_path = tex_path.with_suffix(".pdf")
    if "未安装" in (log or "") or "No such file or directory" in (log or ""):
        return "unavailable", "本机没有可用的 LaTeX 引擎: 只交付 .tex"
    if not pdf_path.exists():
        errors = [line for line in (log or "").splitlines() if line.startswith("!")][:3]
        reason = "；".join(errors) or "编译未产出 PDF"
        return f"failed: {reason}", f"出版级 PDF 编译失败: {reason}"
    missing = _missing_glyphs(log or "")
    if missing:
        # 缺字是隐形的交付缺陷: 文件在, 但正文里有字符是空的
        return (f"failed: PDF 缺字 {', '.join(missing[:5])}",
                f"出版级 PDF 缺失字形 {', '.join(missing[:5])}: 已生成的文件存在空白字符, "
                "按不通过处理")
    pages = _pdf_pages(log or "")
    note = f"出版级 PDF 已生成 ({pages} 页)" if pages else "出版级 PDF 已生成"
    if not ok:
        note += "；编译器报告非零状态, 但产物存在 (见 compile_log.txt)"
    return "ok", note


def _missing_glyphs(log: str) -> list[str]:
    import re

    return sorted(set(re.findall(r"Missing character: There is no (\S+)", log or "")))


def _pdf_pages(log: str) -> int:
    import re

    match = re.findall(r"Output written on .*\((\d+) pages?", log or "")
    return int(match[-1]) if match else 0


def _write_json(path, payload) -> None:
    import json

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _route_after_finalize(state: TheoryState) -> str:
    """收尾后: 若写作缺口回流出**阻塞义务**, 必须回到研究循环真正消解它。

    为什么需要这条回边: 理论图的 `run()` 有"缺口回流 → 重开研究循环"的内层循环,
    但 Web/图路径是逐步驱动 (`step()` + 一次 `finalize()`), **不经过那段循环**。
    此前 `theory_finalize → END`, 于是回流出的阻塞义务只被"登记"却永远无人处理,
    运行以"有一条未关闭义务"收尾、无法成文 (现场 #11 正是这条)。
    上限 `THEORY_REFINALIZE_MAX` 防止"缺口永远消不掉"造成死循环。
    """
    if state.get("needs_clarification"):
        return "end"
    if not state.get("feedback_reopened"):
        return "end"
    limit = _refinalize_limit()
    if int(state.get("finalize_rounds", 0) or 0) >= limit:
        return "end"
    return "step"


def _refinalize_limit() -> int:
    """收尾次数上限 (含最终交付那一次)。`THEORY_REFINALIZE_MAX=1` 表示不重入。"""
    import os

    try:
        return max(1, int(os.getenv("THEORY_REFINALIZE_MAX", "3") or 3))
    except ValueError:
        return 3


def _route_after_bootstrap(state: TheoryState) -> str:
    if state.get("needs_confirmation") and not state.get("needs_clarification"):
        return "confirm"
    if state.get("needs_clarification") or not state.get("bootstrapped") or state.get("done"):
        return "finalize"
    return "step"


def _route_after_step(state: TheoryState) -> str:
    # 回流周期内: 每完成一步就回收尾复核 (缺口义务是否已关闭 / 是否又发现新缺口)。
    # 不能只看 `done`: 消解缺口后引擎可能仍有别的可做动作, 但"缺口是否已关闭"必须由
    # 收尾复核, 否则会一直做下去而不交付。
    if state.get("feedback_reopened"):
        return "finalize"
    return "feedback" if state.get("human_feedback") else (
        "finalize" if state.get("done") else "step")


def _route_after_feedback(state: TheoryState) -> str:
    """反馈后: 需要澄清时直接收尾输出未决报告, 否则继续研究循环。"""
    if state.get("needs_clarification"):
        return "finalize"
    return "finalize" if state.get("done") else "step"


def build_theory_pipeline(checkpointer=None):
    graph = StateGraph(TheoryState)
    graph.add_node("theory_bootstrap", theory_bootstrap_node)
    graph.add_node("theory_confirm", theory_confirm_node)
    graph.add_node("theory_step", theory_step_node)
    graph.add_node("theory_feedback", theory_feedback_node)
    graph.add_node("theory_finalize", theory_finalize_node)

    graph.set_entry_point("theory_bootstrap")
    graph.add_conditional_edges("theory_bootstrap", _route_after_bootstrap,
                                {"confirm": "theory_confirm", "step": "theory_step",
                                 "finalize": "theory_finalize"})
    graph.add_edge("theory_confirm", "theory_bootstrap")
    graph.add_conditional_edges("theory_step", _route_after_step,
                                {"step": "theory_step", "feedback": "theory_feedback",
                                 "finalize": "theory_finalize"})
    graph.add_conditional_edges("theory_feedback", _route_after_feedback,
                                {"step": "theory_step", "finalize": "theory_finalize"})
    # 收尾不是终点: 写作缺口回流出的阻塞义务要回到研究循环处理 (见 _route_after_finalize)
    graph.add_conditional_edges("theory_finalize", _route_after_finalize,
                                {"step": "theory_step", "end": END})

    if checkpointer is None:
        checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def _get_persistent_checkpointer():
    """持久化图检查点 (计划书 §9.1: **从首次运行就启用**)。

    返回 (checkpointer, degradation_note)。降级时把原因作为可展示的说明返回,
    而不是静默用内存检查点冒充"完整持久化"。
    """
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        from src.config import DATA_DIR

        db_path = DATA_DIR / "theory_checkpoints.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(conn), ""
    except Exception as e:  # noqa: BLE001
        note = (f"图检查点降级为内存: {e}. 进程退出后**无法跨进程恢复图状态**; "
                "研究结论仍在 SQLite 研究库中, 可用同一项目重新进入研究循环.")
        print(f"  [warning] theory SQLite checkpointer 不可用: {e}")
        return MemorySaver(), note


def _checkpoint_state(app, config: dict) -> dict:
    """读取该 thread 的已保存图状态 (没有检查点时返回空 dict)。

    R6: `resume` 必须依据**真实检查点**判断能否继续, 而不是只看落盘规格就假定
    "可以续跑"。取值失败时返回空 → 调用方按"无检查点"处理并明确告知。
    """
    try:
        values = dict(app.get_state(config).values or {})
    except Exception:  # noqa: BLE001 - 检查点不可读时按无检查点处理
        return {}
    return values


def resolve_snapshot_check(input_snapshot: dict | None, saved: dict | None, *,
                           resume: bool, project_id: str = "", problem_id: str = "",
                           allow_degraded_resume: bool = False, store=None,
                           ) -> tuple[dict, SnapshotCheck, str]:
    """续跑前置校验: 输入快照里的附件是否仍可用 (R6 收尾)。

    返回 (写入 manifest 的快照, 校验结论, 降级说明)。

    - 快照来源: 调用方显式传入优先 (Web 启动路径); 续跑时**以检查点保存的那一份为准**
      —— 只信调用方重新拼的参数会漏掉"上次真正用了哪些附件、sha256 是什么";
    - `resume=True` 且校验不通过时**拒绝续跑** (`InputSnapshotRejected`): 异常在
      进入图之前抛出, 因此不会写出任何新的交付包, 已有的研究结论保持原样;
    - 调用方显式要求 `allow_degraded_resume=True` 时才降级继续, 并把"为什么不可
      复现"作为降级说明返回 (由调用方写入 notes / manifest)。
    """
    if input_snapshot:
        provided = dict(input_snapshot)
    else:
        provided = dict((saved or {}).get("input_snapshot") or {})
    if not provided:
        return {}, SnapshotCheck(ok=True, reproducible=True, basis="本次运行没有输入快照"), ""
    check = verify_input_snapshot(provided)
    if not (resume and not check.ok):
        return merge_verification(provided, check), check, ""
    detail = _snapshot_rejection(check, project_id, problem_id)
    if store is not None:
        try:
            store.append_event("input_snapshot_rejected",
                               {"project_id": project_id, "problem_id": problem_id,
                                "reason": check.reason, "attachments": check.attachments})
        except Exception:  # noqa: BLE001 - 审计失败不得改变"拒绝续跑"这个结论
            pass
    if not allow_degraded_resume:
        print(f"  [resume] {detail}")
        if store is not None:
            # 拒绝后调用方不会再使用该连接; 这里关掉, 免得留下悬空的 sqlite 句柄
            try:
                store.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响"拒绝续跑"的结论
                pass
        raise InputSnapshotRejected(check, provided)
    # 显式要求降级时才继续: 快照原样带上, 但 reproducible 取**校验结论** (False)
    degrade_note = detail.replace("\n", " ")
    print(f"  [resume] 已按调用方要求降级继续: {degrade_note}")
    return merge_verification(provided, check), check, degrade_note


def _snapshot_rejection(check: SnapshotCheck, project_id: str, problem_id: str) -> str:
    """续跑被拒时的可读说明 (含逐份附件的具体原因)。"""
    lines = [f"拒绝续跑 {project_id}/{problem_id}: {check.reason}"]
    for item in check.attachments:
        if item.get("status") != "ok":
            lines.append(f"  - {item.get('filename') or item.get('attachment_id')} "
                         f"[{item.get('status')}]: {item.get('detail')}")
    lines.append("  研究结论与已有交付包保持原样, 未写出新的交付包; "
                 "请恢复附件原件后重试, 或用 start/fork 以新的输入重新研究。")
    return "\n".join(lines)


def run_theory_pipeline(
    request: str = "",
    topic: str = "",
    project_id: str = "",
    problem_id: str = "problem",
    max_actions: int = 40,
    max_tool_calls: int = 60,
    resume: bool = False,
    selected_candidate: int | None = None,
    interactive: bool = False,
    checkpointer=None,
    source_set_id: str = "",
    source_set_kind: str = "kb",
    source_policy: str = "user_kb",
    input_snapshot: dict | None = None,
    allow_degraded_resume: bool = False,
    resume_value=None,
) -> dict:
    """CLI/Web 入口: 建立问题契约与规格, 跑完研究循环并导出交付包。

    - `start` (默认): 建立新规格并从头研究;
    - `resume=True`: **复用已落盘的规格**, 不重新生成规格与候选 (§9.1); 且续跑前
      必须校验输入快照里的附件仍可用, 不一致时**拒绝续跑** (`InputSnapshotRejected`),
      除非调用方显式要求 `allow_degraded_resume=True` (那会在 manifest 里标
      `reproducible: False` 并写明原因);
    - `input_snapshot`: 不可变的启动输入 (附件 id/文件名/sha256、资料集与策略、预算);
    - `resume_value`: 图停在 interrupt 上时, 用它越过中断继续 (等价于用户已回答);
      只给 `resume=True` 用, 否则研究不会前进。
    - `source_set_id`: 绑定资料源 (P0-2 会据此判定研究类型);
    - 研究方向 → 生成候选问题, 交互模式暂停等待选择 (或由 selected_candidate 指定)。

    检查点默认启用持久化; 降级时会显式写入 notes, 不静默冒充可跨进程恢复。
    """
    import uuid

    from src.kb.sources import build_source_summary
    from src.research.question_planner import build_spec_from_input

    project_id = project_id or (f"proj-{uuid.uuid4().hex[:8]}")
    store = ResearchStore(project_id)

    degradation_note = ""
    spec = None
    if resume:
        # resume: 复用已落盘规格 (不重建), 缺失时才回退到解析输入
        data = store.get(KIND_SPEC, problem_id)
        if data:
            from src.research.schemas import ResearchSpec

            spec = ResearchSpec.model_validate(data)
            request = request or spec.original_request
            topic = topic or spec.problem_statement
            print(f"  [resume] 复用已落盘规格 {problem_id} (v{spec.version}), 不重新生成候选")
    if spec is None:
        spec = build_spec_from_input(
            request=request, topic=topic, project_id=project_id, problem_id=problem_id,
            source_summary=build_source_summary(
                source_set_id=source_set_id, source_set_kind=source_set_kind,
                request=request or topic))
        if source_set_id.strip():
            spec.source_set_id = source_set_id.strip()
            spec.source_set_kind = source_set_kind or "kb"
        from src.research.schemas import SourcePolicy

        spec.source_policy = SourcePolicy(source_policy or "user_kb")
        store.put(KIND_SPEC, problem_id, spec.model_dump(mode="json"))

    if checkpointer is None:
        checkpointer, degradation_note = _get_persistent_checkpointer()
    app = build_theory_pipeline(checkpointer)
    config = {"configurable": {"thread_id": f"theory:{project_id}:{problem_id}"}}
    saved = _checkpoint_state(app, config)
    # 判定类问题的成功判据 (结论明确 / 依据点名 / 不得穷举 / 不得推广到其他参数)
    _record_design_success_conditions(spec, store)

    entry_snapshot: dict | None = None
    if not input_snapshot:
        # 续跑时不要用"本次重建的快照"顶掉检查点里那一份: 重建出来的没有附件记录,
        # 拿它去校验等于把"上次真正用了哪些附件"整段丢掉 (manifest 也就答不上来)。
        # 检查点里没有快照时才退回重建 (此时本次输入就是全部已知条件)。
        if not (resume and saved.get("input_snapshot")):
            entry_snapshot = _entry_input_snapshot(
                spec, request=request, topic=topic, source_set_id=source_set_id,
                source_set_kind=source_set_kind, source_policy=source_policy,
                max_actions=max_actions, max_tool_calls=max_tool_calls)
    else:
        # 调用方显式传入的启动快照优先 (Web 启动路径带着附件 hash 与资料集上下文)
        entry_snapshot = dict(input_snapshot)

    # R6 收尾: 续跑/恢复前先校验输入快照里的附件是否仍可用 (读文件重算 sha256)。
    # 为什么必须在**进入图之前**判定: 一旦放行, 后续节点就会拿"已经变过的输入"
    # 继续研究并写出新的交付包, 而 manifest 仍会写着当初的 sha256 —— 那正是
    # "看起来可完整复现"的假象。拒绝在这一步发生, 因此不会产出任何新交付包。
    # 这里刻意放在"上次已收尾"的分支之前: 那种情况下我们同样会把已有的包当作
    # "可复现的交付"交回给调用方, 输入变了就不能这么说。
    effective_snapshot, snapshot_check, degrade_note = resolve_snapshot_check(
        entry_snapshot, saved, resume=resume, project_id=project_id,
        problem_id=problem_id, allow_degraded_resume=allow_degraded_resume,
        store=store)

    # R6: `resume` 的语义必须明确 —— 之前无论是否有检查点都 `stream(initial)`,
    # 于是"恢复"实际是**从头再跑一遍图**。这里区分三种情况:
    #   1. 已有落盘检查点 → 真正从检查点继续 (`stream(None, config)`);
    #   2. 检查点显示上一次已收尾 → 不静默重跑, 报告已有交付并建议 start/fork;
    #   3. 没有检查点 → 只能从头开始, 并且必须把这件事说出来。
    if resume and saved.get("snapshot_id"):
        final = dict(saved)
        final["resumed"] = False
        final["resume_note"] = (
            f"检查点显示该问题上次已收尾 (快照 {saved['snapshot_id']}, 运行 "
            f"{saved.get('run_id') or '未知'}), 未重新运行研究循环; "
            "需要继续研究请用 fork/新 problem_id, 需要重跑请用 start")
        if effective_snapshot:
            final["input_snapshot"] = dict(effective_snapshot)
        if not snapshot_check.reproducible:
            final["snapshot_reproducible"] = False
        print(f"  [resume] {final['resume_note']}")
        return final

    run_id = str(saved.get("run_id", "") or "")
    if not run_id:
        run_id = f"{problem_id}_{uuid.uuid4().hex[:8]}"
    # 请求文本常常是整段问题描述: 直接当主题会让交付物标题变成一整段粘贴文本
    from src.utils.file_utils import derive_topic

    display_topic = derive_topic(topic or request) or problem_id
    initial: TheoryState = {
        "project_id": project_id,
        "problem_id": problem_id,
        "topic": display_topic,
        "request": request or topic,
        "mode": "theory",
        "run_id": run_id,
        "budget_max_actions": max_actions,
        "budget_max_tool_calls": max_tool_calls,
        "step_index": 0,
        "interactive": bool(interactive),
        "selected_candidate_id": "",
        "current_phase": "theory_start",
    }
    if degradation_note:
        initial["notes"] = [degradation_note]
    if selected_candidate is not None:
        initial["selected_candidate"] = selected_candidate
    if entry_snapshot:
        # 把本次生效的输入快照放进图状态: 收尾节点据此写 manifest, 续跑也据此校验。
        # 续跑时以**检查点里那一份**为准 (已通过校验), 否则会用本次重建的空附件快照
        # 覆盖掉上次真正使用的输入, manifest 就再也回答不了"这次用了哪些附件"。
        fresh_snapshot = (effective_snapshot
                          if (resume and saved and effective_snapshot)
                          else entry_snapshot)
        initial["input_snapshot"] = dict(fresh_snapshot)
    if effective_snapshot:
        # 本次运行实际生效的输入快照 (含逐份附件的校验结论), 供调用方/前端复核
        final_snapshot = dict(effective_snapshot)
    else:
        # 没有启动快照 (直连图节点/测试): 收尾节点会写退化快照, 这里给同一份,
        # 保证返回值与 manifest 描述的是同一个输入
        final_snapshot = merge_verification(
            _degraded_input_snapshot(initial),
            SnapshotCheck(ok=True, reproducible=False,
                          basis="直接调用未提供附件/资料集上下文"))
        snapshot_check = SnapshotCheck(ok=True, reproducible=False,
                                       basis="直接调用未提供附件/资料集上下文")

    inputs: dict | None = initial
    from_checkpoint = False
    resume_note = ""
    if resume and saved:
        from_checkpoint = True
        if resume_value is not None:
            # 图停在 interrupt 上时必须用 `Command(resume=...)` 才能越过中断继续 -
            # 传 None 只会把待处理的 interrupt 再抛一次 (研究不会前进)。
            from langgraph.types import Command

            inputs = Command(resume=resume_value)
        else:
            inputs = None    # 从检查点继续 (图会重新抛 interrupt 等待用户响应)
        print(f"  [resume] 从检查点继续 {config['configurable']['thread_id']} "
              f"(已完成 {saved.get('step_index', 0)} 步)"
              + (f"; 输入快照校验: {snapshot_check.reason}" if effective_snapshot else ""))
    elif resume:
        # 注意: 图节点的 notes 会被引擎自己的 notes 覆盖, 因此这句话必须在
        # 运行结束后**再挂回返回值**, 否则用户看不到"这次其实是从头开始"。
        resume_note = ("没有找到该问题的检查点: 本次不是「从断点继续」, 而是从头开始 "
                       f"(问题 {problem_id})")
        print("  [resume] 未找到检查点, 本次将从头开始研究循环")

    for event in app.stream(inputs, config):
        for node_name, node_state in event.items():
            # 交互模式的 interrupt 事件不是节点输出 (值是一个元组), 跳过即可 ——
            # 这里描述"当前在做哪一步", 中断本身由交互式入口负责呈现。
            if not isinstance(node_state, dict):
                continue
            phase = node_state.get("current_phase", node_name)
            print(f"  [{phase}] 完成")
    try:
        final = dict(app.get_state(config).values)
    except Exception:  # noqa: BLE001
        final = {}
    final["resumed"] = bool(from_checkpoint)
    final["input_snapshot"] = final_snapshot
    if not snapshot_check.reproducible:
        # 结论必须同时能在返回值里看到 (manifest 只在收尾节点写入)
        final["snapshot_reproducible"] = False
    if degrade_note:
        final.setdefault("notes", [])
        if degrade_note not in final["notes"]:
            final["notes"] = list(final["notes"]) + [degrade_note]
        final["resume_degraded"] = True
    if resume_note:
        final.setdefault("notes", [])
        if resume_note not in final["notes"]:
            final["notes"] = list(final["notes"]) + [resume_note]
        final["resume_note"] = resume_note
    if degradation_note:
        final.setdefault("notes", [])
        if degradation_note not in final["notes"]:
            final["notes"] = list(final["notes"]) + [degradation_note]
        final["checkpoint_persistent"] = False
    else:
        final["checkpoint_persistent"] = True
    return final








