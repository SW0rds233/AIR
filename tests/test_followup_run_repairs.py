from src.agents.protocol import ReviewIssueRef
from src.agents.review import judge_review, _needs_from
from src.publication.schemas import Block, Manuscript, Section
from src.publication.render_latex import render_latex
from src.agents.writing import render_markdown
from src.research.delivery import apply_review_gate
import pytest


def test_failed_review_has_no_score_and_cannot_be_current():
    verdict = judge_review([], semantic_reviewed=False)
    assert verdict['score'] in (None, 0)
    result = apply_review_gate({'accepted': True}, {**verdict, 'current': True})
    assert not result['review']['current']
    assert not result['accepted']
    assert any('最后一版未审阅' in text for text in result['blocking'])


def test_metadata_issue_cannot_be_assigned_to_writer():
    issue = ReviewIssueRef(issue_id='issue-citation', category='citation', severity='major',
                           summary='参考文献作者或年份待核对', suggested_owner='writing')
    needs = _needs_from([issue])
    assert needs[0].owner == 'evidence'
    assert needs[0].kind.value == 'more_sources'


def test_math_delimiters_are_not_nested_in_either_renderer():
    manuscript = Manuscript(title='Fixture', sections=[Section(heading='Methods', blocks=[
        Block(math=r'$$p=1-(1-a)(1-b),\qquad \Delta p=p-p_0$$'),
        Block(math=r'$F=qE$，$q$ 为可捕系数'),
        Block(math='$$x=1$$\n\n$$y=2$$'),
    ])])
    latex = render_latex(manuscript)
    assert '$$' not in latex
    assert '&$' not in latex
    markdown = render_markdown(manuscript)
    assert '$$ $$' not in markdown
    assert '$$$$' not in markdown


def test_targeted_patch_preserves_other_blocks_and_references():
    from src.agents.writing import apply_block_patch, revision_targets, build_packet_from_context
    from src.agents.protocol import AgentTask, ContextPack, ObjectRef
    prior = Manuscript(manuscript_id='paper', version=3, title='Study', sections=[
        Section(heading='结果', blocks=[Block(block_id='a', text='old',
            refs=[ObjectRef(id='source')], ref_kinds=['source']), Block(block_id='b', text='keep')])])
    context = ContextPack(objects={'manuscript': [prior.to_dict()]})
    packet = build_packet_from_context(AgentTask(agent='writing', objective='revise'), context)
    assert packet.revision_of == 'paper'
    targets = revision_targets(prior, [{'affected_refs': [{'id': 'a'}]}], {})
    assert [block.block_id for block in targets] == ['a']
    patched = apply_block_patch(prior, [{'block_id': 'a', 'text': 'new'}], {'a'})
    assert patched.all_blocks()[0].refs == prior.all_blocks()[0].refs
    assert patched.all_blocks()[1] == prior.all_blocks()[1]
    assert prior.all_blocks()[0].text == 'old'
    with pytest.raises(ValueError, match='未授权'):
        apply_block_patch(prior, [{'block_id': 'b', 'text': 'bad'}], {'a'})
    with pytest.raises(ValueError, match='引用'):
        apply_block_patch(prior, [{'block_id': 'a', 'refs': []}], {'a'})


def test_omitted_or_empty_topic_uses_task_library_and_is_recorded():
    from src.agents.base import AgentBase
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.runtime import AgentRuntime, ToolSpec, ToolLoop
    class Probe(AgentBase):
        role = 'evidence'
        def tools(self, *_):
            return [ToolSpec('search_local_kb', lambda query, topic='': topic)]
        def _run(self, task, context, runtime, usage, tools):
            loop = ToolLoop(tools)
            for args, expected in [({'query': 'x'}, 'bound'),
                    ({'query': 'x', 'topic': ''}, 'bound'),
                    ({'query': 'x', 'topic': 'explicit'}, 'explicit')]:
                observed = loop._execute({'name': 'search_local_kb', 'args': args}, grant=None)
                assert observed.result == expected
                assert observed.arguments['topic'] == expected
            return self.completed(task, 'checked')
    Probe().run(AgentTask(agent='evidence', objective='search', source_set_ids=['bound']),
                ContextPack(), AgentRuntime())


def test_review_quota_failure_requires_recovery_without_auto_retry(monkeypatch):
    from src.agents.review import ReviewAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    from types import SimpleNamespace
    agent = ReviewAgent()
    def fail(*args, **kwargs):
        raise RuntimeError('429: You exceeded your current quota')
    monkeypatch.setattr(agent, 'tool_loop', fail)
    result = agent._run(AgentTask(agent='review', objective='review'), ContextPack(),
        SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])
    assert result.payload['score'] == 0
    assert result.payload['recovery_required']
    assert not result.followup_needs


@pytest.mark.parametrize('version,relation,expected', [(1, 'partially_supports', 'closed'),
    (2, 'supports', 'blocked'), (1, 'insufficient', 'blocked')])
def test_evidence_recheck_closes_only_current_supported_obligation(version, relation, expected):
    from src.agents.reasoning import ReasoningAgent
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.runtime import AgentRuntime
    from src.research.schemas import Claim, ProofObligation
    claim = Claim(id='claim', statement='A claim')
    obligation = ProofObligation(id='obl', kind='evidence_support', statement='Support',
                                 claim_id=claim.id, acceptance_method='rule')
    context = ContextPack(objects={'claim': [claim.model_dump(mode='json')],
        'obligation': [obligation.model_dump(mode='json')], 'evidence': [{
            'id': 'ev', 'claim_id': claim.id, 'claim_version': version,
            'source_id': 'doc', 'support': relation, 'title': 'Source',
            'credibility': 'high', 'existence_verified': True, 'peer_reviewed': True}]})
    result = ReasoningAgent().run(AgentTask(agent='reasoning', objective='recheck',
        hints={'claim_id': claim.id, 'evidence_support_only': True}), context, AgentRuntime())
    update = next(p for p in result.proposed_changes if p.kind == 'obligation')
    assert update.payload['status'] == expected
    assert result.usage.llm_calls == 0
    assert all(p.kind != 'claim' for p in result.proposed_changes)


def test_pending_need_after_many_attempted_needs_is_dispatchable():
    from src.agents.protocol import ResearchNeed, NeedKind
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent
    brief = ResearchBrief(project_id='p', problem_id='q', main_question='Research')
    needs = [ResearchNeed(kind=NeedKind.derivation, statement=f'need {i}',
                         hints={'trigger_id': f'obligation:{i}'}) for i in range(12)]
    decision = SupervisorAgent().decide(brief=brief, plan=TeamPlan(brief_id=brief.brief_id),
        results={}, open_needs=needs, dispatched=[need.assignment_key('p', 'q') for need in needs[:-1]])
    assert any(task.objective == 'need 11' for task in decision.tasks)


def test_same_review_issue_stops_after_two_attempts():
    from src.agents.protocol import AgentTask, AgentResult, ResearchNeed, NeedKind, TaskOutcome
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent
    brief = ResearchBrief(project_id='p', problem_id='q', main_question='Research')
    plan = TeamPlan(brief_id=brief.brief_id)
    results = {}
    for i in range(2):
        task = AgentTask(agent='writing', objective=f'old revision {i}', hints={'review_issue_ids': ['same']})
        plan.add_task(task)
        results[task.task_id] = AgentResult(task_id=task.task_id, agent='writing', outcome=TaskOutcome.completed)
    need = ResearchNeed(kind=NeedKind.manuscript_revision, statement='new wording for same issue',
                         hints={'review_issue_ids': ['same'], 'trigger_id': 'version-4'})
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results, open_needs=[need])
    assert all(task.objective != need.statement for task in decision.tasks)


def test_round_preserves_unselected_needs_and_rechecks_new_evidence():
    from types import SimpleNamespace
    from src.agents.protocol import AgentTask, AgentResult, ChangeProposal, ResearchNeed, NeedKind, TaskOutcome, ContextPack
    from src.agents.supervisor import ResearchBrief, TeamPlan, SupervisorAgent, SupervisorDecision, DecisionKind
    from src.graph.research_graph import TeamRun, TeamRunOutcome, TeamLoopState
    brief = ResearchBrief(project_id='p', problem_id='q', main_question='Research')
    need = ResearchNeed(kind=NeedKind.more_sources, statement='first')
    waiting = ResearchNeed(kind=NeedKind.derivation, statement='waiting', hints={'trigger_id': 'waiting'})
    task = AgentTask(agent='evidence', objective='first', hints={'need_assignment': need.assignment_key('p', 'q')})
    run = TeamRun.__new__(TeamRun)
    run.project_id, run.problem_id, run.run_id, run.session_id = 'p', 'q', 'r', 's'
    run.prepare = lambda: None
    run.loop = TeamLoopState(brief=brief, plan=TeamPlan(brief_id=brief.brief_id), open_needs=[need, waiting])
    run.outcome = TeamRunOutcome(run_id='r')
    run.cancel = SimpleNamespace(cancelled=False)
    run.runtime = SimpleNamespace(run_budget=None, emit=lambda *_: None)
    run.supervisor = SimpleNamespace(
        decide=lambda **_: SupervisorDecision(decision=DecisionKind.dispatch, tasks=[task]),
        adopt=lambda p, t: p.add_task(t), replan=lambda p, *_a, **_k: p)
    run.max_rounds, run.max_review_revisions = 30, 3
    executed = []
    def execute(task, *_):
        executed.append(task)
        return AgentResult(task_id=task.task_id, agent=task.agent, outcome=TaskOutcome.completed,
            proposed_changes=[ChangeProposal(kind='evidence', payload={'claim_id': 'claim'})]
            if task.agent == 'evidence' else [])
    run._execute_one = execute
    run._build_context = lambda *_: ContextPack(objects={'obligation': [
        {'claim_id': 'claim', 'kind': 'evidence_support', 'status': 'open'}]})
    run._refresh_retry_allowance = lambda *_: None
    run.save_state = lambda: None
    assert run.step()
    assert [need.statement for need in run.loop.open_needs] == ['waiting']
    assert [task.agent for task in executed] == ['evidence', 'reasoning']
    assert executed[-1].hints['evidence_support_only']
    assert run.outcome.task_order == [task.task_id for task in executed]


def test_vector_reindex_resumes_batches_without_duplicates(tmp_path, monkeypatch):
    from src.kb import reindex
    from src.rag import vector_store
    db = tmp_path / 'kb.sqlite'
    db.touch()
    monkeypatch.setattr(reindex, 'default_db_path', lambda _: db)
    class Store:
        def __init__(self, *_): pass
        def list_documents(self): return [{'doc_id': 'doc', 'title': 'Title'}]
        def get_chunks(self, _): return [{'idx': i, 'text': f'chunk {i}'} for i in range(3)]
        def close(self): pass
    class Vector:
        ids = set()
        fail = True
        def get(self, **_): return {'ids': list(self.ids)}
        def add_documents(self, docs, ids):
            if self.fail and self.ids:
                self.fail = False
                raise RuntimeError('quota exhausted')
            assert not self.ids.intersection(ids)
            self.ids.update(ids)
    vector = Vector()
    monkeypatch.setattr(reindex, 'KBStore', Store)
    monkeypatch.setattr(vector_store, 'embedding_available', lambda **_: True)
    monkeypatch.setattr(vector_store, 'get_vector_store', lambda _: vector)
    first = reindex.reindex_library('topic', batch_size=1)
    assert not first['ok'] and first['indexed'] == 1
    second = reindex.reindex_library('topic', batch_size=1)
    assert second['ok'] and second['indexed'] == 2 and second['skipped'] == 1
    third = reindex.reindex_library('topic')
    assert third['indexed'] == 0 and third['skipped'] == 3


def test_model_rejection_names_invalid_field_and_passes_detail_to_rework(monkeypatch):
    from src.agents.modeling import ModelingAgent
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.runtime import AgentRuntime
    agent = ModelingAgent()
    monkeypatch.setattr(agent, 'propose_via_kernel', lambda *_: None)
    monkeypatch.setattr(agent, 'fallback_models', lambda *_: [
        {'name': 'candidate', 'variables': [{'symbol': {'invalid': True}}]}])
    result = agent.run(AgentTask(agent='modeling', objective='model'), ContextPack(), AgentRuntime())
    assert result.payload['schema_errors'][0]['field'] == 'variables.0.symbol'
    assert 'variables.0.symbol' in result.followup_needs[0].why
    assert not result.proposed_changes


def test_chinese_topic_maps_to_ascii_collection_name():
    import re

    from src.rag.vector_store import kb_collection_name

    pattern = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,510}[A-Za-z0-9]$')
    name = kb_collection_name('星网 星链')
    assert pattern.fullmatch(name), name
    assert name == kb_collection_name('星网 星链')          # 稳定: 同一主题同一集合
    # 导入标签 (空格) 与注册 id (下划线) 是同一个库, 不能落到两个集合
    assert name == kb_collection_name('星网_星链')
    assert name != kb_collection_name('星网 星链（二）')
    # 历史 ASCII 主题名必须原样保留, 否则既有向量全部失联
    assert kb_collection_name('research-proj-muwia7ognn2l-problem') == \
        'kb_research-proj-muwia7ognn2l-problem'
    assert kb_collection_name('shared-maritime-satellite-communications') == \
        'kb_shared-maritime-satellite-communications'


def test_reindex_uses_ascii_collection_name_for_chinese_topic(tmp_path, monkeypatch):
    from src.kb import reindex
    from src.rag import vector_store

    db = tmp_path / 'kb.sqlite'
    db.touch()
    monkeypatch.setattr(reindex, 'default_db_path', lambda _: db)
    seen = {}

    class Store:
        def __init__(self, *_): pass
        def list_documents(self): return [{'doc_id': 'doc', 'title': 'Title'}]
        def get_chunks(self, _): return [{'idx': 0, 'text': 'chunk'}]
        def close(self): pass

    class Vector:
        def get(self, **_): return {'ids': []}
        def add_documents(self, docs, ids): seen['ids'] = list(ids)

    monkeypatch.setattr(reindex, 'KBStore', Store)
    monkeypatch.setattr(vector_store, 'embedding_available', lambda **_: True)
    monkeypatch.setattr(vector_store, 'get_vector_store',
                        lambda name: (seen.setdefault('name', name), Vector())[1])

    report = reindex.reindex_library('星网 星链')
    assert report['ok'] and report['indexed'] == 1
    assert seen['name'] == vector_store.kb_collection_name('星网 星链')
