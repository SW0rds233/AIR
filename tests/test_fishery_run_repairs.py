import json
from types import SimpleNamespace

from src.agents.base import AgentBase
from src.agents.protocol import AgentResult, AgentTask, ContextPack
from src.agents.runtime import AgentRuntime
from src.research.acceptance import _cited_numbers, _unlabelled_placeholders
from src.research.evidence import quote_is_locatable
from src.research.schemas import SourceEvidence


def test_usage_is_not_merged_with_itself():
    class OneCall(AgentBase):
        role = 'evidence'

        def _run(self, task, context, runtime, usage, tools):
            usage.llm_calls = 1
            usage.cost_usd = 0.25
            return AgentResult(task_id=task.task_id, usage=usage)

    result = OneCall().run(AgentTask(agent='evidence', objective='test'), ContextPack(), AgentRuntime())
    assert result.usage.llm_calls == 1
    assert result.usage.cost_usd == 0.25


def test_math_and_code_are_not_citations():
    text = r'范围 $c,d\in[0,1]$、\(x\in[2,3]\)、\[[4,5]\]。文献[6,7]。`a[8]`'
    assert _cited_numbers(text) == {6, 7}
    assert _cited_numbers('```python\na[9]\n```\n文献[10]') == {10}


def test_substantive_uncertainty_is_not_a_template_placeholder():
    assert _unlabelled_placeholders('临界阈值待定，需进一步采集数据。') == []
    assert _unlabelled_placeholders('作者姓名：待填写')


def test_entire_quote_must_exist_in_source():
    item = SourceEvidence(excerpt='The mobile service improved market access.', location='abstract')
    assert not quote_is_locatable(item, 'The mobile service improved income by 90 percent.')


def test_quote_matches_across_whitespace_differences():
    """PDF 原文的换行与模型引文的空格只差空白，不应把真实支持判成待审。"""
    wrapped = SourceEvidence(
        excerpt='The mobile service\nimproved   market access.\n\nReported in 2025.',
        location='p3')
    assert quote_is_locatable(wrapped, 'The mobile service improved market access.')
    assert quote_is_locatable(wrapped, 'improved market access. Reported in 2025.')
    # 折叠空白不放宽"逐字一致": 改写过的数字仍然不能命中
    assert not quote_is_locatable(wrapped, 'improved market access. Reported in 2026.')


def test_assessment_skips_when_evidence_model_missing():
    """只有主控配了模型时，证据任务应跳过判定，而不是在取模型时崩溃。"""
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import UsageRecord
    from src.research.schemas import Claim

    calls = []

    def _llm(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('证据模型不可用时不应取模型')

    runtime = SimpleNamespace(
        llm_available=lambda stage='': stage == 'supervisor',
        llm=_llm, emit=lambda *args: None)
    rows = [{'source_id': 'doc-1', 'excerpt': 'text', 'locator': 'abstract',
             'relation': 'insufficient'}]
    claim = Claim(statement='Mobile service improved market access.')
    context = ContextPack(objects={'claim': [claim.model_dump(mode='json')]})
    judged = EvidenceAgent().assess_sources(
        AgentTask(agent='evidence', objective='test'), context, runtime, UsageRecord(), rows)
    assert judged == rows
    assert calls == []


def test_assessment_degrades_when_model_lookup_fails():
    """取模型抛错时保留未判定行，检索任务不得整体失败。"""
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import UsageRecord
    from src.research.schemas import Claim

    def _explode(*args, **kwargs):
        raise RuntimeError('运行时未注入可用的 LLM (离线模式请用确定性实现)')

    runtime = SimpleNamespace(llm_available=lambda *_: True, llm=_explode,
                              emit=lambda *args: None)
    rows = [{'source_id': 'doc-1', 'excerpt': 'text', 'locator': 'abstract',
             'relation': 'insufficient'}]
    claim = Claim(statement='Mobile service improved market access.')
    context = ContextPack(objects={'claim': [claim.model_dump(mode='json')]})
    judged = EvidenceAgent().assess_sources(
        AgentTask(agent='evidence', objective='test'), context, runtime, UsageRecord(), rows)
    assert judged == rows


def test_default_llm_timeout_keeps_reviewer_headroom_and_is_bounded():
    import os

    from src import config

    if os.getenv('LLM_TIMEOUT'):
        import pytest

        pytest.skip('环境显式设置了 LLM_TIMEOUT, 默认值断言不适用')
    # 实测审阅单次约 158s: 默认值既要有余量, 也不能让失控调用拖到 45 分钟
    assert 240 <= config.LLM_TIMEOUT <= 600


def test_semantic_judgment_keeps_claim_binding_and_quote():
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import UsageRecord
    from src.research.schemas import Claim

    claim = Claim(statement='Mobile service improved market access.')
    quote = 'The mobile service improved market access.'
    response = SimpleNamespace(content=json.dumps({
        'relation': 'supports', 'reason': 'Direct measured finding', 'quote': quote,
    }))
    runtime = SimpleNamespace(
        llm_available=lambda *_: True,
        llm=lambda *a, **k: SimpleNamespace(invoke=lambda _: response), emit=lambda *a: None,
    )
    rows = [{'source_id': 'doc-1', 'excerpt': quote, 'locator': 'abstract',
             'content_level': 'abstract', 'relation': 'insufficient'}]
    context = ContextPack(objects={'claim': [claim.model_dump(mode='json')]})
    judged = EvidenceAgent().assess_sources(
        AgentTask(agent='evidence', objective='test'), context, runtime, UsageRecord(), rows)
    assert judged[0]['relation'] == 'supports'
    assert judged[0]['claim_id'] == claim.id
    assert judged[0]['support_evidence'] == quote
    links = EvidenceAgent()._evidence_links(AgentTask(agent='evidence', objective='test'), judged, context)
    assert links[0].payload['relation'] == 'supports'
    assert links[0].payload['excerpt'] == quote


def test_team_retrieval_judgment_reaches_obligation_and_writer(monkeypatch):
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import UsageRecord
    from src.agents.reasoning import _formalisable_subject
    from src.publication.references import research_citation_eligible
    from src.research.schemas import Claim, ProofObligation, RetrievalCoverage
    from src.research.verification_service import _rule_verdict

    claim = Claim(statement='Mobile service improved market access.')
    quote = 'The mobile service improved market access.'
    source = {'source_id': 'doc-mobile', 'title': 'Mobile service and fisheries',
              'authors': 'A Author', 'year': '2025', 'venue': 'Fisheries Research',
              'publication_status': 'published', 'publication_verified_by': 'crossref',
              'publication_type': 'journal-article', 'excerpt': quote,
              'locator': 'abstract chars 0-42', 'content_level': 'abstract',
              'relation': 'insufficient'}
    events = []
    runtime = SimpleNamespace(
        llm_available=lambda *_: True, search_cache={},
        llm=lambda *a, **k: SimpleNamespace(invoke=lambda _: SimpleNamespace(
            content=json.dumps({'relation': 'supports', 'reason': 'Direct finding', 'quote': quote}))),
        emit=lambda *args: events.append(args),
    )
    agent = EvidenceAgent()
    monkeypatch.setattr(agent, 'plan_retrieval', lambda *a: {'queries': []})
    monkeypatch.setattr(agent, 'tool_loop', lambda *a, **k: ('', []))
    monkeypatch.setattr(agent, '_novelty_records', lambda *a: [])
    monkeypatch.setattr(agent, 'retrieve', lambda *a, **k: (
        [source], RetrievalCoverage(executed=True, hits=1, ingested=1, abstract_only=1), []))
    task = AgentTask(agent='evidence', objective=claim.statement, source_policy='both')
    context = ContextPack(request=claim.statement, objects={'claim': [claim.model_dump(mode='json')]})
    result = agent._run(task, context, runtime, UsageRecord(), [])
    evidence = next(change.payload for change in result.proposed_changes if change.kind == 'evidence')
    assert research_citation_eligible(evidence)
    assert any(need.hints.get('claim_id') == claim.id for need in result.followup_needs)
    assert any(kind == 'evidence_retrieval' and data['hits'] == 1 for kind, data in events)
    reasoning_task = AgentTask(agent='reasoning', objective='verify', hints={'claim_id': claim.id})
    context.objects['evidence'] = [evidence]
    _, _, sources = _formalisable_subject(context, reasoning_task)
    assert sources[0].location == 'abstract chars 0-42'
    obligation = ProofObligation(kind='evidence_support', statement='verify support', claim_id=claim.id)
    passed, _, _ = _rule_verdict(claim, obligation, sources)
    assert passed
    # 版本变更后旧关系不能继续支撑新命题。
    context.objects['claim'][0]['version'] = 2
    assert _formalisable_subject(context, reasoning_task)[2] == []


def test_invalid_judgment_cannot_create_support():
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import UsageRecord
    from src.research.schemas import Claim

    row = {'source_id': 'doc-1', 'excerpt': 'The mobile service improved market access.',
           'locator': 'abstract', 'relation': 'insufficient'}
    claim = Claim(statement='Mobile service improved market access.')
    runtime = SimpleNamespace(
        llm_available=lambda *_: True,
        llm=lambda *a, **k: SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps({
            'relation': 'supports', 'quote': 'The mobile service improved income by 90 percent.'}))),
        emit=lambda *a: None)
    result = EvidenceAgent().assess_sources(
        AgentTask(agent='evidence', objective='test'),
        ContextPack(objects={'claim': [claim.model_dump(mode='json')]}), runtime, UsageRecord(), [row])
    assert result[0]['relation'] == 'insufficient'


def test_natural_language_derivative_is_not_sent_to_solver():
    from src.research.critic import counterexample_check
    from src.research.schemas import Claim, Relation

    assert counterexample_check(Claim(statement='effect', lhs='∂p/∂I', rhs='0', relation=Relation.gt)) is None
    assert counterexample_check(Claim(statement='square', lhs='x**2', rhs='0', relation=Relation.ge)) is not None


def test_unresolved_summary_preserves_details_in_attachment(tmp_path):
    from src.research.package import write_unresolved_report
    from src.research.schemas import ResearchSnapshot

    notes = [f'检索过程 {index}' for index in range(300)]
    report = write_unresolved_report(tmp_path, ResearchSnapshot(project_id='test'), notes)
    assert len(report.read_text(encoding='utf-8').splitlines()) < 40
    assert json.loads((tmp_path / 'unresolved-details.json').read_text(encoding='utf-8')) == notes


def test_last_manuscript_gets_one_final_review():
    from src.agents.protocol import TaskOutcome
    from src.graph.research_graph import _final_review_task

    results = {
        'r': AgentResult(task_id='r', agent='review', outcome=TaskOutcome.completed),
        'w': AgentResult(task_id='w', agent='writing', outcome=TaskOutcome.partial),
    }
    task = _final_review_task(results, ['r', 'w'])
    assert task.agent == 'review'
    assert task.hints['final_review_for'] == 'w'
    results['r2'] = AgentResult(task_id='r2', agent='review', outcome=TaskOutcome.completed)
    assert _final_review_task(results, ['r', 'w', 'r2']) is None
