"""主文只通过现役 WritingAgent 与 publication Manuscript 形成。"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.writing import WritingAgent, finalise_manuscript, render_markdown
from src.publication.schemas import Manuscript, WritingPacket


def _packet() -> WritingPacket:
    return WritingPacket(
        project_id="main-text", problem_id="problem", run_id="run-1",
        main_question="211 个节点的排班能否存在？",
        claims=[{"id": "clm-1", "statement": "所述设计不存在", "status": "supported",
                 "version": 1}],
        verifications=[{"id": "ver-1", "claim_id": "clm-1", "version": 1,
                        "validation_status": "verified"}],
        sources=[],
    )


def test_offline_main_text_is_one_publication_ir():
    manuscript, note = WritingAgent().deterministic_manuscript(None, _packet())
    assert isinstance(manuscript, Manuscript)
    finalise_manuscript(manuscript, _packet())
    markdown = render_markdown(manuscript)
    assert "确定性起草" in note
    assert "clm-1" in markdown
    assert "<!-- block:" in markdown
    assert "## 7 参考文献" in markdown
    assert "依据: [" not in markdown
    assert any("claim" in block.ref_kinds for block in manuscript.all_blocks())


def test_offline_team_run_produces_traceable_paper_without_model(tmp_path, monkeypatch):
    from src import config
    from src.graph.team_session import run_team_session

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    request = (Path(__file__).resolve().parents[1] / "problem2.md").read_text(
        encoding="utf-8")
    result = run_team_session(
        request, project_id="main-text", problem_id="problem",
        source_policy="user_kb", llm_factory=lambda _role: None,
    )
    package = Path(result["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    markdown = (package / "manuscript.md").read_text(encoding="utf-8")
    assert "Bruck–Ryser–Chowla" in markdown
    assert "b=v=211" in markdown
    assert manifest["usage"]["llm_calls"] == 0
    assert manifest["manuscript_traceability"]["ok"] is True
    assert manifest["publication_gate_passed"] is True
