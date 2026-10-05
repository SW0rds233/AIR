"""唯一出版稿件 IR 的 Markdown / LaTeX 同源渲染契约。"""

import hashlib
import json

from src.agents.writing import render_markdown
from src.publication.render_latex import escape_latex, render_latex
from src.publication.schemas import Block, BlockRole, Manuscript, Section
from src.research.schemas import ObjectRef


def _manuscript() -> Manuscript:
    claim = Block(role=BlockRole.claim, text="研究结论 clm-1")
    claim.add_ref("claim", ObjectRef(id="clm-1"))
    claim.add_ref("source", ObjectRef(id="doc-1"))
    table = Block(role=BlockRole.table, data={
        "header": ["条件", "结论"], "rows": [["x < 0", "不满足"], ["x >= 0", "满足"]],
    })
    return Manuscript(
        title="命题与依据", abstract="对 2-(211,15,1) 的判定。",
        sections=[Section(heading="1 结果", blocks=[claim, table])],
    )


def test_markdown_and_latex_share_citation_identity():
    paper = _manuscript()
    markdown = render_markdown(paper)
    latex = render_latex(paper, references={"doc-1": "真实来源 A"})
    assert "依据: [1]" in markdown
    assert r"\cite{ref1}" in latex
    assert r"\bibitem{ref1} 真实来源 A" in latex
    assert r"\label{claim:clm-1}" in latex
    assert "研究结论 clm-1" in markdown and "研究结论 clm-1" in latex


def test_latex_layout_has_single_title_abstract_and_bibliography():
    latex = render_latex(_manuscript(), references={"doc-1": "真实来源 A"})
    assert latex.count(r"\maketitle") == 1
    assert latex.count(r"\begin{abstract}") == 1
    assert latex.count(r"\begin{thebibliography}") == 1
    assert r"\section{结果}" in latex
    assert r"\section{1 结果}" not in latex
    assert r"\begin{tabularx}" in latex
    assert r"\end{tabularx}" in latex


def test_no_source_does_not_create_a_fake_citation():
    paper = Manuscript(title="无来源", sections=[Section(
        heading="结论", blocks=[Block(role=BlockRole.limitation, text="尚无可定位来源")])])
    latex = render_latex(paper)
    markdown = render_markdown(paper)
    assert r"\cite{" not in latex
    assert r"\bibitem{" not in latex
    assert "本次运行没有可引用的可定位来源" in latex
    assert "依据: [" not in markdown


def test_escaping_preserves_math_and_escapes_plain_specials():
    assert escape_latex("a_b & c% $x_i$ #1") == r"a\_b \& c\% $x_i$ \#1"


def test_render_numbers_do_not_mutate_source_identity():
    paper = _manuscript()
    before = paper.to_dict()
    render_markdown(paper)
    render_latex(paper, references={"doc-1": "真实来源 A"})
    assert paper.to_dict() == before


def test_figure_ref_is_rendered_in_both_formats_only_with_a_valid_asset():
    block = Block(role=BlockRole.figure_ref, heading="模型结构", text="结构见图")
    block.add_ref("figure", ObjectRef(id="fig-1"))
    paper = Manuscript(title="图示", sections=[Section(heading="结果", blocks=[block])])
    paths = {"fig-1": "figures/fig-1.png"}
    assert "![模型结构](figures/fig-1.png)" in render_markdown(paper, figures=paths)
    assert r"\includegraphics[width=0.85\textwidth]{figures/fig-1.png}" in render_latex(
        paper, figures=paths)
    assert r"\includegraphics" not in render_latex(paper)
    assert "图 fig-1 不可用" in render_markdown(paper)


def test_package_figures_are_scoped_and_hash_checked(tmp_path, monkeypatch):
    from src import config
    from src.graph.team_session import _copy_figure_assets, _figure_sources
    from src.research.store import ResearchStore

    output = tmp_path / "outputs"
    source = output / "figures" / "run-1" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"valid-png-fixture")
    monkeypatch.setattr(config, "OUTPUT_DIR", output)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = ResearchStore("fig-pack", db_path=":memory:")
    try:
        store.put("figure", "fig-1", {
            "id": "fig-1", "_scope": {"run_id": "run-1"},
            "render": {"uri": "figures/run-1/source.png", "sha256": digest},
        })
        store.put("figure", "fig-other", {
            "id": "fig-other", "_scope": {"run_id": "run-2"},
            "render": {"uri": "figures/run-1/source.png", "sha256": digest},
        })
        store.put("figure", "fig-escape", {
            "id": "fig-escape", "_scope": {"run_id": "run-1"},
            "render": {"uri": "../outside.png"},
        })
        found, warnings = _figure_sources(store, "run-1")
        assert list(found) == ["fig-1"]
        assert any("fig-escape" in warning for warning in warnings)
        package = tmp_path / "package"
        package.mkdir()
        (package / "manifest.json").write_text("{}", encoding="utf-8")
        _copy_figure_assets(package, found, warnings)
        assert (package / "figures" / "fig-1.png").read_bytes() == source.read_bytes()
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["figures"][0]["sha256"] == digest
    finally:
        store.close()
