"""格式校验与图表生成测试（离线测试）

运行:
    python tests/test_format_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.format_validator import (
    check_markdown_tables,
    check_figure_numbering,
    check_citation_format,
    format_check_report,
)
from src.rag.figure_generator import (
    parse_taxonomy_from_text,
    parse_timeline_from_text,
    generate_taxonomy_tree,
)


def test_markdown_table_ok():
    draft = """
| A | B |
|---|---|
| 1 | 2 |
"""
    r = check_markdown_tables(draft)
    assert r["tables_found"] == 1
    assert r["ok"] is True


def test_markdown_table_missing_separator():
    draft = """
| A | B |
| 1 | 2 |
"""
    r = check_markdown_tables(draft)
    assert r["ok"] is False
    assert any("分隔行" in i for i in r["issues"])


def test_markdown_table_col_mismatch():
    draft = """
| A | B |
|---|---|
| 1 | 2 | 3 |
"""
    r = check_markdown_tables(draft)
    assert r["ok"] is False


def test_figure_numbering_missing():
    draft = "如图2所示"  # 图1缺失
    r = check_figure_numbering(draft)
    assert r["ok"] is False
    assert any("图编号缺失" in i for i in r["issues"])


def test_citation_zero():
    draft = "见 [0]"
    r = check_citation_format(draft)
    assert r["ok"] is False


def test_format_report_ok():
    draft = """
# 测试

见 [1] [2]。

| 方法 | 精度 |
|------|------|
| A | 0.9 |

## 参考文献

[1] Paper A (2017)
[2] Paper B (2018)
"""
    r = format_check_report(draft)
    assert r["all_ok"] is True


def test_parse_taxonomy():
    tax = parse_taxonomy_from_text("### 深度学习方法\n- CNN\n- RNN\n### 传统方法\n- SVM")
    assert "深度学习方法" in tax
    assert tax["深度学习方法"] == ["CNN", "RNN"]


def test_parse_timeline():
    ms = parse_timeline_from_text("2017: Transformer\n2018: BERT\n2020: GPT-3")
    assert len(ms) == 3
    assert ms[0]["year"] == 2017
    assert ms[0]["event"] == "Transformer"


def test_generate_taxonomy_tree():
    path = generate_taxonomy_tree(
        "测试分类", {"大类A": ["A1", "A2"]}, filename="test_unit_taxonomy.png"
    )
    assert Path(path).exists()
    assert path.endswith(".png")


if __name__ == "__main__":
    tests = [
        test_markdown_table_ok,
        test_markdown_table_missing_separator,
        test_markdown_table_col_mismatch,
        test_figure_numbering_missing,
        test_citation_zero,
        test_format_report_ok,
        test_parse_taxonomy,
        test_parse_timeline,
        test_generate_taxonomy_tree,
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
