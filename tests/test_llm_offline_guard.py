from __future__ import annotations

"""统一团队的离线开关不得创建任何角色模型。"""

import os

from src.bootstrap import role_llm_factory


def test_offline_flag_disables_role_llm(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    for role in ("supervisor", "evidence", "modeling", "reasoning", "validation",
                 "writing", "figures", "review"):
        assert role_llm_factory(role) is None


def test_conftest_forces_offline_theory_llm():
    """跑在测试进程里时, 两个开关都必须是关的 (由 conftest 设置)。"""
    assert os.environ.get("THEORY_LLM") == "0", os.environ.get("THEORY_LLM")
    assert os.environ.get("THEORY_PROPOSER") == "0"


def test_offline_team_run_accounts_zero_llm_calls(tmp_path, monkeypatch):
    """离线研究由团队规则与受限工具推进, 用量不得伪报模型调用。"""
    import json
    from pathlib import Path

    monkeypatch.setenv("THEORY_LLM", "0")
    from src import config
    from src.graph.team_session import run_team_session

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    result = run_team_session("对所有实数 x: x**2 >= 0", project_id="offline",
                              problem_id="p1", source_policy="user_kb")
    assert result["status"] in {"completed", "partial", "blocked"}
    manifest = json.loads((Path(result["package_dir"]) / "manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["usage"]["llm_calls"] == 0
