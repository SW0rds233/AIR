from __future__ import annotations

"""统一团队外层循环 (合并计划 §3 / §4.2 / §10 M1)。

它替代的是"按 `stages` 列表线性跑节点"的旧编排 (§4.2: "不能把旧 `stages` 列表
换个名字继续线性运行")。这里的外层只有一套团队入口, 循环体是:

    主控决策 → 运行时派发任务 → 子智能体提交成果 → 运行时校验/登记 → 主控复盘

子智能体内部可以有工具循环或子图, 但它们不决定团队该怎么走 —— 决定权在主控,
而**执行与状态写入**在运行时与判定层 (职责分离)。

第一版形态 (§10 M1): 串行执行, 同进程。合并计划 §10 明确要求"M4 中的资源隔离应随
M1 开始处理, **完成前保持串行角色执行及受控会话并发**", 因此这里不做并行调度;
并行是可选的显式开关, 且必须带上预算预留与失效传播。
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ContextPack,
    ResearchNeed,
    ReviewIssueRef,
    RunBudget,
    TaskOutcome,
    TaskStatus,
    UsageRecord,
)
from src.agents.registry import describe_team
from src.agents.runtime import AgentRuntime, CancelToken, InProcessExecutor
from src.agents.supervisor import (
    DecisionKind,
    ResearchBrief,
    SupervisorAgent,
    SupervisorDecision,
    TeamPlan,
    assignment_key,
    unresolved_report,
)
from src.research.projection import object_rows
from src.research.schemas import ObjectRef, new_id
from src.research.task_store import (
    TaskStore,
    new_agent_run,
    summarize_recovery,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TeamLoopState",
    "TeamRun",
    "TeamRunOutcome",
    "build_default_team",
    "run_team",
]


@dataclass
class TeamLoopState:
    """主控循环的显式状态 (合并计划 §15.3 第 1 步)。

    为什么必须显式: 会话引擎要"一步一事件、可中断、可从磁盘续跑", 而把状态埋在
    `run()` 的局部变量里时, 轮次之间外部无法接手 (也没法在不重复派工的前提下恢复)。
    字段含义与旧实现的局部变量**一一对应**, 因此 `run()` 改为 `prepare()`+反复
    `step()` 后行为不变。

    幂等/不重复派工依赖 `objectives`(角色+目标 → 历史)、`dispatched`(已派发键)、
    `pending_retries`(放行重试的键) 三者的组合判据, 三者都必须随状态一起保留。
    """

    brief: ResearchBrief | None = None
    plan: TeamPlan | None = None
    results: dict[str, AgentResult] = field(default_factory=dict)
    open_needs: list[ResearchNeed] = field(default_factory=list)
    #: 派工历史: `task_id -> objective`。判"补派是否等价于原任务完成"必须用它 ——
    #: 计划克隆后 task_id 会变, 只看当前计划会漏掉历史成果。
    objectives: dict[str, str] = field(default_factory=dict)
    #: 允许重试的"角色+目标"键。**默认不允许**: 同一件事实失败两次不会因为再派
    #: 一次就成功, 只会把预算烧光 (实测过)。依据版本变了才在这里放行。
    pending_retries: set[str] = field(default_factory=set)
    #: 本运行已派发过的"角色+目标"键。计划任务与补派任务共用, 防止 replan
    #: 克隆出的同键任务被反复派发。
    dispatched: set[str] = field(default_factory=set)
    clarifications: int = 0
    rounds: int = 0
    stop_reason: str = ""
    finished: bool = False

    def snapshot(self) -> dict[str, Any]:
        """给会话引擎/界面用的**只读摘要** (不含正文, 只含身份与状态)。"""
        return {
            "rounds": self.rounds,
            "finished": self.finished,
            "stop_reason": self.stop_reason,
            "tasks": sorted(self.results),
            "open_needs": len(self.open_needs),
            "clarifications": self.clarifications,
        }


@dataclass
class TeamRunOutcome:
    """一次团队运行的产出 (给入口/界面/交付包)。"""

    run_id: str = ""
    brief: ResearchBrief | None = None
    plan: TeamPlan | None = None
    decisions: list[SupervisorDecision] = field(default_factory=list)
    results: dict[str, AgentResult] = field(default_factory=dict)
    task_order: list[str] = field(default_factory=list)
    status: str = "queued"
    rounds: int = 0
    stop_reason: str = ""
    open_needs: list[ResearchNeed] = field(default_factory=list)
    issues: list[ReviewIssueRef] = field(default_factory=list)
    unresolved_report: dict[str, Any] = field(default_factory=dict)
    #: 交付评估 (等级 + 三道门槛的理由, 见 `research/delivery.py`)。**不是**"角色跑过"
    #: 的摘要: 它由研究有效性/论文表达/出版完备门槛算出 (§3.2 G12)。
    delivery: dict[str, Any] = field(default_factory=dict)
    usage: UsageRecord = field(default_factory=UsageRecord)

    def completed_results(self) -> list[AgentResult]:
        return [r for r in self.results.values()
                if r.outcome in (TaskOutcome.completed, TaskOutcome.partial)]

    def needs_human(self) -> bool:
        return any(t.needs_human for d in self.decisions for t in d.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "brief": self.brief.to_dict() if self.brief else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "decisions": [d.to_dict() for d in self.decisions],
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "task_order": list(self.task_order),
            "status": self.status,
            "rounds": self.rounds,
            "stop_reason": self.stop_reason,
            "open_needs": [n.to_dict() for n in self.open_needs],
            "issues": [i.to_dict() for i in self.issues],
            "unresolved_report": dict(self.unresolved_report),
            "delivery": dict(self.delivery),
            "usage": self.usage.to_dict(),
        }


def build_default_team() -> InProcessExecutor:
    """注册七个功能角色 (合并计划 §3 的默认团队)。"""
    from src.agents.evidence import EvidenceAgent
    from src.agents.figures import FigureAgent
    from src.agents.modeling import ModelingAgent
    from src.agents.reasoning import ReasoningAgent
    from src.agents.review import ReviewAgent
    from src.agents.validation_planning import ValidationPlanningAgent
    from src.agents.writing import WritingAgent

    executor = InProcessExecutor(AgentRuntime())
    for agent in (EvidenceAgent(), ModelingAgent(), ReasoningAgent(),
                  ValidationPlanningAgent(), WritingAgent(), FigureAgent(),
                  ReviewAgent()):
        executor.register(agent, prompt_version=agent.prompt_version)
    return executor


class TeamRun:
    """团队运行编排器: 主控循环 + 任务执行 + 成果登记。

    它只管流程与登记; 科学判定 (命题状态、验证等级、交付等级) 在判定层。因此这里
    既不写命题状态, 也不把"任务完成"当成"研究完成" —— `AgentTask` 完成只表示
    "这一小步交回了成果", 是否继续由主控按缺口判断。
    """

    def __init__(self, *, project_id: str, problem_id: str = "", run_id: str = "",
                 request: str = "", supervisor: SupervisorAgent | None = None,
                 executor: InProcessExecutor | None = None,
                 runtime: AgentRuntime | None = None,
                 task_store: TaskStore | None = None,
                 projection: Any | None = None,
                 llm_factory: Callable[[str], Any] | None = None,
                 max_rounds: int = 24,
                 run_budget: RunBudget | None = None,
                 attachments: list[dict[str, Any]] | None = None,
                 attachment_text: str = "",
                 source_set_ids: Iterable[str] = (),
                 source_policy: str = "user_kb",
                 autonomous_retrieval: bool = False,
                 cancel: CancelToken | None = None,
                 sink: Any = None) -> None:
        self.project_id = project_id
        self.problem_id = problem_id or new_id("prob")
        self.run_id = run_id or new_id("run")
        self.request = request
        self.cancel = cancel or CancelToken()
        self.runtime = runtime or AgentRuntime(sink=sink, cancel=self.cancel,
                                               llm_factory=llm_factory,
                                               run_budget=run_budget)
        self.runtime.cancel = self.cancel
        if llm_factory is not None:
            self.runtime._llm_factory = llm_factory
        if run_budget is not None:
            self.runtime.run_budget = run_budget
        self.executor = executor or build_default_team()
        self.executor.runtime = self.runtime
        # 角色通过运行时拿到核验服务 (读本运行的研究库做账本与输入闭包); 对象写入
        # 仍然只走唯一提交口 —— 角色没有直接写库的路径。
        self.runtime.research_store = None
        #: 主控自己的模型调用用量 (它不在某个任务里, 但仍要计入本次运行 ——
        #: 否则"团队真的调了模型"这件事在用量里看不出来)。
        self._supervisor_usage = UsageRecord()
        self.supervisor = supervisor or SupervisorAgent(llm=self._supervisor_model())
        self.task_store = task_store or TaskStore(self.project_id)
        self._owns_task_store = task_store is None
        self.runtime.research_store = self.task_store.store
        if projection is None:
            from src.research.projection import TeamProjection

            # 投影绑定本运行: 查询默认只返回本次运行产出的对象 (§3.3 G14)
            projection = TeamProjection(self.task_store.store, run_id=run_id)
        self.projection = projection
        self.max_rounds = max(1, max_rounds)
        self.attachments = list(attachments or [])
        self.attachment_text = attachment_text
        self.source_set_ids = [s for s in source_set_ids if s]
        self.source_policy = source_policy
        self.autonomous_retrieval = autonomous_retrieval
        self.outcome = TeamRunOutcome(run_id=self.run_id)
        self._context_builder: Callable[[AgentTask, dict[str, Any]],
                                        ContextPack] | None = None
        self._cached_default_builder: Any = None
        self._source_set_rows: list[dict[str, Any]] = []
        #: 唯一提交口 (§6.2): 懒建, 与投影共用同一研究存储。
        self._commit: Any = None
        #: 主控循环的**显式状态** (合并计划 §15.3 第 1 步)。
        #: 抽出来是为了让循环可以**逐轮驱动** (会话引擎需要"一步一事件 + 可中断"),
        #: 而不是把状态埋在 `run()` 的局部变量里 —— 那样外部无法在轮次之间接手。
        self.loop = TeamLoopState()

    # ---- 生命周期 ----
    def _supervisor_model(self):
        """主控的角色模型 (缺省从运行时解析, 并**计入本运行用量**)。

        为什么不能省这一步 (§3.1 G02 / G04): `SupervisorAgent(llm=None)` 时主控永远走
        规则展开, 而它在界面上与"模型理解过任务"长得一模一样。审计复现过这条
        (`llm_calls == 0`), 修完 G04 之后**接线漏在装配层**: 团队入口建 `TeamRun` 时
        仍写死 `llm=None`, 于是模型被解析出来却从未交给主控。

        用量也必须记: 主控的调用不属于任何任务, 不记就让"这次运行用了多少模型"
        少掉一块, 而 `llm_calls == 0` 恰好是审计判断"有没有真调模型"的判据。
        """
        model = self.runtime._resolve_llm("supervisor")
        if model is None:
            return None
        from src.bootstrap import metered_llm

        return metered_llm(model, self._supervisor_usage)

    def close(self) -> None:
        if self._owns_task_store:
            self.task_store.close()

    def __enter__(self) -> TeamRun:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def set_context_builder(self, builder: Callable[[AgentTask, dict[str, Any]],
                                                    ContextPack]) -> None:
        """注入上下文组装器 (由入口按存储提供研究对象快照)。

        没有注入时**缺省走投影层**: 下游角色能看到上游登记的证据/结论/稿件。
        """
        self._context_builder = builder
        self._cached_default_builder = None

    def set_source_sets(self, rows: list[dict[str, Any]]) -> None:
        """登记本任务可用的资料源摘要 (只放可读范围, 不放全文)。"""
        self._source_set_rows = list(rows or [])
        self._cached_default_builder = None

    def _persist_spec(self) -> None:
        """把这次研究的**输入规格**落盘 (团队运行同样要有可查的问题规格)。

        为什么团队也要写 (§6.1 "一份事实模型"): 工作台、问题列表与交付清单都按
        `problem_id` 找规格。团队路径此前不写规格, 于是同一个项目在团队模式下
        "没有研究问题" —— 界面读不到任何对象, 只读端点直接 404, 尽管对象就在库里。

        **不覆盖已有规格**: 规格是冻结的输入契约 (可能是理论路径或上一次运行写的),
        覆盖它等于悄悄改掉"这次研究的问题定义"。
        """
        from src.research.schemas import SourcePolicy
        from src.research.store import KIND_SPEC

        try:
            if self.task_store.store.get(KIND_SPEC, self.problem_id):
                return
            from src.research.question_planner import build_spec_from_input

            spec = build_spec_from_input(
                request=self.request, topic="", project_id=self.project_id,
                problem_id=self.problem_id)
            spec.source_set_id = (self.source_set_ids[0] if self.source_set_ids else "")
            try:
                spec.source_policy = SourcePolicy(self.source_policy)
            except ValueError:
                spec.source_policy = SourcePolicy.user_kb
            self.task_store.store.put(KIND_SPEC, self.problem_id,
                                      spec.model_dump(mode="json"))
            self.runtime.emit("spec_persisted", {
                "project_id": self.project_id, "problem_id": self.problem_id,
                "research_type": spec.research_type})
        except Exception as e:  # noqa: BLE001 - 规格落盘失败不得阻断研究, 但要可见
            self.runtime.emit("spec_persist_failed",
                              {"problem_id": self.problem_id, "reason": str(e)})

    # ---- 主循环: 准备 + 逐轮 ----
    def prepare(self) -> ResearchBrief:
        """建立 `ResearchBrief` 与首版 `TeamPlan` (**幂等**: 已准备过就直接返回)。

        为什么把"准备"和"逐轮"分开: 会话引擎要先拿到画像才能决定是不是接着派工,
        并且要在 interrupt/续跑时**不重复生成计划**(重复生成会换掉任务身份,
        依赖边与幂等键都会失效 —— 这正是 M1 修过的缺陷)。
        """
        if self.loop.brief is not None and self.loop.plan is not None:
            return self.loop.brief
        brief = self.supervisor.brief(
            self.request, project_id=self.project_id, problem_id=self.problem_id,
            attachments=self.attachments, attachment_text=self.attachment_text,
            source_set_ids=self.source_set_ids, source_policy=self.source_policy,
            autonomous_retrieval=self.autonomous_retrieval)
        self.loop.brief = brief
        self.outcome.brief = brief
        plan = self.supervisor.plan(brief, version=1,
                                    source_set_ids=self.source_set_ids,
                                    # 身份由运行时统一注入: 计划里每条任务都必须带
                                    # run_id, 否则投影无法区分同项目的两次运行
                                    run_identity={"project_id": self.project_id,
                                                  "problem_id": self.problem_id,
                                                  "run_id": self.run_id})
        self.loop.plan = plan
        self.outcome.plan = plan
        # 输入规格也要落盘: 工作台、问题列表、交付清单都按 `problem_id` 找规格, 团队
        # 运行此前不写规格 —— 于是同一个项目在团队路径下"没有研究问题", 界面读不到
        # 对象、`resolve_problem` 直接 404 (§6.1 一份事实模型)。
        self._persist_spec()
        # 画像/计划一算出来就落盘: "任务落盘不等于恢复团队" (§3.3 G16) —— 只有画像
        # 与计划也在磁盘上, 续跑才能复用**同一批任务身份与依赖边**, 而不是重新画像。
        self.save_state()
        self.runtime.emit("brief_ready", {"project_id": self.project_id,
                                          "problem_id": self.problem_id,
                                          "brief": brief.to_dict()})
        self.runtime.emit("plan_ready", {"plan_id": plan.plan_id,
                                         "version": plan.version,
                                         "tasks": plan.task_ids()})
        return brief

    def step(self) -> bool:
        """推进**一轮**主控循环; 返回是否应当继续 (`True` = 还有下一轮)。

        单轮做的事 (与 `run()` 内联时逐字一致):
        主控决策 → 派工并执行 → 重规划 → 刷新重试许可; 或按决策收尾。
        `run()` 就是 `prepare()` + 反复 `step()` 直到返回 False —— 因此两条路径
        **共用同一份实现**, 不会出现"逐轮驱动"与"一次跑完"行为不一致。
        """
        self.prepare()
        state = self.loop
        state.rounds += 1
        self.outcome.rounds = state.rounds
        if self.cancel.cancelled:
            self._finish(state, state.stop_reason or self.cancel.reason or "运行被停止",
                         status="stopped")
            self.runtime.emit("run_stopped", {"reason": state.stop_reason})
            return False

        decision = self.supervisor.decide(
            brief=state.brief, plan=state.plan, results=state.results,
            pending=[t for t in state.plan.task_ids() if t not in state.results],
            running=[], open_needs=state.open_needs,
            remaining_budget=self.runtime.run_budget,
            clarifications_asked=state.clarifications, objectives=state.objectives,
            pending_retries=state.pending_retries, dispatched=state.dispatched)
        self.outcome.decisions.append(decision)
        self.runtime.emit("supervisor_decision", {
            "round": state.rounds, "decision": decision.decision.value,
            "reason": decision.reason, "note": decision.note,
            "tasks": [t.task_id for t in decision.tasks],
        })

        if decision.decision == DecisionKind.dispatch:
            if not decision.tasks:
                self._finish(state, "主控决定派工但没有给出任务")
                return False
            state.open_needs = []
            for task in decision.tasks:
                key = assignment_key(state.brief, task.agent, task.objective)
                state.pending_retries.discard(key)   # 本次已放行, 不重复放行
                state.dispatched.add(key)
                need_key = str((task.hints or {}).get("need_assignment", ""))
                if need_key:
                    state.dispatched.add(need_key)
                # 补派/返工任务**并入**计划 (保持既有任务身份与依赖边不变)
                if task.task_id not in state.plan.edges:
                    self.supervisor.adopt(state.plan, task)
                result = self._execute_one(task, state.results)
                state.results[task.task_id] = result
                state.objectives[task.task_id] = task.objective
                self.outcome.task_order.append(task.task_id)
                state.open_needs.extend(result.followup_needs)
                state.plan = self.supervisor.replan(
                    state.plan, {task.task_id: result}, version=state.plan.version)
                self.outcome.plan = state.plan
            # 依据版本变了 -> 允许重试此前失败过的"角色+目标"组合。
            #
            # 判据是**该任务实际依据的对象的版本**(它自己的输入引用与 read-set),
            # 而不是全局版本计数 —— 后者每轮都因为新登记对象而变化, 等于给所有
            # 失败任务无限重试许可 (实测: 同一件事被派 17 次)。
            self._refresh_retry_allowance(state.brief, state.results, state.objectives,
                                          state.pending_retries)
            # 每轮结束都落盘: 进程被杀时最多丢掉"本轮正在执行的那一个动作",
            # 已交回的成果与派工历史不会重跑 (合并计划 §3.3 G16)。
            self.save_state()
            if state.rounds >= self.max_rounds:
                # 轮次上限也是**一个出口**, 因此必须走同一 finalize (§3.2 G12):
                # 此前这里直接 `return False`, 于是达到上限的运行停在 status="queued"、
                # 没有停止原因、也没有交付评估 —— 界面与用例看到的是"没跑过"。
                self._finish(state, f"达到轮次上限 {self.max_rounds}",
                             status=TaskStatus.partial.value)
                return False
            return True

        if decision.decision == DecisionKind.wait:
            self._finish(state, decision.reason or "任务在等待依赖")
            return False

        if decision.decision == DecisionKind.request_clarification:
            state.clarifications += 1
            self._finish(state, decision.reason, status="waiting_user")
            return False

        if decision.decision == DecisionKind.deliver:
            self._finish(state, decision.reason, status=TaskStatus.completed.value)
            return False

        # stop_with_report
        self._finish(state, decision.reason, status=TaskStatus.partial.value)
        return False

    def _finish(self, state: TeamLoopState, stop_reason: str,
                status: str = "") -> None:
        """收尾: 落定状态、未决报告与用量, 并发出 `run_finished`。

        **交付不升级** (§3.2 G12): 主控说"交付"只是**循环该结束了**, 不等于"研究已
        验收通过"。因此这里按研究有效性/论文表达/出版完备三道门槛评估产出, 门槛未过
        一律把状态从 `completed` 降为 `partial`, 并把理由写进未决报告 —— 交付等级
        由 `classify_deliverable` 给出, 绝不因为"角色都跑过"就称完整论文。
        """
        if status:
            self.outcome.status = status
        if not stop_reason:
            stop_reason = "主控循环结束"
        state.stop_reason = stop_reason
        state.finished = True
        self.outcome.stop_reason = stop_reason
        self.outcome.results = state.results
        self.outcome.open_needs = state.open_needs
        self.outcome.issues = self.supervisor.review_issues(state.plan, state.results)
        self.outcome.unresolved_report = unresolved_report(
            state.brief, state.plan, state.results)
        # 用量必须含**主控自己**的模型调用: 它不属任何任务, 漏掉就会让
        # "这次运行有没有真的调模型"在用量里显示为 0 (审计判据之一)。
        self.outcome.usage = _sum_usage(state.results).merge(self._supervisor_usage)
        if self.outcome.status in (TaskStatus.completed.value, TaskStatus.partial.value):
            self._apply_delivery_assessment()
        else:
            # 其它终态 (等澄清/被停止) 没有产出可评估: 如实说明"未评估", 不编一个等级。
            self.outcome.delivery = {
                "level": "", "accepted": False,
                "blocking": [], "unresolved": [],
                "counts": {}, "notes": [f"运行在 {self.outcome.status} 结束, 未做交付评估"],
            }
        # 收尾状态也落盘: 续跑据此知道"这次运行已经结束", 不会重跑一遍 (§3.3 G16)。
        self.save_state(status=self.outcome.status)
        self.runtime.emit("run_finished", {
            "run_id": self.run_id, "status": self.outcome.status,
            "stop_reason": stop_reason, "rounds": self.outcome.rounds,
            "usage": self.outcome.usage.to_dict(),
            "delivery": self.outcome.delivery,
        })

    def _apply_delivery_assessment(self) -> None:
        """用交付门槛复核"主控说可以交付"这件事 (失败即降级, 并给出理由)。"""
        from src.research.delivery import assess_delivery
        from src.research.snapshot import manuscript_markdown

        store = self.task_store.store
        try:
            assessment = assess_delivery(
                store, project_id=self.project_id, problem_id=self.problem_id,
                run_id=self.run_id, manuscript_md=manuscript_markdown(store),
                compile_status="deferred",
                on_skip=lambda payload: self.runtime.emit(
                    "snapshot_export_incomplete", payload))
        except Exception as e:  # noqa: BLE001 - 门槛本身出错不得伪装成"通过"
            self.outcome.delivery = {
                "level": "研究备忘录", "accepted": False,
                "blocking": [f"交付门槛评估失败: {type(e).__name__}: {e}"],
                "unresolved": [], "counts": {},
            }
            self.outcome.status = TaskStatus.partial.value
            self.outcome.stop_reason = (self.outcome.stop_reason +
                                        " | 交付门槛评估失败, 已降级为部分交付")
            self.runtime.emit("delivery_gate_failed",
                              {"reason": str(e), "recoverable": True})
            return
        self.outcome.delivery = assessment.to_dict()
        self.outcome.unresolved_report["delivery"] = assessment.to_dict()
        if not assessment.accepted:
            self.outcome.status = TaskStatus.partial.value
            reason = (f"交付门槛未通过 (等级 {assessment.level}): "
                      + "; ".join((assessment.blocking + assessment.unresolved)[:4]))
            self.outcome.stop_reason = (self.outcome.stop_reason + " | " + reason).strip(" |")
            self.outcome.unresolved_report.setdefault("unresolved", [])
            self.outcome.unresolved_report["unresolved"].extend(
                [*assessment.blocking, *assessment.unresolved])
        self.runtime.emit("delivery_assessed", assessment.to_dict())

    # ---- 主循环 (一次跑完) ----
    def run(self) -> TeamRunOutcome:
        """跑完一轮团队闭环 (= `prepare()` + 反复 `step()`)。"""
        self.prepare()
        while self.step():
            pass
        if not self.loop.finished:
            # 达到轮次上限: 与旧实现同一句话 (措辞参与界面与用例断言)
            self._finish(self.loop, f"达到轮次上限 {self.max_rounds}")
        return self.outcome

    # ---- 单任务执行 ----
    def _execute_one(self, task: AgentTask, results: dict[str, AgentResult],
                     ) -> AgentResult:
        # **把运行身份钉在任务上** (G03): 计划任务与需求任务走的是同一个执行口, 但
        # `needs_to_tasks` 生成的补派任务没有 run_id —— 它提交的对象 `_scope.run_id`
        # 因此为空, 而按运行裁剪的视图 (交付摘要的登记对象、run 过滤的产物清单) 会
        # 把它们算成"不是这次运行产出的" (实测: 工作台有命题, 摘要里 claim 计数为 0)。
        # 身份只在这一个执行口回填, 角色自己不需要 (也不应该) 关心运行身份。
        if not task.run_id:
            task.run_id = self.run_id
        if not task.project_id:
            task.project_id = self.project_id
        if not task.problem_id:
            task.problem_id = self.problem_id
        context = self._build_context(task, results)
        try:
            self.task_store.create(task, plan_id=self.outcome.plan.plan_id
                                   if self.outcome.plan else "")
        except Exception as e:  # noqa: BLE001 - 登记失败不阻断执行, 但要可见
            self.runtime.emit("task_persist_failed",
                              {"task_id": task.task_id, "reason": str(e)})
        run = new_agent_run(task, attempt=self.task_store.next_attempt(task.task_id))
        result = self.executor.execute(task, context)
        result.agent_run_id = result.agent_run_id or run.agent_run_id
        self._persist_result(task, result)
        return result

    def _persist_result(self, task: AgentTask, result: AgentResult) -> None:
        """通过**唯一提交口**落盘任务成果 (合并计划 §3.1 G08 / §6.2)。

        此前这里是 `task_store.finish()` 之后再逐条 `projection.register()` ——
        `TaskStore.commit_result()` 的 read-set 检查与同事务提交能力完全没用上:
        任务终态、对象、事件各自独立提交, 中断时会出现"工具完成了、对象没落盘"。
        现在整批交给 `ResearchCommitService`, 它在**一个事务**里写任务终态、
        接受的对象、义务/命题的判定结果与事件, 并用幂等键防止重放产生第二个对象。

        拒绝理由来自 `finalize()` 已经隔离的候选 (`rejected_changes`) 与提交口自己
        重查的能力表: 越权候选**没有**任何路径进权威对象库。
        """
        service = self._commit_service()
        rejects = [dict(item) for item in (result.rejected_changes or [])]
        watched = dict(result.input_versions or {})
        try:
            outcome = service.commit(
                task, result,
                current_versions=self._current_versions(),
                reject_reasons=rejects,
                events=[("task_finished", {
                    "task_id": task.task_id, "agent": task.agent,
                    "objective": task.objective, "outcome": result.outcome.value,
                    "summary": result.summary})])
        except Exception as e:  # noqa: BLE001 - 提交失败必须可见, 不能静默丢成果
            self.runtime.emit("commit_failed",
                              {"task_id": task.task_id, "agent": task.agent,
                               "reason": f"{type(e).__name__}: {e}"})
            return

        if outcome.stale:
            # 依据的输入在派工之后换代了: 拒绝合入并交给主控决定重做 (不是静默丢弃)
            self.runtime.emit("input_version_conflict", {
                "task_id": task.task_id, "agent": task.agent,
                "stale": {k: list(v) for k, v in outcome.stale.items()},
                "watched": watched,
            })
        if outcome.idempotent:
            self.runtime.emit("commit_replayed", {
                "task_id": task.task_id, "agent": task.agent,
                "agent_run_id": result.agent_run_id,
                "note": "同一 attempt 已提交过, 本次不重复写入"})
        for item in outcome.rejected:
            self.runtime.emit("candidate_rejected", {
                "task_id": task.task_id, "agent": task.agent, **item})
        for proposal in result.proposed_changes:
            object_id = proposal.object_id or ""
            self.runtime.emit("change_proposed", {
                "task_id": task.task_id, "agent": task.agent,
                "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                "object_id": object_id,
                "expected_revision": proposal.expected_revision,
                "registered_version": outcome.versions.get(object_id) if object_id else None,
                "committed": bool(outcome.committed),
            })
        for claim_id, state in outcome.claims.items():
            self.runtime.emit("claim_state_reconciled", {
                "claim_id": claim_id, **state, "source": "commit_service"})

    def _commit_service(self):
        """懒建唯一提交口 (与投影共用同一个研究存储, 不新造第二个库)。"""
        if getattr(self, "_commit", None) is None:
            from src.research.commit import ResearchCommitService

            self._commit = ResearchCommitService(
                self.task_store.store, project_id=self.project_id,
                problem_id=self.problem_id, run_id=self.run_id,
                task_store=self.task_store)
        return self._commit

    def _build_context(self, task: AgentTask,
                       results: dict[str, AgentResult]) -> ContextPack:
        pack = ContextPack(task_id=task.task_id, agent=task.agent,
                           request=self.request, attachments=self.attachments)
        builder = self._context_builder or self._default_context_builder()
        if builder is not None:
            try:
                built = builder(task, results)
            except Exception as e:  # noqa: BLE001 - 组装失败如实记录并继续
                built = None
                pack.notes.append(f"上下文组装失败: {e}")
            if built is not None:
                built.task_id = task.task_id
                built.agent = task.agent
                pack = built
        pack.upstream = pack.upstream or [
            {"task_id": tid, "agent": r.agent, "outcome": r.outcome.value,
             "summary": r.summary, "issues": [i.to_dict() for i in r.issues]}
            for tid, r in list(results.items())[-6:]
        ]
        if self.outcome.brief is not None:
            brief_row = self.outcome.brief.to_dict()
            brief_row.setdefault("id", self.outcome.brief.brief_id)
            pack.objects.setdefault("brief", [brief_row])
        pack.notes.append(f"资料策略 {self.source_policy}")
        return pack

    def _default_context_builder(self):
        """缺省注入投影层: 让下游角色看到上游登记的证据/结论/稿件。

        没有它, 后续角色只能看到请求与资料源摘要, 会如实报"缺输入" —— 那样团队
        永远走不到写作与审阅。**对象范围由 projection 快照决定, 不由角色自己扩大。**
        """
        if self.projection is None:
            return None
        if self._cached_default_builder is not None:
            return self._cached_default_builder
        source_sets = self._source_set_rows
        self._cached_default_builder = make_context_builder(
            brief_getter=lambda: self.outcome.brief,
            object_source=lambda kind: slim_object_rows(
                self.projection.rows(kind) if hasattr(self.projection, "rows")
                else object_rows(self.projection.store, kind)),
            source_sets=source_sets,
            request=self.request,
            attachments=self.attachments,
        )
        return self._cached_default_builder

    # ---- 恢复 ----
    def recovery_view(self) -> dict[str, Any]:
        """可恢复性视图: 已提交/运行中/待派 + **能否重建主控循环**。

        恢复的判据只有一条: 落盘的 `TeamLoopState` 能不能读回来。只报"库里有几条任务"
        是不够的 —— 任务落盘不等于恢复团队 (合并计划 §3.3 G16): 没有画像与计划,
        主控只能从零重新画像并重新派工, 于是"已提交的动作"会被再做一遍。
        """
        view = self.task_store.recoverable()
        restored = self._load_loop_state()
        return {"summary": summarize_recovery(view), **view,
                "loop_state_restorable": restored is not None,
                "resumable": bool(restored and restored.plan and not restored.finished)}

    # ---- 持久化与恢复 (合并计划 §3.3 G16) ----
    def save_state(self, *, status: str = "") -> bool:
        """把主控循环状态落盘 (`KIND_TEAM_RUN`), 返回是否写入成功。

        存的是**可重建循环的最小充分集**: 画像、计划、已交回的成果、派工历史、
        重试许可、轮次与停止原因, 以及运行身份。不存正文 —— 正文在研究对象库与
        交付包里, 这里只有引用与摘要 (与 §6.1 的"图状态只放引用"一致)。
        """
        from src.research.store import KIND_TEAM_RUN

        state = self.loop
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "problem_id": self.problem_id,
            "request": self.request,
            "source_set_ids": list(self.source_set_ids),
            "source_policy": self.source_policy,
            "autonomous_retrieval": bool(self.autonomous_retrieval),
            "attachments": list(self.attachments),
            "attachment_text": self.attachment_text,
            "max_rounds": self.max_rounds,
            "rounds": state.rounds,
            "finished": state.finished,
            "stop_reason": state.stop_reason,
            "clarifications": state.clarifications,
            "status": status or self.outcome.status,
            "brief": state.brief.to_dict() if state.brief else None,
            "plan": state.plan.to_dict() if state.plan else None,
            "objectives": dict(state.objectives),
            "dispatched": sorted(state.dispatched),
            "pending_retries": sorted(state.pending_retries),
            "results": {tid: r.to_dict() for tid, r in state.results.items()},
            "open_needs": [n.to_dict() for n in state.open_needs],
            "task_order": list(self.outcome.task_order),
            "saved_at": _now(),
        }
        try:
            self.task_store.store.put(KIND_TEAM_RUN, self.run_id, payload)
            return True
        except Exception as e:  # noqa: BLE001 - 落盘失败必须可见, 但不阻断运行
            self.runtime.emit("run_state_persist_failed",
                              {"run_id": self.run_id, "reason": str(e)})
            return False

    def _load_loop_state(self) -> TeamLoopState | None:
        """从磁盘重建主控循环状态; 读不回来时返回 `None` (不假装可恢复)。"""
        payload = self._load_run_payload()
        return rebuild_loop_state(payload) if payload else None

    def _load_run_payload(self) -> dict[str, Any]:
        """落盘的运行状态原文 (含收尾状态, 供恢复时如实还原)。"""
        from src.research.store import KIND_TEAM_RUN

        try:
            return dict(self.task_store.store.get(KIND_TEAM_RUN, self.run_id) or {})
        except Exception:  # noqa: BLE001
            return {}

    def submit_feedback(self, *, statement: str, owner: str,
                        target_ref: Any | None = None,
                        why: str = "", acceptance: list[str] | None = None,
                        kind: Any = None, max_rounds: int | None = None) -> dict[str, Any]:
        """把一条用户意见变成**需求**交回主控, 然后继续推进 (合并计划 §5.1)。

        为什么走需求而不是直接改对象: 子智能体之间不互相派工, 人类意见同样必须由
        主控转成任务 —— 否则"谁改了这条结论"就没有单一入口可查。需求进 `open_needs`
        后由 `SupervisorAgent.decide` 转成 `AgentTask`, 成果仍经**唯一提交口**落盘。

        为什么允许"重新开启已收尾的运行": 用户是在看到交付之后才提意见的, 此时运行
        已经 `finished`。这里把状态改回进行中(而不是新建一次运行), 因此**已提交的动作
        不会重跑** —— 画像、计划、派工历史都还在。`stop_reason` 清空并记事件, 让人
        能看到"这次继续是因为用户意见", 而不是把它当成一次新研究。
        """
        from src.agents.protocol import NeedKind, ResearchNeed

        self.prepare()
        need = ResearchNeed(
            kind=kind or NeedKind.clause,
            statement=statement,
            why=why or "用户对当前产出提出意见",
            acceptance=list(acceptance or ["修订结果可查, 或明确说明无法执行的原因"]),
            owner=owner,
            blocking=False,
            blocked_refs=[target_ref] if target_ref is not None else [],
            hints={"feedback": statement[:400]},
        )
        self.loop.open_needs.append(need)
        self.loop.finished = False
        self.loop.stop_reason = ""
        # 用户意见之后必须重新过交付门槛 (等级可能升也可能降), 因此状态回到进行中
        self.outcome.status = TaskStatus.running.value
        self.runtime.emit("user_feedback", {
            "need_id": need.need_id, "owner": owner, "statement": statement[:200],
            "target": (getattr(target_ref, "id", "") if target_ref is not None else "")})
        self.save_state(status=TaskStatus.running.value)
        limit = int(max_rounds if max_rounds is not None else self.max_rounds)
        # **用户意见必须有它自己的轮次额度**: 恢复出来的循环已经用过 `rounds` 轮,
        # 若沿用原上限, `step()` 会因为 `rounds >= max_rounds` 立刻收尾 —— 需求根本
        # 派不出去, 而返回结果看起来像"已处理"(实测)。因此把上限抬到"当前轮次 + 本次额度"。
        self.max_rounds = max(self.max_rounds, self.loop.rounds + limit)
        before = self.loop.rounds
        while self.step() and (self.loop.rounds - before) < limit:
            pass
        if not self.loop.finished:
            self._finish(self.loop, f"用户意见处理后达到轮次上限 {self.max_rounds}",
                         status=TaskStatus.partial.value)
        return {"ok": True, "need_id": need.need_id, "owner": owner,
                "status": self.outcome.status,
                # `rounds` 是绝对轮次 (与恢复出来的循环一致); `rounds_used` 是本次意见
                # 实际用掉的额度 —— 两者含义不同, 混成一个会让"这次处理了几轮"说不清。
                "rounds": self.loop.rounds,
                "rounds_used": self.loop.rounds - before,
                "stop_reason": self.outcome.stop_reason,
                "delivery": dict(self.outcome.delivery),
                "applied": [{"need_id": need.need_id, "owner": owner}]}

    def resume(self) -> TeamRunOutcome:
        """从落盘状态继续这次运行 (已提交的动作**不重跑**)。

        语义 (§9.1 的 start/resume/fork): `resume` = 同一 run 接着跑。因此恢复后:
        - 画像与计划**原样复用**(不重新画像 —— 那会换掉任务身份与依赖边);
        - `results` / `dispatched` / `objectives` / `pending_retries` 一并恢复,
          主控因此不会把已经交回过的那件事再派一次;
        - 已经收尾的运行不再重跑, 直接按**落盘时记下的状态**返回结论
          (不能拿默认状态覆盖它, 否则一次"已完成"会显示成"未跑过")。
        """
        payload = self._load_run_payload()
        restored = rebuild_loop_state(payload) if payload else None
        if restored is None:
            self.runtime.emit("run_state_missing", {"run_id": self.run_id})
            return self.run()
        self.loop = restored
        self.outcome.brief = restored.brief
        self.outcome.plan = restored.plan
        self.outcome.results = restored.results
        self.outcome.open_needs = restored.open_needs
        self.outcome.rounds = restored.rounds
        self.runtime.emit("run_resumed", {
            "run_id": self.run_id, "rounds": restored.rounds,
            "tasks_done": len(restored.results),
            "dispatched": len(restored.dispatched),
            "finished": restored.finished})
        if restored.finished:
            self._finish(restored, restored.stop_reason or "该运行已收尾",
                         status=str(payload.get("status", "") or ""))
            return self.outcome
        while self.step():
            pass
        if not self.loop.finished:
            self._finish(self.loop, f"达到轮次上限 {self.max_rounds}",
                         status=TaskStatus.partial.value)
        return self.outcome

    def _current_versions(self) -> dict[str, int]:
        """当前研究对象版本索引 (用于判断"依据是否变了")。

        投影层不可用时返回空字典 —— 那就没有版本变化可言, 不因此假装有变化。
        """
        if self.projection is None or not hasattr(self.projection, "latest_versions"):
            return {}
        try:
            return dict(self.projection.latest_versions())
        except Exception:  # noqa: BLE001 - 版本索引不可读时按"无变化"处理
            return {}

    def _refresh_retry_allowance(self, brief: ResearchBrief,
                                 results: dict[str, AgentResult],
                                 objectives: dict[str, str],
                                 pending_retries: set[str]) -> None:
        """只对"依据的对象真的换代了"的失败任务放行重试。

        依据集合 = 任务的输入引用 + 它读到的对象版本 (read-set) + 它声明受阻的对象。
        这三者任一对象的当前版本**高于**当时依据的版本, 才说明出现了新条件。
        """
        current = self._current_versions()
        if not current:
            return
        fingerprints: dict[str, str] = getattr(self, "_retry_fingerprints", {})
        for task_id, result in results.items():
            if result.outcome not in (TaskOutcome.failed, TaskOutcome.blocked):
                continue
            objective = objectives.get(task_id, "")
            if not objective:
                continue
            watched = dict(result.input_versions or {})
            for need in result.followup_needs:
                for ref in need.blocked_refs:
                    watched.setdefault(ref.id, ref.version)
            fingerprint = "|".join(f"{k}@{v}" for k, v in sorted(watched.items()))
            before = fingerprints.get(task_id)
            fingerprints[task_id] = fingerprint
            if before is None:
                continue                      # 首次记录: 不视为"依据变了"
            if before == fingerprint:
                continue
            key = assignment_key(brief, result.agent, objective)
            if any(current.get(obj_id, 0) > int(version)
                   for obj_id, version in watched.items()):
                pending_retries.add(key)
        self._retry_fingerprints = fingerprints


def run_team(request: str, *, project_id: str, problem_id: str = "",
             run_id: str = "", **kwargs: Any) -> TeamRunOutcome:
    """便捷入口: 建一次团队运行并跑完。"""
    with TeamRun(project_id=project_id, problem_id=problem_id, run_id=run_id,
                 request=request, **kwargs) as team:
        return team.run()


def rebuild_loop_state(payload: dict[str, Any]) -> TeamLoopState | None:
    """落盘载荷 → `TeamLoopState` (读不回来时返回 `None`, 不半真半假地恢复)。

    字段缺失/类型不对一律按"不可恢复"处理: 一个只恢复了一半的循环比拒绝恢复更危险
    —— 它会用空的派工历史重跑已经做过的事。
    """
    try:
        state = TeamLoopState()
        brief_row = payload.get("brief")
        plan_row = payload.get("plan")
        state.brief = ResearchBrief.model_validate(brief_row) if brief_row else None
        state.plan = TeamPlan.model_validate(plan_row) if plan_row else None
        state.results = {str(tid): AgentResult.model_validate(row)
                         for tid, row in (payload.get("results") or {}).items()}
        state.objectives = {str(k): str(v)
                            for k, v in (payload.get("objectives") or {}).items()}
        state.dispatched = {str(v) for v in (payload.get("dispatched") or [])}
        state.pending_retries = {str(v) for v in (payload.get("pending_retries") or [])}
        state.open_needs = [ResearchNeed.model_validate(row)
                            for row in (payload.get("open_needs") or [])]
        state.rounds = int(payload.get("rounds", 0) or 0)
        state.clarifications = int(payload.get("clarifications", 0) or 0)
        state.finished = bool(payload.get("finished", False))
        state.stop_reason = str(payload.get("stop_reason", "") or "")
        return state
    except Exception:  # noqa: BLE001 - 载荷不完整就按不可恢复处理
        return None


def make_context_builder(*, brief_getter: Callable[[], ResearchBrief | None],
                         object_source: Callable[[str], list[dict[str, Any]]],
                         source_sets: list[dict[str, Any]] | None = None,
                         gaps: list[dict[str, Any]] | None = None,
                         attachments: list[dict[str, Any]] | None = None,
                         request: str = "") -> Callable[[AgentTask,
                                                         dict[str, AgentResult]],
                                                        ContextPack]:
    """构造"按任务裁剪的角色视图"组装器 (合并计划 §6.1: 角色视图由 context 层组装)。

    只放**引用与摘要**: 每条对象的 `text/abstract/excerpt` 截断到展示长度, 正文留在
    存储与产物里。这样图状态与提示词都不会出现"复制一份可写状态"。
    """
    def _builder(task: AgentTask, results: dict[str, AgentResult]) -> ContextPack:
        brief = brief_getter()
        pack = ContextPack(task_id=task.task_id, agent=task.agent, request=request,
                           attachments=list(attachments or []),
                           sources=list(source_sets or []),
                           gaps=list(gaps or []))
        for kind in ("claim", "obligation", "model", "evidence", "verification",
                     "validation_plan", "manuscript", "figure", "gap", "dataset",
                     "case", "revision_task", "review_issue"):
            rows = object_source(kind)
            if rows:
                pack.objects[kind] = rows
        if brief is not None:
            row = brief.to_dict()
            row.setdefault("id", brief.brief_id)
            pack.objects["brief"] = [row]
        pack.upstream = [
            {"task_id": tid, "agent": r.agent, "outcome": r.outcome.value,
             "summary": r.summary, "issues": [i.to_dict() for i in r.issues]}
            for tid, r in list(results.items())[-8:]
        ]
        return pack
    return _builder


#: 进入角色上下文的字段白名单 (其余字段按摘要裁剪, 避免大文本进提示词)。
_CONTEXT_TEXT_KEYS = ("text", "abstract", "excerpt", "quote", "search_text", "body")


def slim_object_rows(rows: list[dict[str, Any]], *, text_limit: int = 600,
                     limit: int = 40) -> list[dict[str, Any]]:
    """把存储行裁剪为角色视图 (长文本截断, 数量上限)。"""
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        slim: dict[str, Any] = {}
        for key, value in row.items():
            if key in _CONTEXT_TEXT_KEYS and isinstance(value, str):
                slim[key] = value[:text_limit]
            elif key == "sections" and isinstance(value, list):
                slim[key] = _slim_sections(value, text_limit)
            else:
                slim[key] = value
        out.append(slim)
    return out


def _slim_sections(sections: list[Any], text_limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in sections[:20]:
        if not isinstance(section, dict):
            continue
        blocks = [{
            "block_id": b.get("block_id", ""), "role": b.get("role", ""),
            "text": str(b.get("text", ""))[:text_limit],
            "refs": b.get("refs", []), "ref_kinds": b.get("ref_kinds", []),
            "needs_check": b.get("needs_check", False), "math": b.get("math", ""),
        } for b in (section.get("blocks") or []) if isinstance(b, dict)]
        out.append({"heading": section.get("heading", ""),
                    "role": section.get("role", ""), "blocks": blocks})
    return out


def _now() -> str:
    """当前时刻 (落盘时间戳; 与项目其它地方的 ISO 格式一致)。"""
    from src.research.schemas import utcnow

    return utcnow()


def _sum_usage(results: dict[str, AgentResult]) -> UsageRecord:
    total = UsageRecord()
    for result in results.values():
        total = total.merge(result.usage)
    return total


def team_capabilities() -> list[dict[str, Any]]:
    """当前团队的真实可用能力 (界面与验收使用)。"""
    return describe_team()


def task_for_role(plan: TeamPlan, role: str) -> list[AgentTask]:
    """从计划里取某角色的任务 (界面按角色显示任务数, 而不是重复头像)。"""
    records = [r for r in plan.tasks if r.get("agent") == role]
    return [task for task in (_restore_task(r) for r in records) if task is not None]


def _restore_task(record: dict[str, Any]) -> AgentTask | None:
    try:
        return AgentTask(**record)
    except Exception:  # noqa: BLE001 - 损坏的计划记录跳过而不是崩掉界面
        return None


def object_refs_from(rows: Iterable[dict[str, Any]]) -> list[ObjectRef]:
    """把存储行投影为 `ObjectRef` (只带身份与版本)。"""
    return [ObjectRef(id=str(row.get("id", "")), version=int(row.get("version", 1) or 1))
            for row in rows if row.get("id")]
