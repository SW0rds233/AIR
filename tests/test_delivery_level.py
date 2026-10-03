from __future__ import annotations

"""交付等级必须扣住实际产出: 只发澄清请求的运行不得报成论文草稿。

用例取自 `evals/cases/combinatorial-design/case.md` (211 节点无重复配对计划):
计数关系自洽但设计不存在 (BRC 定理: 14 阶射影平面不存在)。
交付等级的**语义**由本文件守住; 该题能否被系统自主判定属 S1 能力, 由
`tests/test_design_feasibility.py` 的端到端用例跟踪 (S1 完成后已达论文草稿)。
"""

from pathlib import Path

import pytest

CASE = Path(__file__).resolve().parents[1] / "evals" / "cases" / "combinatorial-design" / "case.md"


@pytest.mark.skipif(not CASE.is_file(), reason="缺少组合设计用例")
def test_delivery_level_matches_actual_output(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    import json

    from src.graph import theory_pipeline

    final = theory_pipeline.run_theory_pipeline(
        request=CASE.read_text(encoding="utf-8"), topic="problem2",
        project_id="p2case", problem_id="design", max_actions=20)

    manifest = json.loads((Path(final["package_dir"]) / "manifest.json")
                          .read_text(encoding="utf-8"))
    claims = json.loads((Path(final["package_dir"]) / "claims.json")
                        .read_text(encoding="utf-8"))
    if final.get("needs_clarification"):
        # 只请求澄清: 门槛不得通过, 等级只能是备忘录, 且明确写出原因
        assert final["gate_passed"] is False
        assert final["delivery_level"] == "研究备忘录"
        assert any("澄清" in str(n) for n in (final.get("notes") or []))
        assert manifest["delivery_level"] == "研究备忘录"
    else:
        # 自主推进时必须真的产出命题才允许高等级 (不得只发笔记就报论文草稿)
        assert manifest["delivery_level"] != "论文草稿" or claims
    # 离线运行不得产生任何外部费用
    assert manifest["usage"]["cost_usd"] == 0.0
    assert manifest["usage"]["llm_calls"] == 0
