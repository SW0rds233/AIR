"""Regression cases taken from the 2026-11-13 22:42 research output."""
import json
from types import SimpleNamespace

import pytest

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.publication.render_latex import escape_latex
from src.research.schemas import Claim, ProofObligation


@pytest.mark.parametrize(('text', 'formula'), [
    (r'\Delta N 非负且取整数', r'\Delta N'),
    (r'\Delta N-\Delta N_{\deg}=0 意味着无净变化', r'\Delta N-\Delta N_{\deg}=0'),
    (r'\textbf{净增量 \Delta N} 与 $NB>0$ 不等价', r'\Delta N'),
    (r'比例 \frac{a_{i}}{b_i} 可测', r'\frac{a_{i}}{b_i}'),
])
def test_bare_math_commands_enter_one_balanced_math_span(text, formula):
    rendered = escape_latex(text)
    assert f'${formula}$' in rendered
    assert rendered.count('$') % 2 == 0
    assert '\x00' not in rendered


def test_definition_is_not_an_identity_between_independent_symbols():
    from src.research.theorist import _check_for
    claim = Claim(claim_type='definitional', statement='定义可接入性', relation='==',
        lhs='acc(v,t)', rhs='1[coverage(v,t)=1 ∧ R_avail(v,t) ≥ D(v,t)]',
        variables=['acc', 'coverage', 'R_avail', 'D', 'v', 't'])
    plan = _check_for(ProofObligation(kind='prove_identity', statement='核验'), claim, {'sympy': True})
    assert plan.operation == 'check_definition'


def test_failed_verification_preserves_diagnostic_without_becoming_stale():
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(statement='bad encoding', lhs='Σ_{i=1}^{n} x_i', rhs='0', variables=['x'])
    outcome = VerificationService(runner=VerificationRunner(inproc=True)).check_tool(
        claim, ProofObligation(kind='prove_identity', statement='核验'))
    assert not outcome.closed
    assert not outcome.record.stale
    assert outcome.record.detail == outcome.result.detail
    assert outcome.record.detail


def test_model_source_failure_gets_one_targeted_repair():
    from src.agents.modeling import ModelingAgent
    candidate = {'name': '候选采纳机制', 'mechanism': '收益与成本比较',
        'variables': [{'symbol': 'x', 'domain': 'real'}],
        'assumptions': [{'statement': '成本影响采纳', 'origin': 'external', 'source_ids': ['doc-b']}]}
    repaired = {**candidate, 'assumptions': [{'statement': '假设成本影响采纳，待观测检验',
        'origin': 'proposed', 'source_ids': []}], 'open_conditions': ['估计成本与采纳的关系']}
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({'models': [candidate]} if len(calls) == 1 else
            {'repairs': [{'index': 0, 'model': repaired}]}, ensure_ascii=False))
    runtime = SimpleNamespace(llm_available=lambda *_: True,
        llm=lambda *_a, **_kw: SimpleNamespace(invoke=invoke), emit=lambda *_: None)
    context = ContextPack(request='研究渔业影响', objects={'evidence': [
        {'id': 'ev-b', 'source_id': 'doc-b', 'relation': 'background',
         'excerpt': '技术背景', 'locator': '摘要'}]})
    result = ModelingAgent()._run(AgentTask(agent='modeling', objective='建模', source_policy='both'),
        context, runtime, UsageRecord(), [])
    assert len(calls) == 2
    assert '缺少支持关系' in calls[1][1].content
    assert len(result.proposed_changes) == 1
    assert result.proposed_changes[0].payload['assumptions'][0]['origin'] == 'proposed'


def test_not_written_as_a_theorem_is_not_a_proof_claim():
    from src.publication.claims import asserts_completed_proof
    assert not asserts_completed_proof('该结论不写作已证明的定理。')
    assert asserts_completed_proof('该结论不写作假设，而是我们已经证明的定理。')


def test_definition_encoding_pass_cannot_close_a_theorem_or_support_the_claim():
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    from src.research.problem_formulator import obligations_for_claim
    from src.research.acceptance import _aligned
    claim = Claim(claim_type='definitional', statement='定义可接入性及其待证性质', relation='==',
        lhs='acc(v,t)', rhs='1[coverage(v,t)=1 ∧ R_avail(v,t) ≥ D(v,t)]',
        variables=['acc', 'coverage', 'R_avail', 'D', 'v', 't'])
    service = VerificationService(runner=VerificationRunner(inproc=True))
    old = service.check_tool(claim, ProofObligation(statement='旧恒等式义务', kind='prove_identity'))
    assert old.result.status.value == 'passed' and old.record.scope.value == 'step'
    assert not old.closed and not old.record.usable
    assert not _aligned(old.record, claim)
    obligations = obligations_for_claim(claim, method_available={'sympy': True})
    definition = next(o for o in obligations if o.kind == 'check_definition')
    checked = service.check(claim, definition)
    assert checked.closed and checked.record.usable and not checked.record.stale
    assert checked.obligation_updates['coverage'].value == 'step'
    assert any(o.acceptance_method == 'manual' and o.required for o in obligations)


@pytest.mark.parametrize(('lhs', 'rhs', 'variables'), [
    ('f(t)', '1[g(t)>0]', ['f', 'g']),  # undeclared t, as in the actual output
    ('f(t)', 'g(t)+g(t,t)', ['f', 'g', 't']),
    ('x', 'x+1', ['x']),
    ('f(t,t)', 't', ['f', 't']),
    ('x', "__import__('os').getcwd()", ['x']),
    ('x', '[a for a in (1,2)]', ['x', 'a']),
    ('x', '1[4]', ['x']),
    ('x', '1[4 or 3]', ['x']),
])
def test_definition_checker_rejects_missing_symbols_arity_cycles_and_code(lhs, rhs, variables):
    from src.verification.definitions import check_definition
    result = check_definition({'lhs': lhs, 'rhs': rhs, 'variables': variables})
    assert result.status.value == 'unsupported' and result.scope == 'step'
    assert result.detail and not result.certificate


def test_source_repair_does_not_promote_unsupported_external_assumptions():
    from src.agents.modeling import validate_model_payload
    problems = validate_model_payload({'name': 'model', 'mechanism': 'cost',
        'assumptions': [{'origin': 'external', 'source_ids': ['background']} ]},
        known_source_ids={'background'}, supporting_source_ids=set())
    assert any('缺少支持关系' in p for p in problems)


def test_review_quotes_must_match_the_current_block():
    from src.agents.review import _review_grounding_conflicts
    context = ContextPack(objects={'manuscript': [{'sections': [{'blocks': [
        {'block_id': 'b', 'text': '行业研报未经核验，本文不将其作为前提。'}]}]}]})
    issue = {'block_id': 'b', 'quoted_text': '研报证明了本文前提。'}
    assert _review_grounding_conflicts({'issues': [issue]}, context)
    issue['quoted_text'] = '本文不将其作为前提'
    assert not _review_grounding_conflicts({'issues': [issue]}, context)


def test_unknown_text_macro_is_literal_and_existing_math_and_formatting_are_preserved():
    rendered = escape_latex(r'\textbf{结果}：$x_i$ 与 \inventedmacro{a}，\Delta N is a count.')
    assert r'\textbf{结果}' in rendered and '$x_i$' in rendered
    assert r'\textbackslash{}inventedmacro' in rendered
    assert '$\\Delta N$ is a count.' in rendered


def test_background_can_reach_conditional_modeling_instead_of_dead_ending_in_kernel():
    from src.agents.modeling import ModelingAgent
    claim = Claim(id='c', statement='研究渔船通信采纳的候选机制', claim_type='causal')
    context = ContextPack(request='研究渔船通信', objects={'claim': [claim.model_dump(mode='json')],
        'evidence': [{'id': 'e', 'source_id': 's', 'claim_id': 'c', 'relation': 'background',
                     'excerpt': '通信技术背景', 'locator': 'p1'}]})
    result = ModelingAgent().propose_via_kernel(AgentTask(agent='modeling', objective='建模'),
        context, SimpleNamespace(), UsageRecord())
    assert result is None  # Conditional LLM path; not a blocked kernel result.


def test_unanchored_review_cannot_be_approved_or_sent_to_writer(monkeypatch):
    from src.agents.review import ReviewAgent, REVIEW_CATEGORIES
    agent = ReviewAgent()
    payload = {'issues': [{'severity': 'major', 'category': 'citation', 'summary': '使用了研报作前提',
        'block_id': 'b', 'quoted_text': '研报证明了前提', 'suggested_owner': 'writing'}],
        'category_scores': {k: 100 for k in REVIEW_CATEGORIES}}
    encoded = json.dumps(payload)
    calls = []
    monkeypatch.setattr(agent, 'tool_loop', lambda *_a, **_kw: (encoded, []))
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=encoded)
    runtime = SimpleNamespace(llm_available=lambda *_: True,
        llm=lambda *_a, **_kw: SimpleNamespace(invoke=invoke))
    context = ContextPack(objects={'manuscript': [{'sections': [{'blocks': [
        {'block_id': 'b', 'text': '研报未经核验，不作为前提。'}]}]}]})
    result = agent._run(AgentTask(agent='review', objective='审阅'), context, runtime, UsageRecord(), [])
    assert len(calls) == 1
    assert not result.payload['semantic_reviewed'] and not result.payload['accepted']
    assert not result.proposed_changes
    assert all(n.kind.value != 'manuscript_revision' for n in result.followup_needs)
    assert any(n.kind.value == 'review' for n in result.followup_needs)


def test_commit_keeps_failure_current_but_unusable_and_preserves_reason():
    from src.research.commit import _apply_disposition, _disposition_for
    obligation = ProofObligation(statement='bad encoding', kind='prove_identity')
    result = {'status': 'unsupported', 'detail': '未声明符号 v'}
    record = _apply_disposition({'stale': False}, obligation,
                               _disposition_for(result, obligation), result)
    assert record['stale'] is False and record['usable'] is False
    assert record['detail'] == '未声明符号 v'


def test_compiler_retains_error_location_and_identifies_missing_runtime(tmp_path, monkeypatch):
    import subprocess
    from src.publication.compiler import compile_latex
    tex = tmp_path / 'paper.tex'
    tex.write_text('test', encoding='utf-8')
    monkeypatch.setattr('src.publication.compiler.subprocess.run', lambda *a, **k:
        subprocess.CompletedProcess(a, 1, '! Missing $ inserted.\n<inserted text>\n$\nl.155 \\Delta N', ''))
    ok, log = compile_latex(str(tex))
    assert not ok and 'l.155' in log
    monkeypatch.setattr('src.publication.compiler.subprocess.run', lambda *a, **k:
        subprocess.CompletedProcess(a, -1073741515, '', ''))
    ok, log = compile_latex(str(tex))
    assert not ok and 'DLL' in log and '0xC0000135' in log


def test_encoding_repair_changes_declarations_instead_of_repeating_failed_kernel(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    original = Claim(id='c', statement='定义可接入性', claim_type='definitional', relation='==',
        lhs='acc(v,t)', rhs='1[coverage(v,t)=1]', variables=['acc', 'coverage'])
    changed = original.model_dump(mode='json')
    changed['variables'] += ['v', 't']
    agent = ReasoningAgent()
    def never_repeat(*args):
        pytest.fail('Must repair encoding before retrying the tool')
    monkeypatch.setattr(agent, 'formal_closure', never_repeat)
    prompts = []
    def repair(*args, **kwargs):
        prompts.append(args[4][1].content)
        return json.dumps({'claims': [changed]}), []
    monkeypatch.setattr(agent, 'tool_loop', repair)
    task = AgentTask(agent='reasoning', objective='修复符号声明', hints={'claim_id': 'c',
        'verification_status': 'unsupported', 'verification_detail': '未声明符号: v'})
    result = agent._run(task, ContextPack(objects={'claim': [original.model_dump(mode='json')]}),
        SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])
    assert '未声明符号: v' in prompts[0]
    proposal = next(p for p in result.proposed_changes if p.kind == 'claim')
    assert proposal.object_id == 'c' and proposal.payload['version'] == 2
    assert proposal.payload['variables'] == ['acc', 'coverage', 'v', 't']
    assert any(n.kind.value == 'derivation' for n in result.followup_needs)
