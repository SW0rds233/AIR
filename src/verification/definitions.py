"""A bounded, non-evaluating grammar for definition encodings, not a prover."""
from __future__ import annotations

import ast
import re

from src.verification.schemas import VerificationResult, VerificationStatus


def is_definition_assignment(claim) -> bool:
    """Only an explicitly named definition with a symbol/function on the left.

    `claim_type=definitional` alone is insufficient: algebraic identities use it
    too. Compound claims can contain consequences; those need separate proof.
    """
    kind = getattr(getattr(claim, 'claim_type', None), 'value', '')
    relation = getattr(getattr(claim, 'relation', None), 'value', '')
    if kind != 'definitional' or relation != '==':
        return False
    if not re.search(r'定义|:=|\bdefin(?:e[ds]?|ition)\b', claim.statement, re.I):
        return False
    try:
        lhs = ast.parse(claim.lhs, mode='eval').body
    except (SyntaxError, ValueError):
        return False
    return isinstance(lhs, ast.Name) or (isinstance(lhs, ast.Call)
        and isinstance(lhs.func, ast.Name) and not lhs.keywords
        and all(isinstance(arg, ast.Name) for arg in lhs.args))


def check_definition(arguments: dict) -> VerificationResult:
    """Check declaration closure and function arity without eval/sympify.

    Supports scalar arithmetic and an outer Iverson bracket. Quantified sums,
    set builders, dimensions, domains and empirical validity remain separate.
    """
    def result(status, detail, certificate=''):
        return VerificationResult(tool='sympy', status=status, scope='step',
            detail=detail, certificate=certificate,
            values={'check_kind': 'definition_encoding', 'proves_claim': False})

    try:
        lhs_text, rhs_text = str(arguments.get('lhs', '')), str(arguments.get('rhs', ''))
        if not lhs_text.strip() or not rhs_text.strip() or len(lhs_text + rhs_text) > 4000:
            raise ValueError('定义两端必须非空且总长不超过 4000 字符')
        lhs = ast.parse(lhs_text, mode='eval').body
        if isinstance(lhs, ast.Name):
            defined = lhs.id
        elif (isinstance(lhs, ast.Call) and isinstance(lhs.func, ast.Name)
              and not lhs.keywords and all(isinstance(a, ast.Name) for a in lhs.args)):
            defined = lhs.func.id
            names = [arg.id for arg in lhs.args]
            if len(set(names)) != len(names):
                raise ValueError('定义的形参不能重复')
        else:
            raise ValueError('定义左端须为声明的符号或函数')
        rhs_text = rhs_text.translate(str.maketrans({'−': '-', '·': '*', '×': '*',
            '≥': '>=', '≤': '<=', '≠': '!=', '∧': ' and ', '∨': ' or '})).replace('^', '**')
        indicator = re.fullmatch(r'\s*1\[(.*)\]\s*', rhs_text, re.S)
        if indicator:
            rhs_text = indicator.group(1)
            rhs_text = re.sub(r'(?<![<>=!])=(?!=)', '==', rhs_text)
        rhs = ast.parse(rhs_text.strip(), mode='eval').body
        allowed = (ast.Name, ast.Load, ast.Constant, ast.Call, ast.BinOp, ast.UnaryOp,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd,
            ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
            ast.BoolOp, ast.And, ast.Or, ast.Not)
        declared = set(arguments.get('variables') or [])
        arities, scalar_names = {}, set()
        def visit(node, depth=0):
            if depth > 50 or not isinstance(node, allowed):
                raise ValueError(f'不支持的定义语法: {type(node).__name__}')
            if isinstance(node, ast.Name):
                if node.id not in declared:
                    raise ValueError(f'未声明符号: {node.id}')
                scalar_names.add(node.id)
            if isinstance(node, ast.Constant):
                if type(node.value) not in (int, float):
                    raise ValueError('只支持数值常量')
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.keywords or not node.args:
                    raise ValueError('只支持已声明的、无关键字参数的函数调用')
                name = node.func.id
                if name not in declared:
                    raise ValueError(f'未声明函数: {name}')
                if name in arities and arities[name] != len(node.args):
                    raise ValueError(f'函数 {name} 的参数个数不一致')
                arities[name] = len(node.args)
                for arg in node.args:
                    visit(arg, depth + 1)
                return
            for child in ast.iter_child_nodes(node):
                visit(child, depth + 1)
        visit(lhs)
        visit(rhs)
        if set(arities) & scalar_names:
            raise ValueError('同一符号不能同时用作标量和函数')
        if any(isinstance(node, ast.Name) and node.id == defined for node in ast.walk(rhs)):
            raise ValueError('右端引用被定义对象，需单独给出递归/隐式定义的存在唯一性条件')
        def predicate(node):
            return isinstance(node, ast.Compare) or (isinstance(node, ast.BoolOp)
                and all(predicate(value) for value in node.values)) or (
                isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) and predicate(node.operand))
        if indicator and not predicate(rhs):
            raise ValueError('指示函数必须包含布尔条件')
        encoding = ast.unparse(rhs)
        if indicator:
            encoding = 'Indicator(' + encoding + ')'
        return result(VerificationStatus.passed,
            '定义编码检查通过：符号已声明、函数元数一致；仅核验编码，不验证量纲、适用性或附带推论。',
            ast.unparse(lhs) + ' := ' + encoding)
    except (SyntaxError, ValueError, TypeError, RecursionError) as error:
        return result(VerificationStatus.unsupported,
            f'定义编码未受理: {error}。请补齐符号声明，或拆出求和/集合/量词的可执行编码；不构成反例。')
