from __future__ import annotations

"""Z3 受限适配器 (P1)。

用于精确编码片段的逻辑与约束求解:
- check_implication: 检验 A => C 时检查 A ∧ ¬C。unsat 支持该编码下的蕴含;
  sat 给出候选反例 (需回映射原问题复核); unknown 保持未决。
- check_satisfiable: 检验约束本身是否可满足 (矛盾假设检测)。

编码边界 (量词、实数/整数域、有界范围) 写入 detail。
"""

import ast

from src.verification.schemas import VerificationResult, VerificationStatus

_MAX_LEN = 4000
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Compare, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.Eq, ast.NotEq, ast.Constant, ast.Name, ast.Load,
)


class _Unsupported(Exception):
    pass


def _parse(text: str) -> ast.Expression:
    if not isinstance(text, str) or not text.strip():
        raise _Unsupported("空表达式")
    if len(text) > _MAX_LEN:
        raise _Unsupported("表达式过长")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise _Unsupported(f"语法错误: {e}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise _Unsupported(f"不支持的节点: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise _Unsupported("只允许数值/布尔常量")
    return tree


def _version() -> str:
    try:
        import z3

        return z3.get_version_string()
    except Exception:  # noqa: BLE001
        return ""


def _import_z3():
    try:
        import z3

        return z3
    except ImportError:
        return None


def _result(status, detail="", raw="", counterexample=None) -> VerificationResult:
    return VerificationResult(
        tool="z3", tool_version=_version(), status=status, detail=detail, raw_output=raw,
        counterexample=counterexample or {},
    )


def _build_env(variables: dict[str, str]):
    z3 = _import_z3()
    if z3 is None:
        return None
    int_mode = any(str(v).lower() in ("int", "integer", "z") for v in variables.values())
    env = {}
    for name, sort in variables.items():
        env[name] = z3.Int(name) if str(sort).lower() in ("int", "integer", "z") else z3.Real(name)
    return env, int_mode


def _to_real(z3, expr):
    return z3.ToReal(expr) if z3.is_int(expr) else expr


def _eval(node, env, int_mode, z3):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env, int_mode, z3)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return z3.BoolVal(node.value)
        if isinstance(node.value, float):
            if int_mode:
                raise _Unsupported("整型域不支持浮点常量")
            return z3.RealVal(repr(node.value))
        return z3.IntVal(node.value) if int_mode else z3.RealVal(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise _Unsupported(f"未声明变量 {node.id}")
        return env[node.id]
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env, int_mode, z3) for v in node.values]
        return z3.And(*vals) if isinstance(node.op, ast.And) else z3.Or(*vals)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return z3.Not(_eval(node.operand, env, int_mode, z3))
        val = _eval(node.operand, env, int_mode, z3)
        return -val if isinstance(node.op, ast.USub) else val
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, env, int_mode, z3)
        right = _eval(node.right, env, int_mode, z3)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if int_mode:
                raise _Unsupported("整型域不支持除法 (请改用实数域)")
            return left / right
        raise _Unsupported(f"不支持运算符 {type(node.op).__name__}")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env, int_mode, z3)
        clauses = []
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, env, int_mode, z3)
            if int_mode:
                l, r = left, right
            else:
                l, r = _to_real(z3, left), _to_real(z3, right)
            if isinstance(op, ast.Lt):
                clauses.append(l < r)
            elif isinstance(op, ast.LtE):
                clauses.append(l <= r)
            elif isinstance(op, ast.Gt):
                clauses.append(l > r)
            elif isinstance(op, ast.GtE):
                clauses.append(l >= r)
            elif isinstance(op, ast.Eq):
                clauses.append(l == r)
            elif isinstance(op, ast.NotEq):
                clauses.append(l != r)
            else:
                raise _Unsupported(f"不支持比较符 {type(op).__name__}")
            left = right
        return clauses[0] if len(clauses) == 1 else z3.And(*clauses)
    raise _Unsupported(f"不支持的节点 {type(node).__name__}")


def _model_to_dict(model, env, z3) -> dict:
    out = {}
    for name, var in env.items():
        val = model.eval(var, model_completion=True)
        if z3.is_true(val) or z3.is_false(val):
            out[name] = z3.is_true(val)
        elif z3.is_int_value(val):
            out[name] = val.as_long()
        elif z3.is_rational_value(val):
            out[name] = val.numerator_as_long() / val.denominator_as_long()
        else:
            out[name] = str(val)
    return out


def op_check_implication(arguments: dict) -> VerificationResult:
    if _import_z3() is None:
        return _result(VerificationStatus.unavailable, "未安装 z3-solver")
    z3 = _import_z3()
    premises = arguments.get("premises", [])
    conclusion = arguments.get("conclusion", "")
    variables = arguments.get("variables", {})
    timeout_ms = int(float(arguments.get("timeout_ms", 10000)))
    built = _build_env(variables)
    if built is None:
        return _result(VerificationStatus.unavailable, "未安装 z3-solver")
    env, int_mode = built
    try:
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        for p in premises:
            solver.add(_eval(_parse(p), env, int_mode, z3))
        # 前提一致性检查: 防止靠不可能成立的假设"证明"任何结论
        consistency = z3.Solver()
        consistency.set("timeout", timeout_ms)
        for p in premises:
            consistency.add(_eval(_parse(p), env, int_mode, z3))
        if consistency.check() == z3.unsat:
            return _result(VerificationStatus.unknown, "premises_unsatisfiable",
                           "前提本身矛盾, 蕴含为空洞, 不作为有意义发现")
        goal = _eval(_parse(conclusion), env, int_mode, z3)
        solver.add(z3.Not(goal))
        r = solver.check()
        if r == z3.unsat:
            return _result(VerificationStatus.passed, "A ∧ ¬C 不可满足 => 蕴含成立",
                           f"logic={'Int' if int_mode else 'Real'}")
        if r == z3.sat:
            model = _model_to_dict(solver.model(), env, z3)
            return _result(VerificationStatus.failed, "存在反模型", "A ∧ ¬C 可满足", model)
        return _result(VerificationStatus.unknown, "求解器返回 unknown (超时/超出片段)")
    except _Unsupported as e:
        return _result(VerificationStatus.unsupported, str(e))
    except Exception as e:  # noqa: BLE001
        return _result(VerificationStatus.error, f"z3 执行异常: {e}")


def op_check_satisfiable(arguments: dict) -> VerificationResult:
    if _import_z3() is None:
        return _result(VerificationStatus.unavailable, "未安装 z3-solver")
    z3 = _import_z3()
    constraints = arguments.get("constraints", [])
    variables = arguments.get("variables", {})
    timeout_ms = int(float(arguments.get("timeout_ms", 10000)))
    built = _build_env(variables)
    if built is None:
        return _result(VerificationStatus.unavailable, "未安装 z3-solver")
    env, int_mode = built
    try:
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        for c in constraints:
            solver.add(_eval(_parse(c), env, int_mode, z3))
        r = solver.check()
        if r == z3.sat:
            model = _model_to_dict(solver.model(), env, z3)
            return _result(VerificationStatus.passed, "可满足", "sat", model)
        if r == z3.unsat:
            return _result(VerificationStatus.failed, "约束不可满足 (前提矛盾)")
        return _result(VerificationStatus.unknown, "求解器返回 unknown")
    except _Unsupported as e:
        return _result(VerificationStatus.unsupported, str(e))
    except Exception as e:  # noqa: BLE001
        return _result(VerificationStatus.error, f"z3 执行异常: {e}")


OPERATIONS = {
    "check_implication": op_check_implication,
    "check_satisfiable": op_check_satisfiable,
}


def run(operation: str, arguments: dict) -> VerificationResult:
    fn = OPERATIONS.get(operation)
    if fn is None:
        return _result(VerificationStatus.unsupported, f"z3 适配器不支持操作 {operation}")
    return fn(arguments)
