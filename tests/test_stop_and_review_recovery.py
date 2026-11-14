"""Regressions from the interrupted 2026-11-14 10:28 run."""
import json
from types import SimpleNamespace
import pytest

from src.agents.protocol import AgentTask, ContextPack, UsageRecord


def test_stop_reaches_active_team_cancel_token():
    from src.sessions.controller import Session
    from src.agents.runtime import CancelToken
    from src.graph.team_session import TeamApp
    token = CancelToken()
    session = Session('stop-test', app=TeamApp(SimpleNamespace(team=SimpleNamespace(cancel=token))))
    session.request_stop()
    assert token.cancelled, 'UI stop must reach the same token checked by remote calls'


def test_legacy_review_quote_is_grounded_in_current_block():
    from src.agents.review import _review_grounding_conflicts
    context = ContextPack(objects={'manuscript':[{'sections':[{'blocks':[
        {'block_id':'blk-current','text':'本研究仅提出条件推论，尚未进行实证检验。'}]}]}]})
    issue = {'category':'science', 'affected_ids':['blk-current'],
             'detail':'稿件 blk-current 称“本研究仅提出条件推论”，应补充适用条件。',
             'locator':'blk-current'}
    assert not _review_grounding_conflicts({'issues':[issue]}, context)
    issue['detail'] = '稿件 blk-current 称“已经完成实证检验”，应修改。'
    assert _review_grounding_conflicts({'issues':[issue]}, context)
    issue.update(affected_ids=['blk-removed'], locator='blk-removed', detail='旧段落需要修订')
    assert _review_grounding_conflicts({'issues':[issue]}, context)


def test_valid_review_findings_survive_one_invalid_anchor(monkeypatch):
    from src.agents.review import ReviewAgent, REVIEW_CATEGORIES
    payload = {'issues':[
        {'category':'readability','severity':'major','summary':'解释条件范围',
         'block_id':'blk-current','quoted_text':'仅提出条件推论','suggested_owner':'writing',
         'acceptance':['交代条件范围']},
        {'category':'science','severity':'major','summary':'旧版段落',
         'block_id':'blk-missing','quoted_text':'不存在的句子','suggested_owner':'writing'}],
        'category_scores':{category:80 for category in REVIEW_CATEGORIES}}
    agent = ReviewAgent()
    encoded = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(agent,'tool_loop',lambda *a,**k:(encoded,[]))
    runtime = SimpleNamespace(llm_available=lambda *_:True,
        llm=lambda *a,**k:SimpleNamespace(invoke=lambda *_:SimpleNamespace(content=encoded)))
    result = agent._run(AgentTask(agent='review', objective='审阅'),
        ContextPack(objects={'manuscript':[{'sections':[{'blocks':[
            {'block_id':'blk-current','text':'本研究仅提出条件推论。'}]}]}]}), runtime, UsageRecord(), [])
    assert result.payload['semantic_reviewed']
    assert not result.payload['accepted'], 'Unresolved coverage must never silently approve a paper'
    assert result.payload['category_scores']
    assert any(n.kind.value == 'manuscript_revision' for n in result.followup_needs)
    assert len(result.issues) == 1


def test_new_encoded_goal_is_verified_before_first_draft(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.supervisor import SupervisorAgent, ResearchBrief, TeamPlan
    claim = {'id':'conditional','statement':'条件推论：p<=1 且 d<c 时 p*d<c',
        'claim_type':'descriptive','study':{'design':'theory'},'variables':['p','d','c'],
        'formal_spec':{'premises':['p>0','p<=1','d>0','d<c'],
                       'conclusion':'p*d<c','variables':{'p':'Real','d':'Real','c':'Real'}}}
    agent = ReasoningAgent()
    monkeypatch.setattr(agent, 'tool_loop', lambda *a,**k:(json.dumps({'claims':[claim]}),[]))
    task = AgentTask(agent='reasoning',objective='综合渔业机制')
    result = agent._run(task, ContextPack(request='研究渔业影响'),
        SimpleNamespace(llm_available=lambda *_:True), UsageRecord(), [])
    plan = TeamPlan()
    plan.add_task(task)
    writer = AgentTask(agent='writing',objective='形成初稿')
    plan.add_task(writer)
    plan.edges[writer.task_id] = [task.task_id]
    decision = SupervisorAgent().decide(brief=ResearchBrief(main_question='研究渔业影响'),
        plan=plan,results={task.task_id:result},open_needs=result.followup_needs)
    assert decision.tasks and decision.tasks[0].agent == 'reasoning'


def test_stop_interrupts_network_and_exports_committed_draft(tmp_path, monkeypatch):
    import threading
    import time
    from pathlib import Path
    from src import config, server
    from src.sessions.controller import Session
    from src.graph.research_graph import TeamRun, TeamLoopState
    from src.graph.team_session import TeamApp, TeamSession
    from src.agents.runtime import AgentRuntime, InProcessExecutor
    from src.agents.supervisor import SupervisorAgent, ResearchBrief, TeamPlan
    from src.agents.protocol import AgentResult
    from src.research.task_store import TaskStore
    from src.publication.schemas import Manuscript, Section, Block
    from src.utils.network_budget import network_call
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(server, '_persist_session', lambda *_:None)
    monkeypatch.setattr(server, '_bind_stdout_bridge', lambda *_:None)
    monkeypatch.setattr(server, '_unbind_stdout_bridge', lambda:None)
    entered, release = threading.Event(), threading.Event()
    calls = []
    class WaitingAgent:
        role = 'evidence'
        def run(self, task, context, runtime):
            calls.append(task.task_id)
            def remote_read():
                entered.set()
                release.wait(5)
                return 'late response'
            network_call(remote_read, phase='llm')
            return AgentResult(task_id=task.task_id, agent='evidence', outcome='completed')
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(WaitingAgent())
    store = TaskStore('p', db_path=str(tmp_path/'research.sqlite'))
    team = TeamRun(project_id='p', problem_id='q', run_id='r', runtime=runtime, executor=executor,
                   task_store=store, supervisor=SupervisorAgent())
    plan = TeamPlan()
    for i in range(2):
        plan.add_task(AgentTask(agent='evidence', objective=f'慢查询{i}'))
    team.loop = TeamLoopState(brief=ResearchBrief(main_question='研究', project_id='p',problem_id='q'),plan=plan)
    team.outcome.plan = plan
    draft = Manuscript(title='部分稿件',sections=[Section(heading='结果',blocks=[Block(text='已保存的条件推论。')])])
    store.store.put('manuscript',draft.manuscript_id,{**draft.to_dict(),
        '_scope':{'project_id':'p','problem_id':'q','run_id':'r'}})
    from src.publication.manuscript_state import manuscript_identity
    team.loop.results['old-review'] = AgentResult(task_id='old-review',agent='review',outcome='completed',
        payload={'semantic_reviewed':True,'accepted':True,'reviewed_manuscript':manuscript_identity(draft.to_dict())})
    team.outcome.task_order = ['old-review']
    session = Session('stop-flow',app=None,run_id='r')
    session.request = {'project_id':'p','problem_id':'q'}
    bridge = TeamSession(team,emit=session.emit)
    monkeypatch.setattr(bridge,'_compile_package',lambda *_: (_ for _ in ()).throw(AssertionError('stop must not compile')))
    session.app = TeamApp(bridge)
    worker = threading.Thread(target=server._run_session,args=(session,{}),daemon=True)
    try:
        worker.start()
        assert entered.wait(3), session.error or team.outcome.stop_reason
        started = time.monotonic()
        session.request_stop()
        worker.join(3)
        assert not worker.is_alive() and time.monotonic()-started < 2
        assert len(calls) == 1, 'No second task may start after cancellation'
        assert session.status == 'stopped' and team.outcome.status == 'stopped'
        package = Path(session.final_state['package_dir'])
        assert '已保存的条件推论' in (package/'manuscript.md').read_text('utf-8')
        manifest = json.loads((package/'manifest.json').read_text('utf-8'))
        assert manifest['run_status'] == 'stopped' and manifest['partial']
        assert not (package/'manuscript.pdf').exists()
        assert store.store.get('team_run','r')['status'] == 'stopped'
        assert team.reopen_deferred()
        assert not team.cancel.cancelled
        assert calls[0] in team.loop.results and team.loop.results[calls[0]].pending_resume
    finally:
        release.set()
        worker.join(3)
        store.close()


def test_invalid_review_retries_once_then_stops_research():
    from src.graph.research_graph import _review_recovery, TeamLoopState
    from src.agents.protocol import AgentResult
    from src.publication.manuscript_state import manuscript_identity
    paper = {'manuscript_id':'m','sections':[{'blocks':[{'text':'当前稿件'}]}]}
    state = TeamLoopState(results={'w':AgentResult(task_id='w', agent='writing', outcome='completed',
                                                 payload={'manuscript':paper})})
    order = ['w']
    for i in range(2):
        tid = f'review-{i}'
        state.results[tid] = AgentResult(task_id=tid,agent='review',outcome='partial',
            payload={'semantic_reviewed':False,'reviewed_manuscript':manuscript_identity(paper)})
        order.append(tid)
        retry, reason = _review_recovery(state,order,paper)
        assert bool(retry) == (i == 0)
        assert bool(reason) == (i == 1)


def test_writer_receives_actual_model_equations_and_direct_paths():
    from src.agents.writing import _section_material
    from src.publication.schemas import WritingPacket
    packet = WritingPacket(models=[{'id':'m','name':'中介模型',
        'formal_encoding':'I = d1*C + d2*S; G = e1*C + e2*S',
        'units':{'S':'指数'},'boundaries':['顺序可忽略性尚未验证']}])
    text = _section_material(packet,'method',[])
    assert 'd2*S' in text and 'e2*S' in text
    assert '顺序可忽略性尚未验证' in text


def test_unfinished_run_recovers_as_interrupted_without_replaying_finished_tasks(tmp_path):
    from src.research.task_store import TaskStore
    from src.graph.research_graph import TeamRun, TeamLoopState
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent, assignment_key
    from src.agents.protocol import AgentResult
    store = TaskStore('p',db_path=str(tmp_path/'research.sqlite'))
    try:
        team = TeamRun(project_id='p',problem_id='q',run_id='r',task_store=store,supervisor=SupervisorAgent())
        plan = TeamPlan()
        done = AgentTask(agent='reasoning',objective='已提交推导')
        waiting = AgentTask(agent='evidence',objective='中断的检索')
        plan.add_task(done)
        plan.add_task(waiting)
        brief = ResearchBrief(main_question='研究',project_id='p',problem_id='q')
        team.loop = TeamLoopState(brief=brief,plan=plan,rounds=4,
            results={done.task_id:AgentResult(task_id=done.task_id,agent='reasoning',outcome='completed')},
            dispatched={assignment_key(brief,t.agent,t.objective) for t in [done,waiting]})
        team.save_state(status='running')
        restored = TeamRun(project_id='p',problem_id='q',run_id='r',task_store=store,supervisor=SupervisorAgent())
        assert restored.restore_state()
        assert restored.outcome.status == 'interrupted'
        assert store.store.get('team_run','r')['status'] == 'interrupted'
        assert set(restored.loop.results) == {done.task_id}
        assert restored.loop.pending_retries == {assignment_key(brief,waiting.agent,waiting.objective)}
    finally:
        store.close()


def test_review_object_identity_does_not_change_with_locator_wording():
    from src.agents.review import _issue_from, _review_object_id, _reuse_review_ids
    task = AgentTask(agent='review',objective='复审',project_id='p',problem_id='q',run_id='r')
    first = _issue_from({'category':'science','summary':'模型方程中的直接路径不一致',
                         'affected_ids':['blk-x'],'locator':'段落一'})
    old_id = _review_object_id(first,task,ContextPack())
    old = {**first.to_dict(),'id':old_id}
    second = _issue_from({'category':'science','summary':'模型方程中的直接路径存在不一致',
                          'affected_ids':['blk-x'],'locator':'方法部分'})
    context = ContextPack(objects={'review_issue':[old]})
    issue, = _reuse_review_ids([second],context)
    assert issue.issue_id == first.issue_id
    assert _review_object_id(issue,task,context) == old_id


def test_review_quote_survives_serialization():
    from src.agents.protocol import ReviewIssueRef
    issue = ReviewIssueRef(issue_id='quote',summary='定位意见',quoted_text='当前稿件原句')
    assert issue.to_dict()['quoted_text'] == '当前稿件原句'


@pytest.mark.parametrize('status', ['stopped', 'done'])
def test_resume_replaces_stopped_session_but_reuses_live_worker(monkeypatch, status):
    from src import server
    from src.sessions.controller import Session
    previous = Session('resume-thread', app=None)
    previous.status = status
    fresh = Session('resume-thread', app=None)
    started = []
    class Worker:
        def __init__(self, **kwargs):
            pass
        def start(self):
            started.append(True)
        def is_alive(self):
            return True
    monkeypatch.setattr(server, 'SESSIONS', {'resume-thread':previous})
    monkeypatch.setattr(server, 'load_conversation', lambda _: {'thread_id':'resume-thread'})
    monkeypatch.setattr(server, '_make_checkpointer', lambda _: (None, None))
    monkeypatch.setattr(server, '_make_session', lambda *a, **k: fresh)
    monkeypatch.setattr(server.threading, 'Thread', Worker)
    server.resume_session('conversation')
    assert server.SESSIONS['resume-thread'] is fresh
    assert started == [True]
    server.resume_session('conversation')
    assert started == [True], 'A live worker must never be started twice'


def test_only_source_metadata_is_exempt_from_prose_quotes():
    from src.agents.review import _review_grounding_conflicts
    context = ContextPack(objects={'evidence':[{'source_id':'doc-x'}], 'manuscript':[{
        'sections':[{'blocks':[{'block_id':'blk-x','text':'本文未采用该文献证明效果。'}]}]}]})
    issue = {'category':'citation','summary':'文献的 DOI 缺失',
             'affected_refs':[{'id':'doc-x'},{'id':'blk-x'}]}
    assert not _review_grounding_conflicts({'issues':[issue]},context)
    issue['summary'] = '文献被用于证明效果'
    assert _review_grounding_conflicts({'issues':[issue]},context)


def test_compound_definition_is_not_refuted_by_assignments_ignoring_its_premises():
    from src.research.schemas import Claim
    from src.research.problem_formulator import obligations_for_claim
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(statement='定义合规净收益差 Nc-Nv', statement_role='definition',
        lhs='Nc-Nv',rhs='p*Delta-(cc-cv)',relation='==',study={'design':'theory'},
        premises=['Nc=F-cc','Nv=F-p*Delta-cv'],variables=['Nc','Nv','F','p','Delta','cc','cv'])
    obligation, = obligations_for_claim(claim,{'sympy':True,'z3':True})
    service = VerificationService(runner=VerificationRunner(inproc=True))
    outcome = service.check(claim,obligation)
    assert not outcome.closed and outcome.obligation_updates['status'].value == 'blocked'
    assert outcome.result is None, 'Unconstrained assignments cannot refute a definition-dependent claim'
    repaired = Claim(statement='在净收益定义下，两者之差等于合规激励指标',claim_type='descriptive',
        study={'design':'theory'},formal_spec={'premises':['Nc == F-cc','Nv == F-p*Delta-cv'],
            'conclusion':'Nc-Nv == p*Delta-(cc-cv)', 'variables':{v:'Real' for v in claim.variables}})
    obligation, = obligations_for_claim(repaired,{'z3':True})
    assert service.check(repaired,obligation).closed
