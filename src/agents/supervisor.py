from __future__ import annotations

"""SupervisorAgent 主控: 理解任务、组队、派工、复盘、交付 (合并计划 §3.1 / §4, M1)。

与旧 `TheoryEngine` 的分工
--------------------------
旧实现里 `TheoryEngine` 同时承担编排、研究对象变更、动作实现、记账、验证与冻结
—— 合并计划 §2 明确说它"不能继续作为主控下面第二个全权总控"。这里把**科研决策**
抽出来: 主控只产出 `ResearchBrief` / `TeamPlan` / `AgentTask` / `SupervisorDecision`,
执行与状态写入交给运行时与判定层。

主控循环 (§4.2)
---------------
每轮读取当前画像、相关对象、待办/运行中任务、结果与缺口、剩余预算, 给出以下之一:
`dispatch` / `request_clarification` / `wait` / `deliver` / `stop_with_report`。
每次派工必须说明: 要回答哪个子问题、预期获得什么新信息、依赖哪些结果、什么算完成、
失败如何处理 —— 因此 `AgentTask` 上这些字段是**契约**, 不是可选说明。

离线可用
--------
`brief()` 与 `decide()` 在无 LLM 时走确定性规则 (题面解析 + 缺口规则), 且**如实标注
依据** (`basis`); 有 LLM 时由模型提议任务类型与子问题, 但提议必须通过同一契约校验
(合并计划 §4.1: "LLM 不能绕过量词/域/方法限制")。

澄清只在"不同答案会改变目标、方法或授权"时提出 (§4.1); 其余在已声明的假设下推进。
"""

import json
import re
from collections.abc import Iterable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ResearchNeed,
    ReviewIssueRef,
    RunBudget,
    TaskBudget,
    TaskOutcome,
    idempotency_key_for,
    utcnow,
)
from src.research.schemas import (
    ObjectRef,
    SourcePolicy,
    new_id,
    stable_id,
)

__all__ = [
    "DELIVERABLES",
    "SUBQUESTION_KINDS",
    "ResearchBrief",
    "SubQuestion",
    "SupervisorAgent",
    "SupervisorDecision",
    "TeamPlan",
    "classify_request",
    "needs_to_tasks",
]


# ----------------------------------------------------------------------
# 可组合任务画像 (§4.1 的三个维度)
# ----------------------------------------------------------------------
#: 子问题类型。一个任务可以有多类子问题, 因此这里是列表而不是单一枚举 ——
#: 这正是"用一个 survey/theory/hybrid 枚举决定所有行为"要避免的。
SUBQUESTION_KINDS: tuple[str, ...] = (
    "literature_synthesis",     # 文献综合
    "existence_proof",          # 存在性/证明
    "mechanism",                # 机理解释
    "model_construction",       # 模型构造
    "case_comparison",          # 案例比较
    "data_availability",        # 数据可用性分析
    "validation_plan",          # 验证方案
)

#: 交付形态。
DELIVERABLES: tuple[str, ...] = (
    "source_list",              # 文献清单
    "problem_report",           # 问题报告
    "theoretical_conclusion",   # 理论结论
    "full_paper",               # 完整论文草稿
    "figures",                  # 图表
    "review_report",            # 审稿意见
    "validation_proposal",      # 验证建议 (未执行)
)


class SubQuestion(BaseModel):
    """一个可独立派工的子问题。"""

    subquestion_id: str = Field(default_factory=lambda: new_id("sq"))
    statement: str
    kind: str = "mechanism"
    #: 需要什么才算回答 (供派工的 acceptance_criteria 引用)。
    needs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    owner: str = ""             # 建议承接角色
    priority: int = 0

    @model_validator(mode="after")
    def _check_kind(self) -> SubQuestion:
        if self.kind not in SUBQUESTION_KINDS:
            raise ValueError(f"未登记的子问题类型: {self.kind!r}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"subquestion_id": self.subquestion_id, "statement": self.statement,
                "kind": self.kind, "needs": list(self.needs),
                "depends_on": list(self.depends_on), "owner": self.owner,
                "priority": self.priority}


class ResearchBrief(BaseModel):
    """主控对任务的理解 (§4.1)。

    必须分开记录三个维度, 并**显式保留未知字段** —— 不因一个关键词强行设成因果研究。
    """

    brief_id: str = Field(default_factory=lambda: new_id("brief"))
    version: int = 1
    project_id: str = ""
    problem_id: str = ""
    original_request: str = ""
    #: 附件来源 (id + basename), 不复制正文。
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    main_question: str = ""
    subquestions: list[SubQuestion] = Field(default_factory=list)
    #: 研究对象 (变量/对象/量词/约束/单位)。空值表示未识别, 不猜。
    objects: list[str] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)
    quantifiers: str = ""
    constraints: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    #: 已有成果 (版本化引用)。
    existing_refs: list[ObjectRef] = Field(default_factory=list)
    #: 资料权限与能力。
    source_set_ids: list[str] = Field(default_factory=list)
    source_policy: str = SourcePolicy.user_kb.value
    autonomous_retrieval: bool = False
    may_execute: bool = False        # 是否允许执行仿真/实验 (首版一律 False)
    #: 输出要求。
    deliverables: list[str] = Field(default_factory=list)
    output_language: str = ""
    #: 未知字段与识别依据 —— 人审要能看出"哪些是规则定的、哪些是模型定的"。
    unknown_fields: list[str] = Field(default_factory=list)
    basis: str = ""
    created_at: str = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _check_deliverables(self) -> ResearchBrief:
        unknown = [d for d in self.deliverables if d not in DELIVERABLES]
        if unknown:
            raise ValueError(f"未登记的交付形态: {', '.join(unknown)}")
        return self

    def add_subquestion(self, statement: str, kind: str, *, owner: str = "",
                        needs: Iterable[str] = (), priority: int = 0) -> SubQuestion:
        existing = next((s for s in self.subquestions
                         if s.statement == statement and s.kind == kind), None)
        if existing is not None:
            return existing
        sub = SubQuestion(statement=statement, kind=kind, owner=owner,
                          needs=list(needs), priority=priority)
        self.subquestions.append(sub)
        return sub

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id, "version": self.version,
            "project_id": self.project_id, "problem_id": self.problem_id,
            "original_request": self.original_request, "attachments": list(self.attachments),
            "main_question": self.main_question,
            "subquestions": [s.to_dict() for s in self.subquestions],
            "objects": list(self.objects), "variables": list(self.variables),
            "quantifiers": self.quantifiers, "constraints": list(self.constraints),
            "success_conditions": list(self.success_conditions),
            "existing_refs": [r.model_dump() for r in self.existing_refs],
            "source_set_ids": list(self.source_set_ids),
            "source_policy": self.source_policy,
            "autonomous_retrieval": self.autonomous_retrieval,
            "may_execute": self.may_execute,
            "deliverables": list(self.deliverables),
            "output_language": self.output_language,
            "unknown_fields": list(self.unknown_fields), "basis": self.basis,
            "created_at": self.created_at,
        }


class TeamPlan(BaseModel):
    """版本化的团队计划: 派了哪些任务、依赖关系、验收标准。"""

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    version: int = 1
    brief_id: str = ""
    tasks: list[dict[str, Any]] = Field(default_factory=list)   # AgentTask.to_dict()
    #: 依赖边: task_id -> 依赖的 task_id 列表。
    edges: dict[str, list[str]] = Field(default_factory=dict)
    rationale: str = ""
    created_at: str = Field(default_factory=utcnow)

    def add_task(self, task: AgentTask) -> None:
        self.tasks = [t for t in self.tasks if t.get("task_id") != task.task_id]
        self.tasks.append(task.to_dict())
        self.edges[task.task_id] = [d for d in task.depends_on]

    def task_ids(self) -> tuple[str, ...]:
        return tuple(str(t.get("task_id", "")) for t in self.tasks)

    def ready(self, completed: Iterable[str], running: Iterable[str] = ()) -> list[str]:
        """依赖已满足且未在处理中的任务 (串行/并行由调用方决定)。"""
        done = set(completed)
        busy = set(running)
        out = []
        for task_id, deps in self.edges.items():
            if task_id in done or task_id in busy:
                continue
            if all(d in done for d in deps):
                out.append(task_id)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "version": self.version,
                "brief_id": self.brief_id, "tasks": list(self.tasks),
                "edges": {k: list(v) for k, v in self.edges.items()},
                "rationale": self.rationale, "created_at": self.created_at}


class DecisionKind(str, Enum):
    """主控每轮的结构化决策 (§4.2)。"""

    dispatch = "dispatch"
    request_clarification = "request_clarification"
    wait = "wait"
    deliver = "deliver"
    stop_with_report = "stop_with_report"


class SupervisorDecision(BaseModel):
    """一轮主控决策 (可落盘、可展示)。"""

    decision: DecisionKind
    reason: str = ""
    tasks: list[AgentTask] = Field(default_factory=list)
    clarification: str = ""
    note: str = ""
    #: 决策依据的对象引用 (为什么这么派)。
    basis_refs: list[ObjectRef] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.value, "reason": self.reason,
                "tasks": [t.to_dict() for t in self.tasks],
                "clarification": self.clarification, "note": self.note,
                "basis_refs": [r.model_dump() for r in self.basis_refs],
                "created_at": self.created_at}


# ----------------------------------------------------------------------
# 题面解析 (规则部分; LLM 提议必须通过同一校验)
# ----------------------------------------------------------------------
_PROOF_MARKERS = ("证明", "是否存在", "存在性", "不存在", "判定", "prove", "exists",
                  "nonexistence", "是否可能")
_MECHANISM_MARKERS = ("机理", "机制", "为什么", "原因", "如何影响", "影响", "mechanism",
                      "why", "explain")
_CASE_MARKERS = ("案例", "个案", "比较", "经验", "case", "survey of cases")
_DATA_MARKERS = ("数据", "数据集", "样本", "面板", "回归", "估计", "csv", "dataset")
_VALIDATION_MARKERS = ("验证方案", "验证办法", "验证建议", "实验设计", "仿真",
                       "检验方案", "怎么验证", "如何验证", "validation", "simulation")
_SYNTHESIS_MARKERS = ("综述", "研究进展", "文献", "总结", "梳理", "review", "survey")
_MODEL_MARKERS = ("模型", "建模", "方程", "公式", "model")

#: 交付形态的规则关键词。
_DELIVERABLE_MARKERS: dict[str, tuple[str, ...]] = {
    "full_paper": ("论文", "成文", "撰写", "写作", "paper", "manuscript", "write up"),
    "source_list": ("文献清单", "文献列表", "bibliography", "list of papers"),
    "figures": ("配图", "绘图", "图表", "figure", "plot"),
    "review_report": ("审稿", "审阅意见", "review report"),
    "validation_proposal": ("验证建议", "实验建议", "validation plan"),
    "theoretical_conclusion": ("结论", "判定", "theorem", "conclusion"),
}

#: 量词/约束的规则识别 (只识别**明确写出**的, 不推断)。
_QUANTIFIER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"对所有|任意|任意给定|∀|for all|for every", "forall"),
    (r"存在|∃|there exists", "exists"),
    (r"不存在|没有这样的|no such", "not_exists"),
)


def classify_request(text: str) -> tuple[list[str], list[str], str]:
    """规则识别 (子问题类型, 交付形态, 依据说明)。

    合并计划 §4.1 要求: 规则分类作为**检查与回退**, 主控语义提议必须通过同一契约。
    因此这里的返回值不直接决定执行, 而是作为 brief 的初始值与校验基线。
    识别不出领域时**不做任何领域假设**。
    """
    body = (text or "").strip()
    lowered = body.lower()
    kinds: list[str] = []

    def hit(markers: Iterable[str]) -> bool:
        return any(m in body or m in lowered for m in markers)

    if hit(_SYNTHESIS_MARKERS):
        kinds.append("literature_synthesis")
    if hit(_PROOF_MARKERS):
        kinds.append("existence_proof")
    if hit(_MECHANISM_MARKERS):
        kinds.append("mechanism")
    if hit(_MODEL_MARKERS):
        kinds.append("model_construction")
    if hit(_CASE_MARKERS):
        kinds.append("case_comparison")
    if hit(_DATA_MARKERS):
        kinds.append("data_availability")
    if hit(_VALIDATION_MARKERS):
        kinds.append("validation_plan")
    if not kinds:
        kinds.append("mechanism")     # 兜底: 不假设领域, 只按一般研究处理

    deliverables: list[str] = []
    for name, markers in _DELIVERABLE_MARKERS.items():
        if hit(markers):
            deliverables.append(name)
    if not deliverables:
        deliverables = ["problem_report"]

    basis = ("规则识别: 匹配到的子问题类型 " + "/".join(kinds)
             + "; 交付形态 " + "/".join(deliverables)
             + ("; 未匹配到领域词, 不做领域假设" if not (
                 hit(_CASE_MARKERS) or hit(_DATA_MARKERS)) else ""))
    return kinds, deliverables, basis


def _detect_quantifier(text: str) -> tuple[str, bool]:
    for pattern, name in _QUANTIFIER_PATTERNS:
        if re.search(pattern, text or ""):
            return name, True
    return "", False


def _compose_main_question(request: str, attachment_text: str = "") -> str:
    """主问题 = 请求文本 + 附件题面 (附件是**来源**, 不因此变成指令)。"""
    parts = [p.strip() for p in (request or "", attachment_text or "") if p and p.strip()]
    return "\n\n".join(parts)


# ----------------------------------------------------------------------
# 主控
# ----------------------------------------------------------------------
#: 每种交付形态对主控的"必须完成的角色"要求 (§3.1 的成果归属)。
_DELIVERABLE_ROLES: dict[str, tuple[str, ...]] = {
    "source_list": ("evidence",),
    "problem_report": ("reasoning",),
    "theoretical_conclusion": ("reasoning",),
    "full_paper": ("reasoning", "writing", "review"),
    "figures": ("figures",),
    "review_report": ("review",),
    "validation_proposal": ("validation",),
}


class SupervisorAgent:
    """科研主控。

    它是**纯决策**组件: 不直接读写研究存储, 不调用工具, 不执行研究动作。
    执行由 `graph/research_graph.py` 驱动, 状态写入由判定层负责。
    """

    role = "supervisor"
    prompt_version = "supervisor/v1"

    def __init__(self, *, llm: Any | None = None, max_plan_version: int = 8,
                 max_tasks_per_round: int = 3) -> None:
        self.llm = llm
        self.max_plan_version = max_plan_version
        self.max_tasks_per_round = max(1, max_tasks_per_round)

    # ---- 理解任务 ----
    def brief(self, request: str, *, project_id: str = "", problem_id: str = "",
              attachments: list[dict[str, Any]] | None = None,
              attachment_text: str = "",
              source_set_ids: Iterable[str] = (), source_policy: str = "user_kb",
              autonomous_retrieval: bool = False, may_execute: bool = False,
              budget_note: str = "") -> ResearchBrief:
        """产出 `ResearchBrief`。

        输入精确时保持用户的定义与边界; 输入仅是方向时提出少量有区别的路线
        (§4.1)。未知字段**保留为未知**并列出 —— 不因一个关键词强行设定类型。
        """
        kinds, deliverables, basis = classify_request(
            f"{request}\n{attachment_text}".strip())
        quantifier, quantifier_known = _detect_quantifier(
            f"{request}\n{attachment_text}")
        brief = ResearchBrief(
            project_id=project_id, problem_id=problem_id,
            original_request=request or "",
            attachments=list(attachments or []),
            main_question=_compose_main_question(request, attachment_text),
            source_set_ids=[s for s in source_set_ids if s],
            source_policy=source_policy,
            autonomous_retrieval=bool(autonomous_retrieval),
            may_execute=bool(may_execute),
            deliverables=deliverables,
            quantifiers=quantifier,
        )
        if not quantifier_known:
            brief.unknown_fields.append("quantifiers")
        if not request.strip() and not attachment_text.strip():
            brief.unknown_fields.append("main_question")
        if not list(source_set_ids) and source_policy == SourcePolicy.user_kb.value:
            brief.unknown_fields.append("source_set_ids")
        brief.basis = basis + (f"; 预算说明: {budget_note}" if budget_note else "")

        # 子问题: 由类型展开, 每类一条; "文献清单"不需要综合, 纯综述也不伪造证明义务。
        owner_map = {
            "literature_synthesis": "reasoning",
            "existence_proof": "reasoning",
            "mechanism": "reasoning",
            "model_construction": "modeling",
            "case_comparison": "evidence",
            "data_availability": "evidence",
            "validation_plan": "validation",
        }
        for index, kind in enumerate(kinds):
            brief.add_subquestion(
                statement=f"{_KIND_LABELS.get(kind, kind)}: {_short(request or attachment_text)}",
                kind=kind, owner=owner_map.get(kind, "reasoning"), priority=index,
                needs=["给出可核查的结论或明确的未决说明"],
            )
        # 资料需求: 只要需要检索就必须有一条独立子问题 (§3.1: 检索覆盖文献/案例/数据)
        if "existence_proof" in kinds or "literature_synthesis" in kinds \
                or "mechanism" in kinds or autonomous_retrieval:
            brief.add_subquestion(
                statement="收集支撑或反驳候选结论的可定位来源",
                kind="literature_synthesis", owner="evidence", priority=9,
                needs=["逐条给出出处定位", "标注支持/冲突关系, 不靠标题相似声称支持"],
            )
        # 交付需要写作时, 写作与审阅各自成为子问题 (而不是藏在某一步里)
        if "full_paper" in deliverables or "theoretical_conclusion" in deliverables:
            brief.add_subquestion(
                statement="把结论与依据写成连贯稿件", kind="mechanism", owner="writing",
                priority=20, needs=["段落可回溯到来源或推导"])
            # 独立审阅 (§3.1): 论文类交付必须过一遍独立检查, 而不是作者自评
            brief.add_subquestion(
                statement="从原始任务与证据独立审阅稿件与图表",
                kind="literature_synthesis", owner="review", priority=30,
                needs=["逐项给出问题、严重度与可核验的验收标准",
                       "科学问题与文字问题分开处理"])
        return brief

    # ---- 组队 ----
    def plan(self, brief: ResearchBrief, *, version: int | None = None,
             input_refs: Iterable[ObjectRef] = (), source_set_ids: Iterable[str] = (),
             budget: TaskBudget | None = None,
             depends_on: dict[str, list[str]] | None = None,
             run_identity: dict[str, str] | None = None) -> TeamPlan:
        """把画像变成**版本化**团队计划。

        每个子问题一条 `AgentTask`, 带上子问题目标、预期收益、验收标准、依赖与预算。
        第一阶段串行执行, 因此依赖关系如实写出, 由运行时决定何时可跑。

        `run_identity` (`project_id`/`problem_id`/`run_id`) 会写进**每条**任务:
        审计复现过"计划的 `run_id` 全为空"(§3.3 G14), 于是
        `team_projection()` 只能把无 run 的任务算进任意一次运行 —— 同项目两次运行
        的对象会互相串。身份由**运行时统一注入** (而不是要求每个调用点都记得传),
        只在任务自己已显式写了该字段时才保留任务的值。
        """
        plan_version = version if version is not None else (brief.version or 1)
        plan = TeamPlan(version=plan_version, brief_id=brief.brief_id,
                        rationale=brief.basis)
        # `project_id` / `problem_id` 由下面显式传入, 因此身份里只补**运行身份**;
        # 直接 `**identity` 会与显式关键字撞车 (TypeError: multiple values)。
        identity = {"run_id": str((run_identity or {}).get("run_id", "") or "")}
        task_by_sub: dict[str, str] = {}
        depends = dict(depends_on or {})
        for sub in sorted(brief.subquestions, key=lambda s: s.priority):
            if not sub.owner:
                continue
            task = AgentTask(
                agent=sub.owner,
                objective=sub.statement,
                subquestion=sub.subquestion_id,
                expected_gain="; ".join(sub.needs) or "给出可核查的新信息",
                project_id=brief.project_id, problem_id=brief.problem_id,
                run_id=identity["run_id"],
                plan_version=plan_version,
                input_refs=list(input_refs),
                depends_on=[task_by_sub[d] for d in depends.get(sub.subquestion_id, [])
                            if d in task_by_sub],
                acceptance_criteria=list(sub.needs),
                budget=(budget.model_copy(deep=True) if budget else TaskBudget()),
                source_set_ids=list(source_set_ids) or list(brief.source_set_ids),
                source_policy=brief.source_policy,
                on_failure="按缺口记录失败原因并交回主控重新规划",
            )
            plan.add_task(task)
            task_by_sub[sub.subquestion_id] = task.task_id
        # 资料是下游的前提: 让同一子问题内"检索"先于"写作/审阅"由 plan 的依赖表达
        self._wire_default_dependencies(plan)
        return plan

    @staticmethod
    def _wire_default_dependencies(plan: TeamPlan) -> None:
        """把"必须先有依据"的默认依赖写进计划 (§5.4 串行条件)。

        只用**已存在**的依赖边做加边, 不改任务本身: 写作依赖推理与检索,
        审阅依赖写作。结论前提变更时的失效传播由判定层处理, 不在这里假装完成。
        """
        by_role: dict[str, list[str]] = {}
        for record in plan.tasks:
            by_role.setdefault(str(record.get("agent", "")), []).append(
                str(record.get("task_id", "")))
        prerequisites = {"writing": ("reasoning", "evidence", "modeling"),
                         "figures": ("reasoning", "evidence"),
                         "review": ("writing", "reasoning", "figures")}
        for role, needed in prerequisites.items():
            for task_id in by_role.get(role, []):
                extra = [t for src in needed for t in by_role.get(src, [])]
                if not extra:
                    continue
                merged = list(dict.fromkeys([*plan.edges.get(task_id, []), *extra]))
                plan.edges[task_id] = merged
                for record in plan.tasks:
                    if record.get("task_id") == task_id:
                        record["depends_on"] = merged

    # ---- 派工决策 ----
    def decide(self, *, brief: ResearchBrief, plan: TeamPlan,
               results: dict[str, AgentResult] | None = None,
               pending: Iterable[str] = (), running: Iterable[str] = (),
               open_needs: Iterable[ResearchNeed] = (),
               remaining_budget: RunBudget | None = None,
               delivered: bool = False, stopped: bool = False,
               clarifications_asked: int = 0,
               satisfied: Iterable[str] = (),
               objectives: dict[str, str] | None = None,
               pending_retries: Iterable[str] = (),
               dispatched: Iterable[str] = ()) -> SupervisorDecision:
        """给出本轮的**一种**结构化判断 (§4.2)。

        顺序有讲究:
        1. 停止/已交付 -> 直接收尾, 不再派工;
        2. 有跨职责需求 -> 转成新任务 (这是"受阻处理"的实现, 不是让子智能体自己找活);
        3. 依赖已满足的任务 -> 派发 (按计划顺序, 本轮有上限, 避免一次全放出去);
        4. 有任务在跑 -> wait;
        5. 一切都完成 -> deliver;
        6. 无事可做且无进展 -> stop_with_report (诚实交付未决)。

        **不重复派工** (两条闸门, 缺一个就会烧光预算 —— 都实测过):
        - 同一"角色 + 目标"组合只派一次, 除非 `pending_retries` 明确允许重试
          (依据版本变了才允许)。否则"受阻 → 上报同一需求 → 再派 → 再受阻"会循环;
        - 未尝试过的任务按"角色 + 目标"键判定, 而不是按 task_id —— 补派任务会拿新 id,
          用 id 判会让同一件事被反复派出去。
        """
        results = results or {}
        pending_set = {p for p in pending if p}
        running_set = {r for r in running if r}
        done_ids = {task_id for task_id, result in results.items()
                    if result.outcome in (TaskOutcome.completed, TaskOutcome.partial)}
        satisfied_keys = {str(k) for k in satisfied if k}
        # 依赖判定按"角色+目标"的等价完成来算, 而不只看任务 id
        known_objectives = {str(k): str(v) for k, v in (objectives or {}).items()}
        plan_task_objectives = {str(t.get("task_id", "")): str(t.get("objective", ""))
                                for t in plan.tasks}

        def _objective_of(task_id: str) -> str:
            return known_objectives.get(task_id) or plan_task_objectives.get(task_id, "")

        plan_keys = {str(t.get("task_id", "")):
                     _assignment_key(brief, str(t.get("agent", "")),
                                     str(t.get("objective", "")))
                     for t in plan.tasks}
        succeeded_keys = {_assignment_key(brief, result.agent, _objective_of(task_id))
                          for task_id, result in results.items()
                          if result.outcome in (TaskOutcome.completed,
                                                TaskOutcome.partial)
                          and result.agent and _objective_of(task_id)}
        satisfied_keys |= succeeded_keys
        # 等价完成: 任务的**"角色+目标"键**若已有成功记录, 它就算完成 —— 不能只看
        # task_id。补派/返工用的是新 task_id, 而 replan 克隆出的计划任务又是另一个
        # id; 只按 id 判会让"已经做过的事"永远不算完成, 下游依赖因此永不满足。
        completed = done_ids | {tid for tid, key in plan_keys.items()
                                if key and key in satisfied_keys}
        # 已派工过的"角色 + 目标"组合与需求键: 计划里已有的, 以及**本运行已尝试过的
        # 任务**。后者必须一起算 —— 否则"某角色受阻后重复上报同一条需求"会每轮派一个
        # 新任务 (实测: 同一句需求被派 10 次直到轮次上限)。
        #
        # 用**与计划版本无关**的键: 升版会改变 `idempotency_key`, 拿它去重会让
        # "升版后的同一条需求"重新变成新任务, 循环照旧。
        known_keys = {str(t.get("idempotency_key", "")) for t in plan.tasks
                      if t.get("idempotency_key")}
        known_keys |= {_assignment_key(brief, result.agent, _objective_of(task_id))
                       for task_id, result in results.items()
                       if result.agent and _objective_of(task_id)}
        # 本运行已派发过的键: 计划任务、补派任务、需求任务共用这一份记录。
        # 缺了它, `replan()` 克隆出的计划任务会与原任务"不同 id、同键", 于是被
        # 反复派发 (实测: 写作任务被派出去后又判成"未尝试", 同一件事做多遍)。
        dispatched_keys = {str(k) for k in (dispatched or ()) if k}
        known_keys |= dispatched_keys
        # 需求类型键: 措辞里的计数会变, 用类型键才能挡住"同一类需求反复上报"
        known_keys |= {str((t.get("hints") or {}).get("need_assignment", ""))
                       for t in plan.tasks
                       if (t.get("hints") or {}).get("need_assignment")}

        if stopped:
            return SupervisorDecision(decision=DecisionKind.stop_with_report,
                                      reason="运行被停止", note="已封存已有成果")
        if delivered:
            return SupervisorDecision(decision=DecisionKind.deliver,
                                      reason="交付条件已满足")

        # 2. 需求 -> 任务 (只派"计划里还没有的")
        needs = list(open_needs)
        if needs:
            tasks = [t for t in needs_to_tasks(
                needs, brief=brief, plan_version=plan.version,
                limit=self.max_tasks_per_round * 2)
                if t.idempotency_key not in known_keys
                and str((t.hints or {}).get("need_assignment", "")) not in known_keys]
            if tasks:
                tasks = tasks[: self.max_tasks_per_round]
                return SupervisorDecision(
                    decision=DecisionKind.dispatch,
                    reason=f"收到 {len(needs)} 条跨职责需求, 派 {len(tasks)} 个新任务",
                    tasks=tasks,
                    note="; ".join(f"{n.kind.value}: {n.statement}" for n in needs[:3]))

        # 3. 可派发。
        #
        # 过滤用**"角色+目标"键**而不是任务 id: 补派任务会拿到新 task_id, 用 id 过滤
        # 会把"已尝试过的那件事"再派一遍, 同时把"已完成"误判成"没做过"。
        # 依赖判定也按同一把尺子 —— 否则补派成功解锁不了下游 (实测过).
        retry_keys = {str(k) for k in pending_retries if k}
        attempted_keys = {_assignment_key(brief, result.agent, _objective_of(task_id))
                          for task_id, result in results.items()
                          if result.agent and _objective_of(task_id)}
        # 依赖判定用 `settled`: **已尝试过**的任务不再阻塞下游。
        #
        # 与"完成"不同: 任务失败/受阻后它自己不会被重派 (由上面的闸门保证), 但下游
        # 不能永远等一个已经确定失败的前置。否则"补检索受阻 → 写作永不派发"会让整个
        # 交付卡死 (实测)。依赖阻塞只针对**还没跑过**的任务。
        settled = completed | {tid for tid, key in plan_keys.items()
                               if key and key in attempted_keys}
        ready = [t for t in plan.ready(settled, running_set)
                 if t not in completed
                 and (plan_keys.get(str(t), "") not in attempted_keys
                      or plan_keys.get(str(t), "") in retry_keys)
                 and (plan_keys.get(str(t), "") not in dispatched_keys
                      or plan_keys.get(str(t), "") in retry_keys)]
        import os as _os
        if _os.getenv("AIR_TEAM_DEBUG"):
            import sys as _sys
            print(f"[decide] completed={sorted(completed)} ready={ready} "
                  f"plan_keys={plan_keys} attempted={sorted(attempted_keys)} "
                  f"dispatched={sorted(dispatched_keys)}", file=_sys.stderr)
        if ready and plan.version:
            tasks, skipped = self._tasks_for(plan, ready, remaining_budget)
            if tasks:
                return SupervisorDecision(
                    decision=DecisionKind.dispatch,
                    reason=f"依赖已满足, 派 {len(tasks)} 个子任务",
                    tasks=tasks, note=("; ".join(skipped) if skipped else ""))
            if skipped:
                return SupervisorDecision(
                    decision=DecisionKind.stop_with_report,
                    reason="可派任务都被预算或权限挡下",
                    note="; ".join(skipped))

        # 4. 还有任务在处理中 -> 等待
        if running_set:
            return SupervisorDecision(
                decision=DecisionKind.wait,
                reason=f"{len(running_set)} 个任务运行中, {len(pending_set)} 个待派")

        # 5. 交付判定: 交付形态要求的角色都成功过
        missing = self._missing_roles(brief, results, completed_ids=completed)
        if not missing:
            return SupervisorDecision(decision=DecisionKind.deliver,
                                      reason="交付形态所需的角色成果齐备")
        if clarifications_asked >= 1 and all(
                results.get(t, AgentResult(task_id=t, outcome=TaskOutcome.blocked))
                .outcome == TaskOutcome.blocked
                for t in results) and results:
            return SupervisorDecision(
                decision=DecisionKind.stop_with_report,
                reason="所有任务均受阻且已请求过澄清",
                note="; ".join(missing))

        # 6. 无事可做: 诚实收尾
        return SupervisorDecision(
            decision=DecisionKind.stop_with_report,
            reason="没有可派发的任务, 且交付形态要求未满足",
            note="未满足: " + "; ".join(missing))

    def _tasks_for(self, plan: TeamPlan, ready: list[str],
                   remaining: RunBudget | None) -> tuple[list[AgentTask], list[str]]:
        """把计划记录还原为任务对象, 并如实报告被挡下的任务。"""
        records = {str(t.get("task_id", "")): t for t in plan.tasks}
        out: list[AgentTask] = []
        skipped: list[str] = []
        for task_id in ready[: self.max_tasks_per_round]:
            record = records.get(task_id)
            if record is None:
                continue
            try:
                task = AgentTask(**record)
            except Exception as e:  # noqa: BLE001 - 计划记录损坏时如实报告
                skipped.append(f"{task_id}: 计划记录无法还原 ({e})")
                continue
            # 主控派发的任务仍然要经运行时预留预算; 这里只做"全局已无余量"的初判
            if remaining is not None:
                room = remaining.total.max_llm_calls - remaining.allocated.max_llm_calls
                if remaining.total.max_llm_calls and room <= 0:
                    skipped.append(f"{task_id}: 全局模型调用额度已用尽")
                    continue
            out.append(task)
        return out, skipped

    @staticmethod
    def _missing_roles(brief: ResearchBrief, results: dict[str, AgentResult],
                       completed_ids: Iterable[str] | None = None) -> list[str]:
        """交付形态要求哪些角色的成果还没齐。

        `completed_ids` 是**等价完成**的任务集合 (含"补派任务已成功"的原任务),
        没有它时补派成功也不会让交付判定通过 —— 原任务仍留在 `results` 里是 blocked。
        """
        required: set[str] = set()
        for deliverable in brief.deliverables:
            required.update(_DELIVERABLE_ROLES.get(deliverable, ()))
        done_ids = set(completed_ids) if completed_ids is not None else {
            t for t, r in results.items()
            if r.outcome in (TaskOutcome.completed, TaskOutcome.partial)}
        satisfied = {r.agent for task_id, r in results.items()
                     if task_id in done_ids and r.outcome in (TaskOutcome.completed,
                                                              TaskOutcome.partial)}
        return sorted(required - satisfied)

    @staticmethod
    def _equivalent_done(brief: ResearchBrief, plan: TeamPlan,
                         results: dict[str, AgentResult]) -> set[str]:
        """**等价完成**的任务 id 集合 (补派成功即视为原任务已解决)。

        判据是 `(agent, objective)` 派生的版本无关键: 角色按同一目标重新做过并成功,
        就等于那件事完成了 —— 否则下游依赖永远等不到就绪 (实测: 补检索成功、写作
        一直不派发, 运行以 "未满足交付形态" 收尾)。
        """
        plan_keys: dict[str, str] = {}
        for record in plan.tasks:
            agent = str(record.get("agent", ""))
            objective = str(record.get("objective", ""))
            plan_keys[str(record.get("task_id", ""))] = _assignment_key(
                brief, agent, objective)
        succeeded = {plan_keys[tid] for tid, result in results.items()
                     if tid in plan_keys
                     and result.outcome in (TaskOutcome.completed, TaskOutcome.partial)}
        return {tid for tid, key in plan_keys.items() if key and key in succeeded}

    # ---- 复盘 ----
    def replan(self, plan: TeamPlan, results: dict[str, AgentResult], *,
               version: int | None = None,
               max_plan_version: int | None = None) -> TeamPlan:
        """按结果**升版**重规划。

        关键不变量: **不能动计划里已有的任务身份**。`TeamPlan.ready()` 按**任务 id**
        比对依赖边, 所以一旦重规划时把任务换成新 id (例如重新跑一次 `plan()`),
        所有依赖边就会指向不存在的任务, 下游永远等不到就绪 —— 实测表现为"补派成功,
        写作却一直不派发, 运行以 '未满足交付形态' 收尾"。

        因此这里只做两件事: 升版本号, 给失败任务的新 attempt 记录原因。任务记录与
        依赖边原样保留 (补派任务是**另外**加入的新条目)。
        """
        limit = max_plan_version or self.max_plan_version
        next_version = (version if version is not None else plan.version + 1)
        if next_version > limit:
            return plan
        clone = TeamPlan(plan_id=plan.plan_id, version=next_version,
                         brief_id=plan.brief_id,
                         tasks=[dict(t) for t in plan.tasks],
                         edges={k: list(v) for k, v in plan.edges.items()},
                         rationale="按结果重新规划")
        records = {str(t.get("task_id", "")): t for t in clone.tasks}
        for task_id, result in results.items():
            record = records.get(task_id)
            if record is None:
                continue
            if result.outcome in (TaskOutcome.completed, TaskOutcome.partial):
                continue
            record["attempt"] = int(record.get("attempt", 1)) + 1
            record["plan_version"] = next_version
            record["hints"] = {**(record.get("hints") or {}),
                               "previous_failure": result.failure_reason}
            clone.edges[task_id] = list(record.get("depends_on", []))
        return clone

    def adopt(self, plan: TeamPlan, extra: AgentTask, *,
              depends_on: Iterable[str] = ()) -> TeamPlan:
        """把**补派/返工任务**并入计划 (保持原有任务与依赖边不变)。

        计划因此始终只有一份任务身份表: 新任务带自己的 id 加入, 已有任务原样保留,
        依赖边继续有效。
        """
        for dep in depends_on:
            if dep not in plan.edges:
                plan.edges[dep] = []
        plan.add_task(extra)
        plan.edges[extra.task_id] = list(depends_on) or list(extra.depends_on)
        return plan

    # ---- 结果验收 ----
    def review_issues(self, plan: TeamPlan,
                      results: dict[str, AgentResult]) -> list[ReviewIssueRef]:
        """汇总所有审阅问题 (供返工派单与界面展示)。

        稳定的 `issue_id` 保证同一问题在多次审阅之间可追踪, 不会每次换新 id 导致
        "问题账本无法关闭"。
        """
        out: list[ReviewIssueRef] = []
        for result in results.values():
            out.extend(result.issues)
        return out

    def rework_tasks(self, issues: Iterable[ReviewIssueRef], *,
                     brief: ResearchBrief, plan_version: int,
                     limit: int = 3) -> list[AgentTask]:
        """按审阅问题派返工任务 (§7.3: 科学问题派研究角色, 文字问题派 Writer)。

        **问题账本可关闭且有修复证据**: 每条返工任务的 `acceptance` 就是该问题的
        验收标准, 因此不能靠"总分上升"结束。
        """
        tasks: list[AgentTask] = []
        for issue in list(issues)[:limit]:
            owner = issue.suggested_owner or _owner_for_issue(issue)
            if not owner:
                continue
            tasks.append(AgentTask(
                agent=owner,
                objective=f"处置审阅问题 {issue.issue_id}: {issue.summary}",
                expected_gain="给出可复核的修复证据或明确的无法修复说明",
                project_id=brief.project_id, problem_id=brief.problem_id,
                plan_version=plan_version,
                input_refs=list(issue.affected_refs),
                acceptance_criteria=list(issue.acceptance) or [
                    f"问题 {issue.issue_id} 有可核查的处置结论"],
                budget=TaskBudget(),
                on_failure="标注为未解决并交回主控",
            ))
        return tasks


_KIND_LABELS = {
    "literature_synthesis": "文献综合",
    "existence_proof": "存在性判定",
    "mechanism": "机理解释",
    "model_construction": "模型构造",
    "case_comparison": "案例比较",
    "data_availability": "数据可用性分析",
    "validation_plan": "验证方案",
}


def _short(text: str, limit: int = 60) -> str:
    body = " ".join((text or "").split())
    return body[:limit] + ("…" if len(body) > limit else "")


_ISSUE_OWNER: dict[str, str] = {
    "science": "reasoning",
    "logic": "reasoning",
    "derivation": "reasoning",
    "citation": "evidence",
    "source": "evidence",
    "evidence": "evidence",
    "model": "modeling",
    "figures": "figures",
    "figure": "figures",
    "readability": "writing",
    "writing": "writing",
    "format": "writing",
}


def _owner_for_issue(issue: ReviewIssueRef) -> str:
    key = (issue.category or "").strip().lower()
    return _ISSUE_OWNER.get(key, "writing")


def _attempted_pairs(plan: TeamPlan,
                     results: dict[str, AgentResult]) -> list[tuple[str, AgentResult]]:
    """把"已尝试的任务 id"还原为 `(objective, result)`, 用于派生幂等键。"""
    records = {str(t.get("task_id", "")): t for t in plan.tasks}
    return [(str(records.get(task_id, {}).get("objective", "")), result)
            for task_id, result in results.items()]


def _assignment_key(brief: ResearchBrief, agent: str, objective: str) -> str:
    """"谁去做哪件事"的键 (重复派工判据)。

    与 `AgentTask.idempotency_key` 同构 (都**不含计划版本**): 同一件事在升版、补派、
    重试后仍是同一件事, 因此两处必须得到同一个键 —— 否则"已派过"的判定会漏。
    """
    # 直接复用协议的幂等键实现: 两处必须是同一个键 (审计与去重共用一把尺子)
    return idempotency_key_for(brief.project_id, brief.problem_id, 0, agent, objective)


def assignment_key(brief: ResearchBrief, agent: str, objective: str) -> str:
    """公开入口: 供团队循环记录"哪件事已完成" (解锁下游依赖)。"""
    return _assignment_key(brief, agent, objective)


def needs_to_tasks(needs: Iterable[ResearchNeed], *, brief: ResearchBrief,
                   plan_version: int, limit: int = 3) -> list[AgentTask]:
    """把跨职责需求转成新任务 (§4.2 受阻处理)。

    这是"主控转换需求"的唯一入口: 子智能体之间不互相派工 (§5.1)。
    需求若没有明确承接角色, 如实记为无法派发, 而不是猜一个。
    """
    out: list[AgentTask] = []
    for need in list(needs)[:limit]:
        owner = need.owner
        if not owner or owner == "supervisor":
            continue
        # 目标文本用**需求措辞**: 它既进提示词, 也进"角色+目标"派工键。
        #
        # 措辞里可能含会变的计数, 因此去重不能只靠它 —— `decide()` 另外用需求类型键
        # (`ResearchNeed.assignment_key`) 挡"同一类需求反复上报"。两条闸门合起来才能
        # 既让措辞可读, 又不至于每轮重派。
        out.append(AgentTask(
            agent=owner,
            objective=need.statement,
            subquestion="",
            expected_gain=need.why or "补上缺失的前置条件",
            project_id=brief.project_id, problem_id=brief.problem_id,
            plan_version=plan_version,
            input_refs=list(need.blocked_refs),
            depends_on=[],
            acceptance_criteria=list(need.acceptance) or [
                f"需求 {need.need_id} 被满足或有明确无法满足的说明"],
            budget=TaskBudget(),
            hints={**dict(need.hints), "need_kind": need.kind.value,
                   "need_assignment": need.assignment_key(brief.project_id,
                                                          brief.problem_id)},
            on_failure="记录失败原因并交回主控",
            needs_human=False,
        ))
    return out


def unresolved_report(brief: ResearchBrief, plan: TeamPlan,
                      results: dict[str, AgentResult]) -> dict[str, Any]:
    """未决报告: 诚实交付部分结果 (§12 完成标准)。

    主控**允许降低交付等级**: 纠正合并前的误判不以"等级不得低于旧版"维持错误结论。
    """
    completed = [t for t, r in results.items() if r.outcome == TaskOutcome.completed]
    blocked = [t for t, r in results.items() if r.outcome == TaskOutcome.blocked]
    failed = [t for t, r in results.items() if r.outcome == TaskOutcome.failed]
    unresolved: list[str] = []
    for result in results.values():
        unresolved.extend(result.unresolved)
        unresolved.extend(f"{n.kind.value}: {n.statement}" for n in result.followup_needs)
    return {
        "main_question": brief.main_question,
        "plan_version": plan.version,
        "completed_tasks": completed,
        "blocked_tasks": blocked,
        "failed_tasks": failed,
        "unresolved": list(dict.fromkeys(unresolved)),
        "deliverables_requested": list(brief.deliverables),
        "note": "未决项逐条列出; 未满足的交付形态不假装完成",
    }


def plan_fingerprint(plan: TeamPlan) -> str:
    """计划指纹 (审计: 两个计划是否为同一安排)。

    用**任务内容**而不是自动生成的 task_id 做基础 —— task_id 每次构造都不同,
    拿它算指纹会让"同一计划"永远不等, 审计意义归零。
    """
    body = [(str(t.get("agent", "")), str(t.get("objective", "")),
             str(t.get("subquestion", ""))) for t in plan.tasks]
    return stable_id("planfp", plan.version,
                     json.dumps(body, ensure_ascii=False, sort_keys=True))
