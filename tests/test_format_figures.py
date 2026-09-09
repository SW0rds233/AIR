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


def test_classify_ref_category():
    from src.rag.figure_generator import _classify_ref_category

    assert _classify_ref_category("Security of IoT via adversarial machine learning") == "安全与对抗"
    assert _classify_ref_category("Channel-robust RF fingerprinting") == "信道鲁棒与泛化"
    assert _classify_ref_category("Differential constellation trace figure") == "特征工程与信号处理"
    assert _classify_ref_category("A Novel Dataset for RF Fingerprinting") == "数据集与实测"
    assert _classify_ref_category("ResNet based deep learning for SEI") == "模型架构与深度学习"


def test_generate_rf_pipeline():
    from src.rag.figure_generator import generate_rf_pipeline

    path = generate_rf_pipeline("测试识别流程", filename="test_pipeline.png")
    assert Path(path).exists()
    assert path.endswith(".png")


def test_generate_framework_overview():
    from src.rag.figure_generator import generate_framework_overview

    path = generate_framework_overview("测试研究框架", filename="test_framework.png")
    assert Path(path).exists()
    assert path.endswith(".png")


def test_generate_topic_year_heatmap():
    from src.rag.figure_generator import _generate_topic_year_heatmap

    refs = [
        {"title": "Robust RF Fingerprinting with CNN", "year": "2019"},
        {"title": "Adversarial attacks on RF authentication", "year": "2021"},
        {"title": "Fractal feature for emitter identification", "year": "2018"},
    ]
    path = _generate_topic_year_heatmap("测试", refs, 0)
    assert path and Path(path).exists()
    assert "heatmap" in path


def test_validate_comparison_data_ok():
    from src.rag.figure_generator import _validate_comparison_data

    ok, _ = _validate_comparison_data(
        ["A", "B", "C"], {"精度": [0.9, 0.8, 0.7], "鲁棒性": [0.5, 0.6, 0.4], "开销": [0.3, 0.5, 0.8]}
    )
    assert ok is True


def test_validate_comparison_data_reject_few_methods():
    from src.rag.figure_generator import _validate_comparison_data

    ok, reason = _validate_comparison_data(["A"], {"精度": [0.9], "鲁棒性": [0.5]})
    assert ok is False and "方法数" in reason


def test_validate_comparison_data_reject_few_metrics():
    from src.rag.figure_generator import _validate_comparison_data

    ok, reason = _validate_comparison_data(["A", "B"], {"精度": [0.9, 0.8]})
    assert ok is False and "指标数" in reason


def test_validate_comparison_data_reject_out_of_range():
    """数值超出 [0,1] 视为编造/异常数据, 拒绝渲染"""
    from src.rag.figure_generator import _validate_comparison_data

    ok, reason = _validate_comparison_data(
        ["A", "B"], {"精度": [0.9, 1.5], "鲁棒性": [0.5, 0.6], "开销": [0.1, 0.2]}
    )
    assert ok is False and "超出" in reason


def test_validate_comparison_data_reject_len_mismatch():
    from src.rag.figure_generator import _validate_comparison_data

    ok, reason = _validate_comparison_data(
        ["A", "B"], {"精度": [0.9, 0.8], "鲁棒性": [0.5], "开销": [0.1, 0.2]}
    )
    assert ok is False and "不一致" in reason


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
        test_classify_ref_category,
        test_generate_rf_pipeline,
        test_generate_framework_overview,
        test_generate_topic_year_heatmap,
        test_validate_comparison_data_ok,
        test_validate_comparison_data_reject_few_methods,
        test_validate_comparison_data_reject_few_metrics,
        test_validate_comparison_data_reject_out_of_range,
        test_validate_comparison_data_reject_len_mismatch,
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
