"""Conservative real-power implication checks with an exact consistency witness.

Z3 proves a relaxation in which positive-base powers are fresh real variables.
SymPy supplies only an exact witness for ALL original premises, never a sampled
proof of the conclusion. A countermodel of the relaxation is not a counterexample.
"""
from __future__ import annotations

import ast
import copy
import json


def _expr(node, env):
    import sympy as s
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return s.Rational(str(node.value))
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _expr(node.operand, env)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _expr(node.left, env), _expr(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            if right.is_number and (right.is_real is not True or right.is_finite is not True or abs(right)>32):
                raise ValueError('数值幂指数超出有界核验范围')
            return left ** right
    raise ValueError('Only bounded arithmetic is supported')


def _witness(trees, variables):
    """Find a point and check the original AST, including every domain/power."""
    import sympy as s
    symbols = {name:s.Symbol(name, real=True) for name in variables}
    equations = []
    for tree in trees:
        node = tree.body
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return None
        if isinstance(node.ops[0], ast.Eq):
            equations.append(_expr(node.left, symbols) - _expr(node.comparators[0], symbols))
    substitutions = {}
    for equation in equations:
        equation = equation.subs(substitutions, simultaneous=True)
        choices = []
        for symbol in sorted(equation.free_symbols, key=str):
            if symbol in substitutions:
                continue
            solved, value = s.solve_linear(equation, symbols=[symbol])
            if solved == symbol and symbol not in value.free_symbols:
                choices.append((s.count_ops(value), str(symbol), symbol, value))
        if choices:
            _, _, symbol, value = min(choices)
            substitutions = {key:val.subs(symbol,value) for key,val in substitutions.items()}
            substitutions[symbol] = value
    free = set(symbols.values()) - set(substitutions)
    comparisons = {ast.Eq:s.Eq, ast.NotEq:s.Ne, ast.Lt:s.Lt, ast.LtE:s.Le, ast.Gt:s.Gt, ast.GtE:s.Ge}
    for trial in (s.Rational(1,2),s.Integer(1),s.Integer(2),s.Integer(-1),s.Integer(0)):
        values = {symbol:trial for symbol in free}
        values.update({symbol:value.subs(values, simultaneous=True) for symbol,value in substitutions.items()})
        if any(v.is_real is not True or v.is_finite is not True for v in values.values()):
            continue
        if any(str(variables[str(symbol)]).lower() in {'int','integer','z'} and value.is_integer is not True
               for symbol,value in values.items()):
            continue
        env = {str(symbol):value for symbol,value in values.items()}
        valid = True
        for tree in trees:
            node = tree.body
            # Evaluate each side before cancellation; x/x at x=0 is not a witness.
            left, right = _expr(node.left,env), _expr(node.comparators[0],env)
            if (left.is_real is not True or right.is_real is not True
                    or left.is_finite is not True or right.is_finite is not True
                    or s.simplify(comparisons[type(node.ops[0])](left,right)) is not s.true):
                valid = False
                break
        if valid:
            return {name:str(value) for name,value in env.items()}
    return None


def check_power_implication(arguments):
    from src.verification.z3_adapter import _Unsupported, _parse, _result, op_check_implication
    from src.verification.schemas import VerificationStatus as Status
    try:
        variables = dict(arguments.get('variables') or {})
        premises = list(arguments.get('premises') or [])
        if len(variables)>16 or len(premises)>24:
            return _result(Status.unsupported,'实数幂辅助核验超出有界规模')
        trees = [_parse(text) for text in [*premises,arguments.get('conclusion','')]]
        if sum(sum(1 for _ in ast.walk(tree)) for tree in trees)>1000:
            return _result(Status.unsupported,'实数幂辅助核验表达式过大')
        powers = {}
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node,ast.Name) and node.id not in variables:
                    raise ValueError('未声明变量 ' + node.id)
                if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow):
                    try:
                        exponent = ast.literal_eval(node.right)
                    except (ValueError,TypeError):
                        exponent = None
                    if isinstance(exponent,(int,float)) and abs(exponent)>32:
                        return _result(Status.unsupported,'数值幂指数超出有界核验范围')
                    powers.setdefault(ast.dump(node),node)
        if not powers or len(powers)>12:
            return _result(Status.unsupported,'实数幂辅助核验超出有界规模')
        # Restrict all powers to the positive real domain, proved without powers.
        plain = [text for text,tree in zip(premises,trees) if not any(isinstance(n,ast.Pow) for n in ast.walk(tree))]
        for node in powers.values():
            if any(isinstance(n,ast.Pow) for n in ast.walk(node.left)):
                return _result(Status.unsupported,'嵌套幂的底数域尚未核验')
            domain = op_check_implication({**arguments,'premises':plain,
                                          'conclusion':f'({ast.unparse(node.left)}) > 0'})
            if domain.status != Status.passed:
                return _result(Status.unknown,'不能确认所有幂的底数在整个前提域内为正实数')
        names = {}
        for key in powers:
            name = '_power_' + str(len(names))
            while name in variables:
                name += '_'
            names[key] = name
            variables[name] = 'Real'
        class AbstractPowers(ast.NodeTransformer):
            def visit_BinOp(self,node):
                if isinstance(node.op,ast.Pow):
                    return ast.copy_location(ast.Name(id=names[ast.dump(node)],ctx=ast.Load()),node)
                return self.generic_visit(node)
        transformed = [ast.unparse(AbstractPowers().visit(copy.deepcopy(tree))) for tree in trees]
        relaxed = op_check_implication({**arguments,'variables':variables,
                                       'premises':transformed[:-1],'conclusion':transformed[-1]})
        if relaxed.status != Status.passed:
            return _result(Status.unknown,'幂的实数抽象不足以证明结论；抽象反模型不作为原命题反例')
        witness = _witness(trees[:-1],arguments['variables'])
        if witness is None:
            return _result(Status.unknown,'未找到满足全部原始幂前提的精确见证，不能排除空洞蕴含')
        import sympy
        certificate = json.dumps({'method':'positive-real-power-relaxation',
            'sympy_version':sympy.__version__,'premise_witness':witness,
            'abstract_powers':{names[key]:ast.unparse(node) for key,node in powers.items()}},ensure_ascii=False)
        result = _result(Status.passed,'Z3 证明保留全部前提的幂抽象蕴含；SymPy 精确见证确认原前提可满足。仅核验所列条件结论。',certificate)
        result.certificate = certificate
        return result
    except ImportError:
        return _result(Status.unavailable, '实数幂辅助核验需要 SymPy')
    except (_Unsupported, ValueError, TypeError, KeyError, NotImplementedError) as error:
        return _result(Status.unsupported,'实数幂辅助核验不受理: ' + str(error))
