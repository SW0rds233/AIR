/**
 * 前后端契约类型 (计划书 P2): 会话事件 / 研究状态 / 反馈 / 交付清单只在这里定义一次。
 *
 * 页面逻辑 (`app.ts`) 仍是从内联脚本迁出的遗留代码, 尚未逐段类型化; 但**边界**
 * (window.AIR / 工作台 / 安全渲染 / 交付清单) 从这里取类型, 视图层不再各自
 * 声明结构相同的匿名对象。
 */

export type RunMode = 'survey' | 'theory';
export type RunStatus = 'idle' | 'running' | 'waiting' | 'done' | 'stopped' | 'error';

/** SSE 事件 (后端 `/api/sessions/{id}/events`)。 */
export interface SessionEvent {
  type: 'connected' | 'node' | 'log' | 'interrupt' | 'done' | 'stopped' | 'error' | string;
  text?: string;
  name?: string;
  message?: string;
  state?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  _seq?: number;
}

/** 会话状态补偿接口的返回。 */
export interface SessionState {
  thread_id: string;
  session_id: string;
  status: RunStatus;
  mode: RunMode;
  run_id: string;
  project_id: string;
  problem_id: string;
  event_seq: number;
  pending_interrupt_id?: string;
  final_state?: Record<string, unknown> | null;
}

/** 问题契约 (P0-2): 研究类型决定允许的方法与结论强度。 */
export type TaskKind = 'mechanism' | 'formal_proof' | 'empirical_causal' | 'scenario';
export type SourcePolicy = 'user_kb' | 'autonomous' | 'both';

export interface ResearchPath {
  path_id: string;
  task_kind: TaskKind;
  statement: string;
  produces: string;
  requires: string[];
  recommended: boolean;
}

export interface ProblemContract {
  goal: string;
  task_kind: TaskKind;
  objects: string[];
  units: Record<string, string>;
  scope: string;
  available_sources: string[];
  allowed_methods: string[];
  forbidden_substitutions: string[];
  success_criteria: string[];
  stop_criteria: string[];
  paths: ResearchPath[];
  clarification: string;
  basis: string;
  frozen_version: number;
}

/** 检索覆盖记录 (P0-1/§1 契约第 2 行)。 */
export interface RetrievalCoverage {
  policy: SourcePolicy;
  executed: boolean;
  queries: string[];
  engines: string[];
  hits: number;
  ingested: number;
  duplicates: number;
  fulltext_available: number;
  abstract_only: number;
  uncovered: string[];
  failures: string[];
  scope_note: string;
}

/** 启动会话的返回。 */
export interface StartSessionResponse {
  thread_id: string;
  session_id: string;
  mode: RunMode;
  run_id: string;
  project_id: string;
  problem_id: string;
  contract: ProblemContract | null;
  spec_reused: boolean;
  resumed: boolean;
}

/** 工作台单行对象 (只列前端真正读取的字段)。 */
export interface ClaimRow {
  id: string;
  version: number;
  statement: string;
  status: string;
  support_kind?: string;
  coverage?: string;
  validation_status?: string;
}

export interface EvidenceRow {
  id: string;
  title: string;
  source_id: string;
  locator: string;
  page?: number;
  support: string;
  excerpt: string;
  /** P1-4: 含视觉异常片段的来源标"需核对", 不作为强证据。 */
  notes?: string;
  needs_review?: boolean;
}

export interface ExperimentRow {
  id: string;
  title: string;
  purpose: string;
  execution_status: string;
  decision_rule: string;
  /** P1-3: 计划书必备要素里还缺哪些 (缺项只能维持草案)。 */
  missing_elements?: string[];
  model_ref?: { id?: string; name?: string };
}

export interface RouteRow {
  id: string;
  strategy: string;
  status: string;
  failure_reason: string;
  recovery_condition: string;
}

/** 工作台状态 (`/api/research/{project}/state`)。 */
export interface WorkbenchState {
  project_id: string;
  problem_id: string;
  run_id: string;
  branch_id: string;
  spec?: Record<string, unknown>;
  claims: ClaimRow[];
  obligations: Array<Record<string, unknown>>;
  verifications: Array<Record<string, unknown>>;
  evidence: EvidenceRow[];
  experiments: ExperimentRow[];
  routes: RouteRow[];
  novelty: Array<Record<string, unknown>>;
  decisions: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
  metrics?: Record<string, unknown>;
  log_anomalies?: Array<Record<string, unknown>>;
  gaps: Array<Record<string, unknown>>;
  budget: Record<string, unknown>;
  objects: Record<string, number>;
  coverage_notes?: string[];
}

/** 对象级反馈 (F1-5): 无法唯一定位时返回澄清候选, 不改研究状态。 */
export interface FeedbackRequest {
  response: string;
  object_id?: string;
  problem_id?: string;
}

export interface FeedbackResponse {
  ok?: boolean;
  needs_clarification?: boolean;
  clarify?: string[];
  actions?: Array<Record<string, unknown>>;
}

/** 交付清单 (manifest.json)。 */
export interface DeliveryManifest {
  project_id: string;
  problem_id: string;
  run_id: string;
  branch_id?: string;
  snapshot_id: string;
  delivery_level: string;
  delivery_gate_passed?: boolean | null;
  writing_map: Record<string, string>;
  source_set: {
    source_set_id: string;
    source_policy: string;
    queries: string[];
    uncovered: string[];
    documents: Array<{ source_id: string; title: string; file_hash: string; locator: string }>;
    note: string;
  };
  model_config: Record<string, string>;
  budget_limits: Record<string, number>;
  usage: Record<string, unknown>;
  manuscript_traceability: {
    ok: boolean;
    mapped: Array<{ claim_id: string; anchor: string }>;
    unmapped_claims: string[];
    missing_anchors: string[];
    note: string;
  };
}

export interface ArtifactEntry {
  name: string;
  size?: number;
  problem_id?: string;
  run_id?: string;
  delivery_level?: string;
}
