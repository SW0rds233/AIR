from __future__ import annotations

"""出版层测试 (方案 v2 §5 阶段 1/3): 出版完备门槛、参考文献双向映射、期刊式稿件。

本文件的用例对应方案里最容易出事的三处:
1. **不许"看起来完整"**: 缺摘要/关键词/参考文献表/PDF 时必须降级并说明缺什么;
2. **引用双向可核**: 正文 `[n]` 与参考文献表必须一一对应 (正反向各一个用例);
3. **引用不得改变结论**: 论文里的命题陈述必须逐字等于冻结快照的 `Claim.statement`,
   且证书摘要不因出版层而改变。
"""

from pathlib import Path

from src.rag import reference_list as refmod
from src.rag.publication_render import (
    escape_latex_keep_commands,
    render_publication_latex,
    render_publication_markdown,
)
from src.rag.theory_render import Block, Manuscript
from src.research.acceptance import publication_complete, publication_gate
from src.research.publication_paper import build_publication_paper
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    ResearchSpec,
    RetrievalCoverage,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> ResearchSnapshot:
    claim = Claim(id="clm-1", statement="计数约束参数 (v=211, k=15, λ=1) 的 2-设计不存在",
                  status=ClaimStatus.supported, support_kind=SupportKind.theorem_application,
                  coverage=Coverage.target, validation_status=ValidationStatus.verified,
                  design_v=211, design_k=15, design_lambda=1, design_b=211, design_r=15,
                  design_verdict="nonexistent")
    record = VerificationRecord(id="v1", claim_id="clm-1", tool="design_necessity",
                               status="passed", validation_status=ValidationStatus.verified,
                               certificate="deadbeef", raw_output="等价于 14 阶射影平面")
    return ResearchSnapshot(project_id="pubtest", problem_id="p1", claims=[claim],
                            verifications=[record])


def _evidence(evidence_id: str = "ev-1", support: str = "supports"):
    from src.research.schemas import Credibility, SourceEvidence, SourceKind, SupportKindOfEvidence

    return SourceEvidence(id=evidence_id, title="On projective planes of small order",
                          claim_id="clm-1", year="1989", location="pp. 1-20",
                          doi="10.1000/xyz", credibility=Credibility.high,
                          source_kind=SourceKind.journal,
                          support=SupportKindOfEvidence(support),
                          support_reason="给出同阶射影平面不存在的判定")


# ----------------------------------------------------------------------
# 出版完备门槛
# ----------------------------------------------------------------------

def test_publication_gate_needs_abstract_keywords_and_references():
    snapshot = _snapshot()
    manuscript, _ = build_publication_paper(snapshot, "题", spec=ResearchSpec(
        project_id="pubtest", problem_id="p1", problem_statement="判断该设计是否存在"))
    text = render_publication_markdown(manuscript)
    gate = publication_gate(text, snapshot, references=[], compile_status="not_attempted",
                            blocks=manuscript.blocks)
    assert gate.passed is True, gate.reasons
    # 缺 PDF 属于实质缺项: 未决 -> 不得称"完整论文"
    assert gate.unresolved, "未编译 PDF 必须记为未决"
    assert publication_complete(gate) is False


def test_publication_gate_rejects_missing_abstract():
    snapshot = _snapshot()
    manuscript = Manuscript(title="无摘要稿件")
    # 正文里有"关键词"字样, 但**没有** abstract/keywords 结构化块 -> 摘要仍算缺失
    manuscript.blocks.append(Block("prose", "正文与关键词说明, 但没有摘要块"))
    gate = publication_gate(render_publication_markdown(manuscript), snapshot,
                            references=[], compile_status="ok", blocks=manuscript.blocks)
    assert gate.passed is False
    assert any("摘要" in r for r in gate.reasons), gate.reasons


def test_publication_gate_detects_citation_not_in_reference_list():
    """正文出现文献表里没有的编号 -> 必须失败 (反幻觉最硬的一条)。"""
    snapshot = _snapshot()
    manuscript, _ = build_publication_paper(snapshot, "题", spec=ResearchSpec(
        project_id="pubtest", problem_id="p1", problem_statement="判断该设计是否存在"))
    text = render_publication_markdown(manuscript).replace(
        "本次运行未执行外部文献检索", "已有研究[7]表明同类设计不存在。本次运行未执行外部文献检索")
    gate = publication_gate(text, snapshot, references=[], compile_status="ok",
                            blocks=manuscript.blocks)
    assert gate.passed is False
    assert any("参考文献表中没有的编号" in r for r in gate.reasons), gate.reasons


def test_publication_gate_marks_uncited_reference_as_unresolved():
    """文献表里有条目但正文没引用 -> 未决 (凑引用)。"""
    snapshot = _snapshot()
    references = [refmod.Reference(key="ref1", title="某文献", authors="A", year="1990")]
    manuscript = Manuscript(title="题")
    for kind, text in (("abstract", "摘要内容"), ("keywords", "关键词：a；b"),
                       ("heading", "参考文献")):
        manuscript.blocks.append(Block(kind, text))
    manuscript.blocks.append(Block("prose", "正文引用了[1]"))
    markdown = render_publication_markdown(manuscript).replace("[1]", "[2]")
    gate = publication_gate(markdown, snapshot,
                            references=[item.to_dict() for item in references],
                            compile_status="ok", blocks=manuscript.blocks)
    assert any("未被正文引用" in item for item in gate.unresolved), gate.unresolved


# ----------------------------------------------------------------------
# 参考文献表与引用编号的双向映射
# ----------------------------------------------------------------------

def test_reference_list_only_accepts_evidence_with_support_relation():

    good = _evidence("ev-1", "supports")
    weak = _evidence("ev-2", "insufficient")
    refs = refmod.build_reference_list([good, weak])
    assert len(refs) == 1, "未判定支持关系的证据不得进参考文献表"
    assert refs.excluded and "支持关系未判定" in refs.excluded[0]["reason"]


def test_citation_placeholder_maps_to_number_and_flags_unknown_key():
    refs = refmod.build_reference_list([_evidence("ev-1")])
    missing: list[str] = []
    # 正文占位形态: `[[REF:key]]` (生成器只写占位, 编号由本模块统一分配)
    text = refmod.render_markdown_citations(
        "见 [[REF:ref1]] 与 [[REF:ref9]]", refs, missing)
    assert "[1]" in text, text
    assert "未收录引用:ref9" in text, text
    assert missing == ["ref9"], "未收录引用不得静默丢弃"


def test_citation_marker_is_literal_not_a_regex_snippet():
    """占位字面量必须是 `[[REF:key]]`。

    回归: 曾用 `CITE_PATTERN.replace(...)` 造占位 —— 那是在**正则**上做字符串替换,
    结果字面反斜杠被印进正文 (`\\[\\[REF:ref1\\]\\]`), 占位永远解析不成 `[n]`。
    """
    marker = refmod.citation_marker("ref1")
    assert marker == "[[REF:ref1]]", marker
    assert "\\" not in marker
    refs = refmod.ReferenceList(references=[refmod.Reference(key="ref1", title="t")])
    assert "[1]" in refmod.render_markdown_citations(marker, refs, [])


def test_reference_section_present_even_without_references():
    refs = refmod.ReferenceList()
    markdown = refs.render_markdown("本次未执行检索。")
    assert "## 参考文献" in markdown
    assert "本次未执行检索" in markdown


# ----------------------------------------------------------------------
# 期刊式稿件: 结论必须原文照搬
# ----------------------------------------------------------------------

def test_publication_paper_copies_claim_statement_verbatim():
    snapshot = _snapshot()
    manuscript, _ = build_publication_paper(snapshot, "题", spec=ResearchSpec(
        project_id="pubtest", problem_id="p1", problem_statement="判断该设计是否存在"))
    claim_blocks = [b for b in manuscript.blocks
                    if b.kind in ("theorem", "proposition", "lemma", "corollary")]
    assert claim_blocks, "主要结论必须成为定理/命题块"
    assert claim_blocks[0].text == snapshot.claims[0].statement, "结论陈述必须逐字照搬快照"
    assert manuscript.writing_map["clm-1"], "结论必须映射到正文位置 (可反查)"


def test_publication_paper_has_abstract_keywords_and_all_chapters():
    snapshot = _snapshot()
    manuscript, _ = build_publication_paper(snapshot, "题", spec=ResearchSpec(
        project_id="pubtest", problem_id="p1", problem_statement="判断该设计是否存在"),
        coverage=RetrievalCoverage(executed=False))
    kinds = [b.kind for b in manuscript.blocks]
    assert "abstract" in kinds and "keywords" in kinds
    # 参考文献用专门 kind (LaTeX 侧由 thebibliography 承担, 不重复输出章节)
    assert "references" in kinds
    headings = [b.text for b in manuscript.blocks if b.kind == "heading"]
    for chapter in ("1 引言", "2 问题与形式化", "3 主要结果", "4 与已有工作比较",
                    "5 讨论与局限", "6 结论"):
        assert chapter in headings, f"缺少章节 {chapter}"
    text = render_publication_markdown(manuscript)
    assert "## 参考文献" in text
    # 离线无检索时不得宣称"无先例"
    assert "未检索不等于" in text or "不构成" in text


def test_publication_markdown_keeps_text_unlatexed():
    """Markdown 侧不得做 LaTeX 转义: `\\_`、`\\[` 在 Markdown 里是字面反斜杠。"""
    manuscript = Manuscript(title="关于 A_1 与 50% 的研究")
    manuscript.blocks.append(Block("prose", "占比 50% 与 A_1 的关系, 见 [[REF:ref1]]"))
    markdown = render_publication_markdown(manuscript)
    assert "A_1" in markdown and r"A\_1" not in markdown
    assert "50%" in markdown and r"50\%" not in markdown
    assert "[[REF:ref1]]" in markdown, "占位符必须原样保留, 交给引用解析步骤"


def test_publication_latex_keeps_abstract_env_and_bibliography():
    snapshot = _snapshot()
    refs = refmod.build_reference_list([_evidence("ev-1")])
    manuscript, _ = build_publication_paper(snapshot, "题", references=refs,
                                            spec=ResearchSpec(
                                                project_id="pubtest", problem_id="p1",
                                                problem_statement="判断该设计是否存在"))
    tex, warnings = render_publication_latex(manuscript, refs)
    assert not warnings, warnings
    assert r"\begin{abstract}" in tex and r"\end{abstract}" in tex
    assert r"\begin{thebibliography}" in tex and r"\bibitem{ref1}" in tex
    assert r"\section{3 主要结果}" in tex
    assert r"\begin{theorem}" in tex
    # 生成器写入的引用命令必须保留, 不能被转义成文本
    assert "[未收录引用" not in tex


def test_escape_keeps_generated_latex_but_escapes_plain_specials():
    text = escape_latex_keep_commands(r"结论 A_1 见 \upcite{ref1}; 变量 $x_i$ 与 50% 的占比")
    assert r"A\_1" in text
    assert r"\upcite{ref1}" in text, text
    assert "$x_i$" in text
    assert r"50\%" in text


def test_latex_special_characters_do_not_break_compilation_inputs():
    """下划线/百分号/& 必须转义, 否则 xelatex 会报 Missing $ inserted。"""
    manuscript = Manuscript(title="关于 A_1 与 50% 的研究")
    manuscript.blocks.append(Block("prose", "占比 50% 与 A_1 的关系"))
    tex, _ = render_publication_latex(manuscript, None)
    assert r"A\_1" in tex and r"50\%" in tex
