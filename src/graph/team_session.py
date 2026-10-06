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

import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from src.agents.protocol import AgentResult, TaskStatus
from src.graph.research_graph import TeamRun

__all__ = ["ExportError", "TeamApp", "TeamSession", "run_team_session"]


class ExportError(RuntimeError):
    """交付包导出失败 (可恢复: 修好原因后重试导出, 不重跑研究)。"""


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

        if inputs is None and self.session.team.loop.brief is None:
            self.session.team.restore_state()
        if self.session._clarification_pending():
            payload = self.session._clarify_payload()
            self.session.pending_interrupt = payload
            yield {"__interrupt__": (_Interrupt(payload),)}
            return
        if not self.session.team.loop.finished:
            self.session.team.prepare()
        while not self.session.team.loop.finished:
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
        # 收尾: 交付包在这里导出 (与团队会话的 export 同一实现)。
        # **导出失败不得静默** (G12): 之前这里 `except: package = None`, 于是"导出失败"
        # 在界面上与"没跑过"完全一样。现在如实发一条可恢复错误事件。
        try:
            package = self.session.export()
            self.session.package_dir = str(package) if package else ""
        except ExportError as e:
            self.session.package_error = str(e)
            yield {"research_team_export_failed": {
                "node": "research_team_export_failed",
                "recoverable": True,
                "message": str(e),
                "summary": self.session.outcome_summary(),
            }}
            return
        yield {"research_team_done": {
            "node": "research_team_done",
            "summary": self.session.outcome_summary(),
            "package_dir": self.session.package_dir,
            "delivery": (self.session.delivery.to_dict()
                         if self.session.delivery is not None else {}),
        }}

    def get_state(self, config: Any = None) -> Any:
        """终态视图: `_run_session` 只读 `values`, 因此这里给一个最小对象。"""
        summary = self.session.outcome_summary()
        package = Path(getattr(self.session, "package_dir", "") or "")
        # 交付包目录名就是冻结快照 id (`snap-…`); 会话终态要把它交出去, 否则
        # "HTTP 返回的运行身份 → 交付包 → 快照"这条链在界面上断掉 (G03 要求三处
        # 是同一个 id)。清单里也有 `snapshot_id`, 但只有跑到导出之后才有。
        snapshot_id = ""
        if package.name.startswith("snap-"):
            snapshot_id = package.name
        else:
            try:
                manifest = json.loads((package / "manifest.json").read_text("utf-8"))
                snapshot_id = str(manifest.get("snapshot_id") or "")
            except Exception:  # noqa: BLE001 - 没有包时如实留空
                snapshot_id = ""
        return _TeamState({
            "engine": "team_v1",
            "package_dir": getattr(self.session, "package_dir", ""),
            "snapshot_id": snapshot_id,
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
        #: 最近一次交付评估 (等级 + 门槛理由); 界面与用例据此看到"为什么是这一级"。
        self.delivery: Any = None
        #: 导出失败原因 (可恢复: 修好原因重试导出即可, 不重跑研究)。
        self.package_error: str = ""

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
    # 交付包 (§15.3 第 3 步 / §3.2 G12)
    # ------------------------------------------------------------------
    def export(self, *, run_id: str = "") -> Path | None:
        """把团队产出导出为交付包 (复用既有 `export_package` 与门槛判定)。

        为什么能复用: 团队登记的候选 (证据/结论/稿件/图表/验证建议) 与理论引擎写进
        同一个 `ResearchStore`; 因此交付包只要按同一套 `ResearchSnapshot` + 门槛 +
        出版层渲染即可, 不需要为团队另造一份报告格式。

        **G12**: 导出必须带上完整的研究/出版门槛结论 (此前一个都没传), 失败时抛
        `ExportError` 让调用方看到"为什么没有包", 而不是静默返回 `None` 让界面把
        "导出失败"显示成"没跑过"。
        """
        from src.research.delivery import assess_delivery
        from src.research.package import export_package

        store = self.team.task_store.store
        from src.research.snapshot import manuscript_markdown
        manuscript_md = manuscript_markdown(store, problem_id=self.team.problem_id)
        # 先渲染 .tex 并**真编译**: 出版门槛要的是"能不能交付 .pdf"这个事实,
        # 不能用"尚未尝试"占位 (§3.3 G17: 预览/Markdown/PDF 同源同版)。
        manuscript_obj = _manuscript_object(store, problem_id=self.team.problem_id)
        figure_sources, figure_warnings = _figure_sources(store, self.team.run_id)
        figure_paths = {figure_id: f"figures/{figure_id}.png"
                        for figure_id in figure_sources}
        if manuscript_obj is not None and figure_paths:
            from src.agents.writing import render_markdown

            manuscript_md = render_markdown(manuscript_obj, figures=figure_paths)
        tex_text = ""
        compile_status = "not_attempted"
        compile_log = ""
        if manuscript_obj is not None:
            from src.publication.render_latex import render_latex

            tex_text = render_latex(manuscript_obj,
                                    references=_reference_labels(store),
                                    figures=figure_paths)
        assessment = assess_delivery(
            store, project_id=self.team.project_id, problem_id=self.team.problem_id,
            run_id=run_id or self.team.run_id, manuscript_md=manuscript_md,
            references=_reference_rows(store, manuscript_obj),
            blocks=manuscript_obj.all_blocks() if manuscript_obj else [],
            latex_source=tex_text,
            compile_status="deferred" if not tex_text else "not_attempted",
            on_skip=lambda payload: self.team.runtime.emit(
                "snapshot_export_incomplete", payload))
        self.delivery = assessment
        snapshot = _snapshot_from_store(store, self.team)
        if snapshot is None:                          # pragma: no cover - 契约保证
            raise ExportError("无法组装研究快照")
        # **冻结快照必须落盘**: 交付包目录名就是快照 id, 而 `fork`/历史对比/`load_snapshot`
        # 都从对象库里按 id 取快照 —— 只写目录不写库时, 派生接口会报"未找到可派生的快照"
        # (实测), 界面又因为目录存在而显示"已冻结"。冻结的唯一入口在 `research/snapshot.py`
        # (它不改任何结论状态, 只是给已提交对象拍照)。
        from src.research.snapshot import freeze_snapshot

        try:
            freeze_snapshot(store, snapshot, run_id=run_id or self.team.run_id)
        except Exception as e:  # noqa: BLE001 - 落盘失败必须可见, 不能静默
            raise ExportError(f"冻结快照落盘失败: {type(e).__name__}: {e}") from e
        spec = None
        try:
            from src.research.store import KIND_SPEC

            raw = store.get(KIND_SPEC, self.team.problem_id)
            if raw:
                from src.research.schemas import ResearchSpec

                spec = ResearchSpec.model_validate(raw)
        except Exception:  # noqa: BLE001 - 规格缺失不阻断导出
            spec = None
        package_notes = [
            *(self.team.outcome.unresolved_report.get("unresolved") or []),
            *[str(gap.get("detail") or gap.get("kind") or "")
              for gap in (manuscript_obj.gaps if manuscript_obj else [])],
        ]
        try:
            package = export_package(
                snapshot, spec, [decision.to_dict() for decision in self.team.outcome.decisions], manuscript_md,
                manuscript=manuscript_obj,
                notes=package_notes,
                gate=assessment.theory,
                delivery=assessment.delivery,
                publication=assessment.publication,
                delivery_level=assessment.level,
                run_id=run_id or self.team.run_id,
                usage=self.team.outcome.usage.to_dict(),
                input_snapshot={"request": self.team.request,
                                "problem_id": self.team.problem_id,
                                "run_id": run_id or self.team.run_id,
                                # 输入快照必须记下**问题说明附件** (R6): 交付清单要能区分
                                # "用户交来的题面/要求"与"研究检索到的证据文献", 否则事后
                                # 无法判断研究是在什么约束下做的 (旧路径记了, 团队路径漏了)。
                                "attachment_ids": [str(a.get("attachment_id", ""))
                                                   for a in (self.team.attachments or [])],
                                "attachments": list(self.team.attachments or []),
                                "source_set_ids": list(self.team.source_set_ids),
                                "source_policy": self.team.source_policy,
                                "engine": "team_v1"},
            )
        except Exception as e:  # noqa: BLE001 - 导出失败必须**可见且可重试**
            raise ExportError(f"交付包导出失败: {type(e).__name__}: {e}") from e
        try:
            _copy_figure_assets(package, figure_sources, figure_warnings)
        except Exception as e:  # noqa: BLE001 - 图文件必须真实进入交付包
            raise ExportError(f"图表打包失败: {type(e).__name__}: {e}") from e
        if tex_text:
            compile_status, compile_log = self._compile_package(package, tex_text)
            # 编译结论要**回头改写**交付等级: 出版层产物是在第一次评估之后才落盘的,
            # 不重评就会让 manifest 停在"尚未尝试编译"的等级上 (实测过这种不一致)。
            self.delivery = assess_delivery(
                store, project_id=self.team.project_id,
                problem_id=self.team.problem_id, run_id=run_id or self.team.run_id,
                manuscript_md=manuscript_md, references=_reference_rows(store, manuscript_obj),
                blocks=manuscript_obj.all_blocks() if manuscript_obj else [],
                latex_source=tex_text,
                compile_status=compile_status,
                on_skip=None)
            _rewrite_manifest(package, self.delivery, compile_status, compile_log)
        from src.research.package import write_unresolved_report
        self.team.outcome.delivery = self.delivery.to_dict()
        self.team.outcome.unresolved_report["delivery"] = self.delivery.to_dict()
        write_unresolved_report(package, snapshot, [
            *package_notes, *(self.delivery.blocking or []), *(self.delivery.unresolved or [])
        ])
        return package

    def _compile_package(self, package: Path, tex_text: str) -> tuple[str, str]:
        """把 `.tex` 写进交付包并编译为 PDF; 返回 `(编译状态, 日志)`。

        状态取值与出版门槛的约定一致: `ok` / `unavailable` / `failed: …`。
        编译失败**不抛异常** —— 交付包本身仍然有效, 只是等级降为"论文草稿";
        这一点必须写进 manifest, 而不是让人以为有 PDF。
        """
        from src.publication.compiler import compile_latex

        tex_path = Path(package) / "manuscript.tex"
        tex_path.write_text(tex_text, encoding="utf-8")
        ok, log = compile_latex(str(tex_path), workdir=str(package))
        pdf_path = Path(package) / "manuscript.pdf"
        # 编译器的"成功"以**PDF 真的存在**为准: 只信返回值会让 manifest 写
        # `compilation ok` 而包里没有 PDF (实测出现过一次), 那正是"看起来完整"。
        has_pdf = pdf_path.is_file() and pdf_path.stat().st_size > 1024
        if ok and has_pdf:
            return "ok", log[-2000:]
        if "未安装" in (log or ""):
            return "unavailable", log[-2000:]
        if ok and not has_pdf:
            return "failed: 编译器报告成功但未生成 PDF", log[-2000:]
        return f"failed: {(log or '').splitlines()[0][:400] if log else '无编译诊断'}", log[-2000:]


def _snapshot_from_store(store, team: TeamRun):
    """由存储里的团队候选组装 `ResearchSnapshot` (薄包装, 映射只有一份实现)。

    真正的映射在 `research/snapshot.py` —— 交付评估 (`research/delivery.py`) 也要用它,
    两处各写一份就是两处丢字段的机会 (§3.2 G11)。
    """
    from src.research.snapshot import snapshot_from_store

    return snapshot_from_store(
        store, project_id=team.project_id, problem_id=team.problem_id,
        run_id=team.run_id,
        on_skip=lambda payload: team.runtime.emit("snapshot_export_incomplete", payload))


#: 兼容再导出: 字段差异表与支持关系映射的**唯一定义**在 `research/snapshot.py`,
#: 这里保留名字是因为既有用例直接读它们 (改判据时要改的是那边)。
from src.research.snapshot import (  # noqa: E402
    EVIDENCE_FIELD_ALIASES,
    SUPPORT_RELATION_MAP,
)

__all__ += ["EVIDENCE_FIELD_ALIASES", "SUPPORT_RELATION_MAP"]


def _manuscript_markdown(store) -> str:
    """取团队登记的稿件 Markdown (实现只有一份, 见 `research/snapshot.py`)。"""
    from src.research.snapshot import manuscript_markdown

    return manuscript_markdown(store)


def _manuscript_object(store, *, problem_id: str = ""):
    """取唯一稿件 IR (渲染 `.tex` 与算 `writing_map` 用的是同一份对象)。"""
    from src.publication.schemas import Manuscript

    try:
        from src.research.snapshot import latest_manuscript_row
        row = latest_manuscript_row(store, problem_id=problem_id)
    except Exception:  # noqa: BLE001 - 读不出来就没有可渲染的稿件
        return None
    if not row:
        return None
    payload = row.get("manuscript") if isinstance(row.get("manuscript"), dict) else row
    try:
        return Manuscript.model_validate({k: v for k, v in payload.items()
                                          if not k.startswith("_")})
    except Exception:  # noqa: BLE001 - 结构不完整的稿件不参与排版
        return None


def _figure_sources(store, run_id: str) -> tuple[dict[str, Path], list[str]]:
    """只接收本运行、产物根内、哈希匹配的 PNG；外部路径不能混入论文。"""
    from src import config

    root = Path(config.OUTPUT_DIR).resolve()
    allowed = (root / "figures").resolve()
    found: dict[str, Path] = {}
    warnings: list[str] = []
    for row in store.list_latest("figure") or []:
        scope = row.get("_scope") or {}
        if str(scope.get("run_id") or "") != run_id:
            continue
        figure_id = str(row.get("id") or "")
        render = row.get("render") or {}
        uri = str(render.get("uri") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", figure_id):
            warnings.append(f"图对象 ID 不可用于文件名: {figure_id}")
            continue
        source = (root / uri).resolve()
        if not uri or not source.is_relative_to(allowed) or source.suffix.lower() != ".png":
            warnings.append(f"图 {figure_id} 的路径不在受控图目录内")
            continue
        if not source.is_file():
            warnings.append(f"图 {figure_id} 的文件不存在")
            continue
        expected = str(render.get("sha256") or "")
        if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            warnings.append(f"图 {figure_id} 的内容哈希不匹配")
            continue
        found[figure_id] = source
    return found, warnings


def _copy_figure_assets(package: Path, sources: dict[str, Path],
                        warnings: list[str]) -> None:
    """把正文可引用的图纳入交付包，清单记录身份与实际复制结果。"""
    target_root = package / "figures"
    target_root.mkdir(exist_ok=True)
    inventory: list[dict[str, Any]] = []
    for figure_id, source in sources.items():
        target = target_root / f"{figure_id}.png"
        shutil.copyfile(source, target)
        inventory.append({"id": figure_id, "path": f"figures/{figure_id}.png",
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                          "bytes": target.stat().st_size})
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["figures"] = inventory
    manifest["figure_warnings"] = warnings
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _evidence_rows(store) -> list[dict[str, Any]]:
    try:
        return list(store.list_latest("evidence") or [])
    except Exception:  # noqa: BLE001 - 读不出来按"没有来源"处理 (门槛会如实报缺)
        return []


def _reference_labels(store) -> dict[str, str]:
    """`{来源对象 id: 参考文献条目文本}` —— 只由**已登记来源**拼出, 不编造。"""
    labels: dict[str, str] = {}
    from src.publication.references import format_reference
    for row in _evidence_rows(store):
        source_id = str(row.get("id") or "")
        if not source_id:
            continue
        labels[source_id] = format_reference(row)
    return labels


def _reference_rows(store, manuscript=None) -> list[dict[str, Any]]:
    """交付门槛要的文献表形态 (编号在渲染时分配, 这里只给条目)。"""
    if manuscript is None:
        return []
    from src.publication.references import cited_reference_rows
    return cited_reference_rows(manuscript, _evidence_rows(store))


def _rewrite_manifest(package: Path, assessment, compile_status: str,
                      compile_log: str) -> None:
    """编译之后刷新 manifest 的门槛/等级 (否则它会停在编译前的等级上)。"""
    import json

    path = Path(package) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - manifest 读不出来不阻断交付
        return
    manifest["delivery_level"] = assessment.level
    from src.research.package import gate_manifest_fields
    manifest.update(gate_manifest_fields(assessment.theory, assessment.delivery, assessment.publication))
    manifest["compilation_status"] = compile_status
    from src.publication.compiler import layout_diagnostics
    manifest["layout_diagnostics"] = layout_diagnostics(compile_log)
    if compile_status == "ok":
        manifest["compilation_log_tail"] = ""
    else:
        manifest["compilation_log_tail"] = compile_log[-800:]
    manifest["publication_gate_passed"] = bool(assessment.publication
                                               and assessment.publication.passed)
    manifest["publication_gate_reasons"] = list(
        (assessment.publication.reasons if assessment.publication else []))
    if compile_status == "ok" and (Path(package) / "manuscript.pdf").is_file():
        manifest["pdf"] = "manuscript.pdf"
    else:
        manifest["pdf"] = ""
    try:
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception:  # noqa: BLE001 - 写不回 manifest 不阻断交付
        pass


def run_team_session(request: str, *, project_id: str = "", problem_id: str = "",
                     run_id: str = "", max_rounds: int = 24,
                     source_set_ids: Iterable[str] = (),
                     source_policy: str = "user_kb",
                     emit=None, llm_factory=None) -> dict[str, Any]:
    """跑一个团队会话并返回终态摘要 (CLI/测试的便捷入口)。

    注意: 摘要与交付包必须在团队存储**仍打开时**取 —— `TeamRun.close()` 会关掉
    `TaskStore` 的连接, 之后再读对象只会得到空列表 (实测过, 那会让"有证据却报 0 条")。

    `llm_factory` 缺省走**唯一装配** (`bootstrap.role_llm_factory`): 于是 CLI 与 Web
    用的是同一份角色模型接线, 而 `THEORY_LLM=0` 仍然是显式离线。此前这个入口根本
    不注入模型, CLI 上的"团队研究"永远只跑规则模板, 却看不出这一点。
    """
    from src.bootstrap import role_llm_factory
    from src.graph.research_graph import TeamRun

    with TeamRun(project_id=project_id or "proj-team", problem_id=problem_id,
                 run_id=run_id, request=request,
                 source_set_ids=list(source_set_ids), source_policy=source_policy,
                 max_rounds=max_rounds,
                 llm_factory=llm_factory or role_llm_factory) as team:
        session = TeamSession(team, emit=emit)
        session.run_to_completion()
        package = session.export()
        summary = session.outcome_summary()
        summary["package_dir"] = str(package) if package else ""
        summary["delivery"] = (session.delivery.to_dict()
                               if session.delivery is not None else {})
        return summary
