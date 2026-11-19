"""Regressions for the 2026-11-19 review/repair loop."""
from src.agents.protocol import ResearchNeed
from src.agents.review import _issue_from, _needs_from
from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord


def science_issue():
    return _issue_from({'category':'science', 'severity':'major',
        'summary':'条件命题混淆覆盖缺口 g 与覆盖改善量 Δg',
        'repair_action':'revise_text', 'suggested_owner':'modeling',
        'affected_ids':['clm-target', 'blk-target'],
        'acceptance':['修正命题 mechanism_links 与 formal_spec 的不一致，再核验新版本']})


def test_scientific_repair_precedes_review_requested_rewrite():
    needs=_needs_from([science_issue()])
    needs.append(ResearchNeed(kind='manuscript_revision',owner='writing',
        statement='按审阅改稿',hints={'review_cycle':True}))
    decision=SupervisorAgent().decide(brief=ResearchBrief(main_question='research'),
                                     plan=TeamPlan(),open_needs=needs)
    assert decision.tasks and all(t.agent in {'modeling','reasoning'} for t in decision.tasks)


def test_claim_review_repair_carries_target_and_acceptance_details():
    need=_needs_from([science_issue()])[0]
    assert need.hints.get('claim_id')=='clm-target'
    assert need.hints.get('repair_claim') is True
    assert need.owner=='reasoning'


def test_prose_grouping_definition_has_disclosure_not_impossible_proof_obligation():
    from src.research.schemas import Claim
    from src.research.problem_formulator import obligations_for_claim
    from src.verification.definitions import is_notation_definition
    claim=Claim(claim_type='definitional',statement_role='definition',
        statement='分组口径定义：轴1作业海域映射到 g，轴2船型映射到成本占比。'
                  '本命题仅命名分组轴与中介变量的对应关系，不含任何经验断言。',
        variables=['g'])
    assert is_notation_definition(claim)
    assert all(o.kind=='notation_scope' and not o.required for o in obligations_for_claim(claim))


def test_compound_inline_equation_keeps_scripts_in_math_mode():
    from src.publication.render_latex import render_latex
    from src.publication.schemas import Manuscript, Section, Block
    paper=Manuscript(sections=[Section(heading='讨论',blocks=[Block(text=
        '多链暴露写成 $S_rt^(2)=f_C(C_rt)$；单链记为 S_rt^(1)=f_S(Cov,Pen,B,L,A)。')])])
    latex=render_latex(paper)
    assert r'S_{rt}^{(2)}' in latex and r'S_{rt}^{(1)}' in latex
    assert r'\textasciicircum{}(1)' not in latex


def test_citations_after_operating_scope_are_not_numeric_intervals():
    from src.research.acceptance import _cited_numbers
    assert _cited_numbers('没有把通信指标转换为成本、收益或作业范围[3,8]。')=={3,8}
    assert not _cited_numbers('取值范围[1,2]；区间[2,3]；x∈[3,4]。')


def test_review_cannot_call_a_structurally_cited_source_unused():
    from src.agents.review import _review_source_conflicts
    from src.agents.protocol import ContextPack
    context=ContextPack(objects={'manuscript':[{'sections':[{'blocks':[
        {'block_id':'blk-cited','text':'实际引用句','ref_kinds':['source'],'refs':[{'id':'doc-cited'}]}]}]}]})
    issue={'category':'citation','summary':'参考文献列入文献表但正文未引用',
           'affected_ids':['doc-cited'],'repair_action':'revise_citation'}
    assert _review_source_conflicts({'issues':[issue]},context)
    issue['affected_ids']=['blk-cited']
    issue['summary']='参考文献[1]在正文中未被引用，却列入正式文献表'
    assert _review_source_conflicts({'issues':[issue]},context)
    issue['summary']='实际引用句超出了来源证据范围'
    assert not _review_source_conflicts({'issues':[issue]},context)


def test_scientific_repair_updates_claim_and_invalidates_old_proof(tmp_path, monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.schemas import Claim
    from src.research.commit import ResearchCommitService
    from src.research.store import ResearchStore
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(id='clm-target', statement='若 x>=0，则 x+1>0', claim_type='descriptive',
        study={'design':'theory'}, mechanism_links=['模型内性质'],
        formal_spec={'premises':['x >= 0'], 'conclusion':'x + 1 > 0','variables':{'x':'Real'}})
    store = ResearchStore('test', tmp_path/'repair.sqlite')
    try:
        runtime = AgentRuntime()
        runtime.research_store = store
        service = VerificationService(store=store, runner=VerificationRunner(inproc=True))
        monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:service)
        task = AgentTask(agent='reasoning', objective='核验', project_id='test',
                         problem_id='p',run_id='r', hints={'claim_id':claim.id})
        seed = ReasoningAgent().formal_closure(task, ContextPack(objects={
            'claim':[claim.model_dump(mode='json')]}), runtime, UsageRecord())
        commit = ResearchCommitService(store)
        assert not commit.commit(task, seed, current_versions={}).rejected
        original = store.get('claim',claim.id)
        assert original['status']=='supported'

        agent = ReasoningAgent()
        def no_shortcut(*_):
            raise AssertionError('A review repair must not return the old proof')
        monkeypatch.setattr(agent, 'formal_closure', no_shortcut)
        changed = {**original, 'mechanism_links':[],
                   'scope_conditions':'仅是模型内代数性质，未检验经验前提'}
        monkeypatch.setattr(agent,'tool_loop',lambda *a, **k:(json.dumps({'claims':[
            changed, {**changed,'id':'clm-unrelated'}]}), []))
        task=AgentTask(agent='reasoning',objective='修正不成立的经验解释',project_id='test',
            problem_id='p',run_id='r',hints={'claim_id':claim.id,'repair_claim':True,
            'review_diagnostic':'修正不成立的经验解释'})
        result = agent._run(task, ContextPack(objects={'claim':[original]}),
                            SimpleNamespace(llm_available=lambda *_:True), UsageRecord(), [])
        proposals = [p for p in result.proposed_changes if p.kind=='claim']
        assert len(proposals)==1 and proposals[0].object_id==claim.id
        assert proposals[0].payload['version']==2 and 'status' not in proposals[0].payload
        assert proposals[0].payload['mechanism_links']==[]
        assert any(n.hints.get('verification_only') for n in result.followup_needs)
        assert any(n.owner=='writing' for n in result.followup_needs)
        assert not commit.commit(task,result,current_versions={claim.id:1}).rejected
        revised = store.get('claim',claim.id)
        assert revised['version']==2 and revised['status']!='supported'
        verification = AgentTask(agent='reasoning',objective='核验修订版本',project_id='test',
            problem_id='p',run_id='r',hints={'claim_id':claim.id,'verification_only':True})
        checked = ReasoningAgent().formal_closure(verification, ContextPack(objects={
            'claim':[revised], 'obligation':store.list_latest('obligation')}), runtime, UsageRecord())
        assert not commit.commit(verification,checked,current_versions={claim.id:2}).rejected
        assert store.get('claim',claim.id)['status']=='supported'
    finally:
        store.close()


def test_noop_claim_repair_is_blocked_and_does_not_spawn_writing(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.research.schemas import Claim
    claim=Claim(id='clm-target',statement='候选机制',claim_type='descriptive').model_dump(mode='json')
    agent=ReasoningAgent()
    monkeypatch.setattr(agent,'tool_loop',lambda *a,**k:(json.dumps({'claims':[claim]}),[]))
    result=agent._run(AgentTask(agent='reasoning',objective='修复',hints={
        'claim_id':claim['id'],'repair_claim':True}),ContextPack(objects={'claim':[claim]}),
        SimpleNamespace(llm_available=lambda *_:True),UsageRecord(),[])
    assert result.outcome.value=='blocked'
    assert not result.proposed_changes and not result.followup_needs


def test_latest_review_selection_drops_resolved_history_and_keeps_issue_identity():
    from src.agents.review_state import latest_review_issues
    from src.agents.review import _reuse_review_ids
    old={'issue_id':'old','summary':'已解决','category':'science',
         '_task_id':'review-1','_submitted_at':'2026-11-19T01:00:00'}
    latest={**old,'issue_id':'current','summary':'变量含义冲突',
            '_task_id':'review-2','_submitted_at':'2026-11-19T02:00:00',
            'affected_refs':[{'id':'clm-target'}]}
    assert latest_review_issues([latest,old])==[latest]
    issue=_issue_from({'issue_id':'current','category':'science','summary':'改写后的同一问题',
                       'affected_ids':['clm-target']})
    assert _reuse_review_ids([issue],ContextPack(objects={'review_issue':[old,latest]}))[0].issue_id=='current'
    bogus=_issue_from({'issue_id':'current','category':'science','summary':'不相关问题',
                       'affected_ids':['clm-other']})
    assert _reuse_review_ids([bogus],ContextPack(objects={'review_issue':[old,latest]}))[0].issue_id!='current'


def test_disclosure_label_does_not_exempt_a_substantive_property():
    from src.research.schemas import Claim
    from src.verification.definitions import is_notation_definition
    claim=Claim(claim_type='definitional',statement_role='definition',variables=['g'],
        statement='分组口径定义：仅命名分组轴，不含任何经验断言，因此 g 单调增加。')
    assert not is_notation_definition(claim)


def test_repair_cap_survives_rewording_and_allows_writing():
    from src.agents.protocol import AgentResult
    plan=TeamPlan()
    results={}
    for i in range(2):
        task=AgentTask(agent='reasoning',objective=f'先前修订{i}',hints={
            'repair_claim':True,'claim_id':'clm-target','repair_key':'claim-review:clm-target'})
        plan.add_task(task)
        results[task.task_id]=AgentResult(task_id=task.task_id,agent='reasoning',outcome='blocked')
    writer=AgentTask(agent='writing',objective='如实收尾',hints={'review_cycle':True})
    plan.add_task(writer)
    need=_needs_from([science_issue()])[0]
    decision=SupervisorAgent().decide(brief=ResearchBrief(main_question='research'),plan=plan,
        results=results,open_needs=[need])
    assert [task.agent for task in decision.tasks]==['writing']


def test_closing_round_allows_scientific_repair_before_synthesis():
    from src.agents.protocol import AgentResult
    from src.agents.supervisor import SupervisorDecision
    from src.graph.research_graph import TeamRun, TeamLoopState, TeamRunOutcome
    from src.publication.manuscript_state import manuscript_identity
    plan=TeamPlan()
    manuscript={'manuscript_id':'m','version':1,'sections':[]}
    writer=AgentTask(agent='writing',objective='初稿')
    reviewer=AgentTask(agent='review',objective='初审')
    for task in (writer,reviewer):
        plan.add_task(task)
    results={writer.task_id:AgentResult(task_id=writer.task_id,agent='writing',outcome='completed',
                  payload={'manuscript':manuscript}),
             reviewer.task_id:AgentResult(task_id=reviewer.task_id,agent='review',outcome='completed',
                  payload={'semantic_reviewed':True,'accepted':False,
                           'reviewed_manuscript':manuscript_identity(manuscript)})}
    repair=AgentTask(agent='reasoning',objective='修订原命题',hints={'repair_claim':True})
    run=TeamRun.__new__(TeamRun)
    run.prepare=lambda:None
    run.loop=TeamLoopState(brief=ResearchBrief(main_question='research'),plan=plan,results=results,rounds=6)
    run.outcome=TeamRunOutcome(task_order=[writer.task_id,reviewer.task_id])
    run.cancel=SimpleNamespace(cancelled=False)
    run.runtime=SimpleNamespace(run_budget=None,emit=lambda *_:None)
    run.supervisor=SimpleNamespace(
        decide=lambda **_:SupervisorDecision(decision='dispatch',tasks=[repair]),
        adopt=lambda p,t:p.add_task(t),replan=lambda p,*a,**k:p)
    run.max_rounds,run.max_review_revisions=10,3
    run._refresh_retry_allowance=lambda *_:None
    run.save_state=lambda **_:True
    executed=[]
    def execute(task,*_):
        executed.append(task)
        return AgentResult(task_id=task.task_id,agent=task.agent,outcome='completed')
    run._execute_one=execute
    assert run.step()
    assert executed==[repair]
