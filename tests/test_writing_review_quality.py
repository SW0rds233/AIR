"""Regressions from the actual 2026-11-13 20:38 manuscript and review."""
import pytest

from src.agents.protocol import ContextPack, ObjectRef
from src.agents.review import deterministic_issues, _overclaims
from src.agents.writing import apply_block_patch, finalise_manuscript
from src.publication.claims import asserts_completed_proof
from src.publication.schemas import Block, Manuscript, Section, WritingPacket


@pytest.mark.parametrize('text', [
    '故按条件性推论陈述，不作为已证明定理；其中情景取值只用于展示符号方向，不是经验事实。',
    '本轮只能列为待核验推导，不能作为已证明定理使用。',
    '本结论不视为已证明定理。',
    '本结论不算作已证明定理。',
    'We have not proved this claim.',
])
def test_explicit_proof_disclaimers_are_not_scientific_overclaims(text):
    assert not asserts_completed_proof(text)
    assert not _overclaims(text)
    paper = Manuscript(sections=[Section(blocks=[Block(text=text)])])
    assert not any(i.category == 'science' for i in deterministic_issues(
        ContextPack(objects={'manuscript': [paper.to_dict()]})))


@pytest.mark.parametrize('text', ['尚未核查来源，但我们证明该结论成立。', '我们已证明该命题。'])
def test_real_proof_claims_remain_flagged(text):
    assert asserts_completed_proof(text) and _overclaims(text)


def test_mixed_patch_keeps_valid_edit_and_reports_dropped_neighbors():
    paper = Manuscript(sections=[Section(blocks=[Block(block_id='target', text='old'),
                                               Block(block_id='neighbor', text='keep')])])
    patched = apply_block_patch(paper, [
        {'block_id': 'neighbor', 'text': 'unauthorized'},
        {'block_id': 'target', 'text': 'new', 'comment': 'extra metadata'},
        {'block_id': 'target', 'text': 'duplicate'},
    ], {'target'})
    assert [b.text for b in patched.all_blocks()] == ['new', 'keep']
    assert paper.all_blocks()[0].text == 'old'
    diagnostics = patched.revision_log[-1]['dropped_blocks']
    assert {d['block_id'] for d in diagnostics} == {'neighbor', 'target'}


def test_valid_bibliography_does_not_create_false_missing_number_gap():
    from src.agents.writing import _reference_numbers
    paper = Manuscript(sections=[Section(heading='参考文献', role='references',
        blocks=[Block(text='[1] An actual reference.'), Block(text='[2] Another reference.')])])
    assert _reference_numbers(paper) == {1, 2}


def test_verified_identity_needs_certificate_not_empirical_citation():
    claim = {'id': 'identity', 'version': 2, 'claim_type': 'definitional', 'relation': '==',
             'statement': 'x+x=2*x', 'lhs': 'x+x', 'rhs': '2*x', 'variables': ['x'],
             'status': 'supported', 'support_refs': [], 'coverage': 'target',
             'verification_scope': 'target', 'validation_status': 'verified',
             'support_kind': 'symbolic_check', 'assurance': 'symbolic_checked'}
    verification = {'claim_id': 'identity', 'claim_version': 2, 'status': 'passed',
                    'id': 'certificate', 'tool': 'sympy', 'usable': True,
                    'arguments': {'lhs': 'x+x', 'rhs': '2*x', 'variables': ['x']}}
    context = ContextPack(objects={'claim': [claim], 'verification': [verification]})
    assert not deterministic_issues(context)
    for modification in ({'claim_version': 1}, {'status': 'unsupported'}, {'usable': False},
                         {'arguments': {'lhs':'x+1', 'rhs':'2*x'}}):
        context.objects['verification'] = [{**verification, **modification}]
        assert any(i.blocking for i in deterministic_issues(context))
    context.objects['verification'] = [verification]
    context.objects['claim'] = [{**claim, 'claim_type': 'causal'}]
    assert any(i.blocking for i in deterministic_issues(context))


def test_review_currency_tracks_actual_content_and_ignores_failed_revision():
    from src.agents.protocol import AgentResult
    from src.graph.research_graph import _review_matches_manuscript, _final_review_task
    from src.publication.manuscript_state import manuscript_identity
    manuscript = Manuscript(manuscript_id='paper', sections=[Section(
        blocks=[Block(block_id='b', text='Original argument.')])]).to_dict()
    results = {
        'w': AgentResult(task_id='w', agent='writing', payload={'manuscript': manuscript}),
        'r': AgentResult(task_id='r', agent='review', payload={'semantic_reviewed': True,
            'reviewed_manuscript': manuscript_identity(manuscript)}),
        'blocked': AgentResult(task_id='blocked', agent='writing', outcome='blocked'),
    }
    order = ['w', 'r', 'blocked']
    assert _review_matches_manuscript(results, order, manuscript)
    assert _final_review_task(results, order, manuscript) is None
    manuscript['version'] += 1  # Updating diagnostics alone does not change the paper.
    assert _review_matches_manuscript(results, order, manuscript)
    manuscript['sections'][0]['blocks'][0]['text'] = 'A changed argument.'
    assert not _review_matches_manuscript(results, order, manuscript)
    assert _final_review_task(results, order, manuscript).agent == 'review'
    # Legacy reviews still ignore blocked writing tasks.
    results['r'].payload.pop('reviewed_manuscript')
    assert _review_matches_manuscript(results, order)


def test_patch_retries_invalid_output_once_without_exposing_neighbor_ids():
    import json
    from types import SimpleNamespace
    from src.agents.protocol import AgentTask, UsageRecord
    from src.agents.writing import WritingAgent
    prior = Manuscript(sections=[Section(heading='Methods', blocks=[
        Block(block_id='target', text='old'), Block(block_id='neighbor', text='keep')])])
    messages = []
    def invoke(prompt):
        messages.append(prompt)
        block = {'block_id': 'neighbor' if len(messages) == 1 else 'target', 'text': 'new'}
        return SimpleNamespace(content=json.dumps({'blocks': [block]}))
    runtime = SimpleNamespace(llm_available=lambda *_: True,
        llm=lambda *_a, **_k: SimpleNamespace(invoke=invoke))
    revised, note = WritingAgent()._patch_with_llm(
        AgentTask(agent='writing', objective='修订', hints={'focus_blocks': ['target']}),
        runtime, UsageRecord(), WritingPacket(prior_manuscript=prior.to_dict()))
    assert len(messages) == 2 and revised is not None, note
    assert 'neighbor' not in messages[0][1].content
    assert [b.text for b in revised.all_blocks()] == ['new', 'keep']
    assert revised.revision_log[-1]['changed_blocks'] == ['target']


def test_invalid_citation_drops_its_whole_block_but_keeps_other_valid_edits():
    prior = Manuscript(sections=[Section(blocks=[Block(block_id='a', text='A'), Block(block_id='b', text='B')])])
    changed = apply_block_patch(prior, [{'block_id': 'a', 'text': 'bad', 'source_ids': ['invented']},
                                      {'block_id': 'b', 'text': 'correct'}], {'a', 'b'}, source_ids=set())
    assert [b.text for b in changed.all_blocks()] == ['A', 'correct']
    assert '引用' in changed.revision_log[-1]['dropped_blocks'][0]['reason']


def test_review_records_manuscript_and_scopes_source_judgements(monkeypatch):
    import json
    from types import SimpleNamespace
    from src.agents.protocol import AgentTask, UsageRecord
    from src.agents.review import ReviewAgent, REVIEW_CATEGORIES, _review_materials
    paper = Manuscript(manuscript_id='paper', version=3, sections=[Section(blocks=[Block(block_id='b',
        text='A background statement.', refs=[ObjectRef(id='source')], ref_kinds=['source'])])])
    context = ContextPack(objects={'manuscript': [paper.to_dict()], 'evidence': [
        {'source_id': 'source', 'claim_id': 'different-claim', 'support_scope': 'claim',
         'support_reason': 'Does not prove a different claim.'}]})
    material = _review_materials(context)
    source = json.loads(material[material.index('{'):])['sources'][0]
    assert source['claim_id'] == 'different-claim' and source['cited_by_blocks'] == ['b']
    agent = ReviewAgent()
    monkeypatch.setattr(agent, 'tool_loop', lambda *_a, **_k: (json.dumps({
        'issues': [], 'category_scores': {k: 100 for k in REVIEW_CATEGORIES}}), []))
    result = agent._run(AgentTask(agent='review', objective='审阅'), context,
        SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])
    identity = result.payload['reviewed_manuscript']
    assert identity['manuscript_id'] == 'paper' and identity['version'] == 3 and identity['content_hash']


def test_geometric_identity_accepts_math_power_notation():
    from src.verification.sympy_adapter import run
    result = run('prove_identity', {'lhs': '2*pi*R^2*h/(R+h)',
        'rhs': '4*pi*R^2*(h/(2*(R+h)))', 'variables': ['R', 'h'],
        'assumptions': {'R': 'positive', 'h': 'positive'}})
    assert result.status.value == 'passed', result.detail


@pytest.mark.parametrize('expression', ["__import__('os').getcwd()^2", 'x.__class__^2', '[x^2 for x in (1,2)]'])
def test_power_normalization_does_not_bypass_expression_safety(expression):
    from src.verification.sympy_adapter import run
    assert run('prove_identity', {'lhs': expression, 'rhs': '0', 'variables': ['x']}).status.value == 'unsupported'


def test_citation_recheck_removes_stale_gap_but_keeps_real_missing_reference():
    source = {'source_id': 'source', 'title': 'Study', 'authors': ['A Author'], 'year': '2025',
              'venue': 'Journal', 'publication_type': 'journal-article', 'relation': 'background',
              'publication_status': 'published', 'publication_verified_by': 'crossref'}
    block = Block(text='Background [1].', refs=[ObjectRef(id='source')], ref_kinds=['source'])
    paper = Manuscript(sections=[Section(heading='Background', blocks=[block])],
        gaps=[{'kind': 'unresolved_citation', 'detail': 'old false gap'}])
    packet = WritingPacket(sources=[source])
    finalise_manuscript(paper, packet)
    assert not any(g['kind'] == 'unresolved_citation' for g in paper.gaps)
    paper.sections[0].blocks[0].text += ' Missing source [2].'
    finalise_manuscript(paper, packet)
    gap = next(g for g in paper.gaps if g['kind'] == 'unresolved_citation')
    assert '[2]' in gap['detail'] and '[1' not in gap['detail']
