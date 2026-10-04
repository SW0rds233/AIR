from __future__ import annotations

"""旧引擎的退役边界 (合并计划 §15.2.1)。

这个文件做两件事, 都不依赖任何"历史包袱":

1. **防回流**: 已退役的模块与取值不得再出现 —— 它们若回来, 说明有人重新引入了
   第二条综述路径, 那正是本次合并要消除的东西;
2. **如实记录删掉了什么**: 删除的用例覆盖的是 stage 图机制 (节点顺序、stage 调度、
   旧 planner 的候选确认流程); 那些机制的替代者与它们的新用例列在下面, 便于核对
   "断言的语义有没有减少"。

删除清单 (2026-10-04, 旧综述图退役):

| 删除项 | 原覆盖 | 语义去向 |
|---|---|---|
| `src/graph/pipeline.py` (2166 行) | stage 图与全部节点 | 综述型请求 → 团队会话引擎 (`graph/team_session.py`); 通用件抽出为 `graph/node_progress.py` |
| `src/gui.py` | Tkinter 包装旧模块入口 | 删除: 主入口是 Web (`server.py`) 与 CLI (`main.py`) |
| `src/main_chat.py` | 交互式旧流水线 | 删除: 交互澄清由 Web 会话 + `interrupt` 承担 |
| `tests/test_pipeline_logic.py` (37 项) | 修订契约、契约增强等 stage 机制 | 研究契约语义在 `research/schemas.py` + `test_research_contract.py`; stage 机制随图删除 |
| `tests/test_interactive.py` (21 项) | 人工反馈 -> 契约解析 | 对象级反馈在 `research/intent.py` + `test_workbench_api.py` 的反馈用例 |
| `tests/test_planner.py` (43 项) | 旧 planner/意图解析/stage 调度 | 意图解析 → `research/intake.py` (新用例 `test_unified_entry_survey_gap.py`); 计划与派工 → `agents/supervisor.py` (`test_agent_supervisor.py`); 舞台调度随图删除 |
| `tests/test_survey_mode.py` (6 项) | 引擎无关的会话契约 | **迁移**到团队引擎, 见 `tests/test_session_entry_contract.py` (5 项) |
| `server.build_pipeline` / `mode="survey"` | 第二张图的入口 | 删除: `_resolve_engine` 只在 theory/team 之间选择 |
| `server._resolve_engine(mode)` 的**引擎选择** (2026-10-04 第六轮) | "形式化请求 → 理论引擎 / 综述请求 → 团队" 的分流 | 删除: `_resolve_engine()` 不收参数, 所有请求进同一张团队图; 判定层边界 (候选→唯一提交口) 由 `test_engine_boundary_contract.py` 继续守住 |
| `server.build_theory_pipeline` + `StartRequest.mode` + `main.py --mode`/`_run_theory_mode` | 旧引擎的入口与建图点 | 删除: 唯一图工厂是 `server._build_team_app(session)`; CLI 只走 `run_team_session` (并真的把 `--source-policy` 传下去) |
| `server.build_theory_initial_state` + `_research_progress` | 旧图的初始状态与进度摘要 (后者为了显示进度**又建了一个引擎**) | 删除: 团队进度由团队事件 (SSE) 携带; 附件/身份由 `build_initial_state` + 会话请求快照承载 |
| `tests/test_theory_state_contract.py` (4 项) | 旧图 `TheoryState` 的 TypedDict 静默丢键契约 | 旧图退役, 该机制不存在于团队路径 (团队用 dataclass `TeamLoopState` + `ContextPack`, 缺字段会直接报错); 附件/身份不丢由 `test_unified_entry_http.py` 与 `test_session_entry_contract.py` 守住 |
| `src/graph/state.py` (`PipelineState`, 127 行) + 7 个旧 Agent (约 2000 行) | 旧综述流水线的图状态; 引用守门/预检/审阅/文献综述/大纲/PDF 入库 | 图状态随流水线删除 (R5 清仓); 引用守门与证据台账 **逐字迁入** `publication/citation_checks.py` 与 `publication/evidence_ledger.py` (用例改指新家); 工具循环 → `AgentRuntime.run` (`test_agent_runtime.py` 11 项); 审阅规则 "引用编号每轮重排" → `ReviewAgent.SYSTEM`; 大纲 → `agents/writing.py`; PDF 入库 → `kb/ingest` |
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: 已退役的模块/文件: 不得再出现。
RETIRED_FILES: tuple[str, ...] = (
    "src/graph/pipeline.py",
    "src/gui.py",
    "src/main_chat.py",
    "tests/test_pipeline_test.py",
    "tests/test_pipeline_logic.py",
    "tests/test_interactive.py",
    "tests/test_planner.py",
    "tests/test_survey_mode.py",
    # 旧图 `TheoryState` 的静默丢键契约 (前端/入口写状态, 节点按名读)。旧图退役后该
    # 机制不存在: 团队用 dataclass 状态, 缺字段直接报错而不是静默变默认值。
    "tests/test_theory_state_contract.py",
)


def _code(path: Path) -> str:
    """读源码文本 (排除注释行, 避免"解释历史"的注释把防回流判据弄红)。"""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_retired_modules_are_gone():
    """旧综述图、GUI 与交互式 CLI 不得回来。"""
    for relative in RETIRED_FILES:
        assert not (REPO / relative).exists(), f"已退役的文件又出现了: {relative}"


def test_server_has_no_second_survey_engine():
    """服务端不得再有"第二张图"的入口, 也不得再接受 `mode` 作为引擎取值。"""
    source = _code(REPO / "src" / "server.py")
    assert "build_pipeline" not in source, "旧的 survey 图入口又回来了"
    assert "build_theory_pipeline" not in source, "旧的形式化图入口又回来了"
    assert 'in ("theory", "survey"' not in source
    assert 'mode="survey"' not in source
    assert 'in ("theory", "team")' not in source, "按 mode 分流的分支又回来了"
    # 只有一种引擎, 且它以常量形式写下来 (可被用例读到)
    assert 'TEAM_ENGINE = "team"' in source


def test_node_progress_helper_is_the_shared_one():
    """节点进度描述必须来自抽出的通用件 (会话与 CLI 共用一份)。"""
    source = _code(REPO / "src" / "server.py")
    assert "from src.graph.node_progress import describe_node" in source
    helper = _code(REPO / "src" / "graph" / "node_progress.py")
    # 团队事件也要能描述, 否则团队会话的进度在界面上是空的
    assert "research_team" in helper
    # 旧节点名保留 (历史会话的 checkpoint 里仍有它们)
    assert "theory_finalize" in helper and "literature_review" in helper


def test_cli_entry_uses_the_team_session_engine():
    """CLI 只有一个入口: 团队会话引擎 (没有 `--mode`, 也没有第二张图)。"""
    source = _code(REPO / "src" / "main.py")
    assert "run_team_session" in source
    assert "run_pipeline" not in source
    assert "_run_theory_mode" not in source, "旧的形式化 CLI 分支又回来了"
    assert "run_theory_pipeline" not in source, "CLI 又去调旧的形式化流水线了"
    assert '"--mode"' not in source, "CLI 又提供了引擎选择"
    # `--source-policy` 必须真的传下去 (曾经硬编码成 user_kb, 命令行选项形同虚设)
    assert "source_policy=getattr(args" in source


#: 已经"从引擎里抽出来"的能力模块: 它们**不得反向依赖** `research/loop.py`。
#: 这是 G01 的可执行前置条件 —— 只要有反向依赖, 删引擎就会连带删掉这项能力。
ENGINE_FREE_MODULES: tuple[str, ...] = (
    "src/research/inspection.py",
    "src/research/reporting.py",
    "src/research/budget.py",
    "src/research/obligations.py",
    "src/research/feedback.py",
    "src/research/forking.py",
    "src/research/novelty_service.py",
    "src/research/snapshot.py",
    "src/research/delivery.py",
    "src/research/commit.py",
    "src/research/verification_service.py",
)


def test_extracted_services_do_not_depend_on_the_engine():
    """抽出来的服务/只读层不得 import 旧引擎 (§5.5)。

    这条是 **G01 的准入判据**: 只要有一个反向依赖, 删 `research/loop.py` 就会连带
    删掉那项能力 (工作台读不出对象、反馈失效、派生消失)。用 import 语句扫, 不看注释。
    """
    import ast

    offenders: list[str] = []
    for relative in ENGINE_FREE_MODULES:
        path = REPO / relative
        assert path.exists(), f"清单里的模块不存在: {relative}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "src.research.loop"):
                offenders.append(f"{relative}: from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.research.loop"):
                        offenders.append(f"{relative}: import {alias.name}")
    assert not offenders, ("抽出来的能力仍依赖旧引擎, 删引擎会连带删掉它们:\n"
                           + "\n".join(offenders))


def test_no_module_imports_the_retired_pipeline():
    """全库 (src + tests + scripts) 不得再 import 已退役的模块。

    只扫 **import 语句**: 本文件自己会提到这些名字 (删除清单与防回流判据), 那不是
    "又用回来了"。
    """
    offenders: list[str] = []
    needles = ("src.graph.pipeline", "src.gui", "src.main_chat")
    for root in (REPO / "src", REPO / "tests", REPO / "scripts"):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                stripped = line.strip()
                if not (stripped.startswith("import ") or stripped.startswith("from ")):
                    continue
                if any(needle in stripped for needle in needles):
                    offenders.append(f"{path.relative_to(REPO)}:{number} -> {stripped}")
    assert not offenders, offenders


def test_there_is_exactly_one_runtime_engine():
    """**唯一运行时**: 引擎选择已删除, 任何 `mode` 都得到同一张团队图 (G01)。

    这条用例过去断言"两个引擎都要能被解析到" —— 那是迁移期的口径。现在断言反面:
    解析不接收参数、没有第二个取值, 而且**请求里的 mode 字段不再被接受** (删掉的
    字段由 Pydantic 忽略, 因此旧客户端不会报错, 也不会因此换引擎)。
    """
    import inspect

    from src.server import TEAM_ENGINE, StartRequest, _resolve_engine

    assert TEAM_ENGINE == "team"
    assert _resolve_engine() == "team"
    assert list(inspect.signature(_resolve_engine).parameters) == []
    # `mode` 不再是请求字段: 传了也不会进模型
    req = StartRequest(request="测试", mode="theory")  # type: ignore[call-arg]
    assert not hasattr(req, "mode"), "StartRequest.mode 又回来了"
    with pytest.raises(Exception):
        _resolve_engine("theory")  # type: ignore[call-arg]


def test_the_only_engine_is_the_team_session(tmp_path, monkeypatch):
    """唯一引擎必须真的能建起来, 且建出来就是团队会话 (而不是"解析得到但构建会炸")。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    session = server._make_session("t-build", topic="测试", session_id="sid-build")
    assert session.app is not None
    from src.graph.team_session import TeamApp

    assert isinstance(session.app, TeamApp)
    assert session.mode == "team"
