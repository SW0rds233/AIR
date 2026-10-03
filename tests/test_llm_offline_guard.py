from __future__ import annotations

"""测试隔离: 自动化测试不得连接真实 LLM。

计划书 P2 的发布判据要求"同一条验收题重复运行得到同一结果"。曾出现的缺陷是:
`THEORY_PROPOSER=0` 只关掉了提议器, 理论流水线**仍然**给引擎注入真实 LLM 并用于
推导步骤 —— 于是同一条代数题会因为模型每次给的步骤不同而时通时不通 (实测约 1/5
失败), 同时真的产生 API 费用。此文件把这条边界固定下来。
"""

import os

from src.graph import theory_pipeline


def test_offline_flag_disables_role_llm(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    assert theory_pipeline._role_llm() is None


def test_conftest_forces_offline_theory_llm():
    """跑在测试进程里时, 两个开关都必须是关的 (由 conftest 设置)。"""
    assert os.environ.get("THEORY_LLM") == "0", os.environ.get("THEORY_LLM")
    assert os.environ.get("THEORY_PROPOSER") == "0"


def test_offline_engine_has_no_llm(tmp_path, monkeypatch):
    """离线启动的引擎不带 LLM: 推导只能来自规则层与受限工具。"""
    monkeypatch.setenv("THEORY_LLM", "0")
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore

    spec = ResearchSpec(project_id="offline", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("offline", db_path=tmp_path / "offline.sqlite")
    engine = TheoryEngine(spec, store, llm=theory_pipeline._role_llm(),
                          budget=ResearchBudget(max_actions=6))
    assert engine.llm is None, "离线模式不得构造模型客户端"
    result = engine.run()
    assert result is not None
    store.close()
