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
    """服务端不得再有"第二张图"的入口, 也不得再接受 `survey` 作为引擎取值。"""
    source = _code(REPO / "src" / "server.py")
    assert "build_pipeline" not in source, "旧的 survey 图入口又回来了"
    assert 'in ("theory", "survey"' not in source
    assert 'mode="survey"' not in source
    # 引擎取值只有两种, 且默认值明确
    assert 'if chosen in ("theory", "team"):' in source
    assert 'DEFAULT_ENGINE = "theory"' in source


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
    """CLI 默认入口必须走团队会话引擎 (而不是已删除的 stage 流水线)。"""
    source = _code(REPO / "src" / "main.py")
    assert "run_team_session" in source
    assert "run_pipeline" not in source
    assert '"team"' in source, "CLI 的 --mode 默认值必须是 team"


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


def test_engine_resolution_covers_both_remaining_engines():
    """两个引擎都要能被解析到, 且退役取值退化为默认引擎。"""
    from src.server import DEFAULT_ENGINE, _resolve_engine

    assert _resolve_engine("theory") == "theory"
    assert _resolve_engine("team") == "team"
    assert _resolve_engine("survey") == DEFAULT_ENGINE
    assert _resolve_engine("") == DEFAULT_ENGINE


@pytest.mark.parametrize("mode", ["theory", "team"])
def test_both_engines_can_be_built(tmp_path, monkeypatch, mode):
    """两个引擎都必须真的能建起来 (而不是"解析得到但构建会炸")。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    session = server._make_session("t-build", topic="测试", session_id="sid-build",
                                   mode=mode)
    assert session.app is not None
    if mode == "team":
        from src.graph.team_session import TeamApp

        assert isinstance(session.app, TeamApp)
    else:
        assert hasattr(session.app, "stream")
