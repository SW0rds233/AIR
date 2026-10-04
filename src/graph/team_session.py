from __future__ import annotations

"""团队会话 (合并计划 §15.3 第 2–3 步): 把团队主控变成**会话引擎**。

为什么需要这一层
----------------
`TeamRun` 已经能"派工 → 执行 → 登记候选", 但它是**一次性**的: `run()` 跑完才返回,
事件只能进 `runtime` 的 sink, 没有"逐步驱动 + 中断等待 + 续跑 + 交付包"这套会话语义。
`server._run_session` 需要的恰恰是后者, 于是界面上的团队视图只能显示能力与连接状态。

这一层把两者接起来, 且**不新建第二套流程**:

- 循环仍然是 `TeamRun.prepare()/step()` (同一份实现, 已用差异测试锁住等价性);
- 事件仍然来自 `runtime.emit` (只是多接一个会话出口);
- 任务生命周期仍然写 `TaskStore` (已有 `restore`/`recovery_view`, 因此**续跑**是
  "接着已提交的成果继续", 不是重跑整个子图);
- 交付包复用 `research/package.export_package` 与判定层的门槛, 不在这一层重新发明。

中断与续跑语义 (§9.3)
--------------------
- 主控要求澄清 (`request_clarification`) 时: 发出 `interrupt` 事件并**停在这一步**,
  等待会话把用户答复投回来 (`submit_response()`)。澄清是终态判断, 不是可重复动作 ——
  与旧引擎的 `clarify_problem` 修正同一条原则。
- `resume()`: 从 `TaskStore` 读回已提交的成果与计划版本, 只补做未完成的部分。
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from src.agents.protocol import AgentResult, TaskStatus
from src.graph.research_graph import TeamRun

__all__ = ["TeamApp", "TeamSession", "run_team_session"]


class _Interrupt:
    """`__interrupt__` 里那一项的最小形态 (会话驱动只读 `.value`)。"""

    def __init__(self, value: Any) -> None:
        self.value = value


class TeamApp:
    """把 `TeamSession` 适配成会话驱动认的"图应用" (合并计划 §15.3 第 4 步)。

    会话驱动 (`server._run_session`) 只用两个能力: `stream(inputs, config)` 逐节点产出
    事件, 以及 `get_state(config)` 取终态。这里就提供这两个 —— 不为此再造一套驱动,
    也不把团队循环搬进 `server.py`。

    事件形态与旧图**逐字对齐**, 否则前端与既有用例会一起失效:
    - 正常事件: `{node_name: node_state}`;
    - 暂停: `{"__interrupt__": (<带 .value 的对象>,)}`;
    - 恢复: 入参是 `Command(resume=...)` 时把答复交给团队会话。
    """

    def __init__(self, session: TeamSession) -> None:
        self.session = session

    # ---- 驱动 ----
    def stream(self, inputs: Any = None, config: Any = None) -> Iterator[dict[str, Any]]:
        # 恢复: 驱动把用户答复包在 `Command(resume=...)` 里再喂回来
        resume = getattr(inputs, "resume", None)
        if isinstance(resume, str) and resume.strip():
            if not self.session.submit_response(resume):
                return

        self.session.team.prepare()
        while True:
            before = len(self.session.bridge.events)
            keep_going = self.session.team.step()
            for record in self.session.bridge.events[before:]:
                yield {"research_team": self._node_state(record)}

            if self.session._clarification_pending():
                payload = self.session._clarify_payload()
                self.session.pending_interrupt = payload
                yield {"__interrupt__": (_Interrupt(payload),)}
                return
            if not keep_going:
                break
        # 收尾: 交付包在这里导出 (与团队会话的 export 同一实现)
        package = None
        try:
            package = self.session.export()
        except Exception:  # noqa: BLE001 - 导出失败不得让会话卡死
            package = None
        self.session.package_dir = str(package) if package else ""
        yield {"research_team_done": {
            "node": "research_team_done",
            "summary": self.session.outcome_summary(),
            "package_dir": self.session.package_dir,
        }}

    def get_state(self, config: Any = None) -> Any:
        """终态视图: `_run_session` 只读 `values`, 因此这里给一个最小对象。"""
        summary = self.session.outcome_summary()
        return _TeamState({
            "engine": "team_v1",
            "package_dir": getattr(self.session, "package_dir", ""),
            "summary": summary,
            "project_id": self.session.team.project_id,
            "problem_id": self.session.team.problem_id,
            # 交付摘要的既有字段 (前端契约) 也一并给出: 团队路径没有"研究门槛通过"
            # 这个概念时保持默认值, 不编造 True。
            "delivery_level": summary.get("delivery_level", ""),
            "usage": summary.get("usage", {}),
            "needs_clarification": False,
        })

    # ---- 兼容 ----
    def __getattr__(self, name: str) -> Any:
        return getattr(self.session.team, name)

    @staticmethod
    def _node_state(record: dict[str, Any]) -> dict[str, Any]:
        """把一条团队事件转成节点状态 (供描述与进度读取)。"""
        payload = record.get("payload") or {}
        name = str(record.get("type") or "team_event")
        state: dict[str, Any] = {"node": name, "event": name, **payload}
        # 任务成果要能被"进度/描述"读成一句话
        if name in ("task_result", "task_started"):
            state["action"] = {"agent": payload.get("agent", ""),
                               "kind": name,
                               "objective": payload.get("objective", "")}
            state["phase"] = f"[{payload.get('agent', '')}] {name}"
        return state


class _TeamState:
    """最小状态对象 (只有 `values`, 与 LangGraph 的返回形状一致)。"""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values


class _EventBridge:
    """把 `runtime.emit` 的事件转成会话级事件 (逐条推给 `emit`)。

    合并计划 §9.4: 事件必须带类型与可选 task/agent ID, 未知事件不得静默丢弃。
    团队事件名 (`supervisor_decision`/`task_started`/...) 直接作为事件类型保留,
    会话层再决定怎么渲染; 这里不翻译成"看起来更友好"的名字, 否则真实进度就丢了。
    """

    def __init__(self, emit) -> None:
        self._emit = emit
        self.events: list[dict[str, Any]] = []

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        record = {"type": kind, "payload": dict(payload)}
        self.events.append(record)
        try:
            self._emit(record)
        except Exception:  # noqa: BLE001 - 渲染失败不得影响研究
            pass

    #: `ObservationSink` 的其余方法 (团队运行时只用到 `event`)。
    def tool_result(self, *args, **kwargs) -> None:  # pragma: no cover - 兼容接口
        pass

    def llm_call(self, *args, **kwargs) -> None:  # pragma: no cover - 兼容接口
        pass


def _task_event(task, result: AgentResult) -> dict[str, Any]:
    """(保留入口) 把一次任务成果转成可渲染的补充事件。

    实际进度直接来自运行时的 `task_started`/`task_result`（它们已带
    `task_id`/`agent`/`outcome`/`usage`），因此这里**不**再另造一套事件名 ——
    否则界面得同时理解两套"任务完成"的说法。保留入口是给需要补一条摘要的调用方用。
    """
    return {
        "type": "task_finished",
        "payload": {
            "task_id": task.task_id,
            "agent": getattr(result, "agent", "") or task.agent,
            "objective": task.objective,
            "outcome": getattr(getattr(result, "outcome", None), "value", ""),
            "summary": getattr(result, "summary", ""),
            "unresolved": list(getattr(result, "unresolved", []) or []),
        },
    }


class TeamSession:
    """一个会话里的团队运行 (会话引擎形态)。

    `TeamSession` **不**决定研究结论: 它只驱动主控循环、转发事件、在结束时导出交付包。
    命题真值/验证等级/交付等级仍由判定层产生 (§14.3)。
    """

    def __init__(self, team: TeamRun, *, emit=None, sink=None) -> None:
        self.team = team
        self.events: list[dict[str, Any]] = []
        # 事件桥**总是**建立: 团队运行时的事件既进会话出口 (SSE), 也留一份在这里供
        # `stream()` 逐轮产出。只在调用方给了 `emit` 时才建桥, 会让"没人接事件"变成
        # "事件根本不存在" —— 实测表现为 `stream()` 只能产出兜底的 task_phase。
        outlet = emit if emit is not None else self.events.append
        self.bridge = _EventBridge(outlet)
        original = team.runtime.sink
        bridge = self.bridge

        class _Fanout:
            def event(self, kind: str, payload: dict[str, Any]) -> None:
                try:
                    original.event(kind, payload)
                except Exception:  # noqa: BLE001
                    pass
                bridge.event(kind, payload)

        self._fanout = _Fanout()
        team.runtime.sink = self._fanout
        self.pending_interrupt: dict[str, Any] | None = None
        self.responses: list[str] = []

    # ------------------------------------------------------------------
    # 驱动
    # ------------------------------------------------------------------
    def step(self) -> bool:
        """推进一轮; 返回是否还能继续 (`False` = 已收尾或正等用户输入)。"""
        return self.team.step()

    def stream(self, emit=None) -> Iterator[dict[str, Any]]:
        """逐轮产出事件, 直到收尾或需要用户澄清。

        每轮至少产出一个 `supervisor_decision` (主控这一轮打算做什么), 派工时再产出
        每个任务的 `task_finished`。这样界面看到的是"主控决策 + 角色成果",
        而不是一句"正在思考"。
        """
        self.team.prepare()
        while True:
            before = len(self.bridge.events)
            keep_going = self.team.step()
            produced = self.bridge.events[before:]
            for record in produced:
                yield record
            if not produced:
                # 一轮没有任何事件是不正常的: 如实产出一条, 不让界面停在旧状态
                yield {"type": "task_phase", "payload": {
                    "round": self.team.loop.rounds,
                    "note": "该轮没有产生事件 (可能被取消或收尾)"}}
            if self._clarification_pending():
                self.pending_interrupt = self._clarify_payload()
                yield {"type": "interrupt", "payload": self.pending_interrupt}
                return
            if not keep_going:
                break
        yield {"type": "run_finished", "payload": self.outcome_summary()}

    def _clarification_pending(self) -> bool:
        return self.team.loop.finished and self.team.outcome.status == "waiting_user"

    def _clarify_payload(self) -> dict[str, Any]:
        return {
            "type": "clarify",
            "title": "需要澄清研究问题",
            "hint": self.team.loop.stop_reason or "请补充研究问题或约束",
            "content": self.team.loop.stop_reason or "",
        }

    def submit_response(self, response: str) -> bool:
        """投递用户答复并允许继续 (澄清后重新进入主控循环)。

        语义: 答复作为**新的一轮输入**回到主控 (它会重新画像), 而不是把答复当成
        某个任务的返回值 —— 主控要求的是"澄清问题", 不是"补一个字段"。
        """
        text = str(response or "").strip()
        if not text:
            return False
        self.responses.append(text)
        self.pending_interrupt = None
        # 把澄清答复并入请求文本: 主控下一轮据此重新理解任务
        self.team.request = f"{self.team.request}\n补充说明: {text}".strip()
        # 解除已完成态, 允许再走一轮 (否则 step() 会立刻按旧状态收尾)
        self.team.loop.finished = False
        self.team.loop.stop_reason = ""
        self.team.outcome.status = TaskStatus.running.value
        # 画像与计划需要按新说明重建
        self.team.loop.brief = None
        self.team.loop.plan = None
        self.team.prepare()
        return True

    def run_to_completion(self, max_rounds: int | None = None) -> None:
        """跑完 (不带逐轮产出): 供 CLI/测试使用。"""
        limit = max_rounds or self.team.max_rounds
        rounds = 0
        while self.step():
            rounds += 1
            if rounds >= limit:
                break

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def outcome_summary(self) -> dict[str, Any]:
        """会话层要的终态摘要 (身份 + 状态 + 未决项 + 用量, **不含正文**)。"""
        outcome = self.team.outcome
        return {
            "run_id": self.team.run_id,
            "project_id": self.team.project_id,
            "problem_id": self.team.problem_id,
            "status": outcome.status,
            "rounds": outcome.rounds,
            "stop_reason": outcome.stop_reason,
            "tasks": len(outcome.results),
            "unresolved": list(outcome.unresolved_report.get("unresolved", []) or []),
            "needs_human": outcome.needs_human(),
            "usage": outcome.usage.to_dict(),
            "objects": {
                kind: len(self.team.projection.rows(kind))
                for kind in ("evidence", "claim", "manuscript", "figure",
                             "validation_plan", "review_issue")
            } if self.team.projection is not None else {},
        }

    # ------------------------------------------------------------------
    # 交付包 (§15.3 第 3 步)
    # ------------------------------------------------------------------
    def export(self, *, run_id: str = "") -> Path | None:
        """把团队产出导出为交付包 (复用既有 `export_package` 与门槛判定)。

        为什么能复用: 团队登记的候选 (证据/结论/稿件/图表/验证建议) 与理论引擎写进
        同一个 `ResearchStore`; 因此交付包只要按同一套 `ResearchSnapshot` + 门槛 +
        出版层渲染即可, 不需要为团队另造一份报告格式。

        没有可导出内容时返回 `None` 并如实记在 `stop_reason` 里, 不产出空包。
        """
        from src.research.package import export_package

        store = self.team.task_store.store
        snapshot = _snapshot_from_store(store, self.team)
        if snapshot is None:
            return None
        spec = None
        try:
            from src.research.store import KIND_SPEC

            raw = store.get(KIND_SPEC, self.team.problem_id)
            if raw:
                from src.research.schemas import ResearchSpec

                spec = ResearchSpec.model_validate(raw)
        except Exception:  # noqa: BLE001 - 规格缺失不阻断导出
            spec = None
        manuscript_md = _manuscript_markdown(store)
        return export_package(
            snapshot, spec, [], manuscript_md,
            notes=[self.team.outcome.stop_reason] if self.team.outcome.stop_reason else [],
            run_id=run_id or self.team.run_id,
            usage=self.team.outcome.usage.to_dict(),
            input_snapshot={"request": self.team.request,
                            "source_set_ids": list(self.team.source_set_ids),
                            "source_policy": self.team.source_policy,
                            "engine": "team_v1"},
        )


def _snapshot_from_store(store, team: TeamRun):
    """由存储里的团队候选组装 `ResearchSnapshot` (只带已登记对象, 不造结论)。

    关键限制: 团队只**提交候选**。因此这里的命题不带 `supported/refuted` 判定 ——
    真值只能由判定层写。交付包里呈现的是"候选 + 证据 + 稿件 + 未决项"。

    即使**什么都还没登记**也要返回快照: 那种情况下交付包本身就是"未决报告"
    (如实写清缺什么、下一步怎么办), 而不是"没有可交付物"。把它当作空包丢掉,
    会让"研究没做出来"在界面上与"没跑过"无法区分 —— 交付级别里的"研究备忘录"
    正是为这种情形准备的。

    两条**不得静默**的规则 (§3.2 G11):
    1. 单条坏数据不阻断整次导出, 但必须**记下是哪条、为什么** (以前是裸 `continue`,
       于是"库里 3 条证据、包里 0 条"这种情况完全无声);
    2. 只汇集 evidence/claim/model 是不够的 —— 义务、验证方案、图表、审阅问题同样属于
       引用闭包, 缺了它们交付包讲不出"结论依赖什么"。
    """
    from src.research.schemas import ResearchSnapshot

    snapshot = ResearchSnapshot(
        project_id=team.project_id, problem_id=team.problem_id, run_id=team.run_id)
    #: 存储 kind -> (目标字段, 映射函数)
    plan = (
        ("evidence", snapshot.evidence, _evidence_of),
        ("claim", snapshot.claims, _claim_of),
        ("model", snapshot.models, _model_of),
        ("obligation", snapshot.obligations, _obligation_of),
        ("assumption", snapshot.assumptions, _assumption_of),
        ("definition", snapshot.definitions, _definition_of),
        ("validation_plan", snapshot.experiment_specs, _validation_plan_of),
    )
    skipped: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for kind, target, mapper in plan:
        try:
            rows = store.list_latest(kind) or []
        except Exception as e:  # noqa: BLE001 - 某类对象读不出来要如实记, 不是当没有
            skipped.append({"kind": kind, "reason": f"读取失败: {type(e).__name__}: {e}"})
            continue
        counts[kind] = len(rows)
        for row in rows:
            try:
                target.append(mapper(row))
            except Exception as e:  # noqa: BLE001 - 单条坏数据不阻断导出, 但要可见
                skipped.append({
                    "kind": kind,
                    "object_id": str(row.get("id", "")) or "(无 id)",
                    "reason": f"{type(e).__name__}: {e}",
                })
    if skipped:
        # 写进快照的缺口列表: 交付包必须能回答"有多少登记对象没能进包、为什么"。
        # `GapType` 里没有"导出映射"这一类, 用 `encoding_mismatch` (字段/结构对不上)
        # 最贴近, 并在 statement 里写清具体原因, 不另造枚举值。
        from src.research.schemas import GapType, ObjectRef, ResearchGap

        for item in skipped:
            snapshot.gaps.append(ResearchGap(
                gap_type=GapType.encoding_mismatch,
                target_ref=ObjectRef(id=str(item.get("object_id", "")) or "export"),
                statement=(f"{item['kind']} 对象未能进入交付快照: "
                           f"{item.get('reason', '')}"),
                blocking=["交付包缺少这部分依据"],
                resolving_actions=["修正字段映射或补全该对象的必填字段后重新导出"],
                resolution_criteria="该对象出现在快照对应列表里, 且没有 export 缺口",
            ))
        team.runtime.emit("snapshot_export_incomplete",
                          {"skipped": skipped, "registered": counts})
    return snapshot


#: 存储行的字段名与领域 schema 的差异表 (§6.1 "统一领域 schema、显式映射并校验")。
#:
#: 为什么要显式映射而不是直接 `model_validate(row)`: 存储行用的是**投影层写入时的
#: 字段名**, 领域 schema 用的是自己的名字。靠"名字碰巧一样"就会静默丢数据 ——
#: 实测 `locator` 进不了 `location` (定位变空)、`relation` 进不了 `support`
#: (支持关系退化成 insufficient)、`version` 被 `pop` 掉 (版本回到 1)。
EVIDENCE_FIELD_ALIASES: dict[str, str] = {
    "locator": "location",
    "relation": "support",
}
#: 投影层写入的支持关系取值 -> `SourceEvidence.support` 的取值。
SUPPORT_RELATION_MAP: dict[str, str] = {
    "supports": "supports",
    "support": "supports",
    "refutes": "contradicts",
    "contradicts": "contradicts",
    "context": "context",
    "insufficient": "insufficient",
    "": "insufficient",
}


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """去掉投影层加的元数据字段 (`_` 前缀), **保留** `version`。"""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _evidence_of(row: dict[str, Any]):
    from src.research.schemas import SourceEvidence

    data = _row_payload(row)
    for stored_name, schema_name in EVIDENCE_FIELD_ALIASES.items():
        if stored_name in data and schema_name not in data:
            data[schema_name] = data.pop(stored_name)
    if "support" in data:
        data["support"] = SUPPORT_RELATION_MAP.get(str(data["support"]).lower(),
                                                  str(data["support"]))
    return SourceEvidence.model_validate(data)


def _claim_of(row: dict[str, Any]):
    from src.research.schemas import Claim

    data = _row_payload(row)
    # 团队提交的是**候选**: 没有判定层结论时一律按 proposed, 不得冒充已确证
    data.setdefault("status", "proposed")
    return Claim.model_validate(data)


def _model_of(row: dict[str, Any]):
    """模型候选 -> `ResearchModel`。

    这里曾 import 一个**不存在**的 `ModelRecord`, 抛出的 `ImportError` 又被上层的
    裸 `except` 吞掉 —— 于是"模型一个都没进包"完全无声 (§3.2 G11)。真实类名是
    `ResearchModel`。
    """
    from src.research.schemas import ResearchModel

    return ResearchModel.model_validate(_row_payload(row))


def _obligation_of(row: dict[str, Any]):
    from src.research.schemas import ProofObligation

    return ProofObligation.model_validate(_row_payload(row))


def _assumption_of(row: dict[str, Any]):
    from src.research.schemas import Assumption

    return Assumption.model_validate(_row_payload(row))


def _definition_of(row: dict[str, Any]):
    from src.research.schemas import Definition

    return Definition.model_validate(_row_payload(row))


def _validation_plan_of(row: dict[str, Any]) -> dict[str, Any]:
    """验证方案以 dict 形式进入 `experiment_specs` (schema 未定义专门类型)。

    补上 `executed=False`: 本轮**不运行**仿真/实验, 方案不是成功证据 (§4 角色表)。
    """
    data = _row_payload(row)
    data.setdefault("executed", False)
    data.setdefault("status", "proposed")
    return data


def _manuscript_markdown(store) -> str:
    """取团队登记的稿件 Markdown (渲染器认的结构化块优先)。"""
    try:
        rows = store.list_latest("manuscript") or []
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    row = rows[-1]
    from src.agents.writing import render_markdown
    from src.publication.schemas import Manuscript

    payload = row.get("manuscript") if isinstance(row.get("manuscript"), dict) else row
    try:
        manuscript = Manuscript.model_validate(payload)
        return render_markdown(manuscript)
    except Exception:  # noqa: BLE001 - 结构不完整时退回已有文本
        text = str(row.get("markdown") or row.get("text") or "")
        if text:
            return text
        return json.dumps(payload, ensure_ascii=False)[:2000]


def run_team_session(request: str, *, project_id: str = "", problem_id: str = "",
                     run_id: str = "", max_rounds: int = 24,
                     source_set_ids: Iterable[str] = (),
                     source_policy: str = "user_kb",
                     emit=None) -> dict[str, Any]:
    """跑一个团队会话并返回终态摘要 (CLI/测试的便捷入口)。

    注意: 摘要与交付包必须在团队存储**仍打开时**取 —— `TeamRun.close()` 会关掉
    `TaskStore` 的连接, 之后再读对象只会得到空列表 (实测过, 那会让"有证据却报 0 条")。
    """
    from src.graph.research_graph import TeamRun

    with TeamRun(project_id=project_id or "proj-team", problem_id=problem_id,
                 run_id=run_id, request=request,
                 source_set_ids=list(source_set_ids), source_policy=source_policy,
                 max_rounds=max_rounds) as team:
        session = TeamSession(team, emit=emit)
        session.run_to_completion()
        package = session.export()
        summary = session.outcome_summary()
        summary["package_dir"] = str(package) if package else ""
        return summary
