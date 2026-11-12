import time
import json
import pytest
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, TaskBudget, UsageRecord


def test_model_call_obeys_remaining_wall_budget():
    from src.agents.runtime import BudgetedLLM
    def hanging_call(*_args, **_kwargs):
        time.sleep(.25)
        return SimpleNamespace(content='late')
    model = BudgetedLLM(SimpleNamespace(invoke=hanging_call), UsageRecord(),
                        TaskBudget(max_wall_seconds=.03))
    started = time.monotonic()
    failed = False
    try:
        model.invoke('request')
    except TimeoutError:
        failed = True
    assert failed, 'A timed-out call must not deliver its late result'
    assert time.monotonic() - started < .2


def test_review_inherits_context_library_when_task_sources_are_empty():
    from src.agents.base import AgentBase
    from src.agents.runtime import AgentRuntime, ToolLoop, ToolSpec
    class Probe(AgentBase):
        role = 'review'
        def tools(self, *_):
            return [ToolSpec('search_local_kb', lambda query, topic='': topic)]
        def _run(self, task, context, runtime, usage, tools):
            observation = ToolLoop(tools)._execute(
                {'name': 'search_local_kb', 'args': {'query': 'mechanism', 'topic': ''}}, grant=None)
            assert observation.result == 'bound-library'
            return self.completed(task, 'ok')
    Probe().run(AgentTask(agent='review', objective='review', session_id='session'),
        ContextPack(sources=[{'source_set_id': 'bound-library'}]), AgentRuntime())


def test_review_revision_is_scheduled_before_repeated_research_needs():
    from src.agents.protocol import NeedKind, ResearchNeed
    from src.agents.supervisor import SupervisorAgent, TeamPlan, ResearchBrief
    brief = ResearchBrief(project_id='p', problem_id='q', main_question='Question')
    needs = [ResearchNeed(kind=NeedKind.more_sources, statement=f'evidence {i}',
                         hints={'trigger_id': str(i)}) for i in range(4)]
    needs.append(ResearchNeed(kind=NeedKind.manuscript_revision, statement='revise review',
                              hints={'review_cycle': True, 'review_issue_ids': ['issue']}))
    decision = SupervisorAgent().decide(brief=brief, plan=TeamPlan(brief_id=brief.brief_id),
                                       results={}, open_needs=needs)
    assert decision.tasks[0].agent == 'writing'


def test_connection_failures_are_shared_and_recovery_keeps_local_work(monkeypatch):
    from src.utils.network_budget import NetworkBudget, NetworkState, network_call, task_network_budget, NetworkUnavailable
    from src.utils import http_client
    calls, events = [], []
    def offline(*args, **kwargs):
        calls.append(args)
        raise ConnectionError('injected offline')
    monkeypatch.setattr(http_client, '_get_client', lambda: SimpleNamespace(request=offline))
    monkeypatch.setattr(http_client, 'retry_wait', lambda *_: None)
    state = NetworkState()
    budget = NetworkBudget(state=state, emit=lambda k, d: events.append((k, d)))
    with task_network_budget(budget):
        with pytest.raises(ConnectionError):
            http_client.get_with_retry('https://offline.invalid', max_retries=9)
        with pytest.raises(NetworkUnavailable):
            network_call(offline, phase='embedding')
    assert len(calls) == 3
    assert any(kind == 'network_unavailable' for kind, _ in events)
    state.unavailable_until = time.monotonic() - 1
    with task_network_budget(NetworkBudget(state=state, emit=lambda k, d: events.append((k, d)))):
        assert network_call(lambda: 'recovered', phase='llm') == 'recovered'
    assert any(kind == 'network_recovered' for kind, _ in events)
    http_client.reset_circuit_breaker()


def test_waiting_network_emits_heartbeat_and_discards_late_response():
    from src.utils.network_budget import NetworkBudget, network_call, task_network_budget
    events = []
    budget = NetworkBudget(seconds=.04, heartbeat_seconds=.008, emit=lambda k, d: events.append((k, d)))
    with task_network_budget(budget), pytest.raises(TimeoutError):
        network_call(lambda: time.sleep(.15), timeout=.08, phase='download')
    assert any(kind == 'task_heartbeat' and data['phase'] == 'download' for kind, data in events)
    assert any(kind == 'network_unavailable' for kind, _ in events)


def test_mechanism_support_is_scoped_and_cannot_close_parent_claim():
    from src.research.evidence import build_support_judge, grade_evidence
    from src.research.mechanisms import mechanism_links
    from src.research.schemas import Claim, SourceEvidence, ProofObligation
    from src.research.verification_service import _rule_verdict
    claim = Claim(statement='卫星覆盖扩大 → 船上通信改善 → 市场信息改善')
    link = mechanism_links(claim.statement)[1]
    quote = '船上通信改善可减少市场信息延迟。'
    response = {'relation': 'supports', 'quote': quote, 'reason': 'supports one link',
                'support_scope': 'mechanism_link', 'supported_link': link,
                'condition_notes': ['仅在用户实际采用通信终端时；未验证星网覆盖或最终渔业收益']}
    judge = build_support_judge(SimpleNamespace(invoke=lambda *_: SimpleNamespace(content=json.dumps(response))))
    evidence = SourceEvidence(title='ICT study', source_id='doc', excerpt=quote, location='p1',
                               credibility='high', existence_verified=True)
    assessed = judge(claim, evidence)
    assert assessed.support == 'partially_supports'
    assert assessed.supported_link == link and assessed.condition_notes
    assert grade_evidence([assessed]) == 'unsupported'
    assert not _rule_verdict(claim, ProofObligation(kind='evidence_support', statement='support'), [assessed])[0]
    response['condition_notes'] = []
    assert judge(claim, evidence.model_copy(deep=True)).support == 'insufficient'


def test_definition_identity_gets_verified_without_empirical_source():
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.schemas import Claim, ClaimType
    claim = Claim(statement='定义下的恒等式', claim_type='definitional',
        lhs='(x+1)**2', rhs='x**2+2*x+1', relation='==',
        variables=['x'], variable_domains={'x': 'real'}, study={'design': 'theory'})
    context = ContextPack(request='研究通信对产业的影响', objects={'claim': [claim.model_dump(mode='json')]})
    result = ReasoningAgent().run(AgentTask(agent='reasoning', objective='核验定义恒等式', source_policy='both',
        hints={'claim_id': claim.id}), context, AgentRuntime())
    assert result.outcome != 'blocked'
    assert any(change.kind == 'verification' for change in result.proposed_changes)
    # The exemption must not validate an empirical direction merely called a theory.
    claim.claim_type = ClaimType.causal
    context.objects['claim'] = [claim.model_dump(mode='json')]
    blocked = ReasoningAgent().run(AgentTask(agent='reasoning', objective='核验因果方向', source_policy='both',
        hints={'claim_id': claim.id}), context, AgentRuntime())
    assert blocked.outcome == 'blocked'


def test_low_review_dispatches_repair_or_reports_specific_limit():
    from src.agents.protocol import AgentResult, ReviewIssueRef
    from src.agents.supervisor import TeamPlan
    from src.graph.research_graph import TeamLoopState, _review_before_delivery
    result = AgentResult(task_id='review', agent='review', outcome='completed',
        payload={'score': 40, 'threshold': 80, 'accepted': False, 'semantic_reviewed': True},
        issues=[ReviewIssueRef(issue_id='issue', category='readability', severity='major', summary='讨论需调整')])
    state = TeamLoopState(plan=TeamPlan(brief_id='brief'), results={'review': result}, rounds=21, review_revisions=1)
    task, reason = _review_before_delivery(state, ['review'], 3, 24)
    assert task and task.agent == 'writing' and task.hints['review_cycle']
    state.rounds = 23
    task, reason = _review_before_delivery(state, ['review'], 3, 24)
    assert task is None and '轮次不足' in reason
    from src.agents.protocol import RunBudget
    state.rounds = 21
    task, reason = _review_before_delivery(state, ['review'], 3, 24,
        RunBudget(total=TaskBudget(max_llm_calls=10), allocated=TaskBudget(max_llm_calls=10)))
    assert task is None and '预算不足' in reason


def test_missing_block_locator_falls_back_to_one_section_only():
    from src.agents.writing import WritingAgent
    from src.publication.schemas import Manuscript, Section, Block, WritingPacket
    seen = []
    prior = Manuscript(title='Draft', sections=[Section(heading='方法', blocks=[Block(block_id='methods', text='keep')]),
        Section(heading='讨论', blocks=[Block(block_id='discussion', text='old')])])
    def invoke(messages):
        seen.append(messages[-1].content)
        return SimpleNamespace(content=json.dumps({'blocks': [{'block_id': 'discussion', 'text': 'revised'}]}))
    runtime = SimpleNamespace(llm_available=lambda *_: True, llm=lambda *_a, **_k: SimpleNamespace(invoke=invoke))
    packet = WritingPacket(prior_manuscript=prior.to_dict(), review_issues=[{'locator': '第2节', 'summary': '讨论需调整'}])
    revised, note = WritingAgent()._patch_with_llm(AgentTask(agent='writing', objective='修订'), runtime, UsageRecord(), packet)
    assert revised and revised.all_blocks()[0].text == 'keep'
    assert revised.all_blocks()[1].text == 'revised'
    assert note and revised.gaps[-1]['kind'] == 'revision_locator_fallback'
    assert 'methods' not in seen[0]


def test_model_schema_repair_runs_once_and_aliases_are_checked():
    from src.agents.modeling import ModelingAgent, validate_model_payload
    from src.research.schemas import ResearchModel
    calls = []
    fixed = {'name': 'model', 'variables': [{'symbol': 'x'}], 'source_ids': ['unregistered']}
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({'repairs': [{'index': 0, 'model': fixed}]}))
    runtime = SimpleNamespace(llm_available=lambda *_: True, llm=lambda *_a, **_k: SimpleNamespace(invoke=invoke),
                              emit=lambda *_: None)
    repaired = ModelingAgent().repair_schema_once([{'variables': [{'symbol': {}}]}],
        AgentTask(agent='modeling', objective='model'), runtime, UsageRecord(), set())
    assert len(calls) == 1
    canonical = ResearchModel.model_validate(repaired[0]).model_dump(mode='json')
    assert any('未登记来源' in problem for problem in validate_model_payload(canonical, known_source_ids=set()))


def test_existing_manuscript_does_not_turn_drafting_task_into_patch(monkeypatch):
    from src.agents.writing import WritingAgent
    from src.publication.schemas import Manuscript, Section, Block
    agent = WritingAgent()
    prior = Manuscript(manuscript_id='paper', title='Prior', sections=[Section(heading='方法', blocks=[Block(text='old')])])
    calls = []
    def draft(*args):
        calls.append('draft')
        return prior.model_copy(deep=True), ''
    def patch(*args):
        pytest.fail('An explicitly scheduled drafting task must not require review locators')
    monkeypatch.setattr(agent, '_draft_with_llm', draft)
    monkeypatch.setattr(agent, '_patch_with_llm', patch)
    monkeypatch.setattr(agent, 'audit_manuscript', lambda *_: [])
    result = agent._run(AgentTask(agent='writing', objective='把结论与依据写成连贯稿件'),
        ContextPack(objects={'manuscript': [prior.to_dict()]}),
        SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])
    assert calls == ['draft'] and result.outcome != 'blocked'
    assert result.payload['manuscript']['version'] == 2


def test_network_events_have_actionable_progress_text():
    from src.graph.node_progress import describe_node
    assert '本地资料' in describe_node('research_team', {'event': 'network_unavailable'})
    assert '等待' in describe_node('research_team', {'event': 'task_heartbeat', 'phase': 'http'})
    assert '恢复' in describe_node('research_team', {'event': 'network_recovered'})
