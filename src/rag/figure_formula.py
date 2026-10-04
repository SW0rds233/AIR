from __future__ import annotations

"""图表公式的**白名单解析 + 受限执行** (合并计划 §3.3 G18)。

修复前 `src/agents/figures.py::_plot_equation` 把方程直接交给
`sympy.parsing.sympy_parser.parse_expr`, 再交给 `lambdify` 求值。`parse_expr`
内部执行 `eval`, 输入里可以夹带任意 Python; 而方程来自模型输出与 `FigureSpec`
—— 属于**不可信材料**。于是"画一条函数曲线"实际上是一条没有权限边界的代码执行
路径 (合并计划 §3.3 G18: "公式直接 parse_expr, 不能视为安全解释器")。

这里把"能算什么"变成显式白名单:

- **语法**: 只允许数字常量、变量名、`+ - * / % **`、一元正负号与白名单函数调用;
  属性访问 (`os.system`)、下标、lambda、推导式、字符串等一律拒绝;
- **名称**: 变量取自调用方声明的定义域 (图里就是模型域), 常量只有 `pi/e/tau`,
  函数只有下面这张表;
- **执行**: 不执行 Python 源码, 而是遍历**已校验的 AST** 自己求值 (无 `eval`,
  无内建函数, 指数有上限), 越界/非法运算如实抛错或返回 `nan`。

因此 `__import__('os').system('echo x')` 在**解析阶段**就被拒绝 (函数名不在白名单;
属性访问也不允许), 而不是"执行之后才发现不该执行"。
"""

import ast
import math
import operator as _operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALLOWED_CONSTANTS",
    "ALLOWED_FUNCTIONS",
    "DEFAULT_FORMULA_VARIABLES",
    "Formula",
    "FormulaRejected",
    "parse_formula",
]


class FormulaRejected(ValueError):
    """表达式含白名单之外的语法/名称 (显式拒绝, 不静默忽略)。"""


#: 曲线图默认允许的自由变量 (模型未声明自己的符号时使用)。
DEFAULT_FORMULA_VARIABLES: tuple[str, ...] = ("x", "y", "t")


def _sign(value: float) -> float:
    return (value > 0) - (value < 0)


#: 允许调用的数学函数 (**唯一**的白名单; 不在表内一律拒绝)。
ALLOWED_FUNCTIONS: dict[str, Any] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "sign": _sign,
    "min": min,
    "max": max,
    "pow": pow,
}

#: 允许的数学常量。
ALLOWED_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_BINARY_OPS: dict[type, Any] = {
    ast.Add: _operator.add,
    ast.Sub: _operator.sub,
    ast.Mult: _operator.mul,
    ast.Div: _operator.truediv,
    ast.Mod: _operator.mod,
    ast.Pow: _operator.pow,
}

#: 表达式长度上限 (防"用一个超长表达式拖死解析")。
MAX_FORMULA_CHARS = 200
#: 指数上限: `9**9**9` 这类幂在求值时会卡死/吃光内存, 必须在解析与求值两处拦住。
MAX_EXPONENT = 64.0


@dataclass(frozen=True)
class Formula:
    """一条**已通过白名单校验**的公式。"""

    text: str                      # 原始文本
    expression: str                # 归一化后的表达式 (^ 已转 **)
    variables: tuple[str, ...]     # 实际用到的自由变量 (已排序, 去重)
    tree: ast.Expression

    def evaluate(self, values: Mapping[str, float]) -> float:
        """在给定变量取值下求值 (受限执行, 不碰 Python 源码)。"""
        return _evaluate(self.tree.body, values)

    def series(self, variable: str = "x", *, low: float = -10.0, high: float = 10.0,
               points: int = 400) -> tuple[list[float], list[float]]:
        """按等距网格求一条曲线; 单点求值失败记 `nan` (绘图方据此留空, 不静默画错)。"""
        n = max(2, int(points))
        span = float(high) - float(low)
        xs = [float(low) + span * i / (n - 1) for i in range(n)]
        ys: list[float] = []
        for x in xs:
            try:
                value = self.evaluate({str(variable): x})
            except FormulaRejected:
                value = math.nan
            ys.append(value if isinstance(value, float) and math.isfinite(value)
                      else math.nan)
        return xs, ys


def parse_formula(text: str,
                  variables: Sequence[str] | None = DEFAULT_FORMULA_VARIABLES
                  ) -> Formula:
    """白名单解析: 通过返回 `Formula`, 否则抛 `FormulaRejected`。

    `variables` 是**允许出现的变量名** (图里取模型域: 模型声明的符号 + 默认 x/y/t)。
    传 `None` 表示"任意非下划线开头的标识符都当作变量", 由调用方在拿到
    `Formula.variables` 之后再判是否越出模型域 (这样"符号不在模型域内"与"语法/函数
    不在白名单"能给出**两种不同**的诊断)。
    """
    raw = str(text or "").strip()
    if not raw:
        raise FormulaRejected("空表达式")
    if len(raw) > MAX_FORMULA_CHARS:
        raise FormulaRejected(f"表达式过长 (>{MAX_FORMULA_CHARS} 字符)")
    body = _strip_assignment(raw).replace("^", "**")
    try:
        tree = ast.parse(body, mode="eval")
    except SyntaxError as e:
        raise FormulaRejected(f"无法解析为数学表达式: {e.msg}") from e
    declared: set[str] | None = None
    if variables is not None:
        declared = {str(name) for name in variables if str(name).strip()}
        if not declared:
            raise FormulaRejected("未声明任何可用变量 (无法校验定义域)")
    used = _validate(tree, declared)
    return Formula(text=raw, expression=body, variables=tuple(sorted(used)), tree=tree)


def _strip_assignment(text: str) -> str:
    """`y = x^2` → `x^2` (只接受左侧是单个变量名的等式)。"""
    if "=" not in text:
        return text
    parts = text.split("=")
    if len(parts) != 2:
        raise FormulaRejected("只支持一个等式 (`y = f(x)`)")
    left, right = parts[0].strip(), parts[1].strip()
    if not left.isidentifier():
        raise FormulaRejected("等号左侧必须是单个变量名")
    if not right:
        raise FormulaRejected("等号右侧为空")
    return right


def _validate(tree: ast.Expression, declared: set[str] | None) -> set[str]:
    """遍历 AST 做白名单检查; 返回用到的变量名集合。

    `declared is None` 时任何(非下划线开头的)标识符都算变量, 由调用方再判域。
    """
    used: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            visit(node.body)
            return
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaRejected(f"只允许数字常量, 不允许 {node.value!r}")
            return
        if isinstance(node, ast.Name):
            if declared is not None and node.id in declared:
                used.add(node.id)
                return
            if node.id in ALLOWED_CONSTANTS:
                return
            if node.id in ALLOWED_FUNCTIONS:
                raise FormulaRejected(f"{node.id!r} 是函数, 必须写成 {node.id}(...)")
            if declared is None and not node.id.startswith("_"):
                used.add(node.id)
                return
            names = ", ".join(sorted(declared)) if declared is not None else "(任意标识符)"
            raise FormulaRejected(
                f"不允许的名称 {node.id!r} (变量: {names};"
                f" 常量: {', '.join(sorted(ALLOWED_CONSTANTS))})")
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BINARY_OPS:
                raise FormulaRejected(f"不允许的运算符 {type(node.op).__name__}")
            _check_power(node)
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise FormulaRejected(f"不允许的一元运算符 {type(node.op).__name__}")
            visit(node.operand)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                if isinstance(node.func, ast.Attribute):
                    raise FormulaRejected(
                        "不允许属性访问/方法调用 (形如 `a.b(...)`); 只允许白名单函数:"
                        f" {', '.join(sorted(ALLOWED_FUNCTIONS))}")
                name = node.func.id if isinstance(node.func, ast.Name) \
                    else type(node.func).__name__
                raise FormulaRejected(
                    f"不允许的函数调用 {name!r}"
                    f" (白名单: {', '.join(sorted(ALLOWED_FUNCTIONS))})")
            if node.keywords:
                raise FormulaRejected("函数调用不允许关键字参数")
            for arg in node.args:
                visit(arg)
            return
        raise FormulaRejected(f"不允许的语法 {type(node).__name__}")

    visit(tree)
    return used


def _check_power(node: ast.BinOp) -> None:
    if not isinstance(node.op, ast.Pow):
        return
    exponent = node.right
    if isinstance(exponent, ast.Constant) and isinstance(exponent.value, (int, float)) \
            and not isinstance(exponent.value, bool) \
            and abs(float(exponent.value)) > MAX_EXPONENT:
        raise FormulaRejected(f"指数 {exponent.value} 超过上限 {MAX_EXPONENT:g}")


def _evaluate(node: ast.AST, values: Mapping[str, float]) -> float:
    """受限求值: 只处理白名单校验过的节点类型。"""
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in values:
            return float(values[node.id])
        if node.id in ALLOWED_CONSTANTS:
            return float(ALLOWED_CONSTANTS[node.id])
        raise FormulaRejected(f"变量 {node.id!r} 未提供取值")
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, values)
        right = _evaluate(node.right, values)
        op = _BINARY_OPS[type(node.op)]
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise FormulaRejected(f"指数 {right:g} 超过上限 {MAX_EXPONENT:g}")
        try:
            return float(op(left, right))
        except ZeroDivisionError as e:
            raise FormulaRejected("除以零") from e
        except (TypeError, ValueError, OverflowError) as e:
            # 例如负数的分数次幂得到复数: 该点无实数值, 如实报为该点不可求值。
            raise FormulaRejected(f"该点无实数值: {e}") from e
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, values)
        return float(operand if isinstance(node.op, ast.UAdd) else -operand)
    if isinstance(node, ast.Call):
        args = [_evaluate(arg, values) for arg in node.args]
        function = ALLOWED_FUNCTIONS[node.func.id]
        try:
            return float(function(*args))
        except (TypeError, ValueError, OverflowError) as e:
            raise FormulaRejected(f"{node.func.id}(...) 无法求值: {e}") from e
    raise FormulaRejected(f"不允许的语法 {type(node).__name__}")
