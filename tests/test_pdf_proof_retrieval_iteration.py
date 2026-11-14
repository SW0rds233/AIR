"""Regressions from the 2026-11-14 11:46 research run."""
import subprocess
from pathlib import Path

import pytest

from src.agents.protocol import AgentResult, AgentTask, ChangeProposal, ResearchNeed
from src.research.schemas import Claim


def conditional_claim(cid='bounded', version=1):
    return Claim(id=cid, version=version, statement='在归一化非负权重下，加权平均不超过上界',
        claim_type='descriptive', study={'design':'theory'},
        formal_spec={'premises':['w1>=0','w2>=0','w1+w2==1','d1<=dmax','d2<=dmax'],
            'conclusion':'w1*d1+w2*d2<=dmax',
            'variables':{v:'Real' for v in ['w1','w2','d1','d2','dmax']}})


def test_intervals_are_not_citations_but_real_citations_remain():
    from src.research.acceptance import _cited_numbers
    assert _cited_numbers('w_k∈[0,1] 且 x∈[2,7]，参见文献[3,4]、[6–8]。') == {3,4,6,7,8}
    assert _cited_numbers('置信区间 [1,9]；结果见[2]；编号[0]不是有效引用。') == {2}
    assert _cited_numbers(r'权重 $w\in[0,1]$；\cite{ref2,ref5}') == {2,5}


@pytest.mark.parametrize('text', ['Σ_{k∈K} w_k=1', 'Σ_{k∈K}^{n} w_k=1', '$Σ_{k∈K} w_k=1$', r'\(Σ_{k∈K} w_k=1\)'])
def test_every_math_entry_normalizes_nested_unicode(text):
    from src.publication.render_latex import escape_latex
    rendered = escape_latex(text)
    assert '∈' not in rendered and 'Σ' not in rendered
    assert r'\in' in rendered and r'\Sigma' in rendered
    assert '\x00' not in rendered


def test_missing_glyph_in_engine_log_is_not_success(tmp_path, monkeypatch):
    from src.publication.compiler import compile_latex, layout_diagnostics
    tex = tmp_path/'paper.tex'
    tex.write_text('fixture', encoding='utf-8')
    diagnostic = 'Missing character: There is no ∈ in font [lmroman8-regular]!'
    def run(args, **kwargs):
        out = tmp_path if '-output-directory' not in args else Path(args[args.index('-output-directory')+1])
        (out/'paper.pdf').write_bytes(b'%PDF-1.7\npartial')
        (out/'paper.log').write_text(diagnostic, encoding='utf-8')
        return subprocess.CompletedProcess(args, 0, stdout='Output written', stderr='')
    monkeypatch.setattr('src.publication.compiler.subprocess.run', run)
    monkeypatch.setattr('src.publication.compiler.time.sleep', lambda *_:None)
    ok, log = compile_latex(str(tex))
    assert not ok and 'Missing character' in log
    assert layout_diagnostics(diagnostic)['severity'] == 'error'


def test_formal_claim_commit_registers_versioned_obligation_atomically(tmp_path):
    from src.research.store import ResearchStore
    from src.research.commit import ResearchCommitService
    store = ResearchStore('p', db_path=str(tmp_path/'research.sqlite'))
    try:
        claim = conditional_claim()
        task = AgentTask(agent='reasoning', objective='推导', project_id='p', problem_id='q', run_id='r')
        result = AgentResult(task_id=task.task_id, agent='reasoning', outcome='completed', proposed_changes=[
            ChangeProposal(kind='claim', object_id=claim.id, payload=claim.model_dump(mode='json'))])
        service = ResearchCommitService(store)
        assert service.commit(task, result).committed
        obligations = store.list_latest('obligation')
        assert len(obligations) == 1
        assert obligations[0]['kind'] == 'check_implication'
        assert obligations[0]['claim_version'] == 1
        assert obligations[0]['_scope']['run_id'] == 'r'
        service.commit(task, result)
        assert len(store.list_latest('obligation')) == 1
        revised = conditional_claim(version=2)
        next_task = AgentTask(agent='reasoning', objective='修订前提', project_id='p',problem_id='q',run_id='r')
        service.commit(next_task, AgentResult(task_id=next_task.task_id, agent='reasoning', proposed_changes=[
            ChangeProposal(kind='claim', object_id=revised.id, payload=revised.model_dump(mode='json'))]))
        assert {row['claim_version'] for row in store.list_latest('obligation')} == {1,2}
    finally:
        store.close()


def test_new_encoded_claim_after_initial_verification_is_not_starved():
    from src.agents.supervisor import SupervisorAgent, ResearchBrief, TeamPlan
    plan = TeamPlan()
    old = AgentTask(agent='reasoning', objective='核验定义', hints={'verification_only':True,'claim_id':'old'})
    plan.add_task(old)
    writer = AgentTask(agent='writing', objective='整合新增定理')
    plan.add_task(writer)
    results = {old.task_id:AgentResult(task_id=old.task_id, agent='reasoning', outcome='completed')}
    need = ResearchNeed(kind='derivation', statement='核验后续条件命题',
        hints={'verification_only':True,'claim_id':'new','root_claim_id':'new','trigger_id':'proof:new:v1'})
    decision = SupervisorAgent().decide(brief=ResearchBrief(main_question='应用研究'), plan=plan,
        results=results, open_needs=[need])
    assert decision.tasks and decision.tasks[0].hints.get('claim_id') == 'new'


@pytest.mark.parametrize('premises,conclusion,status', [
    (['x>0','x<=0'], 'False', 'passed'),
    (['x>0'], 'False', 'failed'),
    (['x>0','x<=0'], 'x==7', 'unknown'),
])
def test_explicit_unsatisfiability_goal_is_distinct_from_vacuous_proof(premises, conclusion, status):
    from src.verification.z3_adapter import op_check_implication
    result = op_check_implication({'premises':premises, 'conclusion':conclusion, 'variables':{'x':'Real'}})
    assert result.status.value == status


def test_latex_notation_does_not_become_a_failed_theorem():
    from src.research.problem_formulator import obligations_for_claim
    from src.research.acceptance import theory_validity_gate
    claim = Claim(statement='定义渠道加权和 T_net，不断言渠道效应大小', statement_role='definition',
        lhs='T_net', rhs=r'\sum_{k \in K} w_k \cdot d_k', relation='==', variables=['T_net','K','w_k','d_k'])
    obligations = obligations_for_claim(claim, {'sympy':True})
    assert not any(o.required for o in obligations)
    assert theory_validity_gate([claim], obligations, [], []).passed
    assert claim.status.value != 'supported'
    assert not theory_validity_gate([claim.model_copy(update={'statement_role':'assertion'})], obligations, [], []).passed
    from src.agents.reasoning import ReasoningAgent
    from src.agents.protocol import ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime
    result = ReasoningAgent().formal_closure(AgentTask(agent='reasoning', objective='核对记号',
        hints={'claim_id':claim.id}), ContextPack(objects={'claim':[claim.model_dump(mode='json')]}),
        AgentRuntime(), UsageRecord())
    assert result.payload['notation_only']
    assert not any(p.kind == 'verification' for p in result.proposed_changes)


def test_team_verifies_all_later_versions_before_writing_without_llm(tmp_path, monkeypatch):
    from src import config
    from src.agents.runtime import AgentRuntime
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
    from src.graph.research_graph import TeamRun, TeamLoopState
    from src.research.commit import ResearchCommitService
    from src.research.task_store import TaskStore
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path/'outputs')
    store = TaskStore('p', db_path=str(tmp_path/'research.sqlite'))
    runtime = AgentRuntime()
    service = VerificationService(store=store.store, runner=VerificationRunner(inproc=True))
    monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:service)
    team = TeamRun(project_id='p', problem_id='q', run_id='r', task_store=store,
        runtime=runtime, supervisor=SupervisorAgent(), max_rounds=12)
    try:
        seed = AgentTask(agent='reasoning', objective='后续提出条件命题', project_id='p',problem_id='q',run_id='r')
        claims = [conditional_claim(f'late-{i}') for i in range(4)]
        result = AgentResult(task_id=seed.task_id, agent='reasoning', outcome='completed', proposed_changes=[
            ChangeProposal(kind='claim',object_id=c.id,payload=c.model_dump(mode='json')) for c in claims])
        ResearchCommitService(store.store).commit(seed, result)
        plan = TeamPlan()
        old = AgentTask(agent='reasoning',objective='已完成的定义核验',hints={'verification_only':True,'claim_id':'old'})
        plan.add_task(old)
        writer = AgentTask(agent='writing',objective='撰写条件定理')
        plan.add_task(writer)
        team.loop = TeamLoopState(brief=ResearchBrief(main_question='应用研究',project_id='p',problem_id='q'),plan=plan,
            results={old.task_id:AgentResult(task_id=old.task_id,agent='reasoning',outcome='completed')})
        team.outcome.plan, team.outcome.brief = plan, team.loop.brief
        for _ in range(3):
            if all(row['status']=='supported' for row in store.store.list_latest('claim')):
                break
            assert team.step()
        current = store.store.list_latest('claim')
        assert len(current) == 4 and all(row['status']=='supported' for row in current)
        assert len(store.store.list_latest('obligation')) == 4
        assert len(store.store.list_latest('verification')) == 4
        assert not any(row.agent == 'writing' for row in team.loop.results.values())
        team._enqueue_unverified_formal_claims()
        assert not any(need.hints.get('verification_only') for need in team.loop.open_needs)
    finally:
        store.close()


@pytest.mark.parametrize('policy,expected', [('both', {'manual-doc','session-doc'}),
    ('user_kb', {'manual-doc'}), ('autonomous', {'session-doc'})])
def test_agent_tools_search_and_read_both_authorized_libraries(tmp_path, monkeypatch, policy, expected):
    from src import config
    from src.agents.base import AgentBase
    from src.agents.protocol import ContextPack
    from src.agents.runtime import AgentRuntime, ToolLoop
    from src.agents.tools import evidence_tools
    from src.kb.schema import LitRecord
    from src.kb.store import KBStore
    from src.kb.service import KnowledgeService
    from src.utils.conversation_layout import conversation_topic
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(KnowledgeService, '_vector_hits', lambda *a:[])
    sid = 'a'*32
    topics = ['bound-library', conversation_topic(sid)]
    for topic, doc_id in zip(topics, ['manual-doc','session-doc']):
        store = KBStore(topic)
        store.upsert_document(LitRecord(doc_id=doc_id, title=f'fishery {doc_id}',
            abstract=f'fishery evidence from {doc_id}'), search_text='fishery evidence')
        store.close()
    class Reader(AgentBase):
        role = 'reasoning'
        def tools(self, *args):
            return [t for t in evidence_tools() if t.name in {'search_local_kb','read_source'}]
        def _run(self, task, context, runtime, usage, tools):
            assert 'query' in next(t for t in tools if t.name == 'search_local_kb').as_langchain_tool().args
            loop = ToolLoop(tools)
            search = loop._execute({'name':'search_local_kb','args':{'query':'fishery'}}, grant=context.grant)
            read = loop._execute({'name':'read_source','args':{'source_id':'session-doc'}}, grant=context.grant)
            forbidden = loop._execute({'name':'read_source','args':{'source_id':'x','topic':'unbound-library'}}, grant=context.grant)
            return AgentResult(task_id=task.task_id, agent=self.role, payload={
                'search':search.result,'read':read.result,'forbidden':forbidden.result or forbidden.error})
    task = AgentTask(agent='reasoning', objective='核读', session_id=sid,
                     source_policy=policy, source_set_ids=['bound-library'])
    runtime = AgentRuntime()
    result = Reader().run(task, ContextPack(grant=runtime.issue_grant(task)), runtime)
    assert {doc for doc in ['manual-doc','session-doc'] if doc in result.payload['search']} == expected
    assert ('fishery evidence from session-doc' in result.payload['read']) == ('session-doc' in expected)
    assert '未授权' in result.payload['forbidden']
    if policy != 'both':
        return
    assert all(topic in result.payload['search'] for topic in topics)
    from src.kb import bridge
    from src.research.schemas import SourcePolicy
    monkeypatch.setattr(bridge, '_harvest_external', lambda *a, **k:[])
    items, coverage = bridge.gather_sources(None, None, topic=topics[0], external_topic=topics[1],
        policy=SourcePolicy.both, claim=Claim(statement='fishery'), extra_queries=['fishery'], max_queries=1)
    assert {item.source_set_id for item in items} == set(topics)
    assert {row['source_set_id'] for row in coverage.library_searches} == set(topics)
    assert all(row['executed'] and row['hits'] for row in coverage.library_searches)
