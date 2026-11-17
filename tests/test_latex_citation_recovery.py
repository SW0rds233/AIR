import json

from src.publication.schemas import Block, Manuscript, Section


def test_aligned_in_prose_survives_without_inline_math_corruption():
    from src.publication.render_latex import render_latex
    block = Block(text=r'链式关系：\begin{aligned}K_t &= f_K(I_{t-\tau},G_t),\\ C_{r,t} &= g_C(K_t).\end{aligned}其中 $K_t$ 是能力。')
    tex = render_latex(Manuscript(sections=[Section(blocks=[block])]))
    assert r'\textbackslash{}begin{aligned}' not in tex
    assert r'I_{t-\tau}' in tex and r'\&=' not in tex
    assert r'\begin{equation}\begin{aligned}' in tex
    assert '其中' in tex


def test_math_environment_is_atomic_even_with_redundant_dollars():
    from src.publication.math_content import math_parts
    value = r'\begin{aligned}$K_t$ &= f_K($I_{t-\tau}$),\\ C&=1.\end{aligned}'
    parts = math_parts(value)
    assert len(parts) == 1 and parts[0][0]
    assert '$' not in parts[0][1] and r'K_t &= f_K(I_{t-\tau})' in parts[0][1]


def test_math_environment_preserves_inline_cases_and_following_identity():
    from src.publication.math_content import math_parts
    from src.publication.render_latex import escape_latex, render_latex
    inline = r'$f(x)=\begin{cases}x&x>0\\0&x\leq0\end{cases}$'
    assert r'\begin{equation}' not in escape_latex(inline)
    expression = r'\begin{aligned}\Delta B&=n\Delta y\end{aligned}\qquad \Delta B=\Pi_P+\Pi_S.'
    assert math_parts(expression)==[(True,expression)]
    tex=render_latex(Manuscript(sections=[Section(blocks=[Block(math=expression)])]))
    assert r'\qquad \Delta B=\Pi_P+\Pi_S.\end{equation}' in tex


def test_table_can_break_pages_and_repeats_header_without_losing_rows():
    from src.publication.render_latex import render_latex
    block = Block(role='table', heading='变量口径', data={'header':['变量','说明'],
        'rows':[[f'x{i}', '完整定义与量纲。'*15] for i in range(24)]})
    tex = render_latex(Manuscript(sections=[Section(blocks=[block])]))
    assert r'\begin{longtable}' in tex and r'\endhead' in tex
    assert r'\begin{table}' not in tex and 'x23' in tex


def publication(**kwargs):
    return {'title':'Study', 'authors':'SHI Weigang', 'year':'2009', 'venue':'Journal',
            'publication_type':'journal-article', 'publication_status':'published',
            'publication_verified_by':'crossref', **kwargs}


def test_structured_corporate_field_cannot_bypass_address_contamination_check():
    from src.publication.references import citation_eligible
    authors = [{'given':'SHI','family':'Weigang'},
        {'name':'Key Laboratory of Fisheries, Wuxi 214081, P. R. China;Freshwater Fisheries Research Center, Wuxi 214081, P. R. China'}]
    assert not citation_eligible(publication(author_metadata=json.dumps(authors)))
    assert citation_eligible(publication(authors='World Health Organization',
        author_metadata=json.dumps([{'name':'World Health Organization'}])))


def test_ambiguous_chinese_order_and_bilingual_aliases_require_reverification():
    from src.publication.references import citation_eligible
    assert not citation_eligible(publication(authors='鸣 马', publication_verified_by='openalex'))
    authors = [{'family':'张兰婷','sequence':'first'}, {'given':'ZHANG','family':'Lanting','sequence':'first'},
               {'family':'韩立民'}, {'given':'HAN','family':'Limin'}]
    assert not citation_eligible(publication(author_metadata=json.dumps(authors,ensure_ascii=False)))


def test_file_line_fatal_error_precedes_secondary_citation_warning():
    from src.publication.compiler import extract_latex_errors
    log = "LaTeX Warning: Citation `ref1' undefined on input line 43.\nE:/AIR/manuscript.tex:113: Mis\nsing } inserted.\n<inserted text>\n }\nl.113 $I_{t-$\n"
    errors = extract_latex_errors(log)
    assert errors and 'Missing } inserted' in errors[0]


def test_publisher_metadata_uses_one_language_and_checks_exact_doi():
    from src.kb.publisher_metadata import publisher_authors
    from src.publication.references import format_reference
    html = '''<meta name="citation_doi" content="10.18307/2009.0102">
    <meta name="citation_authors" xml:lang="cn" content="施炜纲,张敏莹,刘凯,徐东坡,段金荣">
    <meta name="citation_authors" xml:lang="en" content="SHI Weigang, ZHANG Minying, LIU Kai, XU Dongpo and DUAN Jinrong">'''
    metadata = publisher_authors(html, '10.18307/2009.0102', url='https://publisher.example/article')
    assert format_reference(publication(**metadata)).startswith('施炜纲, 张敏莹, 刘凯, 等.')
    assert not publisher_authors(html, '10.18307/other')
    western = publisher_authors('<meta name="citation_doi" content="10.1000/test">'
        '<meta name="citation_author" content="Smith, John">', '10.1000/test')
    assert format_reference(publication(**western)).startswith('SMITH J.')


def test_rechecking_bad_cached_authors_uses_publisher_and_keeps_excerpt():
    from src.kb.publication import verify_publication
    from src.publication.references import citation_eligible
    paper = publication(authors='鸣 马', doi='10.1000/test', excerpt='原文片段', relation='background')
    row = {'title':['Study'], 'DOI':paper['doi'], 'type':'journal-article', 'container-title':['Journal'],
           'author':[{'given':'鸣','family':'马'}], 'published':{'date-parts':[[2009]]}}
    calls = []
    def authors(doi):
        calls.append(doi)
        return {'authors':'马鸣, 赵旭阳, 张亚茹','author_metadata':json.dumps(
            [{'family':n} for n in ['马鸣','赵旭阳','张亚茹']],ensure_ascii=False), 'author_verified_by':'publisher'}
    result = verify_publication(paper, lambda *a, **k:{'message':row}, author_fetch=authors)
    assert calls == [paper['doi']] and citation_eligible(result)
    assert result['authors']=='马鸣, 赵旭阳, 张亚茹' and result['excerpt']==paper['excerpt']
    assert result['relation']=='background'
    unresolved = verify_publication(paper, lambda *a, **k:{'message':row}, author_fetch=lambda doi:{})
    assert not citation_eligible(unresolved) and unresolved['author_verified_by']=='unresolved'
    checked = {**paper,'authors':'马鸣','author_metadata':'[{"family":"马鸣"}]'}
    unresolved = verify_publication(checked, lambda *a, **k:{'message':row}, force=True, author_fetch=lambda doi:{})
    assert not citation_eligible(unresolved)


def test_author_repair_refreshes_registered_source_without_topic_search(monkeypatch):
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime
    from src.research.schemas import ObjectRef
    source = publication(source_id='doc-1',id='doc-1', authors='鸣 马', doi='10.1000/test',
                         excerpt='既有摘录', relation='background', claim_id='claim-1')
    calls=[]
    def verify(paper, **kwargs):
        calls.append(kwargs)
        return {**paper,'authors':'马鸣','author_metadata':'[{"family":"马鸣"}]','author_verified_by':'publisher'}
    monkeypatch.setattr('src.kb.publication.verify_publication', verify)
    monkeypatch.setattr(EvidenceAgent, 'plan_retrieval', lambda *a: (_ for _ in ()).throw(AssertionError('must not search topics')))
    task=AgentTask(agent='evidence', objective='核查参考文献作者元数据',source_policy='both',
                   input_refs=[ObjectRef(id='doc-1')],hints={'root_cause_kind':'citation','repair_action':'verify_source'})
    result=EvidenceAgent()._run(task,ContextPack(objects={'evidence':[source]}),AgentRuntime(),UsageRecord(),[])
    assert calls == [{'force':True}]
    payload=result.proposed_changes[0].payload
    assert payload['authors']=='马鸣' and payload['excerpt']=='既有摘录' and payload['relation']=='background'
    assert result.followup_needs[0].kind.value=='manuscript_revision'


def test_oversized_float_is_not_reported_as_clean_layout():
    from src.publication.compiler import layout_diagnostics
    result=layout_diagnostics('LaTeX Warning: Float too large for page by 385.17682pt on input line 96.')
    assert result['severity']=='error' and result['oversized_float_count']==1


def test_withdrawn_bad_author_reference_keeps_an_actionable_repair_target():
    from src.agents.writing import finalise_manuscript, WritingAgent
    from src.publication.schemas import WritingPacket
    from src.research.schemas import ObjectRef
    source=publication(source_id='doc-1',authors='鸣 马')
    manuscript=Manuscript(sections=[Section(blocks=[Block(text='相关背景。',
        refs=[ObjectRef(id='doc-1')],ref_kinds=['source'])])])
    packet=WritingPacket(sources=[source])
    finalise_manuscript(manuscript,packet)
    needs=WritingAgent()._needs_from(manuscript,packet,[])
    assert any(need.owner=='evidence' and need.hints.get('repair_action')=='verify_source'
               and need.blocked_refs[0].id=='doc-1' for need in needs)
    need=next(need for need in needs if need.owner=='evidence')
    block=manuscript.sections[0].blocks[0]
    assert block.block_id in need.hints['focus_blocks']
    packet.sources[0].update(authors='马鸣',author_metadata='[{"family":"马鸣"}]')
    block.add_ref('source',ObjectRef(id='doc-1'))
    finalise_manuscript(manuscript,packet)
    assert not any(gap.get('kind')=='ineligible_reference' for gap in manuscript.gaps)


def test_export_status_reports_actual_file_line_error(tmp_path, monkeypatch):
    from src.graph.team_session import TeamSession
    log="LaTeX Warning: Citation `ref1' undefined on input line 43.\nE:/AIR/manuscript.tex:113: Mis\nsing } inserted.\n<inserted text>\n }\n"
    monkeypatch.setattr('src.publication.compiler.compile_latex',lambda *a,**k:(False,log))
    (tmp_path/'manuscript.pdf').write_bytes(b'%PDF-1.7'+b'x'*2048)
    status,_=TeamSession._compile_package(None,tmp_path,'tex')
    assert 'Missing } inserted' in status and 'undefined' not in status
    assert (tmp_path/'manuscript.failed.pdf').is_file()
