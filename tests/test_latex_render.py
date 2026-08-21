"""LaTeX 渲染测试：引用解析一致性 / 表格安全 / 转义（离线）

运行:
    python tests/test_latex_render.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.latex_render import (
    render_latex,
    _build_thebibliography_from_draft,
    _convert_table_pandoc,
    _escape_latex,
    _md_to_latex_body,
)


DRAFT = """# 测试综述

## 摘要

本文综述某领域[1,3]。其网络安全令人担忧 [1, 2, 3]。

## 1 引言

早期工作见[1-3]。后续研究[2]。

| 方法 | 代表文献 |
|------|----------|
| 方法A | [11] |
| 方法B | [17] |

## 参考文献

[1] 张三. 论文一[J]. 期刊A, 2020, 1(2): 1-10.
[2] LI X, et al. Paper Two with Raw_I_Q[J]. Journal B, 2021, 3(4): 20-30.
[3] WANG W. Paper Three[C]. Conference C, 2022: 100-110.
"""


def test_thebibliography_matches_body_citations():
    """核心回归: bibitem 必须来自草稿自身参考文献章节,
    与正文引用编号严格一致 (旧版用 state verified_refs,
    回退历史最优稿后编号错位 → PDF 引用全部显示 [?])"""
    tex = render_latex(DRAFT, "测试综述", [], None)
    cites = set()
    for m in re.finditer(r"\\cite\{([^}]+)\}", tex):
        cites.update(k.strip() for k in m.group(1).split(","))
    bibs = set(re.findall(r"\\bibitem\{([^}]+)\}", tex))
    assert cites, "正文引用未转换为 \\cite"
    assert cites <= bibs, f"缺失 bibitem: {cites - bibs}"


def test_cite_range_expanded_and_sorted():
    """[1-3] 展开为 [1,2,3] → \\cite{ref1,ref2,ref3}"""
    tex = render_latex(DRAFT, "测试综述", [], None)
    assert r"\cite{ref1,ref2,ref3}" in tex


def test_table_row_starting_with_bracket_protected():
    """表格行首为 [n] 时必须加 \\relax: 否则 LaTeX 把 \\\\ 后的 [..]
    解析为竖向间距参数 → 'Illegal unit of measure' 编译中断 (实测 PDF 引用全 [?])"""
    md_table = "| 代表文献 | 方法 |\n|----------|------|\n| [11] | 方法A |\n| [17] | 方法B |"
    tab = _convert_table_pandoc(md_table)
    # 数据行行首为 [n] 的要保护
    assert r"\relax [11]" in tab
    assert r"\relax [17]" in tab


def test_table_normal_rows_untouched():
    md_table = "| 方法 | 精度 |\n|------|------|\n| 方法A | 0.9 |"
    tab = _convert_table_pandoc(md_table)
    assert r"\relax" not in tab
    assert "方法A & 0.9" in tab


def test_build_thebibliography_from_draft_entries():
    biblio = _build_thebibliography_from_draft(DRAFT)
    assert r"\bibitem{ref1}" in biblio
    assert r"\bibitem{ref2}" in biblio
    assert r"\bibitem{ref3}" in biblio
    # 下划线转义 (Raw_I_Q 未转义会触发 Missing $ inserted)
    assert r"Raw\_I\_Q" in biblio
    # 按编号排序输出
    assert biblio.index("ref1") < biblio.index("ref2") < biblio.index("ref3")


def test_build_thebibliography_from_draft_empty():
    assert _build_thebibliography_from_draft("没有参考文献章节的文本") == ""


def test_escape_latex_protects_math():
    out = _escape_latex("公式 $x_i$ 与 Raw_I_Q 文本")
    assert "$x_i$" in out          # 数学公式内部不转义
    assert r"Raw\_I\_Q" in out     # 普通文本下划线转义


def test_figure_placeholder_missing_fig_text_fallback():
    body = _md_to_latex_body("[图9: 不存在的图]")
    assert "图9" in body


if __name__ == "__main__":
    tests = [
        test_thebibliography_matches_body_citations,
        test_cite_range_expanded_and_sorted,
        test_table_row_starting_with_bracket_protected,
        test_table_normal_rows_untouched,
        test_build_thebibliography_from_draft_entries,
        test_build_thebibliography_from_draft_empty,
        test_escape_latex_protects_math,
        test_figure_placeholder_missing_fig_text_fallback,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
