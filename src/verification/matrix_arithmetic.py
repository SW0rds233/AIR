"""Bounded component matrices, evaluated without eval or implicit scalar symbols."""
from __future__ import annotations

import ast


def parse_matrix_expression(text: str, symbols: dict):
    import sympy as s
    from src.verification.sympy_adapter import _UnsafeExpression, _FUNCS

    def scalar(value):
        if not isinstance(value, s.Expr) or value.is_commutative is not True:
            raise _UnsafeExpression('矩阵元素与系数必须是标量')
        if value.has(s.nan, s.zoo, s.oo, -s.oo):
            raise _UnsafeExpression('矩阵元素或系数非有限')
        return value

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return s.Rational(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in symbols:
                raise _UnsafeExpression(f'矩阵表达式含未声明变量 {node.id}；请给出所有分量，I 不能默认为标量或虚数单位')
            return symbols[node.id]
        if isinstance(node, ast.Tuple):
            return tuple(evaluate(item) for item in node.elts)
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call):
            args = [evaluate(arg) for arg in node.args]
            if node.func.id == 'matrix':
                if len(args) != 1 or not isinstance(args[0], tuple) or not 1 <= len(args[0]) <= 8:
                    raise _UnsafeExpression('matrix 需要 1 至 8 行分量元组')
                rows = args[0]
                if (not all(isinstance(row, tuple) for row in rows)
                        or not 1 <= len(rows[0]) <= 8 or any(len(row) != len(rows[0]) for row in rows)):
                    raise _UnsafeExpression('matrix 必须是至多 8×8 的矩形分量数组')
                return s.ImmutableMatrix([[scalar(value) for value in row] for row in rows])
            if node.func.id in _FUNCS and hasattr(s, node.func.id):
                return scalar(getattr(s, node.func.id)(*[scalar(arg) for arg in args]))
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.MatMult):
                if not isinstance(left, s.MatrixBase) or not isinstance(right, s.MatrixBase):
                    raise _UnsafeExpression('@ 两端须为已给出维度和全部分量的 matrix；不能将矩阵声明为 Real')
                return left * right
            if isinstance(node.op, ast.Pow):
                if right.is_Integer is not True or not -8 <= right <= 8:
                    raise _UnsafeExpression('分量矩阵表达式仅支持 -8 至 8 的整数幂')
                if right < 0:
                    divisor = left.det() if isinstance(left, s.MatrixBase) else scalar(left)
                    if divisor.is_nonzero is not True:
                        raise _UnsafeExpression('求逆/负幂需要可确认的非零行列式或分母；请显式约束参数或分量展开')
                return left ** right
            if isinstance(node.op, ast.Div):
                divisor = scalar(right)
                if divisor.is_nonzero is not True:
                    raise _UnsafeExpression('分量核验不能省略非零分母条件')
                return left / right
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
        raise _UnsafeExpression('不支持的分量矩阵表达式；使用 matrix(((a,b),(c,d))) 与已声明标量')

    try:
        value = evaluate(ast.parse(text, mode='eval').body)
        if isinstance(value, s.MatrixBase):
            for entry in value:
                scalar(entry)
        else:
            scalar(value)
        return value
    except _UnsafeExpression:
        raise
    except (TypeError, ValueError, AttributeError, NotImplementedError, ZeroDivisionError) as error:
        raise _UnsafeExpression(f'分量矩阵维度或运算无效: {error}') from error


def check_matrix_identity(lhs, rhs, variables, assumptions):
    import sympy as s
    from src.verification.sympy_adapter import _ok, _fail, _unknown, _unsupported, _probe_counterexample

    if not isinstance(lhs, s.MatrixBase) or not isinstance(rhs, s.MatrixBase) or lhs.shape != rhs.shape:
        return _unsupported('矩阵恒等式两端必须是相同维度的分量矩阵')
    diff = (lhs - rhs).applyfunc(s.simplify)
    if all(value == 0 or value.is_zero is True for value in diff):
        return _ok('prove_identity', certificate=f'分量矩阵 {lhs.shape}: {lhs} - ({rhs}) = {diff}',
                   detail='只核验所给维度、分量与参数域；数值例不证明任意维度命题或现实效应')
    for i in range(diff.rows):
        for j in range(diff.cols):
            if diff[i, j].is_number and diff[i, j].is_zero is False:
                return _fail('prove_identity', counterexample={
                    'row': i+1, 'column': j+1, 'difference': str(diff[i, j])},
                    detail=f'矩阵分量 ({i+1},{j+1}) 的精确差非零', raw=str(diff))
            sample = _probe_counterexample(diff[i, j], variables, assumptions=assumptions)
            if sample is not None:
                return _fail('prove_identity', counterexample=sample,
                             detail=f'矩阵分量 ({i+1},{j+1}) 不相等', raw=str(diff))
    return _unknown('部分矩阵分量未能化简为零', raw=str(diff))
