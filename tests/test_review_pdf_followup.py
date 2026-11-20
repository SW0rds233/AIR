"""Regressions from the November 19 evening research run."""
from src.publication.render_latex import _display_math
from src.research.acceptance import delivery_gate
from src.research.schemas import Claim, ProofObligation, ResearchSnapshot
from src.verification.z3_adapter import op_check_implication
import pytest


def test_bare_display_rows_get_a_real_multiline_environment():
    formula=r'E^{*}=\left(\frac{\beta p a X^{\gamma}}{c}\right)^{1/(1-\beta)},\qquad \frac{\partial\ln E^{*}}{\partial\ln a}=\frac{1}{1-\beta}>1,\qquad 0<\beta<1\\[4pt] B^{*}=\frac{c}{p a},\qquad a\equiv q'
    rendered=_display_math(formula)
    assert r'\begin{aligned}' in rendered or r'\begin{gathered}' in rendered
    assert 'B^{*}' in rendered and r'\frac{1}{1-\beta}>1' in rendered


def test_delivery_does_not_reopen_old_version_obligations():
    claim=Claim(id='clm-current',version=2,statement='当前版本候选',claim_type='descriptive')
    old=ProofObligation(id='obl-old',statement='旧版本证明',claim_id=claim.id,claim_version=1,status='blocked',required=True)
    snapshot=ResearchSnapshot(project_id='p',problem_id='q',claims=[claim],obligations=[old],
                              writing_map={claim.id:'blk-current'})
    result=delivery_gate('待核验条件关系及研究局限。'*30,snapshot)
    assert not any('obl-old' in reason for reason in result.reasons)
    assert not result.passed, 'Removing stale obligations must not upgrade an unverified claim'


def test_polynomial_powers_do_not_require_an_llm_encoding_repair():
    result=op_check_implication({'variables':{'x':'Real'},'premises':['x > 0'],
                                 'conclusion':'x**2 > 0'})
    assert result.status.value=='passed', result.detail


def test_power_premise_can_be_used_without_dropping_it():
    args={'variables':{name:'Real' for name in ['beta','a','c','p','X','gamma','E','DE','ED']},
        'premises':['0 < beta','beta < 1','a > 0','c > 0','p > 0','X > 0','gamma > 0','E > 0',
                    'beta*p*a*E**(beta-1)*X**gamma-c == 0','a*(1-beta)*DE-E == 0','ED == a*DE/E'],
        'conclusion':'ED == 1/(1-beta)'}
    result=op_check_implication(args)
    assert result.status.value=='passed', result.detail
    contradictory={**args,'premises':[*args['premises'],'c < 0']}
    assert op_check_implication(contradictory).status.value!='passed'


def test_missing_citation_repair_keeps_the_uncited_target_source():
    from types import SimpleNamespace
    from src.graph.research_graph import TeamRun
    from src.agents.protocol import AgentTask
    source={'id':'doc-missing','source_id':'doc-missing','excerpt':'可补引的原文'}
    rows={'evidence':[{'id':f'doc-unrelated-{i}'} for i in range(60)]+[source],
          'review_issue':[{'issue_id':'missing','category':'citation','summary':'补齐缺少的参考文献',
                           'affected_refs':[{'id':'doc-missing'}]}]}
    run=TeamRun.__new__(TeamRun)
    run.projection=SimpleNamespace(rows=lambda kind:rows.get(kind,[]))
    run._cached_default_builder=None
    run._source_set_rows=[]
    run.outcome=SimpleNamespace(brief=None)
    run.request='研究'
    run.attachments=[]
    pack=run._default_context_builder()(AgentTask(agent='writing',objective='补引',
        hints={'review_issue_ids':['missing']}),{})
    assert source in pack.objects['evidence']


def test_writer_does_not_receive_stale_failed_proofs():
    from src.agents.protocol import AgentTask,ContextPack
    from src.agents.writing import build_packet_from_context
    packet=build_packet_from_context(AgentTask(agent='writing',objective='修订'),ContextPack(objects={
        'claim':[{'id':'c','version':2}],
        'verification':[{'id':'old','claim_id':'c','claim_version':1,'status':'unsupported'},
                        {'id':'new','claim_id':'c','claim_version':2,'status':'passed'}],
        'obligation':[{'id':'old-ob','claim_id':'c','claim_version':1,'status':'blocked'}]}))
    assert [v['id'] for v in packet.verifications]==['new']
    assert not packet.obligations


def test_symbol_accusation_must_match_the_current_formula():
    from src.agents.protocol import ContextPack
    from src.agents.review import _review_grounding_conflicts
    formula=r'r_i=g\left(P_{\mathrm{cov},i},\alpha_i,p_{\mathrm{term}}\right)'
    context=ContextPack(objects={'manuscript':[{'sections':[{'blocks':[
        {'block_id':'blk-symbol','math':formula}]}]}]})
    issue={'category':'readability','summary':'符号a混用','affected_ids':['blk-symbol'],
        'quoted_text':formula,'detail':"公式仍写'r_i=g(P_cov,i, a_i, p_term)'，与文字声明不一致"}
    assert _review_grounding_conflicts({'issues':[issue]},context)
    issue['detail']='应明确 alpha_i 的取值域'
    assert not _review_grounding_conflicts({'issues':[issue]},context)


def test_journal_language_is_not_evidence_of_invalid_publication():
    from src.agents.protocol import ContextPack
    from src.agents.review import _review_source_conflicts
    context=ContextPack(objects={'evidence':[{'id':'doc-bilingual',
        'publication_source_url':'https://api.crossref.org/works/example','venue':'English Journal'}]})
    issue={'category':'citation','summary':'期刊信息存在异常','affected_ids':['doc-bilingual'],
           'detail':'期刊名称中英文混杂，英文刊名与中文内容不匹配，需确认是否正规'}
    assert _review_source_conflicts({'issues':[issue]},context)
    issue['summary']='正文介绍海陆研究却没有引用来源'
    issue['detail']='缺少正文引文与条目'
    assert not _review_source_conflicts({'issues':[issue]},context)


@pytest.mark.parametrize('premises,conclusion',[
    (['x > 0','x**y == -1'],'x == x'),  # Positive real power cannot be negative.
    (['x > 0','y > 0','x**y == 2','x**(2*y) == 5'],'x == x'),
    (['x**y == 1'],'x == x'),  # Unspecified base domain is not silently strengthened.
    (['x > 0','y > 0','x**y > 0'],'x > 100'),
    (['x > 0','x**100000 == 1'],'x == 1'),
])
def test_power_relaxation_never_turns_inconsistency_or_sampling_into_proof(premises,conclusion):
    result=op_check_implication({'variables':{'x':'Real','y':'Real'},
                                 'premises':premises,'conclusion':conclusion,'timeout_ms':500})
    assert result.status.value not in {'passed','failed'}


def test_nested_multiline_environments_and_tags_are_preserved():
    formula=r'\begin{cases}x&x>0\\0&x\leq0\end{cases}'
    assert _display_math(formula)==formula
    rendered=_display_math(r'x=1\\[4pt]y=2\tag{A}')
    assert rendered.count(r'\begin{aligned}')==1
    assert rendered.endswith(r'\end{aligned}\tag{A}')


def test_disclosed_pending_claim_stays_unresolved_without_a_false_assertion_error():
    from src.publication.schemas import Block
    from src.research.schemas import ObjectRef
    claim=Claim(id='clm-pending',statement='候选条件关系',status='blocked')
    obligation=ProofObligation(id='obl-pending',statement='核验该条件关系',claim_id=claim.id,status='blocked')
    snapshot=ResearchSnapshot(project_id='p',claims=[claim],obligations=[obligation],
                              writing_map={claim.id:'block:b'})
    block=Block(block_id='b',text='待核验的候选关系，尚未完成独立核验。',needs_check=True)
    block.add_ref('claim',ObjectRef(id=claim.id))
    result=delivery_gate(block.text*20,snapshot,blocks=[block])
    assert not result.passed and any('obl-pending' in note for note in result.unresolved)
    assert not any('结论被写入正文' in note for note in result.reasons)
    block.text='我们已经证明该关系成立。另有候选机制待核验。'
    result=delivery_gate(block.text*20,snapshot,blocks=[block])
    assert any('结论被写入正文' in note for note in result.reasons)


def test_power_certificate_closes_only_the_current_obligation():
    from src.research.problem_formulator import obligations_for_claim
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim=Claim(id='clm-power',version=2,statement='在所列幂前提下 y=1',claim_type='descriptive',
        study={'design':'theory'},formal_spec={'premises':['x > 0','x**z > 0','y == 1'],
        'conclusion':'y == 1','variables':{'x':'Real','z':'Real','y':'Real'}})
    obligation=obligations_for_claim(claim)[0]
    outcome=VerificationService(runner=VerificationRunner(inproc=True)).check(claim,obligation)
    assert outcome.closed and outcome.record.claim_version==2
    assert outcome.record.arguments['premises']==claim.formal_spec.premises
    assert 'premise_witness' in outcome.result.certificate


def test_power_fallback_still_rejects_unsafe_syntax_as_a_structured_result():
    result = op_check_implication({'variables': {'x': 'Real', 'y': 'Real'},
        'premises': ['x > 0', 'x**y > 0', 'unknown_function(x) == 1'],
        'conclusion': 'x == x'})
    assert result.status.value == 'unsupported'
