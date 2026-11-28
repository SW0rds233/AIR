"""Regressions from the 2026-11-14 20:06 research run."""
import json
import threading

import pytest

from src.agents.protocol import AgentResult, AgentTask


def run_reasoning(monkeypatch, payload, runtime=None):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.protocol import ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime
    runtime = runtime or AgentRuntime()
    monkeypatch.setattr(runtime, 'llm_available', lambda *a: True)
    agent = ReasoningAgent()
    monkeypatch.setattr(agent, 'tool_loop', lambda *a, **k: (payload, []))
    task = AgentTask(agent='reasoning', objective='研究海上通信的影响', project_id='p', problem_id='q', run_id='r')
    return task, agent._run(task, ContextPack(request=task.objective), runtime, UsageRecord(), [])


def test_conditional_claim_survives_structured_mechanism_and_applied_topic(tmp_path, monkeypatch):
    from src.research.store import ResearchStore
    from src.research.commit import ResearchCommitService
    candidate = {'statement': '若 p=k*m 且 k≥0，则检测强度随覆盖单调不减',
        'claim_type': 'associational', 'study': {'design': 'theory'},
        'mechanism_links': [{'from': '监测覆盖', 'to': '检测强度', 'conditions': ['k >= 0']}],
        'formal_spec': {'premises': ['m1<=m2', 'k>=0', 'p1==k*m1', 'p2==k*m2'],
                        'conclusion': 'p1<=p2', 'variables': {v: 'Real' for v in ['m1','m2','k','p1','p2']}}}
    task, result = run_reasoning(monkeypatch, json.dumps({'claims': [candidate]}, ensure_ascii=False))
    store = ResearchStore('p', db_path=str(tmp_path/'r.sqlite'))
    try:
        committed = ResearchCommitService(store).commit(task, result)
        assert committed.committed, committed.rejected
        claim, = store.list_latest('claim')
        assert claim['claim_type'] == 'descriptive'
        assert '监测覆盖' in claim['mechanism_links'][0]
        assert store.list_latest('obligation')[0]['kind'] == 'check_implication'
        from src.agents.reasoning import ReasoningAgent
        from src.agents.runtime import AgentRuntime
        from src.agents.protocol import ContextPack, UsageRecord
        from src.research.verification_service import VerificationService
        from src.verification.runner import VerificationRunner
        runtime = AgentRuntime()
        service = VerificationService(store=store, runner=VerificationRunner(inproc=True))
        monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k: service)
        proof_task = AgentTask(agent='reasoning', objective='核验条件命题', project_id='p', problem_id='q', run_id='r',
                              hints={'verification_only': True, 'claim_id': claim['id']})
        proof = ReasoningAgent()._run(proof_task, ContextPack(objects={'claim': [claim],
            'obligation': store.list_latest('obligation')}), runtime, UsageRecord(), [])
        assert any(p.kind == 'verification' for p in proof.proposed_changes)
        assert ResearchCommitService(store).commit(proof_task, proof).committed
        assert store.list_latest('claim')[0]['status'] == 'supported'
    finally:
        store.close()


def test_malformed_reasoning_gets_one_structured_repair(monkeypatch):
    from types import SimpleNamespace
    from src.agents.runtime import AgentRuntime
    runtime = AgentRuntime()
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({'claims': [{'statement': '在明确假设下讨论覆盖变化',
                                                              'claim_type': 'descriptive'}]}))
    monkeypatch.setattr(runtime, 'llm', lambda *a, **k: SimpleNamespace(invoke=invoke))
    _, result = run_reasoning(monkeypatch, '{"claims": [', runtime)
    assert len(calls) == 1
    assert any(p.kind == 'claim' for p in result.proposed_changes)


def test_invalid_claim_does_not_schedule_phantom_verification(monkeypatch):
    task, result = run_reasoning(monkeypatch, json.dumps({'claims': [{
        'statement': '无效域的候选', 'study': {'design': 'theory'},
        'formal_spec': {'premises': [], 'conclusion': 'x>=0', 'variables': {'x': 'Complex'}}}]}))
    assert not any(n.hints.get('verification_only') for n in result.followup_needs)
    assert result.unresolved


def test_theory_label_does_not_convert_empirical_causality_to_a_theorem(monkeypatch):
    _, result = run_reasoning(monkeypatch, json.dumps({'claims': [{
        'statement': '星网建设造成沿海渔业净收益增加', 'claim_type': 'causal', 'study': {'design':'theory'},
        'formal_spec': {'premises':['x>0'], 'conclusion':'x>=0', 'variables':{'x':'Real'}}}]}))
    assert not any(p.kind == 'claim' for p in result.proposed_changes)
    assert not any(n.hints.get('verification_only') for n in result.followup_needs)


def test_unencoded_naming_definition_is_disclosure_not_theorem():
    from src.research.schemas import Claim
    from src.research.problem_formulator import obligations_for_claim
    from src.verification.definitions import is_notation_definition
    c = Claim(statement='定义：把影响命名为潜在结果差分 Δ_eff := E[Y(1) − Y(0)]（Y 为结果向量），并命名指标映射 M。',
              statement_role='definition', variables=['Δ_eff','Y(1)','Y(0)','M'], study={'design':'theory'})
    assert is_notation_definition(c)
    assert not any(o.required for o in obligations_for_claim(c))
    assert not is_notation_definition(c.model_copy(update={'statement': c.statement + ' 因此效应显著增加。'}))
    assert not is_notation_definition(c.model_copy(update={'statement_role': 'assertion'}))


def test_incomplete_review_preserves_last_semantic_report(tmp_path, monkeypatch):
    from src import config
    from src.research.progress import write_run_progress
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr('src.research.progress.object_rows', lambda *a, **k: [])
    task = AgentTask(agent='review', objective='审阅')
    complete = {'semantic_reviewed': True, 'score': 59, 'issues': [{'issue_id': 'issue-1'}],
                'reviewed_manuscript': {'content_hash': 'v1'}}
    root = write_run_progress(None, project_id='p', problem_id='q', run_id='r', task=task,
        result=AgentResult(task_id=task.task_id, agent='review', payload=complete))
    incomplete = {'semantic_reviewed': False, 'score': 0, 'issues': [],
                  'reviewed_manuscript': {'content_hash': 'v2'}, 'parse_note': 'timeout'}
    write_run_progress(None, project_id='p', problem_id='q', run_id='r', task=task,
        result=AgentResult(task_id=task.task_id, agent='review', outcome='partial', payload=incomplete))
    assert json.loads((root/'review-report.json').read_text('utf-8')) == complete
    assert json.loads((root/'review-attempt.json').read_text('utf-8')) == incomplete


def test_model_call_timeout_does_not_mark_network_unavailable():
    from src.utils.network_budget import NetworkBudget, TaskDeadlineExceeded, model_call, task_network_budget
    events = []
    budget = NetworkBudget(seconds=10, emit=lambda kind, data: events.append(kind))
    release = threading.Event()
    try:
        with task_network_budget(budget), pytest.raises(TaskDeadlineExceeded):
            model_call(lambda: release.wait(2), timeout=.01, phase='llm')
    finally:
        release.set()
    assert 'network_unavailable' not in events
    assert 'network_error' not in events
    assert budget.state.unavailable_until == 0
    assert budget.state.failures == 0


def test_review_timeout_retries_once_with_compact_materials(monkeypatch):
    from types import SimpleNamespace
    from src.agents.review import ReviewAgent, REVIEW_CATEGORIES
    from src.agents.protocol import ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime
    from src.utils.network_budget import ModelCallTimeout
    agent, runtime = ReviewAgent(), AgentRuntime()
    monkeypatch.setattr(runtime, 'llm_available', lambda *a: True)
    def timed_out(*args, **kwargs):
        raise ModelCallTimeout('llm call deadline')
    monkeypatch.setattr(agent, 'tool_loop', timed_out)
    messages_seen = []
    def invoke(messages):
        messages_seen.append(messages)
        return SimpleNamespace(content=json.dumps({'issues': [],
            'category_scores': {c: 90 for c in REVIEW_CATEGORIES}}))
    monkeypatch.setattr(runtime, 'llm', lambda *a, **k: SimpleNamespace(invoke=invoke))
    manuscript = {'manuscript_id': 'm', 'sections': [{'blocks': [{'block_id': 'b', 'text': '稿件末尾必须审阅'}]}]}
    result = agent._run(AgentTask(agent='review', objective='审阅'), ContextPack(objects={
        'manuscript': [manuscript], 'evidence': [{'id': 'uncited', 'title': 'UNRELATED_SOURCE'}]}), runtime, UsageRecord(), [])
    assert len(messages_seen) == 1
    assert result.payload['semantic_reviewed']
    prompt = str(messages_seen[0][-1].content)
    assert '稿件末尾必须审阅' in prompt and 'UNRELATED_SOURCE' not in prompt


def test_failed_latest_review_does_not_make_old_draft_current():
    from src.graph.research_graph import _review_matches_manuscript
    from src.publication.manuscript_state import manuscript_identity
    old = {'manuscript_id': 'm', 'sections': [{'blocks': [{'text':'旧稿'}]}]}
    new = {'manuscript_id': 'm', 'sections': [{'blocks': [{'text':'修订稿'}]}]}
    results = {'old': AgentResult(task_id='old', agent='review', payload={
        'semantic_reviewed': True, 'reviewed_manuscript': manuscript_identity(old)}),
        'bad': AgentResult(task_id='bad', agent='review', payload={
        'semantic_reviewed': False, 'reviewed_manuscript': manuscript_identity(new)})}
    assert _review_matches_manuscript(results, ['old','bad'], old)
    assert not _review_matches_manuscript(results, ['old','bad'], new)


def test_delivery_retains_previous_score_and_issues_but_marks_revision_unreviewed(monkeypatch):
    from types import SimpleNamespace
    from src.graph.research_graph import TeamRun
    from src.publication.manuscript_state import manuscript_identity
    from src.research.delivery import DeliveryAssessment
    old = {'manuscript_id': 'm', 'sections': [{'blocks': [{'text':'旧稿'}]}]}
    new = {'manuscript_id': 'm', 'sections': [{'blocks': [{'text':'修订稿'}]}]}
    results = {'old': AgentResult(task_id='old', agent='review', payload={
        'semantic_reviewed': True, 'score': 59, 'decision': 'major_revision', 'issues': [{'issue_id':'i'}],
        'category_scores': {'science': 60}, 'model_attempts': 2, 'grounding_conflicts': ['旧稿引文待核实'],
        'validation_details': {'score_adjustments': {'science': 60}},
        'reviewed_manuscript': manuscript_identity(old)}),
        'bad': AgentResult(task_id='bad', agent='review', payload={
        'semantic_reviewed': False, 'score': 0, 'recovery_required':'审阅超时后重试失败',
        'reviewed_manuscript': manuscript_identity(new)})}
    monkeypatch.setattr('src.research.delivery.assess_delivery', lambda *a, **k: DeliveryAssessment())
    monkeypatch.setattr('src.research.snapshot.manuscript_markdown', lambda *a, **k: '稿件')
    fake = SimpleNamespace(task_store=SimpleNamespace(store=None), project_id='p', problem_id='q', run_id='r',
        review_threshold=80, loop=SimpleNamespace(results=results),
        runtime=SimpleNamespace(emit=lambda *a: None), _latest_manuscript=lambda: new,
        outcome=SimpleNamespace(brief=SimpleNamespace(deliverables=['full_paper']), task_order=['old','bad'],
                                unresolved_report={}, stop_reason=''))
    TeamRun._apply_delivery_assessment(fake)
    review = fake.outcome.delivery['review']
    assert review['score'] == 59 and review['issues'] == [{'issue_id':'i'}]
    for field in ('category_scores', 'model_attempts', 'grounding_conflicts', 'validation_details'):
        assert review.get(field) == results['old'].payload[field]
    assert review['latest_attempt_incomplete'] and not review['current']
    assert review['recovery_required'] == '审阅超时后重试失败'
    assert not fake.outcome.delivery['accepted']


def test_compact_review_keeps_full_body_and_cited_sources():
    from src.agents.review import _review_prompt
    from src.agents.protocol import ContextPack
    context = ContextPack(objects={'manuscript': [{'manuscript_id':'m', 'sections':[{'blocks':[
        {'block_id':'b', 'text':'正文' * 21000 + '结尾检查', 'refs':[{'id':'src'}], 'ref_kinds':['source']}]}]}],
        'evidence': [{'id':'src', 'title':'CITED_SOURCE'}, {'id':'noise','title':'UNRELATED_SOURCE'}]})
    prompt = _review_prompt(context, compact=True)
    assert '结尾检查' in prompt and 'CITED_SOURCE' in prompt
    assert 'UNRELATED_SOURCE' not in prompt


def test_long_section_title_is_preserved_without_running_heading():
    from src.publication.schemas import Manuscript, Section
    from src.publication.render_latex import render_latex
    title = '候选机制与可检验命题：覆盖—安全、作业半径、MCS、数据服务、成本分化与治理约束'
    tex = render_latex(Manuscript(title='论文', sections=[Section(heading=title)]))
    assert r'\pagestyle{plain}' in tex
    assert r'\section{' + title + '}' in tex


def test_review_omits_unreferenced_models_but_keeps_claim_dependencies():
    from src.agents.review import _review_materials
    from src.agents.protocol import ContextPack
    context = ContextPack(objects={'manuscript':[{'sections':[{'blocks':[
        {'refs':[{'id':'used'}], 'ref_kinds':['model']}]}]}],
        'claim':[{'id':'c', 'premises':['采用 mdl-indirect 的变量定义']}],
        'model':[{'id':'used'}, {'id':'mdl-indirect'}, {'id':'unused'}]})
    material = json.loads(_review_materials(context).split('\n',1)[1])
    assert {m['id'] for m in material['models']} == {'used', 'mdl-indirect'}
    assert material['omitted_unreferenced_models'] == 1
