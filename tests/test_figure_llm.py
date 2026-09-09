"""LLM 图表生成器测试（沙箱执行 + 风格指南，离线测试）

运行:
    python tests/test_figure_llm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.figure_llm import _extract_code, _execute_code, _sanitize_code
from src.rag.figure_style import apply_style, style_guide_prompt, PALETTE
from src.rag.figure_generator import parse_taxonomy_from_text, parse_timeline_from_text


def test_sanitize_windows_path_assignment():
    """LLM 把 Windows 路径当变量赋值 → 整行删除"""
    save = r"E:\SearchAgents\AIR\outputs\figures\taxonomy_llm_0.png"
    code = (
        "import matplotlib.pyplot as plt\n"
        f'{save} = r"{save}"\n'
        "plt.savefig(OUTPUT_PATH)\n"
    )
    cleaned = _sanitize_code(code, save)
    assert "E:" not in cleaned
    assert "plt.savefig(OUTPUT_PATH)" in cleaned


def test_sanitize_literal_path_to_output_path():
    """代码里出现保存路径字符串 → 统一替换为 OUTPUT_PATH"""
    save = r"E:\SearchAgents\AIR\outputs\figures\foo.png"
    code = 'plt.savefig(r"outputs/figures/foo.png", dpi=300)'
    cleaned = _sanitize_code(code, save)
    assert "OUTPUT_PATH" in cleaned
    assert "foo.png" not in cleaned


def test_sanitize_bare_unquoted_path():
    """LLM 把路径写成不带引号的裸表达式 → 替换为 OUTPUT_PATH"""
    save = "outputs/figures/_live_timeline_test.png"
    code = 'plt.savefig(outputs/figures/_live_timeline_test.png, dpi=300, bbox_inches="tight")'
    cleaned = _sanitize_code(code, save)
    assert "plt.savefig(OUTPUT_PATH" in cleaned
    assert "_live_timeline_test" not in cleaned


def test_sanitize_keeps_normal_code():
    code = "import numpy as np\nx = np.array([1,2,3])\nplt.plot(x)"
    assert _sanitize_code(code, "outputs/figures/x.png") == code


def test_extract_code_fenced():
    out = "```python\nimport matplotlib.pyplot as plt\nprint('hi')\n```"
    code = _extract_code(out)
    assert "import matplotlib" in code
    assert "```" not in code


def test_extract_code_no_fence():
    out = "import matplotlib.pyplot as plt\nplt.plot([1,2])"
    code = _extract_code(out)
    assert "plt.plot" in code


def test_extract_code_trailing_space_after_fence():
    """围栏 ```python 后带尾随空格: 旧正则 ```python\\n 匹配不到 → 返回含围栏文本
    → exec 报 SyntaxError (实测 File '<string>', line 41)"""
    out = "```python  \nimport matplotlib.pyplot as plt\nplt.plot([1,2])\n```"
    code = _extract_code(out)
    assert "plt.plot" in code
    assert "```" not in code


def test_extract_code_crlf():
    """CRLF 换行 (```python\\r\\n) 也必须能提取"""
    out = "```python\r\nimport numpy as np\r\nx = np.arange(3)\r\n```"
    code = _extract_code(out)
    assert "np.arange" in code
    assert "```" not in code


def test_extract_code_no_language_tag():
    """无语言标记的围栏 ``` 也能提取"""
    out = "```\nimport matplotlib.pyplot as plt\nplt.show()\n```"
    code = _extract_code(out)
    assert "plt.show" in code
    assert "```" not in code


def test_extract_code_unclosed_fence():
    """闭合围栏缺失时取到文本末尾, 不应返回含围栏的原文"""
    out = "```python\nimport matplotlib.pyplot as plt\nplt.plot([1,2])"
    code = _extract_code(out)
    assert "plt.plot" in code
    assert "```" not in code


def test_extract_code_with_preamble():
    """围栏前有解释文字 (第 N 行才是围栏) 也能定位"""
    out = "以下是代码：\n\n```python\nimport matplotlib.pyplot as plt\nplt.plot([1,2])\n```"
    code = _extract_code(out)
    assert "plt.plot" in code
    assert "```" not in code


def test_execute_code_ok():
    code = "import matplotlib.pyplot as plt\nimport numpy as np\nfig, ax = plt.subplots()\nax.plot([1,2,3])\n"
    ok, out = _execute_code(code, str(Path(".") / "test_output.png"))
    assert ok is True


def test_execute_code_error():
    code = "import matplotlib.pyplot as plt\nraise ValueError('boom')\n"
    ok, out = _execute_code(code, str(Path(".") / "test_output.png"))
    assert ok is False
    assert "boom" in out


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


def test_classify_exec_error_marker():
    from src.rag.figure_llm import _classify_exec_error

    hint = _classify_exec_error("ValueError: Unrecognized marker style '•'")
    assert "标准标记" in hint


def test_classify_exec_error_underscore():
    from src.rag.figure_llm import _classify_exec_error

    assert "转义" in _classify_exec_error("! Missing $ inserted")


def test_classify_exec_error_subscript():
    from src.rag.figure_llm import _classify_exec_error

    assert "dict" in _classify_exec_error("TypeError: 'int' object is not subscriptable")


def test_classify_exec_error_timeout():
    from src.rag.figure_llm import _classify_exec_error

    assert "超时" in _classify_exec_error("code timed out after 60s")


def test_classify_exec_error_unknown_empty():
    from src.rag.figure_llm import _classify_exec_error

    assert _classify_exec_error("random unknown failure") == ""


def test_check_png_quality_low_fill_rejected():
    """内容占比过低 (仅标题/空坐标轴) 应被拒绝"""
    from PIL import Image

    from src.rag.figure_llm import check_png_quality

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

    from src.rag.figure_llm import check_png_quality

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


def test_vision_issues_filters_nitpicks():
    """视觉审阅只认 Critical 硬伤, 过滤'字号略小/配色可优化'等吹毛求疵
    (实测 kimi 每轮报满 5 个此类问题, 修正循环空转耗光预算)"""
    from src.rag.figure_llm import _extract_vision_issues

    report = "问题列表:\n- 字号略小，可优化\n- 配色不够美观\n- 图例位置可微调"
    assert _extract_vision_issues(report) == []


def test_vision_issues_keeps_critical():
    from src.rag.figure_llm import _extract_vision_issues

    report = "问题列表:\n- 文字被截断、超出图框\n- 图例遮挡了数据点\n- 整张图空白"
    out = _extract_vision_issues(report)
    assert len(out) == 3
    assert any("截断" in i for i in out)
    assert any("遮挡" in i for i in out)
    assert any("空白" in i for i in out)


if __name__ == "__main__":
    tests = [
        test_extract_code_fenced,
        test_extract_code_no_fence,
        test_extract_code_trailing_space_after_fence,
        test_extract_code_crlf,
        test_extract_code_no_language_tag,
        test_extract_code_unclosed_fence,
        test_extract_code_with_preamble,
        test_execute_code_ok,
        test_execute_code_error,
        test_sanitize_windows_path_assignment,
        test_sanitize_literal_path_to_output_path,
        test_sanitize_bare_unquoted_path,
        test_sanitize_keeps_normal_code,
        test_style_guide_contains_palette,
        test_apply_style_no_crash,
        test_parse_functions,
        test_classify_exec_error_marker,
        test_classify_exec_error_underscore,
        test_classify_exec_error_subscript,
        test_classify_exec_error_timeout,
        test_classify_exec_error_unknown_empty,
        test_check_png_quality_low_fill_rejected,
        test_check_png_quality_few_colors_rejected,
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
