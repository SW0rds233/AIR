"""Regressions from the November 26 interrupted writing/review run."""
import pytest
from src.agents.protocol import AgentTask, ContextPack, UsageRecord


def test_enclosed_list_markers_are_font_independent_in_prose_and_math():
    from src.publication.render_latex import escape_latex, escape_math
    for text in ['①②③⑳⑴⒇❶❿', 'Ⓐⓩ']:
        prose, math = escape_latex(text), escape_math(text)
        assert not any(c in prose or c in math for c in text)
    assert r'\textcircled{1}' in escape_latex('①')
    assert '(1)' in escape_latex('⑴')


def test_shorthand_subscript_then_superscript_preserves_one_formula():
    from src.publication.render_latex import escape_latex
    result = escape_latex('Y_it^(m) 与 X_it^p')
    assert r'\textasciicircum' not in result
    assert 'Y_{it}^{(m)}' in result and 'X_{it}^{p}' in result


def test_fallback_validation_is_rendered_by_fields_without_truncated_dicts():
    from src.agents.writing import WritingAgent
    from src.publication.schemas import WritingPacket
    plan={'competing':['H1 候选效应','H2 共同冲击'],
          'design':{'treatment':'终端采用','outcome':'事故率','units':'船月',
                    'controls':['气象','油价']},'falsification':['负对照检验']}
    paper,_=WritingAgent().deterministic_manuscript(AgentTask(agent='writing',objective='写作'),
        WritingPacket(validations=[plan]))
    text='\n'.join(b.text for b in paper.all_blocks())
    assert "{'competing':" not in text and "'treatment':" not in text
    assert all(value in text for value in ['H1 候选效应','终端采用','事故率','船月','气象','负对照检验'])


def test_a_transient_model_connection_failure_retries_within_the_same_budget(monkeypatch):
    from src.utils.network_budget import NetworkBudget,model_call,task_network_budget
    monkeypatch.setattr('src.utils.network_budget.time.sleep',lambda _:None)
    attempts=[]
    def invoke():
        attempts.append(1)
        if len(attempts)==1:
            raise ConnectionError('temporary disconnect')
        return 'recovered'
    budget=NetworkBudget(seconds=10,role='evidence')
    with task_network_budget(budget):
        assert model_call(invoke,timeout=2,phase='llm')=='recovered'
    assert budget.model_attempts==2 and budget.state.evidence_model_attempts==2
    assert not budget.deferred and len(attempts)==2


def test_model_retries_never_replay_validation_errors(monkeypatch):
    from src.utils.network_budget import NetworkBudget,model_call,task_network_budget
    attempts=[]
    def invoke():
        attempts.append(1)
        raise ValueError('invalid input')
    with task_network_budget(NetworkBudget(seconds=10)):
        with pytest.raises(ValueError):
            model_call(invoke,timeout=2,phase='llm')
    assert len(attempts)==1


def test_modeling_cannot_persist_a_made_up_claim_reference(monkeypatch):
    import json
    from types import SimpleNamespace
    from src.agents.modeling import ModelingAgent
    agent=ModelingAgent()
    monkeypatch.setattr(agent,'propose_via_kernel',lambda *a,**k:None)
    model={'name':'候选模型','claim_id':'scope=claim','mechanism':'条件路径',
           'variables':[{'symbol':'x','domain':'Real'}]}
    runtime=SimpleNamespace(llm_available=lambda *_:True,emit=lambda *a:None,
        llm=lambda *a,**k:SimpleNamespace(invoke=lambda *_:SimpleNamespace(content=json.dumps({'models':[model]}))))
    context=ContextPack(objects={'claim':[{'id':'clm-real','statement':'研究问题'}],
        'evidence':[{'id':'s','source_id':'s','excerpt':'技术背景','locator':'p1','relation':'background'}]})
    result=agent._run(AgentTask(agent='modeling',objective='建模'),context,runtime,UsageRecord(),[])
    submitted=[p.payload for p in result.proposed_changes if p.kind=='model']
    assert submitted and not submitted[0]['claim_id']
    assert any('scope=claim' in text for text in result.unresolved)


def test_error_diagnostics_keep_cause_types_and_host_without_credentials():
    from types import SimpleNamespace
    from src.utils.network_budget import error_diagnostics
    inner=OSError('secret request details')
    outer=ConnectionError('Connection error')
    outer.__cause__=inner
    outer.request=SimpleNamespace(url='https://user:password@example.test/v1?api_key=secret')
    details=error_diagnostics(outer)
    assert details['endpoint_host']=='example.test'
    assert details['error_chain']==['ConnectionError','OSError']
    assert not any(s in str(details) for s in ['password','api_key','secret','/v1'])


def test_network_cooldown_is_waited_once_and_original_task_is_reopened(monkeypatch):
    from types import SimpleNamespace
    from src.graph.research_graph import TeamRun,TeamLoopState
    from src.agents.protocol import AgentResult
    from src.agents.supervisor import ResearchBrief,TeamPlan,assignment_key
    from src.agents.runtime import CancelToken
    from src.utils.network_budget import NetworkState
    clock=[100.]
    monkeypatch.setattr('src.graph.research_graph.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('src.graph.research_graph.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    task=AgentTask(task_id='review-original',agent='review',objective='审阅')
    result=AgentResult(task_id=task.task_id,agent='review',outcome='partial',
        payload={'pending_resume':True,'deferred_kind':'network','retry_after_seconds':60})
    run=TeamRun.__new__(TeamRun)
    run.loop=TeamLoopState(brief=ResearchBrief(main_question='研究问题'),
        plan=TeamPlan(tasks=[task.model_dump(mode='json')]),results={task.task_id:result},rounds=3)
    run.max_rounds=12
    run.cancel=CancelToken()
    run.runtime=SimpleNamespace(network_state=NetworkState(failures=3,unavailable_until=160.),emit=lambda *a:None)
    run.save_state=lambda **kwargs:None
    assert run._recover_network_task(task,result,run.loop)
    assert clock[0]==160. and run.runtime.network_state.failures==0
    assert assignment_key(run.loop.brief,'review','审阅') in run.loop.pending_retries
    assert not run._recover_network_task(task,result,run.loop)


def test_mixed_language_cartesian_product_can_wrap_in_table_cell():
    from src.publication.render_latex import _render_table
    tex='\n'.join(_render_table({'header':['符号','含义','取值域','单位'],
        'rows':[['P','接触点集合','C_candidate×A_candidate×{空间覆盖,物理设施,频谱,服务可及}×{可能,未知}','四元组']]}))
    assert r'$\times$\allowbreak{}' in tex


def test_reader_text_does_not_leak_model_and_document_storage_ids():
    from src.agents.writing import _strip_internal_ids
    assert 'doc-1234567890' not in _strip_internal_ids('资料 doc-1234567890 未核实')
    assert 'mdl-12345678' not in _strip_internal_ids('模型 mdl-12345678 待检查')


def test_unreviewed_delivery_is_labeled_explicitly():
    from src.research.delivery import apply_review_gate
    result=apply_review_gate({'level':'研究备忘录','accepted':False},
        {'semantic_reviewed':False,'decision':'unreviewed'})
    assert result['level']=='研究备忘录（未审阅）' and not result['accepted']


def test_persistent_model_disconnect_has_a_finite_attempt_limit(monkeypatch):
    from src.utils.network_budget import NetworkBudget,model_call,task_network_budget
    monkeypatch.setattr('src.utils.network_budget.time.sleep',lambda _:None)
    attempts=[]
    def invoke():
        attempts.append(1)
        raise ConnectionError('offline')
    budget=NetworkBudget(seconds=20)
    with task_network_budget(budget), pytest.raises(ConnectionError):
        model_call(invoke,timeout=1,phase='llm')
    assert len(attempts)==3 and budget.deferred


def test_single_call_timeout_is_not_replayed_or_marked_as_network_outage():
    from src.utils.network_budget import NetworkBudget,model_call,task_network_budget,ModelCallTimeout
    attempts=[]
    def invoke():
        attempts.append(1)
        raise ModelCallTimeout('late response')
    budget=NetworkBudget(seconds=20)
    with task_network_budget(budget), pytest.raises(ModelCallTimeout):
        model_call(invoke,timeout=1,phase='llm')
    assert len(attempts)==1 and not budget.deferred and budget.state.failures==0


def test_cooldown_retry_allowance_survives_restoring_checkpoint():
    from src.graph.research_graph import rebuild_loop_state
    restored=rebuild_loop_state({'rounds':3,'network_recoveries':1})
    assert restored is not None and restored.network_recoveries==1


@pytest.mark.parametrize("kind,counter", [
    ("network", "network_recoveries"), ("watchdog_gap", "watchdog_recoveries"),
])
def test_actual_team_steps_resume_the_original_review_without_replaying_done_tasks(tmp_path,monkeypatch,kind,counter):
    from src.graph.research_graph import TeamRun,TeamLoopState
    from src.research.task_store import TaskStore
    from src.agents.supervisor import SupervisorAgent,TeamPlan,ResearchBrief
    from src.agents.protocol import AgentResult
    store=TaskStore('p',db_path=str(tmp_path/'team.sqlite'))
    try:
        run=TeamRun(project_id='p',problem_id='q',run_id='r',task_store=store,
            supervisor=SupervisorAgent(),max_rounds=12)
        task=AgentTask(task_id='original-review',agent='review',objective='审阅当前论文')
        run.loop=TeamLoopState(brief=ResearchBrief(project_id='p',problem_id='q',main_question='研究问题'),
            plan=TeamPlan(tasks=[task.model_dump(mode='json')],edges={task.task_id:[]}))
        monkeypatch.setattr(run,'prepare',lambda:run.loop.brief)
        calls=[]
        def execute(task,results):
            calls.append(task.task_id)
            payload=({'pending_resume':True,'deferred_kind':kind,'retry_after_seconds':0}
                     if len(calls)==1 else {'semantic_reviewed':True})
            return AgentResult(agent='review',task_id=task.task_id,outcome='partial',payload=payload)
        monkeypatch.setattr(run,'_execute_one',execute)
        assert run.step() and not run.loop.finished and getattr(run.loop, counter) == 1
        restored = run._load_loop_state()
        assert restored and getattr(restored, counter) == 1
        assert run.restore_state() and run.reopen_deferred()
        assert run.loop.plan.tasks[0]["attempt"] == 2
        run.step()
        assert calls==['original-review','original-review']
        assert not run.loop.results[task.task_id].pending_resume
    finally:
        store.close()
