import pytest

from src.research.acceptance import delivery_gate, theory_validity_gate
from src.research.schemas import Claim, ResearchSnapshot


def test_explicit_candidate_design_is_not_an_unproved_theorem():
    claim=Claim(claim_type='scenario',statement='建议按离岸距离分层；该分层是候选设计，尚非已证实的因果分组。')
    assert theory_validity_gate([claim],[],[],[]).passed
    prediction=Claim(claim_type='scenario',statement='在覆盖增加的情景下收入上升20%。')
    assert not theory_validity_gate([prediction],[],[],[]).passed


def test_domain_symbol_check_understands_latex_subscripts():
    claim=Claim(statement='条件恒等式',status='supported',coverage='target',
                support_kind='symbolic_check', validation_status='verified',
                variable_domains={'Y_t1':'Real','Y_c1':'Real'})
    snapshot=ResearchSnapshot(project_id='test',claims=[claim],writing_map={claim.id:'b'})
    result=delivery_gate('条件为 $Y_{t1}, Y_{c1} \\in \\mathbb{R}$。'+'研究边界。'*50,snapshot)
    assert not any('适用条件未出现' in value for value in result.reasons)


@pytest.mark.parametrize('sentence',[
    '上述角色定位是设计假设，不是本文的实测结果；在获得单元级数据之前，它们均属设计定位。',
    '本文尚未进行实验，不能声称获得结果。',
    '若实验结果显示收入上升，则支持候选机制。',
    '本研究尚未取得实验结果，不能声称机制成立。',
])
def test_disclaimer_or_condition_does_not_claim_an_experiment_result(sentence):
    snapshot=ResearchSnapshot(project_id='test',experiment_specs=[{'id':'exp','execution_status':'proposed'}])
    result=delivery_gate(sentence+'研究边界。'*50,snapshot)
    assert not any('正文不得出现实验结果' in value for value in result.reasons)


@pytest.mark.parametrize('sentence',['本文实验获得了显著改善。','实验结果并未表明该机制成立。'])
def test_actual_experiment_assertion_still_blocks_unexecuted_spec(sentence):
    snapshot=ResearchSnapshot(project_id='test',experiment_specs=[{'id':'exp','execution_status':'proposed'}])
    result=delivery_gate(sentence+'研究边界。'*50,snapshot)
    assert any('正文不得出现实验结果' in value for value in result.reasons)


def test_csl_fallback_retains_exact_publication_identity_and_provenance():
    from src.kb.publication import verify_publication
    paper={'title':'Study', 'authors':'John Smith', 'doi':'10.1000/test', 'excerpt':'read text', 'relation':'background'}
    def fetch(url, params=None):
        if '/transform/' not in url:
            raise TimeoutError()
        return {'title':'Study', 'author':[{'given':'John','family':'Smith'}], 'DOI':paper['doi'],
                'container-title':'Journal', 'type':'article-journal', 'issued':{'date-parts':[[2026,6]]},
                'publisher':'Publisher', 'ISSN':['1234-5678'], 'volume':'298', 'page':'107745'}
    result=verify_publication(paper, fetch)
    assert result['publication_verified_by']=='crossref-csl'
    assert result['issn']=='1234-5678' and result['published_date']=='2026-6'
    assert result['excerpt']=='read text' and result['relation']=='background'
    assert '/transform/' in result['metadata_provenance']


def test_publication_recheck_does_not_invalidate_good_authors_on_publisher_timeout():
    from src.kb.publication import verify_publication
    from src.publication.references import citation_eligible
    paper={'title':'Study','authors':'John Smith','doi':'10.1000/test'}
    row={'title':['Study'],'author':[{'given':'John','family':'Smith'}],'DOI':paper['doi'],
         'container-title':['Journal'],'type':'journal-article','published':{'date-parts':[[2026]]}}
    def publisher(*args, **kwargs):
        raise TimeoutError()
    result=verify_publication(paper, lambda *a,**kw:{'message':row}, force=True,
                              metadata_only=True, publisher_fetch=publisher)
    assert citation_eligible(result)
    assert result.get('author_verified_by')!='unresolved'


def test_jsonld_and_ris_match_article_not_a_doi_in_its_references():
    import json
    from src.kb.publisher_metadata import publisher_metadata, ris_metadata
    record={'@type':'ScholarlyArticle','identifier':{'propertyID':'DOI','value':'10.1000/test'},
            'headline':'Study','author':[{'@type':'Person','name':'John Smith'}],
            'datePublished':'2026-06-01','volumeNumber':'298','pagination':'107745'}
    html='<script type="application/ld+json">'+json.dumps(record)+'</script>'
    result=publisher_metadata(html,'10.1000/test',url='https://publisher.example/study')
    assert result['authors']=='John Smith' and result['volume']=='298'
    assert not publisher_metadata(html,'10.1000/wrong')
    assert not publisher_metadata('<p>References doi:10.1000/test</p>','10.1000/test')
    ris='TY  - JOUR\nDO  - 10.1000/wrong\nTI  - Wrong\nER  -\nTY  - JOUR\nDO  - 10.1000/test\nAU  - Smith, John\nTI  - Study\nVL  - 298\nSP  - 107745\nER  -'
    assert ris_metadata(ris,'10.1000/test')['title']=='Study'
    assert not ris_metadata(ris,'10.1000/absent')


def test_publisher_direct_url_does_not_depend_on_doi_redirect():
    from types import SimpleNamespace
    from src.kb.publisher_metadata import fetch_publisher_metadata
    calls=[]
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['max_retries']==1  # HTTP helper counts total attempts.
        assert url=='https://publisher.example/study'
        return SimpleNamespace(url=url,text='<meta name="citation_doi" content="10.1000/test">'
                                '<meta name="citation_author" content="Smith, John">')
    assert fetch_publisher_metadata('10.1000/test',urls=['https://publisher.example/study'],fetch=fetch)['authors']
    assert len(calls)==1


def test_canonical_metadata_is_visible_and_unfounded_doi_accusation_is_rechecked():
    from src.agents.review import _review_materials, _review_source_conflicts
    from src.agents.protocol import ContextPack
    source={'source_id':'doc-1','doi':'10.1016/j.fishres.2026.107745','venue':'Fisheries Research',
            'publisher':'Elsevier BV','issn':'0165-7836','volume':'298','published_date':'2026-6',
            'publication_source_url':'https://api.crossref.org/works/10.1016%2Fj.fishres.2026.107745'}
    context=ContextPack(objects={'evidence':[source]})
    material=_review_materials(context)
    assert all(source[key] in material for key in ('publisher','issn','volume','publication_source_url'))
    payload={'issues':[{'category':'citation','summary':'DOI 与期刊不匹配',
                        'affected_ids':['doc-1'],'repair_action':'verify_source'}]}
    assert _review_source_conflicts(payload,context)
    # A content mismatch is still a valid issue and does not require a registry dispute.
    payload['issues'][0]['summary']='原文为方法类比，正文却声称已证实增产'
    assert not _review_source_conflicts(payload,context)


def test_metadata_repair_runs_after_evidence_budget_without_llm(monkeypatch):
    from src.agents.evidence import EvidenceAgent
    from src.agents.review import _issue_from, _needs_from
    from src.agents.protocol import AgentTask, AgentResult, ContextPack, UsageRecord, ResearchNeed
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
    from src.agents.runtime import AgentRuntime
    plan=TeamPlan()
    done=[AgentTask(agent='evidence',objective=f'search {i}') for i in range(8)]
    for task in done:
        plan.add_task(task)
    results={task.task_id:AgentResult(task_id=task.task_id,agent='evidence',outcome='completed',
                                   payload={'model_attempts':20}) for task in done}
    needs=_needs_from([_issue_from({'severity':'major','category':'citation','summary':'核查 DOI 与期刊',
                                  'affected_ids':['doc-1'],'repair_action':'verify_source'})])
    needs.append(ResearchNeed(owner='writing',kind='manuscript_revision',statement='修订当前引用'))
    decision=SupervisorAgent().decide(brief=ResearchBrief(main_question='fisheries',source_policy='both'),
                                    plan=plan,results=results,open_needs=needs)
    assert [t.agent for t in decision.tasks]==['evidence']
    monkeypatch.setattr('src.kb.publication.verify_publication',lambda row,**kw:{**row,'publisher':'Verified'})
    monkeypatch.setattr(EvidenceAgent,'plan_retrieval',lambda *a:pytest.fail('must not retrieve or invoke LLM'))
    result=EvidenceAgent()._run(decision.tasks[0],ContextPack(objects={'evidence':[
        {'id':'doc-1','source_id':'doc-1','title':'Study','authors':'John Smith','excerpt':'Original'}]}),
        AgentRuntime(),UsageRecord(),[])
    assert result.proposed_changes[0].payload['publisher']=='Verified'
    assert result.usage.llm_calls==0 and result.usage.tool_calls==1
    assert result.followup_needs[0].owner=='writing'


def test_exhausted_source_replacement_routes_to_prose_closure():
    from src.agents.protocol import AgentTask, AgentResult, ResearchNeed
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan
    plan=TeamPlan()
    task=AgentTask(agent='evidence',objective='search')
    plan.add_task(task)
    results={task.task_id:AgentResult(task_id=task.task_id,agent='evidence',outcome='partial',
                                    payload={'model_attempts':150})}
    need=ResearchNeed(owner='evidence',kind='more_sources',statement='Replace unrelated citation',
                      hints={'root_cause_kind':'citation','repair_action':'replace_source','repair_key':'cite:1'})
    decision=SupervisorAgent().decide(brief=ResearchBrief(main_question='fisheries'),plan=plan,
                                    results=results,open_needs=[need])
    assert [t.agent for t in decision.tasks]==['writing']
    assert '撤引' in decision.tasks[0].objective


def test_failed_replacement_returns_to_writing_even_without_new_sources(monkeypatch):
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask, AgentResult, ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime
    monkeypatch.setattr(EvidenceAgent,'_research',lambda self,task,*a:AgentResult(
        task_id=task.task_id,agent='evidence',outcome='blocked'))
    task=AgentTask(agent='evidence',objective='Replace irrelevant source',
                   hints={'root_cause_kind':'citation','repair_action':'replace_source','focus_blocks':['block-1']})
    result=EvidenceAgent()._run(task,ContextPack(),AgentRuntime(),UsageRecord(),[])
    assert result.followup_needs[0].owner=='writing'
    assert result.followup_needs[0].hints['focus_blocks']==['block-1']


def test_transient_metadata_outage_keeps_successful_record_and_reports_refresh_failure():
    from src.kb.publication import verify_publication
    from src.publication.references import citation_eligible
    paper={'title':'Study','authors':'John Smith','year':'2026','doi':'10.1000/test','venue':'Journal',
           'publication_status':'published','publication_verified_by':'crossref','publication_type':'journal-article'}
    def fail(*a,**kw):
        raise TimeoutError()
    result=verify_publication(paper,fail,force=True,metadata_only=True,publisher_fetch=fail)
    assert citation_eligible(result) and '本次刷新未完成' in result['publication_note']


def test_title_match_cannot_silently_replace_a_different_doi():
    from src.kb.publication import verify_publication
    from src.publication.references import citation_eligible
    row={'title':['Study'],'author':[{'given':'John','family':'Smith'}],'DOI':'10.1000/wrong',
         'container-title':['Journal'],'type':'journal-article','published':{'date-parts':[[2026]]}}
    def fetch(url, params=None):
        return {'message':{'items':[row]}} if params else {'message':row}
    result=verify_publication({'title':'Study','authors':'John Smith','doi':'10.1000/right'},fetch)
    assert not citation_eligible(result)


def test_exact_work_supplements_missing_pages_without_overwriting_authors():
    from src.kb.publication import verify_publication
    row={'title':['Study'],'author':[{'given':'John','family':'Smith'}],'DOI':'10.1000/test',
         'container-title':['Journal'],'type':'journal-article','published':{'date-parts':[[2026,3]]}}
    def fetch(url, params=None):
        if 'openalex' not in url:
            return {'message':row}
        return {'title':'Study','doi':'https://doi.org/10.1000/test','type':'article',
                'authorships':[{'author':{'display_name':'John Smith'}}], 'publication_year':2026,
                'locations':[{'is_published':True,'source':{'type':'journal','display_name':'Journal'}}],
                'biblio':{'volume':'54','issue':'5','first_page':'1177','last_page':'1214'}}
    result=verify_publication({'title':'Study','authors':'John Smith','doi':'10.1000/test'},fetch,
                              force=True,metadata_only=True,publisher_fetch=lambda *a,**kw:{})
    assert result['volume']=='54' and result['pages']=='1177-1214'
    assert result['published_date']=='2026-3' and result['authors']=='John Smith'
    assert 'openalex.org' in result['metadata_provenance']
    cached = verify_publication(result,lambda *a,**kw:{'message':row},force=True,metadata_only=True,
                                publisher_fetch=lambda *a,**kw:pytest.fail('do not refetch complete records'))
    assert cached['pages']=='1177-1214' and 'openalex.org' in cached['metadata_provenance']
