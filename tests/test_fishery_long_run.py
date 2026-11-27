import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.reasoning import ReasoningAgent
from src.agents.writing import render_markdown
from src.publication.render_latex import render_latex
from src.publication.schemas import Block, Manuscript, Section
from src.research.acceptance import theory_validity_gate


def propose(monkeypatch, *, task=None, **fields):
    candidate = {
        'id': 'c-coverage-v1',
        'statement': '若覆盖量 x 非负，则扩大一单位后的覆盖量大于零。',
        'claim_type': 'descriptive', 'study': {'design': 'theory'},
        'formal_spec': {'variables': {'x': 'Real'}, 'premises': ['x>=0'], 'conclusion': 'x + 1 > 0'},
        'lhs': 'x+1', 'relation': '>', 'rhs': '0', **fields,
    }
    agent = ReasoningAgent()
    response = {'claims': [candidate], 'needs': [
        {'kind': 'derivation', 'statement': '核查候选 c-coverage-v1 的符号推论'}]}
    monkeypatch.setattr(agent, 'tool_loop', lambda *a, **kw: (json.dumps(response), []))
    return agent._run(task or AgentTask(agent='reasoning', objective='综合机制'), ContextPack(request='研究覆盖'),
                      SimpleNamespace(llm_available=lambda *args: True), UsageRecord(), [])


def test_redundant_comparison_does_not_erase_conditional_candidate(monkeypatch):
    result = propose(monkeypatch)
    claims = [c for c in result.proposed_changes if c.kind == 'claim']
    assert len(claims) == 1
    payload = claims[0].payload
    assert payload['formal_spec']['premises'] == ['x>=0']
    assert not payload.get('lhs') and not payload.get('rhs')
    assert any(n.hints.get('claim_id') == claims[0].object_id for n in result.followup_needs)


def test_different_targets_do_not_silently_complete_or_spawn_verification(monkeypatch):
    result = propose(monkeypatch, rhs='4')
    assert result.outcome.value == 'blocked'
    assert result.payload['candidate_problems']
    assert not any(n.hints.get('verification_only') for n in result.followup_needs)
    assert not any('c-coverage-v1' in n.statement for n in result.followup_needs)

    from src.agents.supervisor import ResearchBrief, needs_to_tasks
    repair, = needs_to_tasks(result.followup_needs, brief=ResearchBrief(main_question='研究覆盖'),
                             plan_version=1)
    corrected = propose(monkeypatch, task=repair, lhs='', rhs='', relation='custom')
    assert any(c.kind == 'claim' for c in corrected.proposed_changes)
    assert any(n.hints.get('verification_only') for n in corrected.followup_needs)


def test_theory_gate_needs_a_claim():
    gate = theory_validity_gate([], [], [], [])
    assert not gate.passed and any('命题' in issue for issue in gate.unresolved)


def test_table_mentions_follow_render_order_and_leave_the_authoring_blocks_editable():
    first = Block(role='table', text='变量定义表汇总分析单位。', heading='定义',
                  data={'header':['符号'], 'rows':[['x']]})
    second = Block(role='table', text='下表比较两种测量口径。', heading='比较',
                   data={'header':['口径'], 'rows':[['连续']]})
    paper = Manuscript(sections=[Section(blocks=[second, first])])
    for text in (render_markdown(paper), render_latex(paper)):
        assert '表 1 比较两种测量口径' in text and '表 2 汇总分析单位' in text
    assert second.text.startswith('下表')
