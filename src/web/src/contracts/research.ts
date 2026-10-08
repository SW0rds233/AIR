/**
 * 研究契约: 问题形式化、研究对象与工作台读取的行。
 *
 * 这些字段的权威在 `research/schemas.py`; 前端只声明**自己真正读取**的部分
 * (多声明一个字段就等于多一处会漂移的假设)。
 */

/** 问题契约: 研究类型决定允许的方法与结论强度。 */
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
