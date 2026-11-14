"""Regressions from the 2026-11-14 08:35 run: research must reach review."""
from types import SimpleNamespace

from src.agents.protocol import AgentResult, AgentTask, ContextPack, ResearchNeed, UsageRecord
from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
from src.research.schemas import Claim
import pytest


def test_current_manuscript_review_precedes_more_derivation_needs():
    brief = ResearchBrief(main_question='研究星网对渔业的条件影响', deliverables=['full_paper'])
    plan = TeamPlan()
    writer = AgentTask(agent='writing', objective='初稿')
    reviewer = AgentTask(agent='review', objective='独立审阅')
    plan.add_task(writer)
    plan.add_task(reviewer)
    plan.edges[reviewer.task_id] = [writer.task_id]
    results = {writer.task_id: AgentResult(task_id=writer.task_id, agent='writing', outcome='partial',
        payload={'manuscript': {'manuscript_id': 'm', 'sections': [{'blocks': [{'text': '条件性结论'}]}]}})}
    needs = [ResearchNeed(kind='derivation', statement=f'再拆子命题 {i}',
                        hints={'claim_id': f'c{i}', 'trigger_id': f'proof:c{i}'}) for i in range(20)]
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results, open_needs=needs)
    assert [task.agent for task in decision.tasks] == ['review']


def test_reasoning_cap_leaves_room_for_writing_even_when_new_claim_ids_keep_arriving():
    brief = ResearchBrief(main_question='理论与实验建议', deliverables=['full_paper'])
    plan, results = TeamPlan(), {}
    for i in range(12):
        task = AgentTask(agent='reasoning', objective=f'推导 {i}', hints={'claim_id': f'c{i}'})
        plan.add_task(task)
        results[task.task_id] = AgentResult(task_id=task.task_id, agent='reasoning', outcome='blocked')
    plan.add_task(AgentTask(agent='writing', objective='整理有界结论与未决项'))
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results,
        open_needs=[ResearchNeed(kind='derivation', statement='又一个引理', hints={'claim_id': 'new'})])
    assert [task.agent for task in decision.tasks] == ['writing']


def test_definition_has_no_mandatory_human_acceptance():
    from src.research.problem_formulator import obligations_for_claim
    claim = Claim(statement='定义复合增益 G', claim_type='definitional', lhs='G', rhs='r*e*g',
                  relation='==', variables=['G', 'r', 'e', 'g'])
    obligations = obligations_for_claim(claim, {'sympy': True})
    assert not any(o.required and o.acceptance_method in {'manual', 'informal_review'} for o in obligations)


def test_conditional_algebra_in_applied_research_does_not_require_empirical_support(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(id='c', statement='模型内恒等式，非经验效应：x*(y+z)=x*y+x*z',
        claim_type='descriptive', study={'design': 'theory'}, strategy='derivation',
        lhs='x*(y+z)', rhs='x*y+x*z', relation='==', variables=['x','y','z'],
        variable_domains={'x':'real','y':'real','z':'real'})
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime, 'llm_available', lambda *_: False)
    monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:
        VerificationService(runner=VerificationRunner(inproc=True)))
    result = ReasoningAgent().formal_closure(AgentTask(agent='reasoning', objective='核验模型内推导',
        source_policy='both', hints={'claim_id':'c'}), ContextPack(request='研究星网对渔业影响',
        objects={'claim':[claim.model_dump(mode='json')]}), runtime, UsageRecord())
    assert result.outcome.value != 'blocked'
    assert any(p.kind == 'verification' and p.payload['result']['status'] == 'passed'
               for p in result.proposed_changes)


def _conditional_claim(conclusion='In == 0', premises=None):
    return Claim(id='product', statement='条件命题：乘积编码下 Phys=0 推出 In=0，并非经验效应',
        claim_type='descriptive', study={'design':'theory'}, strategy='derivation',
        variables=['In','Phys','Comp','Info'],
        formal_spec={'premises': premises if premises is not None else ['In == Phys*Comp*Info','Phys == 0'],
                     'conclusion':conclusion, 'variables':{v:'Real' for v in ['In','Phys','Comp','Info']}})


@pytest.mark.parametrize('conclusion,premises,status', [
    ('In == 0', None, 'passed'),
    ('In == 1', None, 'failed'),
    ('In == 0', ['In == Phys*Comp*Info'], 'failed'),
    ('In == 0', ['Phys == 0', 'Phys != 0'], 'unknown'),
])
def test_real_product_implication_checks_preserve_conditions(conclusion, premises, status):
    from src.research.problem_formulator import obligations_for_claim
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    from src.research.acceptance import _aligned
    claim = _conditional_claim(conclusion, premises)
    obligation, = obligations_for_claim(claim, {'z3':True})
    outcome = VerificationService(runner=VerificationRunner(inproc=True)).check(claim, obligation)
    assert outcome.result.status.value == status
    if status == 'passed':
        assert outcome.closed and _aligned(outcome.record, claim)
        changed = claim.model_copy(deep=True)
        changed.formal_spec.premises = ['Phys == 0']
        assert not _aligned(outcome.record, changed)
    if status == 'failed':
        assert outcome.result.counterexample


def test_latest_function_definition_encodings_are_accepted_without_proving_theorem():
    from src.verification.definitions import check_definition
    for lhs, rhs, variables in [
        ('A(v,t)', 'coverage(v,t)*1[R_avail(v,t) >= D(v,t)]',
         ['A(v,t)','coverage(v,t)','R_avail(v,t)','D(v,t)']),
        ('In(v,t)', 'Phys(v,t)*Comp(v,t)*Info(v,t)',
         ['In(v,t)','Phys(v,t)','Comp(v,t)','Info(v,t)','v','t']),
    ]:
        result = check_definition({'lhs':lhs,'rhs':rhs,'variables':variables})
        assert result.status.value == 'passed', result.detail
        assert result.scope == 'step' and result.values['proves_claim'] is False
    result = check_definition({'lhs':'A(t)', 'rhs':'coverage(t)[0]', 'variables':['A(t)','coverage(t)']})
    assert result.status.value == 'unsupported'


def test_kernel_conditional_proof_is_usable_by_state_reconciliation_and_review(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.agents.review import deterministic_issues
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    from src.research.schemas import ProofObligation, VerificationRecord
    from src.research.reasoning_kernel import claim_state_for, apply_claim_state
    from src.research.acceptance import _aligned
    claim = _conditional_claim()
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime, 'llm_available', lambda *_:False)
    monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:
        VerificationService(runner=VerificationRunner(inproc=True)))
    result = ReasoningAgent().formal_closure(AgentTask(agent='reasoning', objective='核验条件命题',
        source_policy='both', hints={'claim_id':claim.id}), ContextPack(request='星网对渔业的影响',
        objects={'claim':[claim.model_dump(mode='json')]}), runtime, UsageRecord())
    obligations = [ProofObligation(**p.payload) for p in result.proposed_changes if p.kind == 'obligation']
    records = [VerificationRecord(**p.payload['record']) for p in result.proposed_changes if p.kind == 'verification']
    state = claim_state_for(claim, obligations, records, aligned=_aligned)
    assert state.status.value == 'supported'
    supported = apply_claim_state(claim, state)
    assert not deterministic_issues(ContextPack(objects={'claim':[supported.model_dump(mode='json')],
        'verification':[r.model_dump(mode='json') for r in records]}))


def test_reasoning_root_limit_survives_new_child_ids():
    brief, plan, results = ResearchBrief(main_question='核心定理'), TeamPlan(), {}
    for i in range(3):
        task = AgentTask(agent='reasoning', objective=f'尝试{i}', hints={'root_claim_id':'root','claim_id':f'c{i}'})
        plan.add_task(task)
        results[task.task_id] = AgentResult(task_id=task.task_id,agent='reasoning',outcome='blocked')
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results,
        open_needs=[ResearchNeed(kind='derivation',statement='新 ID 的子引理',
                                hints={'root_claim_id':'root','claim_id':'new'})])
    assert not any(t.agent == 'reasoning' for t in decision.tasks)


def test_no_figure_score_penalty_for_text_only_paper():
    from src.agents.review import judge_review
    verdict = judge_review([], category_scores={'science':90,'fidelity':90,'citation':90,
        'readability':90,'completeness':90,'figure':0}, excluded_categories={'figure'})
    assert verdict['score'] == 90 and 'figure' not in verdict['category_scores']


def test_checked_notation_is_not_an_unresolved_theorem_or_a_supported_claim():
    from src.research.acceptance import theory_validity_gate
    from src.research.problem_formulator import obligations_for_claim
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(statement='定义复合量 G', claim_type='definitional', statement_role='definition',
                  lhs='G', rhs='r*e*g', relation='==', variables=['G','r','e','g'])
    obligation, = obligations_for_claim(claim, {'sympy':True})
    checked = VerificationService(runner=VerificationRunner(inproc=True)).check(claim, obligation)
    obligation = obligation.model_copy(update=checked.obligation_updates)
    assert theory_validity_gate([claim], [obligation], [checked.record], []).passed
    assert claim.status.value == 'proposed'
    # Without an explicit declaration, missing/stale records or a changed RHS are not exempt.
    assert not theory_validity_gate([claim.model_copy(update={'statement_role':'assertion'})],
                                   [obligation], [checked.record], []).passed
    assert not theory_validity_gate([claim], [obligation], [], []).passed
    changed = claim.model_copy(update={'rhs':'r*e*g + 1'})
    assert not theory_validity_gate([changed], [obligation], [checked.record], []).passed


def test_conditional_proof_and_attempt_survive_real_store_commit(tmp_path, monkeypatch):
    from src.research.store import ResearchStore
    from src.research.commit import ResearchCommitService
    from src.research.snapshot import snapshot_from_store
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    store = ResearchStore('test', tmp_path/'research.sqlite')
    try:
        runtime = AgentRuntime()
        runtime.research_store = store
        service = VerificationService(store=store, runner=VerificationRunner(inproc=True))
        monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:service)
        claim = _conditional_claim()
        task = AgentTask(agent='reasoning',objective='核验条件命题',project_id='test',problem_id='p',run_id='r',
                         hints={'claim_id':claim.id})
        result = ReasoningAgent().formal_closure(task, ContextPack(objects={'claim':[claim.model_dump(mode='json')]}),
                                                runtime, UsageRecord())
        committed = ResearchCommitService(store).commit(task, result, current_versions={})
        assert not committed.rejected
        snapshot = snapshot_from_store(store, project_id='test',problem_id='p',run_id='r')
        assert snapshot.claims[0].status.value == 'supported'
        assert snapshot.claims[0].formal_spec == claim.formal_spec
        assert snapshot.attempts and snapshot.verifications
    finally:
        store.close()


def test_unencoded_theorem_requests_encoding_repair_without_human_dependency(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = Claim(id='unencoded', statement='在给定约束下乘积为零', claim_type='descriptive',
                  study={'design':'theory'}, strategy='derivation')
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:
        VerificationService(runner=VerificationRunner(inproc=True)))
    result = ReasoningAgent().formal_closure(AgentTask(agent='reasoning',objective='推导',
        hints={'claim_id':claim.id}), ContextPack(objects={'claim':[claim.model_dump(mode='json')]}),
        runtime, UsageRecord())
    assert not any(p.kind == 'obligation' and p.payload.get('acceptance_method') in {'manual','informal_review'}
                   and p.payload.get('required',True) for p in result.proposed_changes)
    assert any(n.kind.value == 'derivation' and n.hints.get('verification_status') == 'unsupported'
               for n in result.followup_needs)


def test_counterexample_resolves_goal_instead_of_spawning_repairs(monkeypatch):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime
    from src.research.verification_service import VerificationService
    from src.verification.runner import VerificationRunner
    claim = _conditional_claim(conclusion='In == 1')
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime, 'verification_service', lambda *a, **k:
        VerificationService(runner=VerificationRunner(inproc=True)))
    result = ReasoningAgent().formal_closure(AgentTask(agent='reasoning',objective='推导',
        hints={'claim_id':claim.id}), ContextPack(objects={'claim':[claim.model_dump(mode='json')]}),
        runtime, UsageRecord())
    assert any(p.kind == 'obligation' and p.payload['status'] == 'refuted' for p in result.proposed_changes)
    assert not result.followup_needs


def test_default_planning_does_not_schedule_figures():
    brief = ResearchBrief(main_question='条件理论论文',deliverables=['full_paper'])
    brief.add_subquestion(statement='自动配图',kind='mechanism',owner='figures')
    assert all(row['agent'] != 'figures' for row in SupervisorAgent().plan(brief).tasks)


def test_formal_spec_cannot_hide_a_different_target_or_variable_domain():
    data = _conditional_claim().model_dump(mode='json')
    with pytest.raises(ValueError, match='另一组'):
        Claim(**{**data, 'lhs':'In', 'rhs':'1'})
    with pytest.raises(ValueError, match='整数域'):
        Claim(**{**data, 'variable_domains':{'In':'integer'}})


def test_continuous_new_needs_still_reach_write_review_revision_and_re_review():
    from src.agents.protocol import ReviewIssueRef
    from src.graph.research_graph import TeamRun, TeamLoopState, TeamRunOutcome
    from src.publication.manuscript_state import manuscript_identity
    brief = ResearchBrief(main_question='条件定理与实验建议', deliverables=['full_paper'])
    plan = TeamPlan()
    core = AgentTask(agent='reasoning',objective='核心推导')
    writer = AgentTask(agent='writing',objective='初稿')
    reviewer = AgentTask(agent='review',objective='审阅')
    for task in [core,writer,reviewer]:
        plan.add_task(task)
    plan.edges[writer.task_id] = [core.task_id]
    plan.edges[reviewer.task_id] = [writer.task_id]
    run = TeamRun.__new__(TeamRun)
    run.prepare = lambda:None
    run.loop = TeamLoopState(brief=brief,plan=plan)
    run.outcome = TeamRunOutcome()
    run.cancel = SimpleNamespace(cancelled=False)
    run.runtime = SimpleNamespace(run_budget=None,emit=lambda *_:None)
    run.supervisor = SupervisorAgent()
    run.max_rounds, run.max_review_revisions = 12, 3
    run._refresh_retry_allowance = lambda *_:None
    run.save_state = lambda **_:True
    executed, manuscripts, reviews = [], [], []
    def execute(task, results):
        executed.append(task.agent)
        payload, needs, issues = {}, [], []
        if task.agent == 'reasoning':
            needs = [ResearchNeed(kind='derivation',statement=f'永远新的引理{len(executed)}-{i}',
                     hints={'claim_id':f'new-{len(executed)}-{i}'}) for i in range(5)]
        if task.agent == 'writing':
            manuscript = {'manuscript_id':'m','version':len(manuscripts)+1,
                'sections':[{'blocks':[{'block_id':'b','text':f'条件结论与实验建议 v{len(manuscripts)+1}'}]}]}
            manuscripts.append(manuscript)
            payload = {'manuscript':manuscript}
        if task.agent == 'review':
            reviews.append(manuscripts[-1])
            payload = {'semantic_reviewed':True,'accepted':len(reviews)>1,
                       'reviewed_manuscript':manuscript_identity(manuscripts[-1])}
            if len(reviews)==1:
                issues = [ReviewIssueRef(issue_id='readability',category='readability',severity='major',
                          summary='解释适用边界',suggested_owner='writing')]
                needs = [ResearchNeed(kind='manuscript_revision',statement='修订边界解释',
                         hints={'review_cycle':True,'review_task_id':task.task_id,'review_issue_ids':['readability']})]
        return AgentResult(task_id=task.task_id,agent=task.agent,outcome='completed',
                           payload=payload,followup_needs=needs,issues=issues)
    run._execute_one = execute
    def finish(state, reason, status=''):
        state.finished=True
        run.outcome.stop_reason=reason
    run._finish = finish
    while run.step():
        assert run.loop.rounds < 13
    assert executed[:5] == ['reasoning','writing','review','writing','review']
    assert executed[-1] == 'review'
    assert manuscript_identity(reviews[-1]) == manuscript_identity(manuscripts[-1])
    assert run.loop.open_needs, '未决缺口须保留，不能伪装成已解决'
    assert run.loop.review_revisions == 1
