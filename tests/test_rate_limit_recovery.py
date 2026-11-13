"""The 19:50 run must not turn literature API throttling into a run-wide freeze."""
from types import SimpleNamespace

import httpx
import pytest


def test_literature_429_does_not_defer_model_calls_or_other_hosts(monkeypatch):
    from src.utils import http_client
    from src.utils.network_budget import NetworkBudget, NetworkState, network_call, task_network_budget
    http_client.reset_circuit_breaker()
    calls, events = [], []
    def request(method, url, **kwargs):
        calls.append(url)
        return httpx.Response(200 if 'healthy' in url else 429,
            headers={'Retry-After': '120'}, request=httpx.Request(method, url))
    monkeypatch.setattr(http_client, '_get_client', lambda: SimpleNamespace(request=request))
    monkeypatch.setattr('src.utils.network_budget.time.sleep', lambda *_: None)
    state = NetworkState()
    budget = NetworkBudget(state=state, emit=lambda k, d: events.append((k, d)))
    with task_network_budget(budget):
        for host in ('crossref.invalid', 'openalex.invalid'):
            with pytest.raises(httpx.HTTPStatusError):
                http_client.get_with_retry('https://' + host, max_retries=3)
        assert network_call(lambda: 'model still works', phase='llm') == 'model still works'
        assert http_client.get_with_retry('https://healthy.invalid').status_code == 200
    assert state.unavailable_until == 0 and budget.retry_attempts == 0
    assert len(calls) == 3
    assert [d['host'] for k, d in events if k == 'http_rate_limited'] == ['crossref.invalid', 'openalex.invalid']
    assert not any(k == 'network_unavailable' for k, _ in events)
    http_client.reset_circuit_breaker()


def test_deferred_roles_stop_as_recoverable_instead_of_delivering_or_spinning():
    from src.agents.protocol import AgentResult, AgentTask, ResearchNeed
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
    brief = ResearchBrief(main_question='Research fisheries', deliverables=['full_paper'])
    plan = TeamPlan()
    results = {}
    for role in ('evidence', 'modeling', 'reasoning', 'validation', 'writing', 'review', 'figures'):
        task = AgentTask(agent=role, objective=role)
        plan.add_task(task)
        results[task.task_id] = AgentResult(task_id=task.task_id, agent=role,
            outcome='partial', payload={'deferred': True, 'pending_resume': True})
    for needs in ([], [ResearchNeed(kind='review', statement='Retry review')]):
        decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results, open_needs=needs)
        assert decision.decision.value == 'stop_with_report'
        assert '可恢复' in decision.reason
        assert not decision.tasks


def test_rate_limit_cooldown_is_respected_then_host_recovers(monkeypatch):
    import time
    from src.utils import http_client
    http_client.reset_circuit_breaker()
    calls = []
    def request(method, url, **_):
        calls.append(url)
        return httpx.Response(429 if len(calls) == 1 else 200, headers={'Retry-After': '120'},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(http_client, '_get_client', lambda: SimpleNamespace(request=request))
    url = 'https://limited.invalid'
    assert http_client.get_with_retry(url, raise_on_status=False).status_code == 429
    assert http_client.circuit_remaining_cooldown(url) > 119
    with pytest.raises(http_client.CircuitBreakerOpenError):
        http_client.get_with_retry(url)
    assert len(calls) == 1
    http_client._breaker._open_until['limited.invalid'] = time.monotonic() - 1
    assert http_client.get_with_retry(url).status_code == 200
    assert not http_client.is_circuit_open(url)
    http_client.reset_circuit_breaker()


def test_retry_after_http_date_and_bad_header():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    from src.utils.http_client import _retry_after
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert 58 <= _retry_after(httpx.Response(429, headers={'Retry-After': value})) <= 60
    assert _retry_after(httpx.Response(429, headers={'Retry-After': 'invalid'})) >= 4


def test_explicit_resume_retries_original_task_before_new_work():
    from src.agents.protocol import AgentTask, AgentResult
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent
    from src.graph.research_graph import TeamRun, TeamLoopState, TeamRunOutcome
    brief = ResearchBrief(main_question='Research', deliverables=['full_paper'])
    done = AgentTask(agent='evidence', objective='collect')
    pending = AgentTask(agent='reasoning', objective='derive', depends_on=[done.task_id])
    writer = AgentTask(agent='writing', objective='write', depends_on=[pending.task_id])
    plan = TeamPlan()
    for task in (done, pending, writer):
        plan.add_task(task)
    results = {
        done.task_id: AgentResult(task_id=done.task_id, agent='evidence'),
        pending.task_id: AgentResult(task_id=pending.task_id, agent='reasoning', outcome='partial',
                                    payload={'deferred': True})}
    run = TeamRun.__new__(TeamRun)
    run.loop = TeamLoopState(brief=brief, plan=plan, results=results, finished=True, rounds=3)
    run.outcome = TeamRunOutcome(run_id='run')
    run.max_rounds = 24
    run.runtime = SimpleNamespace(emit=lambda *_: None)
    saved = []
    run.save_state = lambda **kw: saved.append(kw)
    assert run.reopen_deferred() and not run.loop.finished and saved
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results,
                                        pending_retries=run.loop.pending_retries)
    assert [t.task_id for t in decision.tasks] == [pending.task_id]
    assert decision.tasks[0].attempt == 2
    results[pending.task_id] = AgentResult(task_id=pending.task_id, agent='reasoning')
    assert not run.reopen_deferred()


def test_finish_persists_the_same_reason_it_reports():
    from src.agents.protocol import UsageRecord
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent
    from src.graph.research_graph import TeamRun, TeamLoopState, TeamRunOutcome
    run = TeamRun.__new__(TeamRun)
    run.loop = TeamLoopState(brief=ResearchBrief(main_question='Research'), plan=TeamPlan())
    run.outcome = TeamRunOutcome(run_id='run')
    run.run_id = 'run'
    run.supervisor = SupervisorAgent()
    run._supervisor_usage = UsageRecord()
    events = []
    run.runtime = SimpleNamespace(emit=lambda k, d: events.append((k, d)))
    run.save_state = lambda **_: None
    def assess():
        run.outcome.stop_reason += ' | paper remains unreviewed'
    run._apply_delivery_assessment = assess
    run._finish(run.loop, 'recoverable network interruption', status='partial')
    assert events[-1][1]['stop_reason'] == run.loop.stop_reason == run.outcome.stop_reason


@pytest.mark.parametrize('resuming', [False, True])
def test_first_deferred_task_stops_batch_without_consuming_another_revision(resuming):
    from src.agents.protocol import AgentResult, AgentTask, ResearchNeed
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent, SupervisorDecision
    from src.graph.research_graph import TeamRun, TeamLoopState, TeamRunOutcome
    task = AgentTask(agent='writing', objective='revise', hints={'review_cycle': True})
    later = AgentTask(agent='review', objective='review')
    deferred = AgentResult(task_id=task.task_id, agent='writing', outcome='partial',
        payload={'deferred': True}, followup_needs=[ResearchNeed(kind='review', statement='retry')])
    plan = TeamPlan()
    plan.add_task(task)
    run = TeamRun.__new__(TeamRun)
    run.loop = TeamLoopState(brief=ResearchBrief(main_question='Research'), plan=plan,
        review_revisions=3 if resuming else 0,
        results={task.task_id: deferred} if resuming else {})
    run.outcome = TeamRunOutcome(run_id='run')
    run.prepare = lambda: None
    run.cancel = SimpleNamespace(cancelled=False)
    run.runtime = SimpleNamespace(run_budget=None, emit=lambda *_: None)
    run.max_rounds, run.max_review_revisions = 24, 3
    run.supervisor = SimpleNamespace(decide=lambda **_: SupervisorDecision(
        decision='dispatch', tasks=[task, later]), replan=SupervisorAgent().replan)
    executed = []
    run._execute_one = lambda task, _: (executed.append(task.task_id), deferred)[1]
    run._finish = lambda state, reason, **_: setattr(state, 'stop_reason', reason)
    assert not run.step()
    assert executed == [task.task_id]
    assert '可恢复' in run.loop.stop_reason and not run.loop.open_needs
    assert run.loop.review_revisions == (3 if resuming else 1)


def test_resumed_attempt_preserves_usage_and_model_attempts():
    from src.agents.protocol import AgentResult, AgentTask, ContextPack, UsageRecord
    from src.graph.research_graph import TeamRun, TeamRunOutcome
    task = AgentTask(agent='reasoning', objective='derive', attempt=2)
    prior = AgentResult(task_id=task.task_id, agent=task.agent, outcome='partial',
        payload={'deferred': True, 'model_attempts': 4},
        usage=UsageRecord(llm_calls=2, input_tokens=100, cost_usd=0.1))
    fresh = AgentResult(task_id=task.task_id, agent=task.agent,
        payload={'model_attempts': 3}, usage=UsageRecord(llm_calls=3, input_tokens=200, cost_usd=0.2))
    run = TeamRun.__new__(TeamRun)
    run.run_id, run.project_id, run.problem_id, run.session_id = 'run', 'p', 'q', ''
    run.source_set_ids, run.source_policy = [], 'both'
    run.outcome = TeamRunOutcome(run_id='run')
    run._build_context = lambda *_: ContextPack()
    run.task_store = SimpleNamespace(create=lambda *_a, **_k: None, next_attempt=lambda _: 2)
    run.executor = SimpleNamespace(execute=lambda *_: fresh)
    persisted = []
    run._persist_result = lambda task, result: persisted.append(result)
    result = run._execute_one(task, {task.task_id: prior})
    assert result.usage.llm_calls == 5 and result.usage.input_tokens == 300
    assert result.usage.cost_usd == 0.3 and result.payload['model_attempts'] == 7
    assert not result.pending_resume and persisted == [result]
    assert prior.usage.llm_calls == 2


def test_post_export_assessment_replaces_stale_compilation_state():
    from src.graph.team_session import TeamSession
    from src.graph.research_graph import TeamRunOutcome
    old = '尚未尝试编译 .tex'
    outcome = TeamRunOutcome(run_id='run', status='partial',
        stop_reason='网络或等待预算受限（可恢复） | 交付门槛未通过: ' + old,
        delivery={'blocking': [old]}, unresolved_report={'unresolved': [old, '缺少实测数据']})
    events, saved = [], []
    session = TeamSession.__new__(TeamSession)
    session.team = SimpleNamespace(outcome=outcome, loop=SimpleNamespace(stop_reason=''),
        save_state=lambda **kw: saved.append(kw),
        runtime=SimpleNamespace(emit=lambda k, d: events.append((k, d))))
    final = {'level': '论文草稿', 'accepted': False, 'blocking': ['最后一版未审阅'],
             'publication': {'accepted': True}, 'unresolved': []}
    session._publish_final_delivery(final)
    assert outcome.delivery == outcome.unresolved_report['delivery'] == final
    assert old not in outcome.stop_reason and '可恢复' in outcome.stop_reason
    assert outcome.unresolved_report['unresolved'] == ['缺少实测数据', '最后一版未审阅']
    assert session.team.loop.stop_reason == outcome.stop_reason and saved
    assert events[-1][1]['phase'] == 'after_export'
    assert events[-1][1]['publication']['accepted']


@pytest.mark.parametrize('model_limit', [False, True])
def test_executor_distinguishes_hard_cap_from_resumable_network_failure(model_limit):
    from src.agents.protocol import AgentTask
    from src.agents.runtime import AgentRuntime, InProcessExecutor
    from src.utils.network_budget import current_budget, model_call
    runtime = AgentRuntime()
    runtime.network_state.evidence_model_attempts = 150 if model_limit else 0
    class Probe:
        role = 'evidence'
        def run(self, task, context, runtime):
            if not model_limit:
                current_budget().defer('llm', cooldown=True)
            model_call(lambda: pytest.fail('Must not reach the SDK'), timeout=1, phase='llm')
    executor = InProcessExecutor(runtime)
    executor.register(Probe())
    result = executor.execute(AgentTask(agent='evidence', objective='collect'))
    assert result.outcome.value == 'partial' and result.payload['deferred']
    assert result.pending_resume is not model_limit
