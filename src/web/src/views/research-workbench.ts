/*!
 * 工作台只读视图契约与标签映射 (计划书 §2 F4 / §4 `views/*`)。
 *
 * 为什么把这些搬出 `app.ts`:
 * - 它们**不接触 DOM**, 是完全可以单元测试的纯函数; 留在 1700 行的遗留页面脚本里
 *   既无法被类型检查, 也无法在 Vitest 里直接验证;
 * - 计划书 §4 要求前端按 `views/*` 整理, 服务端只提供**稳定的工作台投影**。
 *   这里把该投影的前端契约 (字段名与可选性) 写成显式类型, 字段改名/少字段时
 *   由类型检查而不是"页面上出现 undefined"来发现。
 *
 * 硬约束: 本模块只做"数据 → 文本/选项", 不发起请求、不读写 `window` 状态,
 * 也不把外部文本当 HTML (转义一律走 `escapeHtml`)。
 */

/** 工作台状态里的一条结论 (只列出界面真正读取的字段)。 */
export interface WorkbenchClaim {
  id: string;
  statement: string;
  problem_id?: string;
  status?: string;
  support_kind?: string;
  coverage?: string;
  validation_status?: string;
  assurance?: string;
  novelty_status?: string;
  evidence_grade?: string;
  claim_type?: string;
  model_ref?: { id: string; version?: number | string } | null;
  conditions?: string[];
  effect_estimate?: Record<string, unknown>;
  study_design?: string;
  not_covered?: string[];
  notes?: string;
}

export interface WorkbenchObligation {
  id: string;
  statement: string;
  kind?: string;
  status?: string;
  validation_status?: string;
  claim_id?: string;
  required?: boolean;
  detail?: string;
}

export interface WorkbenchVerification {
  id: string;
  claim_id?: string;
  tool?: string;
  status?: string;
  validation_status?: string;
  scope?: string;
  stale?: boolean;
  certificate?: string;
}

export interface WorkbenchEvidence {
  id: string;
  claim_id?: string;
  title?: string;
  support?: string;
  support_reason?: string;
  locator?: string;
  source_id?: string;
  excerpt?: string;
}

export interface WorkbenchNovelty {
  claim_id?: string;
  status?: string;
  conclusion?: string;
  covered_sources?: string[];
  inaccessible?: string[];
}

export interface WorkbenchExperiment {
  id?: string;
  title?: string;
  claim_id?: string;
  purpose?: string;
  execution_status?: string;
  decision_rule?: string;
  metrics?: string[];
  limitations?: string[];
}

export interface WorkbenchModelSelectionClaim {
  declared_scheme?: string;
  selected_state?: string;
  capability_action?: string;
  capability?: string;
  claim_type?: string;
  model_ref?: { id: string; version?: number | string } | null;
}

export interface WorkbenchData {
  project_id?: string;
  problem_id?: string;
  /** R6: 运行身份与分支身份 (与产物清单/manifest 同一个值) */
  run_id?: string;
  branch_id?: string;
  problems?: { problem_id: string }[];
  /** 服务端给出的"还缺什么"说明 (计数为 0 的原因), 前端不得自行推断 */
  coverage_notes?: string[];
  /** 日志契约异常 (缺必需键 / 未登记事件类型) */
  log_anomalies?: { kind?: string; missing?: string; reason?: string }[];
  objects?: Record<string, number>;
  metrics?: {
    identity?: Record<string, string>;
    anomalies?: string[];
    stopped_reason?: string;
    counts?: Record<string, number>;
    proposals?: Record<string, number>;
  };
  claims?: WorkbenchClaim[];
  obligations?: WorkbenchObligation[];
  verifications?: WorkbenchVerification[];
  evidence?: WorkbenchEvidence[];
  novelty?: WorkbenchNovelty[];
  experiments?: WorkbenchExperiment[];
  model_selection?: {
    claims?: Record<string, WorkbenchModelSelectionClaim>;
    models?: {
      id: string; name?: string; version?: number | string;
      selected?: boolean; sources?: unknown; verified_scope?: string;
    }[];
  };
  modeling?: {
    mechanisms?: {
      id: string; name?: string; relation?: string;
      predictions?: string[]; source_refs?: string[]; missing?: string[];
    }[];
    distinguishing?: { kind?: string; statement?: string }[];
  };
  assumptions?: { id: string; statement?: string; accepted?: boolean }[];
  steps?: { id: string; text?: string; claim_id?: string }[];
}

export type WorkbenchObjects = {
  assumptions?: Record<string, string>;
  claims?: Record<string, string>;
  obligations?: Record<string, string>;
  evidence?: Record<string, string>;
  models?: Record<string, string>;
  verifications?: Record<string, string>;
};

export interface PickerOption {
  value: string;
  label: string;
}

/** HTML 文本转义 (所有把数据拼进 HTML 片段的地方都必须经过它)。 */
export function escapeHtml(raw: unknown): string {
  return String(raw === null || raw === undefined ? '' : raw)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function wbEsc(raw: unknown): string {
  return escapeHtml(raw);
}

const STATUS_LABEL: Record<string, string> = {
  proposed: '待研究', in_progress: '研究中', supported: '已成立', refuted: '被否定',
  blocked: '受阻', open: '未关闭', closed: '已关闭',
  verified: '已验证', counterexample_found: '有反例', unknown: '未决',
  unchecked: '未检查', unsupported: '不支持', timeout: '超时', unavailable: '工具不可用',
  execution_error: '执行错误', encoding_mismatch: '编码不一致',
  unchecked_novelty: '未检索',
  known_equivalent: '已知等价', potentially_distinct: '可能有差异',
  expert_reviewed: '专家复核', stale: '已过期',
};

const SUPPORT_KIND_LABEL: Record<string, string> = {
  none: '未声明', textual_support: '原文支持', informal_argument: '非形式化论证',
  symbolic_check: '符号检查', constraint_solve: '约束求解', formal_proof: '形式化证明',
  numerical_test: '数值测试', statistical_estimate: '统计估计',
};

const COVERAGE_LABEL: Record<string, string> = {
  step: '局部步骤', subgoal: '子目标', target: '完整目标',
};

const SUPPORT_RELATION_LABEL: Record<string, string> = {
  insufficient: '不足（未建立支持）', supports: '支持', contradicts: '反对',
  partially_supports: '部分支持', background: '背景',
};

const EXECUTION_LABEL: Record<string, string> = {
  proposed: '仅方案（未执行）', spec_validated: '规格已校验（未执行）',
  executed: '已执行', analyzed: '已分析',
};

const ROUTE_LABEL: Record<string, string> = {
  active: '进行中', suspended: '已挂起', failed: '已失败',
  abandoned: '已放弃', completed: '已完成',
};

const TAG_CLASSES = ['supported', 'refuted', 'in_progress', 'proposed', 'blocked',
  'closed', 'open'];

export const OBJECT_KIND_LABEL: Record<string, string> = {
  assumption: '假设', claim: '结论', obligation: '证明义务',
  evidence: '文献证据', model: '模型', verification: '验证记录',
  manuscript: '稿件', validation_plan: '验证建议', review_issue: '审阅问题',
};

function lookup(map: Record<string, string>, key: unknown, fallback: string): string {
  const text = key === null || key === undefined ? '' : String(key);
  return map[text] || text || fallback;
}

export function statusLabelRaw(status: unknown): string {
  return lookup(STATUS_LABEL, status, '未知');
}

export function statusTag(status: unknown): string {
  const key = String(status === null || status === undefined ? '' : status);
  const cls = TAG_CLASSES.includes(key) ? key : 'neutral';
  return '<span class="tag t-' + cls + '">' + wbEsc(statusLabelRaw(key)) + '</span>';
}

export function supportKindLabel(kind: unknown): string {
  return lookup(SUPPORT_KIND_LABEL, kind, '-');
}

export function coverageLabel(coverage: unknown): string {
  return lookup(COVERAGE_LABEL, coverage, '-');
}

export function supportRelationLabel(relation: unknown): string {
  return lookup(SUPPORT_RELATION_LABEL, relation, '-');
}

export function executionLabel(status: unknown): string {
  return lookup(EXECUTION_LABEL, status, '-');
}

export function routeLabel(status: unknown): string {
  return lookup(ROUTE_LABEL, status, '-');
}

/**
 * 反馈的作用对象选项 (F1-5)。
 *
 * 语义: 空值 = 由系统按意见内容判断; 其余值提交 `object_id`, 服务端仍会校验,
 * 无法唯一定位时必须回传候选而不是猜。
 */
export function feedbackObjectOptions(objects: WorkbenchObjects = {}): PickerOption[] {
  const options: PickerOption[] = [
    { value: '', label: '（请选择对象；留空将请求澄清）' },
  ];
  const push = (kind: string, id: string, text: unknown): void => {
    options.push({
      value: id,
      label: OBJECT_KIND_LABEL[kind] + ' ' + id + ' — ' +
        String(text === null || text === undefined ? '' : text).slice(0, 60),
    });
  };
  const walk = (kind: string, group?: Record<string, string>): void => {
    Object.entries(group || {}).forEach(([id, text]) => push(kind, id, text));
  };
  walk('assumption', objects.assumptions);
  walk('claim', objects.claims);
  walk('obligation', objects.obligations);
  walk('evidence', objects.evidence);
  walk('model', objects.models);
  walk('verification', objects.verifications);
  return options;
}

/**
 * 结论详情 (F3 详情导航): 从一条结论反查它的模型依据、义务、证据、验证与建议。
 *
 * 只返回 HTML 片段字符串 (调用方决定插到哪里); 所有字段都经过转义。
 */
export function claimDetailRow(data: WorkbenchData | null, claimId: string): string {
  const d: WorkbenchData = data || {};
  const claims = d.claims || [];
  const claim = claims.find(c => c.id === claimId);
  if (!claim) return '';
  const obligations = (d.obligations || []).filter(o => o.claim_id === claimId);
  const evidence = (d.evidence || []).filter(e => e.claim_id === claimId);
  const verifications = (d.verifications || []).filter(v => v.claim_id === claimId);
  const models = ((d.model_selection || {}).claims || {})[claimId] || {};
  const novelty = (d.novelty || []).filter(n => n.claim_id === claimId);
  const experiments = (d.experiments || []).filter(s => s.claim_id === claimId);

  const rows: string[] = [];
  rows.push('<div class="wb-detail-title">模型依据</div>');
  rows.push('<div class="wb-note">' +
    wbEsc(models.declared_scheme || '(未记录)') + ' · ' +
    wbEsc(models.selected_state || '') +
    (models.model_ref
      ? (' · ' + wbEsc(models.model_ref.id) + ' v' + wbEsc(models.model_ref.version))
      : '') +
    '</div>');

  rows.push('<div class="wb-detail-title">证明义务 (' + obligations.length + ')</div>');
  if (!obligations.length) rows.push('<div class="wb-note">无义务记录</div>');
  obligations.forEach(o => {
    rows.push('<div class="wb-detail-item">' + statusTag(o.status) + ' ' +
      wbEsc(o.statement) + ' <span class="mono">' + wbEsc(o.id) + '</span>' +
      (o.validation_status
        ? ' <span class="wb-note">验证: ' + wbEsc(o.validation_status) + '</span>' : '') +
      (o.detail ? '<div class="wb-note">' + wbEsc(o.detail) + '</div>' : '') + '</div>');
  });

  rows.push('<div class="wb-detail-title">证据 (' + evidence.length + ')</div>');
  if (!evidence.length) rows.push('<div class="wb-note">该结论尚无归属证据</div>');
  evidence.forEach(e => {
    rows.push('<div class="wb-detail-item">' + wbEsc(e.title || '(无标题)') +
      ' · ' + wbEsc(e.support || '') +
      (e.locator ? (' · 定位 ' + wbEsc(e.locator)) : '') +
      (e.support_reason
        ? '<div class="wb-note">' + wbEsc(e.support_reason) + '</div>' : '') +
      '</div>');
  });

  rows.push('<div class="wb-detail-title">验证记录 (' + verifications.length + ')</div>');
  if (!verifications.length) rows.push('<div class="wb-note">暂无验证记录</div>');
  verifications.forEach(v => {
    rows.push('<div class="wb-detail-item mono">' + wbEsc(v.tool || '') + ' · ' +
      wbEsc(v.validation_status || v.status || '') +
      (v.stale ? ' · (已过期)' : '') +
      (v.certificate
        ? '<div class="wb-note">' + wbEsc(String(v.certificate).slice(0, 200)) + '</div>'
        : '') +
      '</div>');
  });

  if (novelty.length) {
    rows.push('<div class="wb-detail-title">新颖性对照</div>');
    novelty.forEach(n => rows.push('<div class="wb-detail-item">' + wbEsc(n.status || '') +
      (n.conclusion ? ' — ' + wbEsc(n.conclusion) : '') + '</div>'));
  }
  if (experiments.length) {
    rows.push('<div class="wb-detail-title">实验/仿真建议</div>');
    experiments.forEach(s => rows.push('<div class="wb-detail-item">' +
      wbEsc(s.title || '') + ' · 状态 ' + wbEsc(s.execution_status || '') + '</div>'));
  }
  return '<tr class="wb-detail-row"><td colspan="6">' +
    '<div class="wb-detail">' + rows.join('') + '</div></td></tr>';
}

/** 工作台身份描述: 视图必须显示**同一次运行**的项目/问题/运行, 不靠猜。 */
export function identityLine(data: WorkbenchData | null): string {
  const d: WorkbenchData = data || {};
  const parts: string[] = [];
  if (d.project_id) parts.push('项目 ' + d.project_id);
  if (d.problem_id) parts.push('问题 ' + d.problem_id);
  if (d.run_id) parts.push('运行 ' + d.run_id);
  return parts.join(' · ');
}

export const ResearchWorkbench = {
  escapeHtml,
  wbEsc,
  statusLabelRaw,
  statusTag,
  supportKindLabel,
  coverageLabel,
  supportRelationLabel,
  executionLabel,
  routeLabel,
  feedbackObjectOptions,
  claimDetailRow,
  identityLine,
  OBJECT_KIND_LABEL,
};

export default ResearchWorkbench;
