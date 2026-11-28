import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.review import ReviewAgent, deterministic_issues


def test_negated_necessity_does_not_create_a_science_penalty():
    limits = '一旦资源向另一通道转移，原模型的方向关系在模型内也不再必然成立。'
    ctx = ContextPack(objects={'manuscript': [{'sections': [{'blocks': [
        {'block_id': 'blk-limit', 'text': limits},
        {'block_id': 'blk-overclaim', 'text': limits + '但干预效应必然成立。'},
    ]}]}]})
    findings = deterministic_issues(ctx)
    assert [ref.id for issue in findings for ref in issue.affected_refs] == ['blk-overclaim']


def test_anchored_overstatement_uses_current_claim_and_reaches_writer(monkeypatch):
    quote = '未达到激励阈值的船只必然违规。'
    ctx = ContextPack(objects={'manuscript': [{'manuscript_id': 'ms-current', 'version': 4,
        'sections': [{'blocks': [{'block_id': 'blk-threshold', 'text': quote}]}]}],
        'claim': [{'id': 'clm-threshold', 'version': 2, 'statement': '满足激励条件的集合扩大'}]})
    issue = {'category': 'science', 'severity': 'major', 'summary': '将激励条件混同于行为选择',
        'detail': '模型未包含违规收益，命题已限定为激励阈值；当前正文仍宣称必然违规。',
        'affected_ids': ['clm-threshold', 'blk-threshold'], 'quoted_text': quote,
        'suggested_owner': 'writing', 'repair_action': 'revise_text',
        'acceptance': ['删除“必然违规”的表述，说明阈值只描述满足激励条件的集合']}
    response = {'issues': [issue], 'category_scores': dict.fromkeys(
        ['science', 'fidelity', 'citation', 'readability', 'completeness'], 85)}
    agent = ReviewAgent()
    monkeypatch.setattr(agent, 'tool_loop', lambda *args, **kwargs: (json.dumps(response), []))
    result = agent._run(AgentTask(agent='review', objective='复核'), ctx,
                        SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])
    assert result.payload['semantic_reviewed']
    assert any(n.kind.value == 'manuscript_revision' and 'blk-threshold' in n.hints['focus_blocks']
               for n in result.followup_needs)
    ref = next(r for r in result.issues[0].affected_refs if r.id == 'clm-threshold')
    assert ref.version == 2
