from __future__ import annotations

"""SymPy 受限适配器 (P1)。

安全边界:
- 只接受受白名单约束的数学表达式字符串 (AST 校验, 禁止属性访问/调用任意函数)。
- 限制表达式长度与嵌套深度。
- SymPy 假设查询存在 True/False/None 三态; None 一律返回 unknown, 不得当成通过。

覆盖: 恒等变换、非负性/不等式、等式条件 (含严格不等式反例)。
"""

import ast
import re

from src.verification.schemas import VerificationResult, VerificationStatus

MAX_LEN = 2000
MAX_DEPTH = 50
MAX_VECTOR_DIM = 4096

_FUNCS = {
    "sqrt", "exp", "log", "Abs", "sin", "cos", "tan", "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "Max", "Min", "Rational", "sign", "floor", "ceiling",
    "factorial", "binomial", "dot",
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.UAdd, ast.USub,
    ast.Load, ast.Tuple,
)

_COMPLEX_DOMAINS = {"complex", "complexes", "c"}


class _UnsafeExpression(ValueError):
    pass


def _validate(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise _UnsafeExpression("空表达式")
    if len(text) > MAX_LEN:
        raise _UnsafeExpression(f"表达式过长 (>{MAX_LEN})")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise _UnsafeExpression(f"表达式语法错误: {e}") from e
    depth = 0

    def _walk(node, d=0):
        nonlocal depth
        depth = max(depth, d)
        if not isinstance(node, _ALLOWED_NODES):
            raise _UnsafeExpression(f"不允许的表达式节点: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise _UnsafeExpression("只允许调用白名单数学函数")
            if node.keywords:
                raise _UnsafeExpression("不允许关键字参数")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise _UnsafeExpression("只允许数值常量")
        for child in ast.iter_child_nodes(node):
            _walk(child, d + 1)

    _walk(tree)
    if depth > MAX_DEPTH:
        raise _UnsafeExpression(f"表达式嵌套过深 (>{MAX_DEPTH})")


def _to_symbols(variables, assumptions):
    import sympy

    assumption_map = assumptions or {}
    symbols = {}
    for name in variables or []:
        dom = str(assumption_map.get(name, "real")).lower()
        kw = {}
        if dom in ("real", "reals", "r"):
            kw = {"real": True}
        elif dom in ("integer", "int", "integers", "z"):
            kw = {"integer": True}
        elif dom in ("rational", "rationals", "q"):
            kw = {"rational": True}
        elif dom in ("positive", "pos", "positive_real", "positivereal", "r+", "r_+"):
            kw = {"positive": True}
        elif dom in ("nonnegative", "nonneg", "nonnegative_real", "nonneg_real", "r>=0"):
            kw = {"nonnegative": True}
        elif dom in ("complex", "complexes", "c"):
            kw = {"complex": True}
        elif dom in ("nonzero", "non_zero", "nonzero_real"):
            kw = {"real": True, "nonzero": True}
        else:
            kw = {"real": True}
        symbols[name] = sympy.Symbol(name, **kw)
    return symbols, assumption_map


def _complex_domain(assumptions) -> str | None:
    for name, dom in (assumptions or {}).items():
        if str(dom).lower() in _COMPLEX_DOMAINS:
            return name
    return None


def _parse(text: str, symbols: dict):
    import sympy

    _validate(text)
    local = dict(symbols)
    for fn in _FUNCS:
        if hasattr(sympy, fn):
            local[fn] = getattr(sympy, fn)
    local["dot"] = _finite_dot
    try:
        return sympy.sympify(text, locals=local, evaluate=True)
    except Exception as e:
        raise _UnsafeExpression(f"表达式解析失败: {e}") from e


def _finite_dot(left, right):
    """只核验给出全部坐标的有限向量，不把抽象向量当作数值证书。"""
    import sympy

    if not isinstance(left, (tuple, sympy.Tuple)) or not isinstance(right, (tuple, sympy.Tuple)):
        raise _UnsafeExpression("dot 需要两条已给出坐标的有限向量")
    if not left or len(left) != len(right) or len(left) > MAX_VECTOR_DIM:
        raise _UnsafeExpression("dot 向量维数必须相同且处于限制内")
    if not all(getattr(value, "is_number", False) and value.is_finite is True
               for value in (*left, *right)):
        raise _UnsafeExpression("dot 的坐标必须是有限的精确数值")
    return sympy.Add(*(a * b for a, b in zip(left, right)))


def _ok(op: str, certificate: str = "", raw: str = "", counterexample: dict | None = None,
        detail: str = "") -> VerificationResult:
    return VerificationResult(
        tool="sympy", tool_version=_version(), status=VerificationStatus.passed,
        certificate=certificate, raw_output=raw, counterexample=counterexample or {}, detail=detail,
    )


def _fail(op: str, counterexample: dict | None = None, raw: str = "", detail: str = "") -> VerificationResult:
    return VerificationResult(
        tool="sympy", tool_version=_version(), status=VerificationStatus.failed,
        counterexample=counterexample or {}, raw_output=raw, detail=detail,
    )


def _unknown(detail: str, raw: str = "") -> VerificationResult:
    return VerificationResult(
        tool="sympy", tool_version=_version(), status=VerificationStatus.unknown,
        detail=detail, raw_output=raw,
    )


def _unsupported(detail: str) -> VerificationResult:
    return VerificationResult(
        tool="sympy", tool_version=_version(), status=VerificationStatus.unsupported, detail=detail,
    )


def _version() -> str:
    try:
        import sympy

        return sympy.__version__
    except Exception:  # noqa: BLE001
        return ""


def _structural_nonnegative(expr) -> bool:
    """结构判定: 平方/偶次幂/绝对值/非负项之和/非负常数。"""
    import sympy

    if expr.is_number:
        return bool(expr.is_nonnegative)
    if expr.is_Pow:
        base, exp = expr.base, expr.exp
        if exp.is_integer and exp.is_even:
            return bool(base.is_real) or base.is_real is None
    if isinstance(expr, sympy.Abs):
        return True
    if expr.is_Mul:
        # 正系数 * 非负因子
        nonneg_factors = []
        for factor in expr.args:
            if factor.is_number:
                if factor.is_negative:
                    return False
                continue
            nonneg_factors.append(factor)
        return all(_structural_nonnegative(f) for f in nonneg_factors) if nonneg_factors else bool(expr.is_nonnegative)
    if expr.is_Add:
        return all(_structural_nonnegative(t) for t in expr.args)
    return False


def _prove_nonnegative_expr(expr, symbols) -> VerificationResult:
    import sympy

    forms = []
    for fn in (sympy.factor, sympy.simplify, lambda e: sympy.simplify(sympy.factor(e))):
        try:
            form = fn(expr)
        except Exception:  # noqa: BLE001
            continue
        if all(str(form) != str(existing) for existing in forms):
            forms.append(form)
    if not forms:
        forms = [expr]
    negative = None
    for form in forms:
        if form.is_number:
            if form.is_nonnegative:
                return _ok("prove_nonnegative", certificate=f"{form} >= 0", raw=str(form))
            if form.is_negative:
                negative = form
                continue
        if form.is_nonnegative is True:
            return _ok("prove_nonnegative", certificate=f"{form} >= 0 (sympy 假设)", raw=str(form))
        if _structural_nonnegative(form):
            return _ok("prove_nonnegative",
                       certificate=f"{form} = 非负项之和 / 偶次幂, 在实数域 >= 0", raw=str(form))
        if form.is_nonnegative is False and negative is None:
            negative = form
    if negative is not None:
        return _fail("prove_nonnegative", detail=f"{negative} 可取负值")
    return _unknown(
        f"sympy 无法判定 {forms[0]} 的非负性 (假设查询返回 None)"
    )


def _real_counterexample_from_solution(sol, variables) -> dict | None:
    """把 solve 的解映射为实数反例。无法确认实数时返回 None (保持未决)。"""
    import sympy

    mapping = sol if isinstance(sol, dict) else None
    if mapping is None:
        return None
    by_name = {str(k): v for k, v in mapping.items()}
    free = set()
    for v in mapping.values():
        free |= getattr(v, "free_symbols", set())
    free = {s for s in free if str(s) not in by_name}
    sub = {s: sympy.Integer(0) for s in free}
    witness = {}
    for name in variables:
        expr = by_name.get(name)
        if expr is None:
            witness[name] = 0
            continue
        val = sympy.simplify(expr.subs(sub))
        if not val.is_number:
            return None
        if val.is_real is False:
            return None
        witness[name] = _exact_witness(val)
    return witness


def _solve_zero(diff, variables):
    import sympy

    # 必须复用表达式中已有假设的 symbol (Symbol('x') 与 Symbol('x', real=True) 不同)
    by_name = {str(s): s for s in diff.free_symbols}
    targets = [by_name[v] for v in variables if v in by_name]
    if not targets:
        targets = [sympy.Symbol(v) for v in variables]
    for form in (diff, _safe_factor(diff)):
        if form is None:
            continue
        try:
            sols = sympy.solve(sympy.Eq(form, 0), targets, dict=True)
            if sols:
                return sols
        except Exception:  # noqa: BLE001
            continue
    try:
        sols = sympy.solve(sympy.Eq(diff, 0), targets, dict=True)
        return sols or []
    except Exception:  # noqa: BLE001
        return None


def _safe_factor(expr):
    import sympy

    try:
        return sympy.factor(expr)
    except Exception:  # noqa: BLE001
        return None


# ---------------- 公开操作 ----------------

def op_simplify(arguments: dict) -> VerificationResult:
    variables = arguments.get("variables", [])
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        expr = _parse(arguments["expr"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    import sympy

    simplified = sympy.simplify(expr)
    return _ok("simplify", certificate=str(simplified), raw=str(simplified))


def op_prove_identity(arguments: dict) -> VerificationResult:
    variables = arguments.get("variables", [])
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        lhs = _parse(arguments["lhs"], symbols)
        rhs = _parse(arguments["rhs"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    import sympy

    diff = sympy.simplify(sympy.expand(lhs - rhs))
    if diff == 0 or diff.is_zero:
        return _ok("prove_identity", certificate=f"{lhs} - ({rhs}) = 0", raw=str(diff))
    # 数值探测: 若给出精确反例则判定失败 (精确有理点, 非随机采样)
    sample = _probe_counterexample(lhs - rhs, variables,
                                   assumptions=arguments.get("assumptions"))
    if sample is not None:
        return _fail("prove_identity", counterexample=sample,
                     detail="存在赋值使两边不等")
    return _unknown(f"无法化简差为 0 (差: {diff})")


def op_prove_inequality(arguments: dict) -> VerificationResult:
    lhs_s = arguments["lhs"]
    rhs_s = arguments["rhs"]
    relation = arguments.get("relation", ">=")
    variables = arguments.get("variables", [])
    complex_var = _complex_domain(arguments.get("assumptions"))
    if complex_var:
        return _unsupported(f"变量 {complex_var} 为复数域, 通常大小关系不适用")
    if relation not in (">=", ">", "<=", "<"):
        return _unsupported(f"不支持的关系 {relation}")
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        lhs = _parse(lhs_s, symbols)
        rhs = _parse(rhs_s, symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    diff = lhs - rhs if relation in (">=", ">") else rhs - lhs
    res = _prove_nonnegative_expr(diff, symbols)
    if res.status != VerificationStatus.passed:
        return res
    if relation in (">", "<"):
        # 严格: 还需确认差值不能为 0
        sols = _solve_zero(diff, variables)
        if sols is None:
            return _unknown("已证非负, 但无法判定是否存在取等点 (保持未决)")
        witness = None
        for sol in sols:
            candidate = _real_counterexample_from_solution(sol, variables)
            if candidate is not None and _witness_in_domain(candidate, arguments.get("assumptions")):
                witness = candidate
                break
        if witness is not None:
            return _fail("prove_inequality", counterexample=witness,
                         detail="存在使等号成立的赋值, 严格不等式不成立")
        if sols == []:
            res.certificate += "; 等号不可达 => 严格成立"
        else:
            return _unknown("存在等号解但无法确认为实数值 (保持未决)")
    return res


def op_prove_monotonicity(arguments: dict) -> VerificationResult:
    """核验因变量 expr 关于自变量 wrt 的单调/影响方向。"""
    variables = arguments.get("variables", [])
    wrt = arguments.get("wrt", "")
    direction = (arguments.get("direction") or "nondecreasing").lower()
    complex_var = _complex_domain(arguments.get("assumptions"))
    if complex_var:
        return _unsupported(f"变量 {complex_var} 为复数域, 单调性无定义")
    if direction not in ("increasing", "decreasing", "nondecreasing", "nonincreasing"):
        return _unsupported(f"不支持的单调方向 {direction}")
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        expr = _parse(arguments["expr"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    if wrt not in symbols:
        return _unsupported(f"自变量 {wrt} 未声明")
    import sympy

    deriv = sympy.simplify(sympy.diff(expr, symbols[wrt]))
    increasing = direction in ("increasing", "nondecreasing")
    strict = direction in ("increasing", "decreasing")
    signed = deriv if increasing else -deriv
    res = _prove_nonnegative_expr(signed, symbols)
    proof = f"d/d{wrt} ({expr}) = {deriv}"
    if res.status == VerificationStatus.passed and strict:
        sols = _solve_zero(signed, variables)
        if sols is None:
            return _unknown(f"{proof}; 已证非负但无法判定取零 (保持未决)", str(deriv))
        if sols == []:
            res.certificate = f"{proof}; 导数不可为 0 => 严格成立"
            return res
        witness = _find_monotonicity_witness(
            expr, symbols[wrt], variables, increasing, strict,
            dict(arguments.get("assumptions") or {}), symbols)
        if witness is not None:
            return _fail("prove_monotonicity", counterexample=witness,
                         raw=str(deriv), detail=f"{proof}; 存在域内点对违反严格单调")
        return _unknown(f"{proof}; 导数可能取零，未找到违反严格单调的点对；仅凭驻点不能反驳严格单调",
                        str(deriv))
    if res.status != VerificationStatus.passed:
        # 未能证明方向成立: 只有给出**域内可回代的反例**才算数学反驳;
        # 否则保持未决 (计划书 §4.3-3: 不用普通执行错误冒充反驳)。
        witness = _find_monotonicity_witness(
            expr, symbols[wrt], variables, increasing, strict,
            dict(arguments.get("assumptions") or {}), symbols)
        if witness is not None:
            return _fail("prove_monotonicity", counterexample=witness, raw=str(deriv),
                         detail=f"{proof}; 存在域内点对使 {expr} 反向变化, 单调性不成立")
        return _unknown(f"{proof}; 无法判定导数符号且未在声明域内找到反例 (保持未决)",
                        str(deriv))
    if res.certificate:
        res.certificate = f"{proof}; {res.certificate}"
    res.raw_output = str(deriv)
    return res


def op_equality_condition(arguments: dict) -> VerificationResult:
    variables = arguments.get("variables", [])
    complex_var = _complex_domain(arguments.get("assumptions"))
    if complex_var:
        return _unsupported(f"变量 {complex_var} 为复数域")
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        lhs = _parse(arguments["lhs"], symbols)
        rhs = _parse(arguments["rhs"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    import sympy

    diff = sympy.expand(lhs - rhs)
    if diff == 0 or diff.is_zero:
        return _ok("equality_condition", certificate="恒成立（声明域内所有赋值均取等）")
    sols = _solve_zero(diff, variables)
    if sols is None:
        return _unknown("无法求解等号条件")
    if not sols:
        return _ok("equality_condition", certificate="无解 (等号不可达)")
    import sympy as _sp

    rendered = []
    for sol in sols:
        parts = [f"{k} = {_sp.simplify(v)}" for k, v in sol.items()]
        rendered.append(", ".join(parts) if parts else "(恒成立)")
    return _ok("equality_condition", certificate="; ".join(rendered), raw=str(sols))


def op_find_counterexample(arguments: dict) -> VerificationResult:
    # 单调性反例: expr 关于 wrt 的方向被违反
    if arguments.get("expr") and arguments.get("wrt"):
        return _find_monotonicity_counterexample(arguments)
    lhs_s = arguments["lhs"]
    rhs_s = arguments["rhs"]
    relation = arguments.get("relation", ">")
    variables = arguments.get("variables", [])
    assumptions = arguments.get("assumptions") or {}
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        lhs = _parse(lhs_s, symbols)
        rhs = _parse(rhs_s, symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    if relation in (">", ">="):
        diff = lhs - rhs
    elif relation in ("<", "<="):
        diff = rhs - lhs
    else:
        diff = lhs - rhs
    if relation in (">", "<"):
        # 严格不等式: 差式小于或等于零都构成反例；仅查等号会漏掉
        # 处处为负而没有等号点的假命题。
        sample = _probe_counterexample(diff, variables, want_negative=True,
                                       assumptions=assumptions)
        if sample is not None:
            return _fail("find_counterexample", counterexample=sample,
                         detail="存在使严格不等式反向的域内赋值")
        sols = _solve_zero(diff, variables)
        if sols:
            for sol in sols:
                witness = _real_counterexample_from_solution(sol, variables)
                if witness is not None and _witness_in_domain(witness, assumptions):
                    return _fail("find_counterexample", counterexample=witness,
                                 detail="等号可达, 严格不等式被精确反例反驳")
        return _unknown("有界精确采样与等号求解未发现域内反例 (不构成证明)")
    if relation not in (">=", "<=", "=="):
        return _unsupported(f"不支持的反例关系 {relation}")
    # >=: lhs-rhs 应非负；<=: rhs-lhs 应非负。采样只在差式为负时构成反例。
    sample = _probe_counterexample(
        diff, variables, want_negative=relation != "==", assumptions=assumptions)
    if sample is not None:
        return _fail("find_counterexample", counterexample=sample, detail="存在违反不等式的赋值")
    return _unknown("有界精确采样未发现反例 (不构成证明)")


def _find_monotonicity_counterexample(arguments: dict) -> VerificationResult:
    variables = arguments.get("variables", [])
    wrt = arguments.get("wrt", "")
    direction = (arguments.get("direction") or "nondecreasing").lower()
    complex_var = _complex_domain(arguments.get("assumptions"))
    if complex_var:
        return _unsupported(f"变量 {complex_var} 为复数域")
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        expr = _parse(arguments["expr"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    if wrt not in symbols:
        return _unsupported(f"自变量 {wrt} 未声明")
    import sympy

    deriv = sympy.simplify(sympy.diff(expr, symbols[wrt]))
    increasing = direction in ("increasing", "nondecreasing")
    strict = direction in ("increasing", "decreasing")
    signed = deriv if increasing else -deriv
    proof = f"d/d{wrt} ({expr}) = {deriv}"

    # 单调性反例必须是**可回代的见证**, 且必须落在声明的变量域内:
    # 域外见证不构成反驳 (计划书 §13.2 "域外伪反例必须拒绝")。
    witness = _find_monotonicity_witness(
        expr, symbols[wrt], variables, increasing, strict,
        dict(arguments.get("assumptions") or {}), symbols)
    if witness is not None:
        return _fail("find_counterexample", counterexample=witness, raw=str(deriv),
                     detail=f"{proof}; 存在 x1 < x2 使 {expr} 反向变化, 单调性被精确反例反驳")
    if signed.is_nonnegative and not strict:
        return _ok("find_counterexample", certificate=f"{proof}; 导数非负, 未找到反例",
                   raw=str(deriv))
    if signed.is_negative:
        return _unknown(
            f"{proof}; 导数为负但未在声明域内找到可回代的点对反例 (保持未决, 不算反驳)",
            str(deriv))
    return _unknown(f"{proof}; 有界精确采样未发现反例 (不构成证明)", str(deriv))


# 变量域 -> 允许的采样网格 (域外取值不得用作见证)
def _domain_grid(domain: str, strict_positive: bool = False):
    import sympy

    low = str(domain or "real").lower()
    singleton = re.fullmatch(r"\{\s*([+-]?\d+(?:/\d+)?)\s*\}", low)
    if singleton:
        return [sympy.Rational(singleton.group(1))]
    if low in ("integer", "int", "integers", "z"):
        return [sympy.Integer(v) for v in (-2, -1, 0, 1, 2)]
    if low in ("positive", "pos", "positive_real", "positivereal", "r+", "r_+"):
        return [sympy.Rational(v, 2) for v in (1, 2, 3, 4, 5, 6, 8, 10)]
    if low in ("nonnegative", "nonneg", "nonnegative_real", "nonneg_real", "r>=0"):
        return [sympy.Rational(v, 2) for v in (0, 1, 2, 3, 4, 5, 6, 8, 10)]
    if low in ("nonzero", "non_zero", "nonzero_real"):
        return [sympy.Rational(v, 2) for v in (-4, -3, -2, -1, 1, 2, 3, 4)]
    if low in ("real", "reals", "r", "rational", "rationals", "q", ""):
        return [sympy.Rational(v, 2) for v in (-4, -3, -2, -1, 0, 1, 2, 3, 4)]
    return []


def _in_domain(value, domain: str) -> bool:
    """见证是否满足声明的变量域 (域外见证一律拒绝)。"""
    import sympy

    low = str(domain or "real").lower()
    try:
        v = sympy.Rational(value)
    except Exception:  # noqa: BLE001
        return False
    singleton = re.fullmatch(r"\{\s*([+-]?\d+(?:/\d+)?)\s*\}", low)
    if singleton:
        return bool(v == sympy.Rational(singleton.group(1)))
    if low in ("integer", "int", "integers", "z"):
        return bool(v.is_integer)
    if low in ("positive", "pos", "positive_real", "positivereal", "r+", "r_+"):
        return bool(v > 0)
    if low in ("nonnegative", "nonneg", "nonnegative_real", "nonneg_real", "r>=0"):
        return bool(v >= 0)
    if low in ("nonzero", "non_zero", "nonzero_real"):
        return bool(v != 0)
    return low in ("real", "reals", "r", "rational", "rationals", "q", "")


def _witness_in_domain(witness: dict, assumptions: dict | None) -> bool:
    return all(_in_domain(value, str((assumptions or {}).get(name, "real")))
               for name, value in witness.items())


def _find_monotonicity_witness(expr, symbol, variables, increasing: bool, strict: bool,
                               assumptions: dict | None = None,
                               symbols: dict | None = None) -> dict | None:
    """在**声明域内**的精确有理网格上找一对点 (x1 < x2) 使单调方向被违反。

    返回 {wrt: x1, f"{wrt}2": x2} 形式的可回代见证; 找不到或只有域外反例时返回 None。
    这是构造反例的手段, 不是证明。
    """
    import sympy

    assumptions = assumptions or {}
    grid = _domain_grid(assumptions.get(str(symbol), "real"))
    others = [(name, sym) for name, sym in (symbols or {}).items() if name != str(symbol)]
    other_grids = [_domain_grid(assumptions.get(name, "real")) for name, _ in others]

    for i, x1 in enumerate(grid):
        for x2 in grid[i + 1:]:
            if not x1 < x2:
                continue
            for combo in _product(other_grids):
                sub1 = {symbol: x1, **{sym: val for (_, sym), val in zip(others, combo)}}
                sub2 = {symbol: x2, **{sym: val for (_, sym), val in zip(others, combo)}}
                try:
                    v1 = sympy.simplify(expr.subs(sub1))
                    v2 = sympy.simplify(expr.subs(sub2))
                except Exception:  # noqa: BLE001
                    continue
                if not (v1.is_number and v2.is_number):
                    continue
                violates = v2 < v1 if increasing else v2 > v1
                if strict:
                    violates = violates or v2 == v1
                if not violates:
                    continue
                witness = {str(symbol): str(x1), f"{symbol}2": str(x2)}
                for (name, _), val in zip(others, combo):
                    witness[name] = str(val)
                return witness
    return None


def _product(grids):
    import itertools

    if not grids:
        return [()]
    return list(itertools.product(*grids))


def op_prove_nonnegative(arguments: dict) -> VerificationResult:
    variables = arguments.get("variables", [])
    complex_var = _complex_domain(arguments.get("assumptions"))
    if complex_var:
        return _unsupported(f"变量 {complex_var} 为复数域, 非负性无定义")
    symbols, _ = _to_symbols(variables, arguments.get("assumptions"))
    try:
        expr = _parse(arguments["expr"], symbols)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    return _prove_nonnegative_expr(expr, symbols)


def _probe_counterexample(diff, variables, want_negative: bool = False,
                          assumptions: dict | None = None) -> dict | None:
    """在小的精确有理网格上探测反例 (有界, 仅用于构造反例; 非证明手段)。

    见证按**符号名**构造: 不能假定 variables 的顺序与 diff.free_symbols 或网格
    维度一致, 否则反例赋值会错位到错误的变量上。
    """
    import itertools

    import sympy

    if not variables:
        return None
    by_name = {str(s): s for s in diff.free_symbols}
    # (变量名, 符号) 对: 只保留确实出现在表达式中的声明变量, 顺序即网格维度顺序
    pairs = [(name, by_name[name]) for name in variables if name in by_name]
    if not pairs:
        pairs = [(name, sympy.Symbol(name)) for name in variables]
    grids = [_domain_grid((assumptions or {}).get(name, "real")) for name, _ in pairs]
    if any(not grid for grid in grids):
        return None
    for combo in itertools.product(*grids):
        sub = {sym: val for (_, sym), val in zip(pairs, combo)}
        try:
            val = sympy.simplify(diff.subs(sub))
        except Exception:  # noqa: BLE001 - 单点失败不影响其余网格
            continue
        if not val.is_number:
            continue
        hit = val.is_negative if want_negative else (val.is_zero is False)
        if hit and all(_in_domain(value, (assumptions or {}).get(name, "real"))
                       for (name, _), value in zip(pairs, combo)):
            # 见证必须按符号名映射, 且保留**精确值** (写成 `1/2` 而不是取整):
            # 取整会把反例替换成另一个点, 回代时可能根本不违反命题。
            return {str(sym): _exact_witness(point) for (_, sym), point in zip(pairs, combo)}
    return None


def _exact_witness(value):
    """把网格点写成可回代的精确值 (整数保持整数, 有理数写成字符串分数)。"""
    import sympy

    if value.is_Integer:
        return int(value)
    return str(sympy.nsimplify(value))


OPERATIONS = {
    "simplify": op_simplify,
    "prove_identity": op_prove_identity,
    "prove_inequality": op_prove_inequality,
    "prove_nonnegative": op_prove_nonnegative,
    "prove_monotonicity": op_prove_monotonicity,
    "equality_condition": op_equality_condition,
    "find_counterexample": op_find_counterexample,
}


def run(operation: str, arguments: dict) -> VerificationResult:
    fn = OPERATIONS.get(operation)
    if fn is None:
        return _unsupported(f"sympy 适配器不支持操作 {operation}")
    try:
        return fn(arguments)
    except _UnsafeExpression as e:
        return _unsupported(str(e))
    except Exception as e:  # noqa: BLE001
        return VerificationResult(
            tool="sympy", tool_version=_version(), status=VerificationStatus.error,
            detail=f"sympy 执行异常: {e}",
        )
