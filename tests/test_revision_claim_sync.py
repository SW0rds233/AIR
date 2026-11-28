import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord, ObjectRef
from src.agents.writing import WritingAgent, build_packet_from_context
from src.publication.schemas import Block, Manuscript, Section


def test_revised_claim_reaches_direct_anchor_conclusion_and_abstract_without_rewriting_shared_sources():
    def cited(bid, kind, key):
        block = Block(block_id=bid, text=bid + ' 原稿')
        block.add_ref(kind, ObjectRef(id=key, version=1))
        return block
    paper = Manuscript(abstract='原摘要', sections=[
        Section(blocks=[cited('unrelated', 'source', 'doc-one'), cited('anchor', 'source', 'doc-one')]),
        Section(role='conclusion', blocks=[cited('conclusion', 'claim', 'clm-one'),
                                          cited('unchanged', 'claim', 'clm-one')])])
    issue = {'issue_id':'i-one', 'category':'science', 'summary':'阈值和行为混同',
             'affected_refs':[{'id':'anchor'}, {'id':'doc-one'}, {'id':'clm-one'}]}
    task = AgentTask(agent='writing', objective='修订', hints={'review_issue_ids':['i-one'],
                     'focus_blocks':['anchor','doc-one','clm-one'], 'review_cycle':True})
    ctx = ContextPack(objects={'manuscript':[paper.to_dict()], 'review_issue':[issue],
              'claim':[{'id':'clm-one','version':2,'statement':'阈值单调。','status':'proposed'}]},
              upstream=[{'issues':[issue, issue]}])
    packet = build_packet_from_context(task, ctx)
    assert len(packet.review_issues) == 1
    sent = []
    def invoke(messages):
        sent.append(messages[-1].content)
        return SimpleNamespace(content=json.dumps({'blocks':[
            {'block_id':'anchor', 'text':'条件只界定激励阈值。', 'checked_claim_versions':{'clm-one':2}},
            {'block_id':'conclusion', 'text':'该阈值不能单独预测行为。', 'checked_claim_versions':{'clm-one':2}},
            {'block_id':'unchanged','checked_claim_versions':{'clm-one':2}}],
            'abstract':'模型推导的是激励阈值的变化。在处罚和合规成本保持不变的条件下，检测概率上升扩大满足激励条件的集合。实际选择还取决于违规收益，目前缺少同一船只在不同检测条件下的配对观测。'}))
    runtime = SimpleNamespace(llm_available=lambda *_:True,
                              llm=lambda *a,**kw:SimpleNamespace(invoke=invoke))
    patched, note = WritingAgent()._patch_with_llm(task, runtime, UsageRecord(), packet)
    assert patched is not None, note
    assert '"block_id": "unrelated"' not in sent[0]
    assert sent[0].index('"block_id": "anchor"') < sent[0].index('"block_id": "conclusion"')
    assert patched.abstract.startswith('模型推导')
    assert [[r.version for k,r in zip(b.ref_kinds,b.refs) if k == 'claim']
            for b in patched.all_blocks()] == [[],[2],[2],[1]]
