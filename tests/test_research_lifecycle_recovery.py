"""Regressions from the November 23 run: repair the method, not the score."""
from src.research.schemas import Claim


def naming_claim(**updates):
    return Claim(id='clm-labels', claim_type='definitional', statement_role='definition',
        statement='环节命名（仅命名，不含性质）：生产、经营、安全、管理四类标签。'
                  '该命题只建立分类词表，不对任何环节的方向、大小或因果性作断言。', **updates)


def test_current_prose_naming_generates_disclosure_instead_of_an_empty_proof():
    from src.research.problem_formulator import obligations_for_claim
    obligations = obligations_for_claim(naming_claim())
    assert len(obligations) == 1 and obligations[0].kind == 'notation_scope'
    assert not obligations[0].required


def test_unmeasured_candidate_does_not_spawn_sample_tests():
    from src.research.adversarial import review_claim
    claim = Claim(claim_type='causal', statement='候选机制，尚未验证因果：链路改善可能改变发现概率。',
        mechanism_links=['通信可用→平台可观测性'], evidence_requirements=['需验证逐环证据'])
    review = review_claim(claim)
    assert not {'sample_selection_bias','measurement_error','reverse_causality'} & {f.check_id for f in review.hits}


def test_citation_of_unknown_report_requests_metadata_before_writing():
    from src.agents.protocol import ContextPack
    from src.agents.review import _issue_from, _route_unverified_source_repairs, _needs_from
    issue = _issue_from({'category':'citation','severity':'major','summary':'白皮书没有进入参考文献',
        'repair_action':'revise_citation','suggested_owner':'writing',
        'affected_ids':['doc-report','blk-text'],'acceptance':['补充白皮书的机构署名、年份并引用']})
    context = ContextPack(objects={'evidence':[{'id':'doc-report','publication_status':'unknown'}]})
    needs = _needs_from(_route_unverified_source_repairs([issue], context))
    assert needs[0].owner == 'evidence' and needs[0].hints['repair_action'] == 'verify_source'


def test_missing_model_is_a_modeling_task_even_when_review_suggests_writing():
    from src.agents.review import _issue_from, _needs_from
    issue = _issue_from({'category':'science','severity':'major',
        'summary':'新增替代效应，但模型库中未登记对应模型','detail':'需增加可区分预测与参数约束',
        'repair_action':'research','suggested_owner':'writing','affected_ids':['blk-discussion']})
    assert _needs_from([issue])[0].owner == 'modeling'


def test_revision_atomically_registers_current_naming_duty(tmp_path):
    from src.agents.protocol import AgentTask, AgentResult
    from src.agents.base import build_proposal
    from src.research.store import ResearchStore
    from src.research.commit import ResearchCommitService
    store = ResearchStore('p', tmp_path/'life.sqlite')
    try:
        store.put('claim','clm-labels',naming_claim(version=1).model_dump(mode='json'))
        task = AgentTask(agent='reasoning',objective='修订命名',project_id='p',problem_id='q',run_id='r')
        result = AgentResult(agent='reasoning',task_id=task.task_id,outcome='completed',
            proposed_changes=[build_proposal('claim',object_id='clm-labels',
                payload=naming_claim(version=2).model_dump(mode='json'))])
        committed = ResearchCommitService(store).commit(task,result)
        assert not committed.rejected
        duties = store.list_latest('obligation')
        assert any(o['claim_version']==2 and o['kind']=='notation_scope' and not o['required'] for o in duties)
    finally:
        store.close()


def test_prose_claim_cannot_get_fabricated_empty_difference_steps():
    from src.research.theorist import _steps_for
    assert not _steps_for(Claim(statement='候选机制，无数学编码'), 'direct')


def test_no_empirical_data_does_not_make_an_actual_causal_assertion_exempt():
    from src.research.adversarial import review_claim
    claim = Claim(claim_type='causal',statement='已证实政策一定提高收入',
        study={'design':'did','treatment':'政策','outcome':'收入'})
    assert 'sample_selection_bias' in {f.check_id for f in review_claim(claim).hits}


def test_support_budget_prioritizes_substantive_claims_over_naming():
    from src.agents.evidence import _support_targets
    from src.agents.protocol import AgentTask, ContextPack
    labels = [naming_claim().model_copy(update={'id':f'label-{i}'}) for i in range(5)]
    mechanism = Claim(id='mechanism',statement='候选环节',claim_type='causal',mechanism_links=['X→Y'])
    targets = _support_targets(AgentTask(agent='evidence',objective='收集支持资料'),
        ContextPack(objects={'claim':[c.model_dump(mode='json') for c in [*labels,mechanism]]}))
    assert [c.id for c in targets] == ['mechanism']


def test_local_report_cover_recovers_identity_without_claiming_effects():
    from src.kb.report_metadata import recover_report_metadata
    from src.publication.references import citation_eligible, format_reference
    row = {'source_id':'doc-report','file_hash':'abc12345','content_level':'fulltext','page':1,
        'title':'11_白皮书_卫星互联网承载网技术白皮书_紫金山实验室',
        'excerpt':'未来网络技术发展系列白皮书（2025） 卫星互联网承载网技术 白皮书 '
            '版权声明 本白皮书版权属于紫金山实验室及其合作单位所有。'
            '编写说明 主要编写单位： 紫金山实验室 北京邮电大学 主要编写人员： 张甲、李乙。',
        'relation':'insufficient','support':'insufficient'}
    result = recover_report_metadata(row)
    assert citation_eligible(result)
    assert result['authors'] == '紫金山实验室, 北京邮电大学'
    assert '[R]' in format_reference(result) and '[R/OL]' not in format_reference(result)
    assert result['relation'] == 'insufficient' and not result.get('url')
    assert 'file-hash:' in result['metadata_provenance']
    assert not citation_eligible(recover_report_metadata({**row,'content_level':'abstract'}))
    assert not citation_eligible(recover_report_metadata({**row,'file_hash':''}))
    assert not citation_eligible(recover_report_metadata({**row,'title':'完全无关白皮书'}))
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    handoff=EvidenceAgent()._repair_report_metadata(AgentTask(agent='evidence',objective='核对报告',
        source_policy='user_kb', hints={'repair_action':'verify_source','focus_blocks':['doc-report']}),
        ContextPack(objects={'evidence':[row]}), UsageRecord())
    assert handoff and [(n.owner,n.hints['repair_action']) for n in handoff.followup_needs] == [
        ('evidence','read_source'), ('writing','revise_citation')]


def test_registered_identity_survives_replacing_cover_with_body_excerpt():
    from src.agents.evidence import _inherit_registered_bibliography
    from src.agents.protocol import ContextPack
    previous = {'source_id':'doc-r','file_hash':'hash1','publication_status':'published',
        'publication_verified_by':'document-frontmatter','publication_type':'report',
        'title':'已核对报告','authors':'某研究院','year':'2025','publisher':'某研究院'}
    fresh = {'source_id':'doc-r','file_hash':'hash1','title':'filename','excerpt':'正文案例',
        'relation':'insufficient','locator':'p40','authors':'','publication_status':'unknown'}
    row = _inherit_registered_bibliography([fresh], ContextPack(objects={'evidence':[previous]}))[0]
    assert row['authors']=='某研究院' and row['excerpt']=='正文案例' and row['locator']=='p40'
    assert row['relation']=='insufficient'
    changed = {**fresh,'file_hash':'other-file'}
    assert _inherit_registered_bibliography([changed], ContextPack(objects={'evidence':[previous]}))[0] == changed


def test_bibliography_removes_unknown_command_leak_without_touching_math():
    from src.publication.references import format_reference
    text = format_reference({'title':r'Marine processing and \\fusion: $\alpha$ models',
        'authors':'Zhang','year':'2016','venue':'Science','publication_type':'journal-article'})
    assert r'\fusion' not in text and 'fusion' in text and r'\alpha' in text


def test_semantic_revision_without_a_new_version_cannot_reuse_old_duties(tmp_path):
    from src.agents.protocol import AgentTask, AgentResult
    from src.agents.base import build_proposal
    from src.research.store import ResearchStore
    from src.research.commit import ResearchCommitService
    store = ResearchStore('p', tmp_path/'version.sqlite')
    try:
        original = Claim(id='c',statement='x=x',lhs='x',rhs='x',relation='==',variables=['x'])
        store.put('claim','c',original.model_dump(mode='json'))
        revision = original.model_copy(update={'statement':'x=x+1','rhs':'x+1'})
        task=AgentTask(agent='reasoning',objective='修订',project_id='p',problem_id='q')
        result=AgentResult(agent='reasoning',task_id=task.task_id,outcome='completed',
            proposed_changes=[build_proposal('claim',object_id='c',payload=revision.model_dump(mode='json'))])
        outcome=ResearchCommitService(store).commit(task,result)
        assert outcome.rejected and store.get('claim','c')['rhs']=='x'
    finally:
        store.close()


def test_naming_closure_retires_legacy_duty_without_a_proof_or_supported_status():
    from types import SimpleNamespace
    from src.agents.reasoning import ReasoningAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    from src.research.schemas import ProofObligation
    claim=naming_claim(version=2)
    old=ProofObligation(id='old',claim_id=claim.id,claim_version=2,
        statement='复合等式证明',kind='formal_argument',acceptance_method='formal_encoding')
    result=ReasoningAgent().formal_closure(AgentTask(agent='reasoning',objective='核验',
        hints={'claim_id':claim.id}), ContextPack(objects={'claim':[claim.model_dump(mode='json')],
        'obligation':[old.model_dump(mode='json')]}),SimpleNamespace(),UsageRecord())
    assert all(p.kind=='obligation' and p.payload['required'] is False for p in result.proposed_changes)
    assert not any(p.kind in {'attempt','verification','claim'} for p in result.proposed_changes)


def test_evidence_requirement_change_is_a_real_versioned_revision(monkeypatch):
    import json
    from types import SimpleNamespace
    from src.agents.reasoning import ReasoningAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    original = Claim(id='clm-target',statement='给定条件的候选关系',study={'design':'theory'}).model_dump(mode='json')
    updated = {**original,'evidence_requirements':['新增需要核查的参数来源']}
    agent=ReasoningAgent()
    monkeypatch.setattr(agent,'tool_loop',lambda *a,**k:(json.dumps({'claims':[updated]},ensure_ascii=False),[]))
    result=agent._run(AgentTask(agent='reasoning',objective='修正证据要求',
        hints={'claim_id':'clm-target','repair_claim':True}),ContextPack(objects={'claim':[original]}),
        SimpleNamespace(llm_available=lambda *_:True),UsageRecord(),[])
    assert any(p.kind=='claim' and p.payload['version']==2 for p in result.proposed_changes)


def test_review_model_repair_precedes_ready_writing_checkpoint():
    from src.agents.review import _issue_from, _needs_from
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent
    from src.agents.protocol import ResearchNeed
    issue=_issue_from({'category':'science','severity':'major','summary':'模型库缺少替代效应模型',
        'repair_action':'research','suggested_owner':'modeling','affected_ids':['blk-discussion']})
    needs=_needs_from([issue]) + [ResearchNeed(kind='manuscript_revision',owner='writing',
        statement='按审阅改稿',hints={'review_cycle':True})]
    plan=TeamPlan(tasks=[{'task_id':'write','agent':'writing','objective':'按审阅改稿','depends_on':[]}])
    decision=SupervisorAgent().decide(brief=ResearchBrief(main_question='研究问题'),plan=plan,open_needs=needs)
    assert decision.tasks and all(t.agent=='modeling' for t in decision.tasks)
    from src.agents.protocol import AgentResult
    model_task=decision.tasks[0].model_dump(mode='json')
    for tid in ('m1','m2'):
        plan.tasks.append({**model_task,'task_id':tid})
    results={tid:AgentResult(agent='modeling',task_id=tid,outcome='completed') for tid in ('m1','m2')}
    exhausted=SupervisorAgent().decide(brief=ResearchBrief(main_question='研究问题'),
        plan=plan,open_needs=needs,results=results)
    assert exhausted.tasks and all(t.agent=='writing' for t in exhausted.tasks)
