"""图表质量检测与审阅文本解析测试（离线测试）

运行:
    python tests/test_figure_llm.py

G18 变更说明 (合并计划 §3.3 G18): 本文件原来还有一整套"LLM 写 matplotlib 脚本 →
`_execute_code` 子进程执行 → 代码级/视觉审阅"的用例 (`_sanitize_code` /
`_extract_code` / `_execute_code` / `_classify_exec_error` 等)。那条自由脚本路径
已被删除 —— 它接受模型产出的任意 Python 并真的执行, 而进程隔离不等于权限隔离;
绘图现在只走声明式 `FigureSpec` (`src.agents.figures.render_figure_spec`), 公式走
白名单解析 (`src.rag.figure_formula`)。因此**因功能退场**删掉对应用例, 同时新增
一条"自由脚本入口必须不存在"的结构用例 (见
`tests/test_figure_artifact_service.py::test_free_script_execution_path_is_gone`),
并保留与执行无关的能力用例 (PNG 质量检测、审阅文本解析、风格指南)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.figure_generator import parse_taxonomy_from_text, parse_timeline_from_text
from src.rag.figure_llm import (
    _extract_review_issues,
    _extract_vision_issues,
    check_png_quality,
)
from src.rag.figure_style import PALETTE, apply_style, style_guide_prompt


def test_style_guide_contains_palette():
    guide = style_guide_prompt()
    assert "配色" in guide
    assert PALETTE[0] in guide  # 色板包含在指南中
    assert "DPI" in guide


def test_apply_style_no_crash():
    apply_style(style="journal")
    apply_style(style="presentation")


def test_parse_functions():
    tax = parse_taxonomy_from_text("### 方法A\n- 子类1\n- 子类2")
    assert tax == {"方法A": ["子类1", "子类2"]}
    tl = parse_timeline_from_text("2017: 事件X\n2020: 事件Y")
    assert len(tl) == 2


def test_check_png_quality_low_fill_rejected():
    """内容占比过低 (仅标题/空坐标轴) 应被拒绝"""
    from PIL import Image

    p = Path(__file__).resolve().parent / "_sparse_test.png"
    img = Image.new("RGB", (800, 600), (255, 255, 255))
    # 只在中间画一小块内容 (< 2%)
    for x in range(390, 410):
        for y in range(295, 305):
            img.putpixel((x, y), (0, 0, 0))
    img.save(p)
    try:
        ok, reason = check_png_quality(str(p))
        assert ok is False
        assert "空白" in reason or "占比" in reason
    finally:
        if p.exists():
            p.unlink()


def test_check_png_quality_few_colors_rejected():
    """唯一颜色数过少 (近纯色塌缩) 应被拒绝; 用高分辨率双色噪点图规避 PNG 高压缩"""
    import numpy as np
    from PIL import Image

    p = Path(__file__).resolve().parent / "_fewcolor_test.png"
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 2, (300, 400))
    arr = np.repeat(np.repeat(arr, 4, axis=0), 4, axis=1)  # 1600x1200
    rgb = np.where(arr[..., None] == 0, (40, 40, 40), (200, 200, 200)).astype(np.uint8)
    Image.fromarray(rgb).save(p)
    try:
        ok, reason = check_png_quality(str(p))
        assert ok is False
        assert "颜色" in reason
    finally:
        if p.exists():
            p.unlink()


def test_extract_review_issues_skips_template_echo():
    text = "问题列表:\n- 缺少 x 轴标签\n- 图例过多\n（若全部合规则输出: 问题列表:\n- 无）"
    issues = _extract_review_issues(text)
    assert "缺少 x 轴标签" in issues
    assert "图例过多" in issues
    assert "若全部合规则" not in issues  # 模板回显不作为问题
    assert _extract_review_issues("问题列表:\n- 无") == []


def test_vision_issues_filters_nitpicks():
    """视觉审阅只认 Critical 硬伤, 过滤'字号略小/配色可优化'等吹毛求疵
    (实测 kimi 每轮报满 5 个此类问题, 修正循环空转耗光预算)"""
    report = "问题列表:\n- 字号略小，可优化\n- 配色不够美观\n- 图例位置可微调"
    assert _extract_vision_issues(report) == []


def test_vision_issues_keeps_critical():
    report = "问题列表:\n- 文字被截断、超出图框\n- 图例遮挡了数据点\n- 整张图空白"
    out = _extract_vision_issues(report)
    assert len(out) == 3
    assert any("截断" in i for i in out)
    assert any("遮挡" in i for i in out)
    assert any("空白" in i for i in out)


if __name__ == "__main__":
    tests = [
        test_style_guide_contains_palette,
        test_apply_style_no_crash,
        test_parse_functions,
        test_check_png_quality_low_fill_rejected,
        test_check_png_quality_few_colors_rejected,
        test_extract_review_issues_skips_template_echo,
        test_vision_issues_filters_nitpicks,
        test_vision_issues_keeps_critical,
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
