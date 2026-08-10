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


if __name__ == "__main__":
    tests = [
        test_extract_code_fenced,
        test_extract_code_no_fence,
        test_execute_code_ok,
        test_execute_code_error,
        test_sanitize_windows_path_assignment,
        test_sanitize_literal_path_to_output_path,
        test_sanitize_bare_unquoted_path,
        test_sanitize_keeps_normal_code,
        test_style_guide_contains_palette,
        test_apply_style_no_crash,
        test_parse_functions,
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
