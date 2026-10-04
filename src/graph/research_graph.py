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
        self.supervisor = supervisor or SupervisorAgent(llm=None)
        self.task_store = task_store or TaskStore(self.project_id)
        self._owns_task_store = task_store is None
        if projection is None:
            from src.research.projection import TeamProjection

            projection = TeamProjection(self.task_store.store)
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
        #: 主控循环的**显式状态** (合并计划 §15.3 第 1 步)。
        #: 抽出来是为了让循环可以**逐轮驱动** (会话引擎需要"一步一事件 + 可中断"),
        #: 而不是把状态埋在 `run()` 的局部变量里 —— 那样外部无法在轮次之间接手。
        self.loop = TeamLoopState()

    # ---- 生命周期 ----
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
                                    source_set_ids=self.source_set_ids)
        self.loop.plan = plan
        self.outcome.plan = plan
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
            return state.rounds < self.max_rounds

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
        """收尾: 落定状态、未决报告与用量, 并发出 `run_finished`。"""
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
        self.outcome.usage = _sum_usage(state.results)
        self.runtime.emit("run_finished", {
            "run_id": self.run_id, "status": self.outcome.status,
            "stop_reason": stop_reason, "rounds": self.outcome.rounds,
            "usage": self.outcome.usage.to_dict(),
        })

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
        try:
            self.task_store.finish(task.task_id, result)
        except Exception as e:  # noqa: BLE001
            self.runtime.emit("task_persist_failed",
                              {"task_id": task.task_id, "reason": str(e)})
        # 候选变更由投影层**登记**为可查询的版本化对象 (不是判定: 它不写命题真值)。
        if self.projection is not None and result.proposed_changes:
            try:
                versions = self.projection.register(task, result)
            except Exception as e:  # noqa: BLE001
                versions = {}
                self.runtime.emit("projection_failed",
                                  {"task_id": task.task_id, "reason": str(e)})
            for proposal in result.proposed_changes:
                self.runtime.emit("change_proposed", {
                    "task_id": task.task_id, "agent": task.agent,
                    "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                    "object_id": proposal.object_id,
                    "expected_revision": proposal.expected_revision,
                    "registered_version": versions.get(
                        proposal.object_id or proposal.proposal_id),
                })

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
        view = self.task_store.recoverable()
        return {"summary": summarize_recovery(view), **view}

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
