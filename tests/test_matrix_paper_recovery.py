"""Regressions from the November 20 manuscript and review trace."""
from src.publication.render_latex import escape_latex
from src.research.acceptance import delivery_gate
from src.research.problem_formulator import obligations_for_claim
from src.research.schemas import Claim, ResearchSnapshot
from src.verification.sympy_adapter import op_prove_identity
import pytest


def test_unicode_signed_scripts_are_one_math_script():
    rendered = escape_latex('(I−ρW)⁻¹ + x⁺²')
    assert '⁻' not in rendered and '⁺' not in rendered
    assert '^{-1}' in rendered and '^{+2}' in rendered


def test_informal_descriptive_mechanism_uses_evidence_obligations():
    claim = Claim(statement='候选机制：通信改善可能提高发现概率并影响违规率',
        claim_type='descriptive', study={'design': 'theory'},
        mechanism_links=['链路可用性上升 → 监控覆盖增加', '监控覆盖增加 → 违规率变化'],
        evidence_requirements=['需要独立观测及替代解释检验'])
    kinds = {o.kind for o in obligations_for_claim(claim)}
    assert 'formal_argument' not in kinds
    assert {'evidence_support', 'scope_check', 'control_confound'} <= kinds


def test_solver_local_real_types_are_not_missing_empirical_conditions():
    claim = Claim(id='c', statement='两点差分恒等式', status='supported',
        coverage='target', support_kind='constraint_solve', validation_status='verified',
        variable_domains={'y1': 'Real', 'y2': 'Real', 'A1': 'Real', 'A2': 'Real'})
    snapshot = ResearchSnapshot(project_id='p', claims=[claim], writing_map={'c': 'block:b'})
    result = delivery_gate('对任意两点，结果之差满足所列恒等式。'*30, snapshot)
    assert not any('适用条件未出现在正文' in reason for reason in result.reasons)


def test_exact_two_region_matrix_example_is_checkable():
    # (I - .3 W)^(-1) (I + .2 W), W=[[0,1],[1,0]].
    result = op_prove_identity({
        'lhs': '(matrix(((1,0),(0,1))) - Rational(3,10)*matrix(((0,1),(1,0))))**(-1) @ '
               '(matrix(((1,0),(0,1))) + Rational(1,5)*matrix(((0,1),(1,0))))',
        'rhs': 'matrix(((Rational(106,91),Rational(50,91)),(Rational(50,91),Rational(106,91))))',
        'variables': [], 'assumptions': {}})
    assert result.status.value == 'passed', result.detail


def test_wide_table_formula_gets_full_width_without_losing_terms():
    from src.publication.render_latex import _render_table
    formula = 'S=(I−ρW)⁻¹(β₁I+β₅W)+(I−ρW)⁻¹(β₂I+β₆W)Γ'
    tex = '\n'.join(_render_table({'header': ['陈述', '表达式', '条件', '地位'],
        'rows': [['空间效应', formula, '行标准化', '未核验']]}))
    assert r'\multicolumn{4}' in tex
    assert r'\beta_{6}' in tex and r'\Gamma' in tex and '^{-1}' in tex
    assert '行标准化' in tex and '未核验' in tex


@pytest.mark.parametrize('lhs,rhs', [
    ('matrix(((x,0),(0,1)))**(-1) @ matrix(((x,0),(0,1)))', 'matrix(((1,0),(0,1)))'),
    ('matrix(((1,2),(2,4)))**(-1)', 'matrix(((1,0),(0,1)))'),
    ('x @ y', 'x*y'),
    ('matrix(((1,2),(3,4))) @ matrix(((1,2),))', 'matrix(((1,2),(3,4)))'),
    ('matrix(((1,0),(0,1))).inv()', 'matrix(((1,0),(0,1)))'),
])
def test_matrix_verifier_does_not_invent_invertibility_or_scalarize(lhs, rhs):
    result = op_prove_identity({'lhs': lhs, 'rhs': rhs, 'variables': ['x','y']})
    assert result.status.value == 'unsupported', result.detail


def test_matrix_multiplication_is_not_commutative():
    a, b = 'matrix(((1,1),(0,1)))', 'matrix(((1,0),(1,1)))'
    result = op_prove_identity({'lhs': f'{a} @ {b}', 'rhs': f'{b} @ {a}', 'variables': []})
    assert result.status.value == 'failed', result.detail


def test_positive_domain_restrictions_still_require_disclosure():
    claim = Claim(id='c', statement='条件关系', status='supported', coverage='target',
        support_kind='constraint_solve', validation_status='verified', variable_domains={'x':'positive'})
    snapshot = ResearchSnapshot(project_id='p', claims=[claim], writing_map={'c':'block:b'})
    assert any('适用条件未出现在正文' in r for r in delivery_gate('无条件关系。'*60,snapshot).reasons)


def test_formal_mechanism_relation_still_requires_proof():
    claim = Claim(statement='给定前提下的机制等式', study={'design':'theory'}, claim_type='descriptive',
        mechanism_links=['X影响Y'], evidence_requirements=['需验证经验适用性'],
        formal_spec={'variables':{'x':'Real','y':'Real'},'premises':['y == 2*x'],'conclusion':'y-x == x'})
    assert [o.kind for o in obligations_for_claim(claim)] == ['check_implication']


def test_blocked_matrix_review_is_repaired_before_rewriting_prose():
    from src.agents.review import _issue_from, _needs_from
    issue = _issue_from({'category':'science','severity':'major','summary':'数值示例缺乏透明推导',
        'detail':'clm-matrix 的 status 为 blocked，因 sympy 不受理 MatMult 节点。',
        'suggested_owner':'writing','repair_action':'revise_text',
        'affected_ids':['clm-matrix','blk-example'],'acceptance':['给出可复现的矩阵推导与数值计算']})
    need = _needs_from([issue])[0]
    assert need.owner == 'reasoning' and need.hints.get('repair_claim') is True


def test_table_of_contents_does_not_displace_the_same_documents_body():
    from types import SimpleNamespace
    from src.kb.bridge import gather_sources
    from src.kb.service import SourceRef, SearchOutcome
    toc = SourceRef(source_id='doc-report',chunk_id='doc-report#0',title='技术白皮书',
        excerpt='行业应用........................20\n标准化进展........................56',locator='p1')
    body = SourceRef(source_id='doc-report',chunk_id='doc-report#8',title='技术白皮书',
        excerpt='渔船实时位置经通信系统传输至指挥中心，效应仍需独立评估。',locator='p9',page=9)
    service = SimpleNamespace(available=True,topic='library',
        search=lambda request:SearchOutcome(refs=[toc,body]),
        resolve=lambda ref:{'has_fulltext':True,'file_hash':'same-file'},
        read=lambda ref:{'text':ref.excerpt,'locator':ref.locator,'page':ref.page})
    items, _ = gather_sources(service,None,topic='library',extra_queries=['相关资料'],max_queries=1)
    assert len(items) == 1 and items[0].location == 'p9'
    assert items[0].support.value == 'insufficient', 'Better retrieval is not scientific support'


def test_long_indicator_conditions_wrap_inside_existing_aligned():
    from src.publication.render_latex import _display_math
    formula = (r'\begin{aligned}B(A_k,T)&=\mathbf{1}\!\left['
        r'R_{\rm avail}(A_k,T)\ge r_{\rm req},\ '
        r'\Delta_{\max}(A_k,T)\le T_{\rm gap}^{\rm req},\ '
        r'\max_{p\in G_k,t\in T}D(p,t)\le D_{\rm req}\right].\end{aligned}')
    rendered = _display_math(formula)
    assert r'\begin{gathered}' in rendered
    assert r'\max_{p\in G_k,t\in T}D(p,t)' in rendered


def test_existing_aligned_can_wrap_a_long_additive_row():
    from src.publication.render_latex import _display_math
    formula = (r'\begin{aligned}D(p,t)&=\frac{d_{\rm ul}(p,t)+d_{\rm dl}(p,t)}{c}'
        r'+\sum_{\ell\in{\rm route}(p,t)}\frac{d_{{\rm isl},\ell}}{c}'
        r'+D_{\rm proc}(p,t)+D_{\rm queue}(p,t)+D_{\rm retx}(p,t)\end{aligned}')
    rendered = _display_math(formula)
    assert r'\\ &+' in rendered
    assert r'\frac{d_{\rm ul}(p,t)+d_{\rm dl}(p,t)}{c}' in rendered


def test_matrix_identity_closes_the_actual_reasoning_obligation_set():
    from src.agents.reasoning import _obligations_for_existing_claim
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(id='clm-matrix', version=2, statement='固定二维分量矩阵的乘法恒等式',
        claim_type='descriptive', study={'design':'theory'}, relation='==',
        lhs='matrix(((x,1),(0,y))) @ matrix(((2,0),(0,2)))',
        rhs='matrix(((2*x,2),(0,2*y)))',variables=['x','y'], variable_domains={'x':'Real','y':'Real'})
    service = VerificationService(runner=VerificationRunner(inproc=True))
    for obligation in _obligations_for_existing_claim(claim):
        outcome = service.check(claim,obligation)
        assert outcome.closed, (obligation.kind, outcome.obligation_updates)
        assert outcome.record.claim_version == 2 and outcome.record.arguments['lhs'] == claim.lhs


def test_free_matrix_name_without_definition_is_not_proved():
    result = op_prove_identity({'lhs':'S','rhs':'(I-rho*W)**(-1) @ (beta1*I+beta5*W)',
        'variables':['S','W','rho','beta1','beta5'],
        'assumptions':{'S':'Real','W':'Real','rho':'Real','beta1':'Real','beta5':'Real'}})
    assert result.status.value == 'unsupported' and '未声明变量 I' in result.detail


def test_table_formula_preserves_explicit_delimiters_without_nesting():
    from src.publication.render_latex import _render_table
    formula = r'\(S=(I-\rho W)^{-1}(\beta_1 I+\beta_5 W)\)'
    tex = '\n'.join(_render_table({'header':['name','formula','scope','state'],
        'rows':[['S',formula,'conditional','unchecked']]}))
    assert r'\multicolumn{4}' in tex
    assert r'\displaystyle \(' not in tex and tex.count(r'\(') == tex.count(r'\)') == 1


def test_table_english_description_does_not_become_a_formula():
    from src.publication.render_latex import _render_table
    text = 'population = adult participants from the coastal region'
    tex = '\n'.join(_render_table({'header':['field','description'], 'rows':[['scope',text]]}))
    assert r'\multicolumn' not in tex and text in tex
