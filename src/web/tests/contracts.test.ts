/**
 * P2 契约测试: 前端类型必须能承接后端**真实**返回的字段。
 *
 * 这些 fixture 取自后端契约测试 (`tests/test_workbench_api.py`、
 * `tests/test_research_contract.py`、`tests/test_evalS` 之外的交付清单断言),
 * 用真实 payload 走一遍类型与字段检查, 避免前后端字段名悄悄漂移。
 */
import { describe, expect, it } from 'vitest';

// 契约按域分文件 (§9.5): 会话 / 研究 / 交付物。公共入口仍是 `contracts/index.ts`,
// 这里**直接按域导入** —— 那样"某个类型属于哪个域"在测试里也是显式的。
import type { SessionEvent, SessionState, StartSessionResponse } from '../src/contracts/session';
import type {
  ClaimRow,
  EvidenceRow,
  ExperimentRow,
  FeedbackResponse,
  ProblemContract,
  RetrievalCoverage,
  WorkbenchState,
} from '../src/contracts/research';
import type { DeliveryManifest } from '../src/contracts/publication';

const contract: ProblemContract = {
  goal: '分析信道变化对射频指纹可分性的影响',
  task_kind: 'scenario',
  objects: ['可分性', '信道变化'],
  units: {},
  scope: '',
  available_sources: ['rf-A'],
  allowed_methods: ['参数化情景推导'],
  forbidden_substitutions: ['缺数据时不得声称已估计因果效应'],
  success_criteria: ['结论能追溯到明确的前提、来源或验证输入'],
  stop_criteria: ['资料覆盖不足时说明覆盖限制'],
  paths: [{
    path_id: 'path-1', task_kind: 'scenario', statement: '在给定情景下分析',
    produces: '条件性结论', requires: ['情景参数范围及其依据'], recommended: true,
  }],
  clarification: '', basis: '只给文献/无数据', frozen_version: 0,
};

const coverage: RetrievalCoverage = {
  policy: 'autonomous', executed: true, queries: ['信道变化 可分性'],
  engines: ['arXiv'], hits: 3, ingested: 2, duplicates: 1,
  fulltext_available: 1, abstract_only: 1,
  uncovered: ['自主检索未返回任何可用记录'], failures: [], scope_note: '策略 autonomous',
};

describe('P2 前后端契约', () => {
  it('启动响应带问题契约与运行身份', () => {
    const payload: StartSessionResponse = {
      thread_id: 't1', session_id: 's1', mode: 'theory', run_id: 'run-1',
      project_id: 'p1', problem_id: 'p1', contract, spec_reused: false, resumed: false,
    };
    expect(payload.contract?.task_kind).toBe('scenario');
    expect(payload.contract?.paths).toHaveLength(1);
    expect(Object.keys(payload)).toEqual(expect.arrayContaining(
      ['thread_id', 'run_id', 'project_id', 'problem_id', 'contract']));
  });

  it('会话事件与状态补偿字段对齐', () => {
    const event: SessionEvent = { type: 'node', name: 'theory_step', text: '步骤', _seq: 3 };
    const state: SessionState = {
      thread_id: 't1', session_id: 's1', status: 'waiting', mode: 'theory',
      run_id: 'run-1', project_id: 'p1', problem_id: 'p1', event_seq: 3,
      pending_interrupt_id: 'int-1',
    };
    expect(event._seq).toBe(state.event_seq);
    expect(state.pending_interrupt_id).toBe('int-1');
  });

  it('工作台对象含 P1-3/P1-4 新增的可复核字段', () => {
    const claim: ClaimRow = {
      id: 'clm-1', version: 1, statement: '信道变化使可分性下降',
      status: 'supported', support_kind: 'textual_support',
    };
    const evidence: EvidenceRow = {
      id: 'ev-1', title: '文献', source_id: 'doc-1', locator: 'p.3',
      support: 'partially_supports', excerpt: '原文片段',
      notes: '该来源含视觉异常片段 (需核对, 不作为强证据): p.1', needs_review: true,
    };
    const experiment: ExperimentRow = {
      id: 'exp-1', title: '可区分检验', purpose: 'compare_mechanisms',
      execution_status: 'proposed', decision_rule: '若 A 则保留, 否则转为替代解释',
      missing_elements: ['模型/求解方式 (solver, model_equations)'],
      model_ref: { id: 'mdl-m1', name: '噪声路径' },
    };
    const workbench: WorkbenchState = {
      project_id: 'p1', problem_id: 'p1', run_id: 'run-1', branch_id: 'no-route',
      spec: { contract, coverage }, claims: [claim], obligations: [], verifications: [],
      evidence: [evidence], experiments: [experiment], routes: [], novelty: [],
      decisions: [], events: [], gaps: [], budget: { actions_used: 3 },
      objects: { claims: 1, evidence: 1 }, coverage_notes: ['尚无已确定结论'],
    };
    expect(workbench.evidence[0].needs_review).toBe(true);
    expect(workbench.experiments[0].missing_elements).toHaveLength(1);
    expect(workbench.objects.claims).toBe(1);
  });

  it('反馈响应区分“已落实动作”与“需要澄清”', () => {
    const clarified: FeedbackResponse = {
      ok: false, needs_clarification: true,
      clarify: ['要作用在哪条命题上? (clm-1 / clm-2)'], actions: [],
    };
    expect(clarified.needs_clarification).toBe(true);
    expect(clarified.actions).toEqual([]);
  });

  it('交付清单含资料版本、模型配置与正文可反查性', () => {
    const manifest: DeliveryManifest = {
      project_id: 'p1', problem_id: 'p1', run_id: 'run-1', snapshot_id: 'snap-1',
      delivery_level: '条件性研究报告', delivery_gate_passed: false,
      writing_map: { 'clm-1': 'claim-clm-1' },
      source_set: {
        source_set_id: 'rf-A', source_policy: 'user_kb', queries: ['信道 可分性'],
        uncovered: [], documents: [{ source_id: 'doc-1', title: '文献',
                                     file_hash: 'abc', locator: 'p.3' }],
        note: '只记录版本/hash 与原文定位, 不外发原始数据',
      },
      model_config: { main: 'deepseek-chat', prompt_version: '未登记' },
      budget_limits: { max_tokens: 0, max_cost_usd: 0 },
      usage: { tokens: 1234 },
      manuscript_traceability: {
        ok: true, mapped: [{ claim_id: 'clm-1', anchor: 'claim-clm-1' }],
        unmapped_claims: [], missing_anchors: [], note: '正文中的每条核心论断都能回到冻结快照对象',
      },
    };
    expect(manifest.source_set.documents[0].file_hash).toBe('abc');
    expect(manifest.manuscript_traceability.ok).toBe(true);
    expect(manifest.model_config.prompt_version).toContain('未登记');
  });
});
