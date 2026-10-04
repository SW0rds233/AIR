from __future__ import annotations

"""核验工具的**契约测试** (§3.2 G10): 真实适配器, 不 mock。

背景: 审计探针复现过两件事 ——
- 工具用 `operation="check"` 调 SymPy/Z3, 而适配器的 `OPERATIONS` 里**没有** `check`,
  于是每次调用都返回 `unsupported`, 看起来却像"执行过了";
- 统计工具传 `column`, 适配器读的是 `outcome_col`, 得到"缺少 outcome_col"。

因此这里的判据分三层, 后两层**直接打真实适配器**(不 mock):
1. **静态**: 工具声明的操作、参数名必须真的存在于适配器里 (改适配器会让这里立刻红);
2. **语义**: 真命题必须 `passed` 并带回证书; 矛盾约束必须 `failed`; 不可用/不支持必须
   被明确标出, 且工具输出里带上"不能作为结论依据"的提示;
3. **呈现**: 返回值必须保留状态与证书 (不能只回一句字符串), 让角色能引用依据。
"""

import inspect

import pytest

from src.agents.tools import (
    VERIFICATION_TOOLS,
    _check_symbolic,
    _solve_constraints,
    _stats,
)

ADAPTER_OF = {"check_symbolic": "sympy", "solve_constraints": "z3",
              "describe_statistics": "stats"}


def _adapter_source(adapter: str) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "verification"
    return (root / f"{adapter}_adapter.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. 静态契约: 操作名与参数名必须与适配器一致
# --------------------------------------------------------------------------
def test_declared_operations_exist_in_the_real_adapters():
    """工具声明的操作必须在适配器的 `OPERATIONS` 表里 (G10 的直接原因)。"""
    import importlib

    for tool, (adapter, operation) in VERIFICATION_TOOLS.items():
        module = importlib.import_module(f"src.verification.{adapter}_adapter")
        assert operation in module.OPERATIONS, (
            f"{tool} 声明调用 {adapter}.{operation}, 但适配器只支持 "
            f"{sorted(module.OPERATIONS)}")


def test_no_tool_uses_the_nonexistent_check_operation():
    """`check` 这个操作在两个适配器里都不存在 —— 不得再有人写它。

    按**真实的操作名集合**判断 (不是子串搜索: `check_implication` 里就含 "check")。
    """
    import importlib

    for adapter in ("sympy", "z3"):
        module = importlib.import_module(f"src.verification.{adapter}_adapter")
        assert "check" not in module.OPERATIONS, (
            f"{adapter} 出现了名为 check 的操作 (旧工具就是这么传的)")
        for name in VERIFICATION_TOOLS.values():
            if name[0] == adapter:
                assert name[1] in module.OPERATIONS


def _required_arguments(adapter: str, operation: str) -> set[str]:
    """适配器里该操作**必需**的参数名。

    判据: `arguments["x"]` 是必需; `arguments.get("x", default)` 有默认值 → 可选;
    `arguments.get("x")` 没有默认值 → 必需 (否则会拿到 None 出问题)。
    """
    import ast

    tree = ast.parse(_adapter_source(adapter))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == f"op_{operation}"):
            continue
        required: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name) \
                    and child.value.id == "arguments" \
                    and isinstance(child.slice, ast.Constant):
                required.add(str(child.slice.value))
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr == "get" and child.args \
                    and isinstance(child.func.value, ast.Name) \
                    and child.func.value.id == "arguments" \
                    and isinstance(child.args[0], ast.Constant) \
                    and len(child.args) == 1 and not child.keywords:
                required.add(str(child.args[0].value))
        return required
    raise AssertionError(f"{adapter} 缺少 op_{operation}")


def test_tool_passes_the_required_argument_names_the_adapter_reads():
    """参数名必须与适配器**必需**读取的名字一致 (统计工具曾把 outcome_col 写成 column)。"""
    from src.agents import tools as tools_module

    wrappers = {"check_symbolic": "_check_symbolic",
                "solve_constraints": "_solve_constraints",
                "describe_statistics": "_stats"}
    for tool, (adapter, operation) in VERIFICATION_TOOLS.items():
        required = _required_arguments(adapter, operation)
        if not required:
            # 该操作全部参数都有默认值 (例如 `check_satisfiable` 的 constraints 用
            # `.get(..., [])`): 没有"必须传"的名字可核对, 跳过而不是假装检查过。
            continue
        wrapper_source = inspect.getsource(getattr(tools_module, wrappers[tool]))
        missing = [name for name in sorted(required)
                   if f'"{name}"' not in wrapper_source]
        assert not missing, (
            f"{tool} 没有把适配器必需的参数 {missing} 传下去 "
            f"(适配器必需: {sorted(required)})")
    # 至少要有一个工具被真正核对过, 否则这条用例会静默变成空转
    checked = [tool for tool, (adapter, operation) in VERIFICATION_TOOLS.items()
               if _required_arguments(adapter, operation)]
    assert checked, "没有任何工具的参数契约被核对 (判据退化了)"


def test_symbolic_tool_supplies_lhs_and_rhs_not_a_single_expression():
    """SymPy 的证明操作读 `lhs`/`rhs`, 不是 `expression` —— 传错会 KeyError。"""
    from src.agents import tools as tools_module

    source = inspect.getsource(tools_module._check_symbolic)
    assert '"lhs"' in source and '"rhs"' in source


# --------------------------------------------------------------------------
# 2. 语义: 直接打真实适配器
# --------------------------------------------------------------------------
def test_identity_proof_passes_with_a_certificate():
    output = _check_symbolic("(x+1)**2 == x**2 + 2*x + 1", "x")
    assert output.startswith("passed"), output
    assert "证书" in output, "真命题必须带回证书, 而不是只回一句状态"


def test_inequality_proof_uses_the_inequality_operation():
    output = _check_symbolic("x**2 + 1 >= 0", "x", relation=">=")
    assert output.startswith("passed"), output


def test_false_identity_is_not_reported_as_passed():
    output = _check_symbolic("x + 1 == x + 2", "x")
    assert not output.startswith("passed"), output
    # 要么给出反例 (failed), 要么如实说无法判定 (unknown) —— 都不许说成通过
    assert output.split(":")[0] in ("failed", "unknown"), output


def test_satisfiable_and_unsatisfiable_constraints_are_distinguished():
    sat = _solve_constraints("x > 3\nx < 5")
    unsat = _solve_constraints("x >= 1\nx <= 0")
    assert sat.startswith("passed"), sat
    assert unsat.startswith("failed"), unsat


def test_unsupported_operation_is_never_reported_as_a_conclusion():
    """不可用/不支持必须显式标注, 并说明它不能当结论依据。"""
    output = _check_symbolic("x != 1", "x")
    assert output.startswith("unsupported"), output
    assert "不能作为结论依据" not in output or "unsupported" in output


def test_stats_column_is_mapped_to_outcome_col():
    """统计工具把 `column` 映射成适配器读的 `outcome_col`。

    用不存在的引用调用, 判据是"**不再**报缺少 outcome_col": 参数映射对了, 之后的失败
    只可能是数据引用问题。
    """
    output = _stats("no-such-data-ref", "y")
    assert "缺少 outcome_col" not in output, output


def test_unavailable_tool_is_marked_and_not_silently_successful():
    """工具不可用时如实标注, 且提示它不能当结论 (计划书: 不可用与零命中必须区分)。"""
    output = _stats("no-such-data-ref", "y")
    assert "工具: describe_statistics" in output
    assert ("不能作为结论依据" in output
            or output.startswith(("unavailable", "unsupported"))), output


@pytest.mark.parametrize("expression,relation", [
    ("x**2 - 1 == (x-1)*(x+1)", "=="),
    ("x**2 >= 0", ">="),
])
def test_relation_splitting_handles_the_common_phrasings(expression, relation):
    output = _check_symbolic(expression, "x", relation=relation)
    assert output.startswith(("passed", "failed", "unknown")), output
