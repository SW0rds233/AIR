from __future__ import annotations

"""理论研究状态 (TypedDict) 的**契约测试** (计划书 §4.3 状态不变量)。

为什么需要这份测试
------------------
LangGraph 的图状态是一个 `TypedDict`: **未声明的键不会进入状态**。而入口
(`build_theory_initial_state` / `start_session`) 会往状态里塞字段, 节点再按字段名读。
于是"入口写了、状态没声明"就表现为**静默丢数据** —— 代码看起来完全正常, 只是那个值
永远是默认值。本项目已经踩过两次同样的坑:

1. `input_snapshot` 未声明 → manifest 里的启动输入一直是空的;
2. `attachment_ids` 未声明 → 题面作为附件交上来时, 研究循环读不到附件正文,
   于是一个完整、精确的问题被判成"缺少可检验对象"并请求澄清。

这类缺陷不该靠人记住, 所以把不变量写成测试:
- 入口产出/写入的**每一个**状态键都必须在 `TheoryState` 里声明;
- 声明的值类型必须与实际写入的值一致 (用临时 `TypedDict` 继承真实定义做运行时校验,
  因此类型漂移也会被挡住);
- 声明的键必须真的**存活到引擎**: 按入口产出的状态构造引擎时, 附件等关键字段必须读到。
"""

import re
from pathlib import Path
from typing import get_type_hints

from src.graph import theory_pipeline
from src.graph.theory_state import TheoryState
from src.server import StartRequest, build_theory_initial_state

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = REPO_ROOT / "src" / "server.py"

# 同症状的历史缺陷: 这些键一旦漏声明, 就会复现"数据静默丢失"
REGRESSION_KEYS = ("input_snapshot", "attachment_ids")


def _declared() -> dict:
    return get_type_hints(TheoryState)


def test_every_key_written_by_theory_entry_is_declared():
    """入口写进图状态的键必须全部在 `TheoryState` 里声明。

    未声明的键不会进入图状态 —— 表现为"入口写了、节点读不到", 且没有任何报错。
    """
    declared = set(_declared())
    state = build_theory_initial_state(StartRequest(
        request="分析信道变化对可分性的影响", mode="theory",
        project_id="contract-proj", problem_id="p1",
        attachment_ids=["att-contract"], source_set_id="kb-contract",
        source_policy="autonomous"))
    undeclared = sorted(set(state) - declared)
    assert not undeclared, (
        f"入口写入了未在 TheoryState 声明的键 {undeclared}: 这些值不会进入图状态, "
        "节点读到的是默认值")

    for key in REGRESSION_KEYS:
        assert key in state, f"入口没有产出回归键 {key}"
        assert key in declared, f"回归键 {key} 未在 TheoryState 声明 (历史缺陷会复现)"


def test_keys_assigned_later_by_start_session_are_declared():
    """`start_session` 在入口之后还会补写状态 (资料源绑定/契约复用), 同样必须声明。"""
    declared = set(_declared())
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    assigned = set(re.findall(r'initial_state\["([a-z_]+)"\]', source))
    assert assigned, "没有找到 start_session 对 initial_state 的赋值 (正则失效?)"
    undeclared = sorted(assigned - declared)
    assert not undeclared, f"start_session 写入了未声明的键: {undeclared}"


def test_declared_types_match_what_the_entry_writes():
    """声明的值类型必须与入口实际写入的值一致 (逐键运行时校验)。

    做法: 用 `TypeAdapter` 按每个键的声明类型校验入口实际写入的值 —— 类型漂移
    (例如声明成 dict 却写入 list) 会在这里失败。逐键校验而不是整体校验, 因为
    入口只写"启动时已知"的那部分键, 其余键由图节点在执行中补齐。
    """
    from pydantic import TypeAdapter

    state = build_theory_initial_state(StartRequest(
        request="分析信道变化对可分性的影响", mode="theory",
        project_id="contract-proj", problem_id="p1",
        attachment_ids=["att-contract"], source_set_id="kb-contract",
        source_policy="autonomous"))
    declared = _declared()
    for key, value in state.items():
        assert key in declared, key
        TypeAdapter(declared[key]).validate_python(value)


def test_declared_keys_survive_into_the_engine(tmp_path, monkeypatch):
    """声明的键必须真的到达引擎: 附件 id 读不到就会复现"附件题面被丢弃"的缺陷。"""
    from src import config
    from src.graph.theory_state import TheoryState as State

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")

    state: State = {
        "project_id": "contract-proj", "problem_id": "p1", "mode": "theory",
        "request": "研究此问题", "topic": "研究此问题", "run_id": "run-contract",
        "budget_max_actions": 4, "budget_max_tool_calls": 4,
        "attachment_ids": ["att-contract"],
        "input_snapshot": {"attachment_ids": ["att-contract"], "attachments": []},
    }
    engine = theory_pipeline._engine_from_state(state)
    # 顶层与快照里的附件 id 都要被合并进来 (续跑时以快照为准)
    assert engine.attachment_ids == ["att-contract"], engine.attachment_ids
