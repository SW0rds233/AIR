from __future__ import annotations

r"""阶段 4 (方案 v2 M4): 撰写子图接入与长文附录测试。

关注三件事:
1. **映射正确**: 冻结快照 → 撰写层输入的字段必须逐项可解释, 引用编号与正文同一套;
2. **不改结论**: 长文只能作为附录追加, 不能覆盖第 3 节的判定链, 且不写回冻结快照;
3. **失败可降级**: 撰写层报错/离线时保留阶段 3 稿件并如实记原因。
"""

from pathlib import Path

import pytest

from src.rag import reference_list as refmod
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)
from src.research.writing_bridge import (
    append_latex_appendix,
    append_long_form_appendix,
    build_writing_inputs,
    draft_to_latex_section,
    write_long_form,
)

CASE = Path(__file__).resolve().parents[1] / "evals" / "cases" / "combinatorial-design" / "case.md"


_CERTIFICATE = {
    "design": {"v": 211, "k": 15, "lam": 1},
    "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
    "evidence": [{"condition": "Bruck–Ryser–Chowla 必要条件", "result": False,
                  "theorem": "Bruck–Ryser–Chowla 定理 (射影平面)",
                  "inputs": {"n": 14}}],
}


def _snapshot() -> ResearchSnapshot:
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在", status=ClaimStatus.supported,
                  support_kind=SupportKind.theorem_application, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified, design_v=211, design_k=15,
                  design_lambda=1, design_b=211, design_r=15, design_verdict="nonexistent")
    # 证书真实存放位置: `VerificationRecord.arguments["design_report"]`
    # (`certificate` 字段只是摘要 sha256, 不是判定链本体)
    record = VerificationRecord(id="v1", claim_id="clm-1", tool="design_necessity",
                               status="passed", validation_status=ValidationStatus.verified,
                               certificate="deadbeef",
                               arguments={"design_report": dict(_CERTIFICATE)})
    return ResearchSnapshot(project_id="lf", problem_id="p1", claims=[claim],
                            verifications=[record])


def _references() -> refmod.ReferenceList:
    return refmod.ReferenceList(references=[
        refmod.Reference(key="ref1", title="On the nonexistence of a 2-(211,15,1) design",
                         authors="A. Author", year="1990", venue="J. Combin. Theory",
                         source_id="doc-1")])


# ----------------------------------------------------------------------
# 输入映射
# ----------------------------------------------------------------------

def test_writing_inputs_carry_topic_notes_and_outline():
    inputs = build_writing_inputs(_snapshot(), "Problem 2", _references(), question="能否存在")
    assert inputs["research_topic"] == "Problem 2"
    assert inputs["skip_retrieval"] is True, "素材已就绪, 不许撰写层自行检索"
    # 判定链与证书必须作为素材进入 (否则长文与冻结结论脱钩)
    assert "判定链" in inputs["literature_review_notes"]
    assert "deadbeef" in inputs["literature_review_notes"]
    assert "2-(211,15,1) 设计不存在" in inputs["literature_review_notes"]
    # 骨架含六章与不可改写的附录约束
    for chapter in ("1 引言", "3 主要结果", "6 结论", "附录 A"):
        assert chapter in inputs["paper_outline"], inputs["paper_outline"]


def test_reference_numbers_match_the_publication_layer():
    """`ref_number` 必须与正文 `[n]` 同一套编号, 否则两栏的 [1] 指向不同文献。"""
    inputs = build_writing_inputs(_snapshot(), "t", _references())
    refs = inputs["verified_references"]
    assert refs and refs[0]["ref_number"] == "1"
    assert refs[0]["title"] == "On the nonexistence of a 2-(211,15,1) design"


# ----------------------------------------------------------------------
# 只读快照 + 降级
# ----------------------------------------------------------------------

def test_write_long_form_is_off_by_default(monkeypatch):
    monkeypatch.delenv("THEORY_LONG_FORM", raising=False)
    result = write_long_form(_snapshot(), "t", _references())
    assert result.produced is False
    assert "未启用" in result.note


def test_write_long_form_never_raises_on_writer_failure(monkeypatch):
    monkeypatch.setenv("THEORY_LONG_FORM", "1")

    def _boom(_state):
        raise RuntimeError("模型超时")

    result = write_long_form(_snapshot(), "t", _references(), writer=_boom)
    assert result.produced is False
    assert "模型超时" in result.note


def test_write_long_form_reports_writer_error_field(monkeypatch):
    monkeypatch.setenv("THEORY_LONG_FORM", "1")
    result = write_long_form(_snapshot(), "t", _references(),
                             writer=lambda _state: {"error": "无任何文献素材或已验证引用"})
    assert result.produced is False
    assert "无任何文献素材" in result.note


def test_write_long_form_does_not_mutate_the_snapshot(monkeypatch):
    """长文写作不得改动研究对象 (命题/验证记录/义务)。"""
    monkeypatch.setenv("THEORY_LONG_FORM", "1")
    snapshot = _snapshot()
    before = snapshot.model_dump(mode="json")
    write_long_form(snapshot, "t", _references(),
                    writer=lambda _state: {"paper_draft": "# 长文\n正文内容"})
    assert snapshot.model_dump(mode="json") == before


def test_write_long_form_returns_draft(monkeypatch):
    monkeypatch.setenv("THEORY_LONG_FORM", "1")
    result = write_long_form(_snapshot(), "t", _references(),
                             writer=lambda _state: {"paper_draft": "# 长文\n正文内容"})
    assert result.produced is True
    assert "正文内容" in result.draft


# ----------------------------------------------------------------------
# 附录组装: 不覆盖判定链
# ----------------------------------------------------------------------

def test_appendix_is_added_without_touching_the_main_body():
    main = "# 题\n\n## 3 主要结果\n\n**定理 1**：结论\n\n## 参考文献\n\n[1] 某文献\n"
    merged = append_long_form_appendix(main, "# 长文初稿\n\n这是生成性正文。")
    assert merged.startswith(main.rstrip()), "正文必须原样保留在前面"
    assert "附录 B" in merged
    assert "以第 3 节为准" in merged, "必须声明冲突时以判定链为准"


def test_appendix_is_not_added_when_no_draft():
    main = "# 题\n\n正文\n"
    assert append_long_form_appendix(main, "   ") == main


def test_latex_appendix_escapes_dangerous_characters():
    tex = "\\documentclass{ctexart}\n\\begin{document}\n\\end{document}\n"
    merged = append_latex_appendix(tex, "# 小标题\n占比 50% 与 A_1 与 $x$ 与 100%")
    assert merged.index("附录 B") < merged.index("\\end{document}"), "附录必须在文末之前"
    # 取附录正文 (跳过 \section*/addcontentsline 两行, 否则拿到的只是节标题)
    body = merged.split("长文初稿}", 1)[1]
    assert r"50\%" in body and r"A\_1" in body, body
    assert "$x$" not in body, "生成性正文里的裸 $ 必须转义, 否则整篇编译失败"
    assert r"\$x\$" in body, body


def test_latex_appendix_absent_when_no_draft():
    tex = "\\begin{document}\n\\end{document}\n"
    assert append_latex_appendix(tex, "") == tex


def test_draft_to_latex_section_keeps_line_structure():
    section = draft_to_latex_section("# 第一节\n\n正文一\n\n## 子节\n正文二")
    assert "\\section*{附录 B 长文初稿}" in section
    assert "\\subsection*{第一节}" in section
    assert "\\subsection*{子节}" in section
    assert "正文一" in section and "正文二" in section


# ----------------------------------------------------------------------
# 端到端: 附录必须真的进入交付包 (且不改结论)
# ----------------------------------------------------------------------

@pytest.mark.skipif(not CASE.is_file(), reason="缺少组合设计用例")
def test_long_form_appendix_reaches_package_without_changing_certificate(tmp_path, monkeypatch):
    from src import config
    from src.graph import theory_pipeline

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    monkeypatch.setenv("THEORY_LONG_FORM", "1")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    import src.agents.paper_writer as writer_mod

    def _fake_writer(state):  # 离线: 用确定性文本代替模型, 只验证桥接与组装
        assert "判定链" in state["literature_review_notes"]
        return {"paper_draft": "# 长文初稿\n\n## 1 引言\n这是撰写层产出的正文。",
                "usage": {"tokens": 0}}

    monkeypatch.setattr(writer_mod, "run_paper_writing", _fake_writer)
    final = theory_pipeline.run_theory_pipeline(
        request=CASE.read_text(encoding="utf-8"), topic="Problem 2", project_id="lfp",
        problem_id="p1", max_actions=12, max_tool_calls=12)

    package = Path(final["package_dir"])
    publication = (package / "publication.md").read_text(encoding="utf-8")
    tex = (package / "publication.tex").read_text(encoding="utf-8")
    assert "附录 B" in publication, final.get("notes")
    assert "这是撰写层产出的正文" in publication
    assert "附录 B" in tex
    assert (package / "publication.pdf").is_file(), final.get("notes")
    # 结论与证书不受长文影响
    claims = (package / "claims.json").read_text(encoding="utf-8")
    assert "nonexistent" in claims
    assert "9089eb5c2c4d8fc3139a5c0798a1f2a57a6c66e6190a668817741d4cdd6eb32b" in claims \
        or "9089eb5c2c4d8fc3" in claims, claims
    assert final["delivery_level"] == "完整论文", final.get("notes")
