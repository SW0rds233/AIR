import json

from src.publication.schemas import Block, Manuscript, Section
from src.research.schemas import ObjectRef


def cited_block(text, *ids):
    return Block(text=text, refs=[ObjectRef(id=i) for i in ids], ref_kinds=['source']*len(ids))


def test_citation_groups_are_sorted_deduplicated_and_share_numbering():
    from src.agents.writing import render_markdown
    from src.publication.render_latex import render_latex
    manuscript = Manuscript(title='论文', sections=[Section(heading='正文', blocks=[
        cited_block('首次引用。', 'a','b','c'), cited_block('再次引用。','c','a','b','a')])])
    assert '再次引用[1-3]。' in render_markdown(manuscript)
    assert r'再次引用\cite{ref1,ref2,ref3}。' in render_latex(manuscript)


def test_refuted_records_are_presented_as_counterexamples_to_writer():
    from src.agents.writing import _section_material
    from src.publication.schemas import WritingPacket
    packet = WritingPacket(verifications=[{'id':'v', 'claim_id':'c', 'validation_status':'counterexample_found',
        'status':'failed', 'scope':'target', 'counterexample':{'coverage':2}, 'detail':'反例赋值'}])
    material = _section_material(packet, 'revision', [])
    assert '已发现的反例' in material and '"coverage": 2' in material
    assert 'counterexample_found' in material


def test_unencoded_causal_mechanism_gets_evidence_not_formal_encoding():
    from src.research.schemas import Claim
    from src.research.problem_formulator import obligations_for_claim
    claim = Claim(claim_type='causal', study={'design':'theory'},
        statement='候选因果机制：数据回传在身份绑定与执法响应条件下提高违规成本',
        mechanism_links=['数据回传 → 可归因 → 执法响应 → 违规成本'])
    obligations = obligations_for_claim(claim)
    assert not any(o.acceptance_method == 'formal_encoding' for o in obligations)
    assert {'evidence_support','scope_check'} <= {o.kind for o in obligations}


def test_structured_author_names_and_chapter_format():
    from src.publication.references import format_reference
    from src.kb.publication import _crossref
    source = _crossref({'title':['研究'], 'author':[{'family':'马','given':'鸣'}],
                       'published':{'date-parts':[[2025]]}, 'type':'journal-article'})
    assert source['authors'] == '马鸣'
    entry = format_reference({'title':'Chapter', 'authors':'John Smith', 'publication_type':'book-chapter',
        'author_metadata':json.dumps([{'family':'Smith','given':'John'}]), 'venue':'Collected Studies',
        'publisher':'Press', 'publication_place':'London', 'year':'2020', 'pages':'12-19'})
    assert entry == 'SMITH J. Chapter[M]//Collected Studies. London: Press, 2020: 12-19.'


def test_online_report_retains_access_date_and_url():
    from src.publication.references import format_reference
    entry = format_reference({'title':'年度报告','authors':'中国科学院','publication_type':'report',
        'publisher':'中国科学院','publication_place':'北京','year':'2025','medium':'OL',
        'accessed_date':'2026-11-15','url':'https://example.org/report'})
    assert '[R/OL]' in entry and '[2026-11-15]' in entry and 'https://example.org/report' in entry


def test_affiliations_are_not_accepted_as_article_authors():
    from src.publication.references import citation_eligible
    source = {'title':'Study','authors':'Li Huang, 1. Engineering Research Center for Big Data, A University',
        'venue':'Journal', 'year':'2025', 'publication_type':'journal-article',
        'publication_status':'published', 'publication_verified_by':'crossref'}
    assert not citation_eligible(source)


def test_fidelity_source_repair_goes_to_evidence():
    from src.agents.protocol import ReviewIssueRef
    from src.agents.review import _needs_from
    issue = ReviewIssueRef(issue_id='i', category='fidelity', severity='major', summary='核查覆盖参数来源',
                           repair_action='verify_source', suggested_owner='writing')
    need, = _needs_from([issue])
    assert need.owner == 'evidence' and need.kind.value == 'more_sources'


def test_markdown_table_becomes_numbered_table_in_both_formats():
    from src.agents.writing import render_markdown
    from src.publication.render_latex import render_latex
    manuscript = Manuscript(title='论文', sections=[Section(heading='结果', blocks=[Block(
        text='表1 分析边界\n\n| 条件 | 用途 |\n| --- | --- |\n| 未决 | 进一步检验 |\n\n表后说明。')])])
    tex, md = render_latex(manuscript), render_markdown(manuscript)
    assert r'\caption{分析边界}' in tex and r'\begin{tabularx}' in tex
    assert '| --- |' not in tex and '表后说明。' in tex
    assert '**表 1 分析边界**' in md and '| 条件 | 用途 |' in md


def test_revision_covers_late_blocks_in_bounded_batches(monkeypatch):
    from types import SimpleNamespace
    from src.agents.writing import WritingAgent
    from src.publication.schemas import WritingPacket
    from src.agents.protocol import AgentTask, UsageRecord
    from src.agents.runtime import AgentRuntime
    manuscript = Manuscript(title='论文', sections=[Section(heading='结论', blocks=[
        Block(block_id=f'b{i}', text='待修订') for i in range(14)])])
    packet = WritingPacket(prior_manuscript=manuscript.to_dict(), review_issues=[
        {'issue_id':'i', 'affected_refs':[{'id':f'b{i}'} for i in range(14)]}])
    batches = []
    def invoke(messages):
        body = messages[-1].content
        data = json.JSONDecoder().raw_decode(body[body.index('{'):])[0]
        blocks = data['blocks']; batches.append(len(blocks))
        return SimpleNamespace(content=json.dumps({'blocks':[{'block_id':b['block_id'], 'text':'修订后'} for b in blocks]}))
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime,'llm_available',lambda *a:True)
    monkeypatch.setattr(runtime,'llm',lambda *a, **k:SimpleNamespace(invoke=invoke))
    revised, _ = WritingAgent()._patch_with_llm(AgentTask(agent='writing', objective='修订'), runtime, UsageRecord(), packet)
    assert batches == [12,2]
    assert all(b.text == '修订后' for b in revised.all_blocks())


def test_source_ranking_deduplicates_and_keeps_claim_scoped_assessments():
    from src.agents.writer_plan import rank_sources
    source = {'source_id':'s', 'title':'Paper', 'authors':'A Author','year':'2025','venue':'Journal',
        'publication_type':'journal-article','publication_status':'published','publication_verified_by':'crossref',
        'relation':'background','claim_id':'c1','content_level':'abstract','excerpt':'原文内容'}
    rows = rank_sources([source, {**source,'claim_id':'c2','relation':'insufficient'}])
    assert len(rows) == 1 and len(rows[0].get('_support_assessments', [])) == 2


def test_unverified_parameter_request_is_not_sent_as_a_text_only_revision():
    from src.agents.protocol import ContextPack, ReviewIssueRef
    from src.agents.review import _route_unverified_source_repairs, _needs_from
    issue = ReviewIssueRef(issue_id='i', category='fidelity', severity='major',
        summary='星网参数缺失，应补充来源中的数值', repair_action='revise_text', suggested_owner='writing',
        affected_refs=[ObjectRef(id='report')])
    context = ContextPack(objects={'evidence':[{'source_id':'report','publication_status':'unknown'}]})
    routed = _route_unverified_source_repairs([issue],context)
    assert _needs_from(routed)[0].owner == 'evidence'


def test_table_patch_changes_cells_without_replacing_object_identity():
    from src.agents.writing import apply_block_patch
    block=Block(block_id='table',role='table',heading='旧题',data={'header':['条件'], 'rows':[['旧值']]})
    manuscript=Manuscript(sections=[Section(blocks=[block])])
    revised=apply_block_patch(manuscript,[{'block_id':'table','heading':'新题','data':{'header':['条件'], 'rows':[['新值']]}}],{'table'})
    assert revised.all_blocks()[0].block_id == 'table' and revised.all_blocks()[0].data['rows']==[['新值']]


def test_review_gate_labels_unreviewed_complete_draft():
    from src.research.delivery import apply_review_gate
    result=apply_review_gate({'level':'完整论文','accepted':True},
                            {'semantic_reviewed':True,'accepted':False,'current':False,'score':62})
    assert result['level']=='完整论文（待复审）' and not result['accepted']


def test_review_does_not_apply_another_claims_insufficient_relation():
    from src.agents.protocol import ContextPack
    from src.agents.review import deterministic_issues
    source = {'source_id':'s', 'title':'Paper', 'authors':'A Author','year':'2025','venue':'Journal',
        'publication_type':'journal-article','publication_status':'published','publication_verified_by':'crossref',
        'relation':'background','claim_id':'c1','locator':'p. 1'}
    block = cited_block('相关工作背景。', 's')
    block.add_ref('claim', ObjectRef(id='c1'))
    manuscript = Manuscript(sections=[Section(blocks=[block])])
    context = ContextPack(objects={'manuscript':[manuscript.to_dict()],
        'evidence':[source, {**source,'claim_id':'c2','relation':'insufficient'}]})
    assert not any('支持关系不足' in issue.summary for issue in deterministic_issues(context))
    block.refs[-1] = ObjectRef(id='c2')
    context.objects['manuscript'] = [manuscript.to_dict()]
    assert any('支持关系不足' in issue.summary for issue in deterministic_issues(context))
