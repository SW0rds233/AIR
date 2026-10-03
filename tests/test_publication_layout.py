from __future__ import annotations

r"""出版级排版契约 (方案 v2 §2 模板规格)。

这些都是**实测踩过**的排版缺陷, 每一条都对应一次"生成的 PDF 看起来粗糙"的现场反馈:

| 现象 | 成因 | 本文件的用例 |
|---|---|---|
| 每一节都是"1 1 引言" | 稿件自带编号 + LaTeX 自动编号 | `test_sections_are_not_double_numbered` |
| 正文出现"1 英文题名与摘要" | 英文摘要被当成编号章节 | `test_english_abstract_is_not_a_numbered_section` |
| "参考文献"打印两遍 | `thebibliography` 自带标题 + 又加了 `\section*` | `test_references_heading_appears_once` |
| 作者/单位出现两行 | `\author{}` 与正文块重复 | `test_author_block_is_not_repeated` |
| 参考文献条目丢序号 | `[1]` 被 LaTeX 当可选参数吃掉 | `test_citations_render_as_superscript` |
| 判定链挤成一整段 | 多行文本被合并成一段 | `test_prose_preserves_line_breaks` |
"""


from src.rag import reference_list as refmod
from src.rag.publication_render import render_publication_latex, render_publication_markdown
from src.research.publication_paper import build_publication_paper
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    ResearchSpec,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

CERT = {"design": {"v": 211, "k": 15, "lam": 1, "b": 211, "r": 15},
        "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
        "sha256": "9089eb5c2c4d8fc3",
        "evidence": [
            {"key": "count_relations", "theorem": "计数恒等式 (设计定义)",
             "condition": "计数整除条件", "statement": "r(k-1)=λ(v-1) 且 bk=vr 必须成立",
             "inputs": {"v": 211, "k": 15, "λ": 1, "k-1": 14, "v·r": 3165},
             "result": True, "conclusion": "r=λ(v-1)/(k-1)=210/14=15 为整数",
             "citation": "组合设计基本计数 (定义直接推论)"},
            {"key": "brc", "theorem": "Bruck–Ryser–Chowla 定理 (射影平面)",
             "condition": "n≡1,2 (mod 4) 的射影平面阶必须是两平方和",
             "statement": "若 n 阶射影平面存在且 n ≡ 1, 2 (mod 4), 则 n 必为两平方和",
             "inputs": {"n": 14, "n mod 4": 2},
             "result": False, "conclusion": "n=14 不是两个平方数之和",
             "citation": "Bruck–Ryser–Chowla (1949/1950)"}]}


def _paper(with_reference: bool = True):
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在",
                  status=ClaimStatus.supported, support_kind=SupportKind.theorem_application,
                  coverage=Coverage.target, validation_status=ValidationStatus.verified,
                  design_v=211, design_k=15, design_lambda=1, design_b=211, design_r=15,
                  design_verdict="nonexistent")
    record = VerificationRecord(id="v1", claim_id="clm-1", tool="design_necessity",
                                status="passed", validation_status=ValidationStatus.verified,
                                scope=Coverage.target, arguments={"design_report": dict(CERT)})
    snapshot = ResearchSnapshot(project_id="fmt", problem_id="p1", claims=[claim],
                                verifications=[record])
    references = refmod.ReferenceList()
    related: list = []
    if with_reference:
        references = refmod.ReferenceList(references=[refmod.Reference(
            key="ref1", title="Nonexistence Certificates for Ovals",
            authors="C. Bright", year="2020", venue="arXiv", source_id="doc-1",
            support="background", location="arXiv:2001.11974")])
        # 第 4 节的关系判定来自证据 (出版层检索结果), 因此这里必须给一条
        related = [_evidence()]
    spec = ResearchSpec(project_id="fmt", problem_id="p1",
                        problem_statement="Problem 2：211 个实验节点的无重复配对计划\n\n某大型实验有 211 个节点。")
    manuscript, _ = build_publication_paper(snapshot, "Problem 2", references, spec=spec,
                                            related_evidence=related)
    # 结构化证书必须传进渲染器: 只有它能支撑公式块与必要条件表 (逐字排版只会是符号堆叠)
    tex, warnings = render_publication_latex(
        manuscript, references, certificates={"clm-1": dict(CERT)})
    return manuscript, tex, warnings


def _evidence():
    from src.research.schemas import Credibility, SourceEvidence, SourceKind, SupportKindOfEvidence

    return SourceEvidence(
        id="doc-1", title="Nonexistence Certificates for Ovals", claim_id="clm-1",
        excerpt="we prove nonexistence in a projective plane of order ten",
        location="arXiv:2001.11974", year="2020", credibility=Credibility.medium,
        source_kind=SourceKind.preprint,
        support=SupportKindOfEvidence.background, support_reason="主题相关, 仅作背景")


def test_sections_are_not_double_numbered():
    """稿件自带编号, LaTeX 必须关闭自动编号 —— 否则打印成"1 1 引言"。"""
    _, tex, _ = _paper()
    assert r"\setcounter{secnumdepth}{-2}" in tex
    assert r"\section{1 引言}" in tex
    assert r"\section{1 1 引言}" not in tex


def test_english_abstract_is_not_a_numbered_section():
    _, tex, _ = _paper()
    # 标题用论文语气 ("Abstract (English)"), 不是模板说明 ("Title and Abstract in English")
    assert r"\section*{Abstract (English)}" in tex
    assert "Title and Abstract in English" not in tex
    assert r"\section{英文题名与摘要}" not in tex
    assert "Abstract: We formalize" in tex


def test_references_heading_appears_once():
    """`thebibliography` 自带标题, 再加 `\\section*` 会打印两遍。"""
    _, tex, _ = _paper()
    assert r"\begin{thebibliography}" in tex
    assert tex.count("参考文献") == 0 or r"\section*{参考文献}" not in tex


def test_author_block_is_not_repeated():
    """作者/单位只在 `\\author{}` 里 (Markdown 侧保留, LaTeX 侧不重复)。"""
    _, tex, _ = _paper()
    assert tex.count("待作者补充）") >= 1        # 出现在 \author{} 内
    lines = [line for line in tex.splitlines() if line.strip() == "作者单位（待作者补充，含城市与邮编）"]
    assert not lines, "作者单位被重复输出到正文"


def test_citations_render_as_superscript():
    """正文引用必须是 `\\upcite{refN}`: 直接写 `[1]` 会被当可选参数吃掉。"""
    _, tex, _ = _paper()
    assert r"\upcite{ref1}" in tex
    assert r"\bibitem{ref1}" in tex


def test_abstract_uses_environment_and_keywords_follow():
    _, tex, _ = _paper()
    assert r"\begin{abstract}" in tex and r"\end{abstract}" in tex
    assert "关键词" in tex


def test_prose_preserves_line_breaks():
    """多行块必须按行渲染: 判定链挤成一段就读不了了。"""
    _, tex, _ = _paper()
    blank_line_separated = tex.count("\n\n")
    assert blank_line_separated > 10, "段落之间必须有空行 (否则全部挤在一起)"


def test_markdown_side_keeps_abstract_heading():
    """Markdown 侧要有"## 摘要": 少了它就看不出哪段是摘要 (LaTeX 侧由环境承担)。"""
    manuscript, tex, _ = _paper()
    markdown = render_publication_markdown(manuscript)
    assert "## 摘要" in markdown
    assert r"\begin{abstract}" in tex


# ----------------------------------------------------------------------
# 数学排版 (现场反馈: "符号公式应当更标准, 而不是文字与公式堆叠")
# ----------------------------------------------------------------------

def test_certificate_renders_as_display_math():
    """判定对象与计数关系必须是 `equation*` 公式块, 而不是塞进段落的一串符号。"""
    _, tex, _ = _paper()
    assert r"\begin{equation*}" in tex and r"\end{equation*}" in tex
    assert r"\frac{\lambda(v-1)}{k-1}" in tex, "计数恒等式应排成分式"
    assert "$v = 211" in tex or "v = 211" in tex


def test_certificate_has_per_condition_items_and_summary_table():
    """每条必要条件分条列出 (定理/输入/结论), 并附汇总表 —— 这是"可读"的关键。"""
    _, tex, _ = _paper()
    assert r"\subsection*{必要条件逐条核验}" in tex
    assert r"\item[满足]" in tex or r"\item[违反]" in tex
    assert r"\subsection*{必要条件汇总}" in tex
    assert r"\begin{tabular}" in tex and r"\toprule" in tex


def test_chinese_never_inside_math_mode():
    r"""中文不得进数学模式 —— 会报 "Missing character" 并排版错乱 (实测)。

    判定方式: 取出每个 `$...$` 片段 (不跨行、不允许中间再出现 `$`), 断言其中没有汉字。
    正则里必须排除 `$`: 否则相邻的两个公式 (`$v=211$、$k=15$`) 之间那段中文会被
    误判成"数学模式里的中文"。
    """
    import re

    _, tex, _ = _paper()
    assert "$$" not in tex, "出现空数学模式 `$$` (相邻公式之间会排成空公式)"
    for body in re.findall(r"\$([^$\n]+)\$", tex):
        assert not re.search(r"[\u4e00-\u9fff]", body), f"数学模式里有中文: {body}"


def test_no_doubled_backslash_before_commands():
    r"""不得出现 `\\lambda` / `\\cdot` 这种双反斜杠命令。

    实测: 双反斜杠会排成"反斜杠+字母", PDF 里出现过 "lamdbda = 1"、"cdotr = 3165"
    这种把命令名当变量名的排版。
    """
    _, tex, _ = _paper()
    for bad in (r"\\lambda", r"\\cdot", r"\\geq", r"\\times"):
        assert bad not in tex, f"出现双反斜杠命令 {bad}"


def test_math_commands_are_terminated_so_they_do_not_swallow_letters():
    r"""数学命令必须**成对**使用, 且命令名不得被后续字母并掉。

    两类实测事故各用一条直白判据覆盖 (不用正则位置匹配 —— 那种写法在不同调用方式下
    结论不一致, 反而成了噪声):
    1. `\geq` 后直接跟字母会变成名为 `geqv` 的未知命令, 中断整篇编译 ——
       因此危险命令在正文里**要么**后跟 `{}`, **要么**后跟非字母;
    2. 数学模式必须成对 (由 `test_inline_math_output_has_no_nested_or_doubled_dollars`
       覆盖)。
    """

    from src.rag.publication_render import _MATH_SYMBOLS, _normalize_math_symbols

    # 每条命令在本渲染器里的规范形式都带 `{}` 终止符
    for symbol, latex in _MATH_SYMBOLS.items():
        assert latex.endswith("}"), f"{symbol} 的替换 {latex!r} 缺少终止花括号"

    # 紧跟字母的替换会被并进命令名 -> 规范化时必须补 `{}`
    # (只测**紧邻字母**的形态: 符号被空格隔开时无需补终止符)
    for text, expect in (("b ≥v", r"\geq{}"),
                         ("v·r", r"\cdot{}"),
                         ("n≡1", r"\equiv{}")):
        got = _normalize_math_symbols(text)
        assert expect in got, f"{text!r} -> {got!r} 未补终止符"

    _, tex, _ = _paper()
    body = tex.split(r"\begin{document}", 1)[-1]
    assert "geqv" not in body and "cdotr" not in body, "命令名被后续字母并掉"
    # 数学模式成对 (奇数个 `$` 说明有未闭合的数学模式)
    assert body.count("$") % 2 == 0, "数学模式定界符数量不是偶数"


# ----------------------------------------------------------------------
# 行内公式 (现场反馈: "突出数学语言, 而不是文字与公式堆叠")
# ----------------------------------------------------------------------

def test_inline_formulas_in_prose_are_wrapped():
    from src.rag.publication_render import wrap_inline_math as wrap

    assert wrap("计数约束参数 (v=211, k=15, λ=1)") == \
        "计数约束参数 ($v=211$, $k=15$, $\\lambda{}=1$)"
    assert wrap("由 r(k-1)=λ(v-1) 与 bk=vr 推出") == \
        "由 $r(k-1)=\\lambda{}(v-1)$ 与 $bk=vr$ 推出"
    assert wrap("都满足 b ≥ v") == "都满足 $b \\geq{} v$"


def test_inline_math_never_touches_chinese_or_existing_math():
    from src.rag.publication_render import wrap_inline_math as wrap

    # 纯中文不动
    assert wrap("仅有中文的句子。") == "仅有中文的句子。"
    # 已在数学模式里的内容不动 (不能出现嵌套 `$`)
    assert wrap("已有 $x_i$ 与 A_1") == "已有 $x_i$ 与 A_1"
    # 不含关系符的片段不动
    assert wrap("2-(v,k,λ) 设计") == "2-(v,k,λ) 设计"
    # `^` 与 `mod` 不进数学模式 (数学模式里语义/排版都不对)
    assert wrap("素因数分解 = 2^1 × 7") == "素因数分解 = 2^1 × 7"
    assert wrap("n ≡ 1, 2 (mod 4)") == "$n \\equiv{} 1$, 2 (mod 4)"


def test_inline_math_output_has_no_nested_or_doubled_dollars():
    import re

    from src.rag.publication_render import wrap_inline_math as wrap

    text = wrap("计数约束参数 (v=211, k=15, λ=1) 满足 b ≥ v 与 r(k-1)=λ(v-1)")
    assert "$$" not in text
    # 数学模式成对
    assert text.count("$") % 2 == 0, text
    # 括号不得被卷进数学模式 (前导与末尾都要留在外面)
    assert "($v=211$, $k=15$, $\\lambda{}=1$)" in text, text
    assert not re.search(r"\$[^$]*[（(]\s*$", text)


# 本渲染器**只会**产生这些命令。多出来的命令名一定是符号替换出错
# (例如 `\geq` 吞掉后一个字母变成 `\geqv`, 或双反斜杠产生 `\\lambda`)。
KNOWN_COMMANDS = {
    "lambda", "cdot", "geq", "leq", "times", "neq", "approx", "in", "equiv",
    "infty", "pm", "frac", "qquad", "quad", "text", "noindent", "textbf",
    "bmod", "item", "toprule", "midrule", "bottomrule", "label", "ref",
    "begin", "end",
    "upcite", "cite", "bibitem", "section", "subsection", "texttt", "author",
    "documentclass", "usepackage", "title", "date", "maketitle", "hypersetup",
    "newtheorem", "theoremstyle", "renewcommand", "setcounter", "newcolumntype",
    "addcontentsline", "centering", "emptyspace",
}


def test_no_unknown_backslash_commands_leak_into_the_tex():
    r"""所有 `\命令` 必须在本渲染器的已知命令集合里。

    这条判据精确定位"符号替换出错": `\geq` 吞掉后一个字母会变成 `\geqv`,
    双反斜杠会变成 `\\lambda` (LaTeX 排成"反斜杠+字母")。两者都不在集合里。
    """
    import re

    from src.rag.publication_render import render_publication_latex

    manuscript, tex, _ = _paper()
    # 只看**正文**部分: 导言区里的包名与命令由模板提供
    body = tex.split(r"\begin{document}", 1)[-1]
    unknown = sorted({name for name in re.findall(r"\\([A-Za-z]+)", body)
                      if name not in KNOWN_COMMANDS})
    assert not unknown, f"出现未知 LaTeX 命令 (疑似符号替换出错): {unknown}"
    assert render_publication_latex(manuscript, None)[0]
