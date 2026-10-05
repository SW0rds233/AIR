/*!
 * 研究工作台总览视图 (合并计划 §9.5: `app.ts::renderWorkbench/*Detail*` 迁出)。
 *
 * 迁移原则: **行为零变化** —— 这里只是把 `app.ts` 里原本直接读写 DOM 的渲染逻辑
 * 变成"输入数据 → 输出 HTML"的纯函数, 于是:
 *
 * - 视图本身可单测 (不需要真实页面);
 * - 仍然遵守 CSP 约束: 动态按钮一律 `data-action` + 文档级委托, 没有任何内联处理器;
 * - 所有外部文本经 `wbEsc` 转义 (安全渲染行为不变);
 * - `id` / 类名保持不变 (`#wbobj`、`#wbfeedback`、`#wbsnapid`、`.claim-link` ...),
 *   因为现有 e2e 与后端联调依赖它们。
 *
 * 依赖注入: 页面只传"怎么拿工作台数据"和"哪些结论详情是展开的"这两个查询函数,
 * 视图不持有任何页面状态 —— 唯一权威状态仍是 `state/research-store.ts`
 * 与 `window.AIR.research` (计划书 §9.3: 不引入第二份状态)。
 */

import type { WorkbenchData } from './research-workbench';
import {
  claimDetailRow as wbClaimDetailRow,
  coverageLabel,
  executionLabel,
  feedbackObjectOptions,
  routeLabel,
  statusLabelRaw,
  statusTag,
  supportKindLabel,
  supportRelationLabel,
  wbEsc,
} from './research-workbench';

/** 工作台总览要读的数据来源 (页面提供)。 */
export interface ResearchOverviewSource {
  /** 最近一次工作台投影; 未加载时为 null。 */
  data(): WorkbenchData | null;
  /** 当前展开详情的结论 id (空串表示没有展开)。 */
  openClaimId(): string;
}

function labelOf(map: Record<string, string>, key: string): string {
  return map && map[key] !== undefined ? map[key] : key;
}

/** 「项目还没有研究记录」空态 (草稿身份与未建立运行两种文案)。 */
export function workbenchMissingHtml(projectId: string): string {
  return '<div class="empty">项目 ' + wbEsc(projectId) +
    ' 还没有研究记录。启动一次理论研究（或从「会话」下拉框选一个历史会话）后即可加载工作台。</div>';
}

/** 「该项目还没有研究记录, 请确认项目 ID」空态 (查询被显式拦下时)。 */
export function workbenchUnboundHtml(projectId: string): string {
  return '<div class="empty">项目 ' + wbEsc(projectId) +
    ' 还没有研究记录。启动一次理论研究（或从「会话」下拉框选一个历史会话）后即可加载工作台。' +
    '若这是你之前研究过的项目，请确认项目 ID 没有改动。</div>';
}

/** 「尚未关联项目」空态。 */
export function workbenchNoProjectHtml(): string {
  return '<div class="empty">尚未关联理论研究项目。' +
    '启动一次理论研究，或在高级选项里填写项目 ID 后点「加载工作台」。</div>';
}

/** 「新会话」空态 (与上面区分: 新会话明确说明"尚未关联")。 */
export function workbenchNewSessionHtml(): string {
  return '<div class="empty">新会话：尚未关联理论研究项目。' +
    '启动一次理论研究，或在高级选项里填写项目 ID 后点「加载工作台」。</div>';
}

/**
 * 读取工作台失败时的说明 + 按状态给出的提示。
 *
 * 409 (该项目含多个研究问题) 给出可点的 `selectProblem` 按钮; 404 明确说明
 * "该项目下没有这个研究问题"; 其它状态如实回显服务端 detail, 不伪装成空数据。
 */
export function workbenchErrorHtml(status: number, body: any): string {
  let hint = '';
  if (status === 409 && body && body.detail && body.detail.problems) {
    hint = '<div class="wb-note">该项目含多个研究问题，请选择其一：' +
      body.detail.problems.map((p: any) => '<button class="ghost" data-action="selectProblem" ' +
        'data-id="' + wbEsc(encodeURIComponent(p.problem_id)) + '">' +
        wbEsc(p.problem_id) + '</button>')
        .join(' ') + '</div>';
  } else if (status === 404) {
    hint = '<div class="wb-note">该项目下没有这个研究问题。</div>';
  }
  return '<div class="empty">读取工作台失败 (' + status + '): ' +
    wbEsc((body && body.detail) ? (body.detail.message || JSON.stringify(body.detail))
                                : JSON.stringify(body)) + '</div>' + hint;
}

/**
 * 从结论点开所用模型、原文证据、证明义务与验证记录 (§9.5 `*Detail*`)。
 * 薄封装: 具体渲染仍在 `views/research-workbench.ts` (已有单测)。
 */
export function claimDetailRow(data: WorkbenchData | null, claimId: string): string {
  return wbClaimDetailRow(data, claimId);
}

/** 结论详情展开/收起; 返回新的展开结论 id (纯函数, 页面据此重渲染)。 */
export function toggledClaimId(current: string, claimId: string): string {
  return current === claimId ? '' : claimId;
}

/**
 * 问题类型能力声明与领域模型 (计划书 §5.2 / §7.2)。
 *
 * R2: 候选机制比较 + 可区分检验 + 术语量纲也必须显示, 否则"缺模型/缺单位"这类
 * 缺口在界面上不可见。
 */
export function renderCapabilityCard(d: any): string {
  const sel = d.model_selection || {};
  const perClaim = sel.claims || {};
  const models = sel.models || [];
  const ids = Object.keys(perClaim);
  if (!ids.length && !models.length) return '';

  const stateLabel = {
    selected: '已选模型',
    missing_selection: '缺模型选择',
    not_required: '无需领域模型',
    no_model: '尚无模型',
  };
  const rows = ids.map((cid: any) => {
    const s = perClaim[cid];
    const ref = s.model_ref ? (s.model_ref.id + ' v' + s.model_ref.version) : '—';
    const warn = s.capability_action !== 'proceed' || s.selected_state === 'missing_selection';
    const cls = warn ? ' style="color:var(--warn)"' : '';
    return '<tr><td class="mono">' + wbEsc(cid) + '</td><td class="mono">' + wbEsc(s.claim_type) +
      '</td><td' + cls + '>' + wbEsc(s.declared_scheme) + '</td>' +
      '<td class="mono">' + wbEsc(ref) + '</td>' +
      '<td' + cls + '>' + wbEsc(labelOf(stateLabel, s.selected_state)) + '</td>' +
      '<td' + cls + '>' + wbEsc(s.capability) + '</td></tr>';
  }).join('');

  const modelRows = models.map((m: any) => '<tr><td class="mono">' + wbEsc(m.id) + ' v' + wbEsc(m.version) +
    '</td><td>' + wbEsc(m.name) + '</td><td>' + (m.selected ? '已选' : '候选') + '</td>' +
    '<td>' + wbEsc(String(m.sources)) + '</td><td>' + wbEsc(m.verified_scope || '—') + '</td></tr>').join('');

  // R2: 候选机制比较 + 可区分检验 + 术语量纲
  const modeling = d.modeling || {};
  const mechRows = (modeling.mechanisms || []).map((m: any) =>
    '<tr><td class="mono">' + wbEsc(m.id) + '</td><td>' + wbEsc(m.name) + '</td>' +
    '<td>' + wbEsc(m.relation || '—') + '</td>' +
    '<td>' + wbEsc((m.predictions || []).join('；') || '—') + '</td>' +
    '<td>' + wbEsc((m.source_refs || []).length ? (m.source_refs || []).length + ' 条来源' : '无来源') + '</td>' +
    '<td' + ((m.missing || []).length ? ' style="color:var(--warn)"' : '') + '>' +
    wbEsc((m.missing || []).join('、') || '—') + '</td></tr>').join('');
  const testRows = (modeling.distinguishing || []).map((t: any) =>
    '<tr><td class="mono">' + wbEsc(t.kind) + '</td><td>' + wbEsc(t.statement) + '</td>' +
    '<td>若 ' + wbEsc((t.discriminates || [])[0] || '?') + ': ' + wbEsc(t.expected_if_a || '待定') +
    '<br>若 ' + wbEsc((t.discriminates || [])[1] || '?') + ': ' + wbEsc(t.expected_if_b || '待定') +
    '</td></tr>').join('');
  const termRows = (modeling.terms || []).map((t: any) =>
    '<tr><td class="mono">' + wbEsc(t.name) + '</td><td>' + wbEsc(t.meaning || '—') + '</td>' +
    '<td' + (t.unit ? '' : ' style="color:var(--warn)"') + '>' + wbEsc(t.unit || '单位缺失') +
    '</td><td>' + wbEsc(t.domain || '—') + '</td></tr>').join('');

  return '<div class="wb-section"><h3>问题类型与模型' +
    '<span class="wb-count">能力声明 ' + ids.length + ' 条 / 模型 ' + models.length + ' 个</span></h3>' +
    (rows ? '<table class="wb"><tr><th>结论</th><th>类型</th><th>所需模型</th><th>当前模型</th>' +
      '<th>选择状态</th><th>能力声明</th></tr>' + rows + '</table>' : '') +
    (modelRows ? '<table class="wb" style="margin-top:6px"><tr><th>模型</th><th>名称</th>' +
      '<th>选中</th><th>来源数</th><th>已验证范围</th></tr>' + modelRows + '</table>' : '') +
    (mechRows ? '<h3 style="margin-top:10px">候选机制比较<span class="wb-count">' +
      (modeling.selected ? '选中 ' + wbEsc(modeling.selected) + '：' + wbEsc(modeling.why_selected || '') : '') +
      '</span></h3><table class="wb"><tr><th>机制</th><th>名称</th><th>变量关系</th>' +
      '<th>可观测预测</th><th>来源</th><th>缺失项</th></tr>' + mechRows + '</table>' : '') +
    (testRows ? '<h3 style="margin-top:10px">可区分检验</h3><table class="wb">' +
      '<tr><th>类型</th><th>检验</th><th>预期分离</th></tr>' + testRows + '</table>' : '') +
    (termRows ? '<h3 style="margin-top:10px">术语与量纲</h3><table class="wb">' +
      '<tr><th>术语</th><th>含义</th><th>单位</th><th>取值域</th></tr>' + termRows + '</table>' : '') +
    '<div class="wb-note">能力声明只描述该类型问题需要什么 (数据/设计/后端); ' +
    '「缺模型选择」表示该结论尚未绑定具体模型版本；缺失项表示建模信息不足。</div>' +
    '</div>';
}

/**
 * 整个研究工作台总览的 HTML (不含团队区块: 团队区块由 `team-controller` 单独挂载,
 * 且永远插在 `#workbench` 容器最前)。
 *
 * 迁移前这是 `app.ts::renderWorkbench`, 其中唯一的状态是"哪条结论详情展开了"; 现在
 * 由调用方通过 `source.openClaimId()` 传入, 视图自身无状态。
 */
export function renderResearchOverview(d: any, source: ResearchOverviewSource): string {
  const spec = d.spec || {};
  const budget = d.budget || {};
  const objects = d.objects || {};
  const claims = d.claims || [];
  const obligations = d.obligations || [];
  const verifications = d.verifications || [];
  const evidence = d.evidence || [];
  const experiments = d.experiments || [];
  const routes = d.routes || [];
  const novelty = d.novelty || [];
  const events = d.events || [];

  const problem = spec.problem_statement || spec.original_request || spec.direction || '(未记录研究问题)';
  const parts: string[] = [];
  const openClaimDetail = source.openClaimId();

  // 状态总览
  parts.push(
    '<div class="wb-section"><h3>研究问题<span class="wb-count">项目 ' + wbEsc(d.project_id) + '</span></h3>' +
    '<div class="wb-note" style="font-size:12px;color:var(--text)">' + wbEsc(problem) + '</div>' +
    (Object.keys(spec.variable_domains || {}).length
      ? '<div class="wb-note">变量域: ' + wbEsc(Object.entries(spec.variable_domains)
          .map(([k, v]) => k + ' ∈ ' + v).join('、')) + '</div>' : '') +
    (spec.unknown_fields && spec.unknown_fields.length
      ? '<div class="wb-note">未确定字段 (需澄清): ' + wbEsc(spec.unknown_fields.join(', ')) + '</div>' : '') +
    '</div>'
  );

  // 问题类型能力声明与领域模型 (计划书 §5.2 / §7.2)
  parts.push(renderCapabilityCard(d));

  // 进度统计
  const stats = [
    ['已成立结论', objects.claims_supported, 'supported'],
    ['被否定结论', objects.claims_refuted, 'refuted'],
    ['未决/受阻', (objects.claims_open || 0) + (objects.claims_blocked || 0), 'blocked'],
    ['未关闭义务', objects.obligations_open, ''],
    ['受阻义务', objects.obligations_blocked, 'blocked'],
    ['证据条目', objects.evidence, ''],
    ['验证记录', objects.verifications, ''],
    ['动作/上限', (budget.actions_used || 0) + '/' + (budget.max_actions || 0), ''],
  ];
  parts.push(
    '<div class="wb-section"><h3>进度' +
    (budget.done ? '<span class="wb-count">研究循环已结束</span>' :
                   '<span class="wb-count">研究循环进行中</span>') +
    '</h3><div class="wb-grid">' +
    stats.map(([k, v, cls]) =>
      '<div class="stat"><div class="k">' + k + '</div><div class="v ' + cls + '">' +
      (v === undefined || v === null ? 0 : v) + '</div></div>').join('') +
    '</div></div>'
  );

  // 当前动作与缺口
  const decisions = d.decisions || [];
  if (decisions.length || (d.gaps || []).length) {
    const last = decisions.length ? decisions[decisions.length - 1] : null;
    parts.push('<div class="wb-section"><h3>当前在做什么</h3>');
    if (last) {
      parts.push('<div class="wb-note" style="color:var(--text)">最近动作: <b>' +
        wbEsc(last.action) + '</b>' + (last.target_gap ? '（针对缺口 ' + wbEsc(last.target_gap) + '）' : '') +
        '<br>' + wbEsc(last.reason || '') + '</div>');
    }
    if ((d.gaps || []).length) {
      parts.push('<table class="wb"><tr><th>缺口</th><th>说明</th><th>可消除动作</th></tr>' +
        d.gaps.map((g: any) => '<tr><td class="mono">' + wbEsc(g.gap_type) + '</td><td>' +
          wbEsc(g.statement) + '</td><td class="mono">' +
          wbEsc((g.resolving_actions || []).join(', ')) + '</td></tr>').join('') + '</table>');
    }
    parts.push('</div>');
  }

  // 研究过程事件
  if (events.length) {
    parts.push('<div class="wb-section"><h3>研究过程<span class="wb-count">最近 ' +
      events.length + ' 条</span></h3><div class="wb-events">' +
      events.slice().reverse().map((e: any) =>
        '<div class="wb-event"><span class="seq">#' + wbEsc(e.seq) + '</span><span>' +
        wbEsc(e.detail) + '</span></div>').join('') + '</div></div>');
  }

  // 结论状态表
  parts.push('<div class="wb-section"><h3>结论状态表<span class="wb-count">' +
    claims.length + ' 条</span></h3>');
  if (!claims.length) {
    parts.push('<div class="empty">尚无结论</div>');
  } else {
    parts.push('<table class="wb"><tr><th>结论</th><th>状态</th><th>支持方式</th>' +
      '<th>覆盖</th><th>适用条件 / 未覆盖因素</th><th>ID</th></tr>' +
      claims.map((c: any) => {
        const cond = (c.conditions || []).join('、');
        const unc = (c.not_covered || []).length
          ? '<div class="wb-note" style="color:var(--warn)">未覆盖: ' + wbEsc(c.not_covered.join('；')) + '</div>'
          : '';
        const est = c.effect_estimate && Object.keys(c.effect_estimate).length && c.effect_estimate.estimate !== undefined
          ? '<div class="wb-note">估计 ' + wbEsc(String(c.effect_estimate.estimate)) +
            ' (95%CI [' + wbEsc(String(c.effect_estimate.ci_low)) + ', ' +
            wbEsc(String(c.effect_estimate.ci_high)) + '])</div>'
          : '';
        return '<tr><td>' +
          '<button class="claim-link" aria-expanded="' +
            (openClaimDetail === c.id ? 'true' : 'false') + '" ' +
            'data-action="toggleClaimDetail" data-id="' + wbEsc(c.id) + '" ' +
            'title="展开该结论的证据/义务/验证详情">' + wbEsc(c.statement) + '</button>' +
          est + '</td><td>' + statusTag(c.status) + '</td>' +
          '<td>' + wbEsc(supportKindLabel(c.support_kind)) + '</td>' +
          '<td>' + wbEsc(coverageLabel(c.coverage)) + '</td>' +
          '<td>' + wbEsc(cond || '-') + unc + '</td>' +
          '<td class="mono">' + wbEsc(c.id) + '</td></tr>' +
          (openClaimDetail === c.id ? claimDetailRow(d, c.id) : '');
      }).join('') + '</table>');
  }
  parts.push('</div>');

  // 未决义务
  parts.push('<div class="wb-section"><h3>证明义务<span class="wb-count">' +
    obligations.length + ' 条</span></h3>');
  if (!obligations.length) {
    parts.push('<div class="empty">尚无义务</div>');
  } else {
    const claimText: Record<string, string> = {};
    claims.forEach((c: any) => { claimText[c.id] = c.statement; });
    parts.push('<table class="wb"><tr><th>义务</th><th>类型</th><th>状态</th>' +
      '<th>验证结果</th><th>说明</th></tr>' +
      obligations.map((o: any) => {
        const ce = o.counterexample && Object.keys(o.counterexample).length
          ? '<div class="wb-note" style="color:var(--danger)">反例: ' +
            wbEsc(JSON.stringify(o.counterexample)) + '</div>' : '';
        return '<tr><td>' + wbEsc(o.statement) +
          (claimText[o.claim_id] ? '<div class="wb-note">结论: ' +
            wbEsc(claimText[o.claim_id].slice(0, 60)) + '</div>' : '') + ce + '</td>' +
          '<td class="mono">' + wbEsc(o.kind) + '</td>' +
          '<td>' + statusTag(o.status) + (o.required ? '' : '<div class="wb-note">非必需</div>') + '</td>' +
          '<td>' + wbEsc(statusLabelRaw(o.validation_status)) + '</td>' +
          '<td>' + wbEsc(o.detail || '-') + '</td></tr>';
      }).join('') + '</table>');
  }
  parts.push('</div>');

  // 证据定位
  if (evidence.length) {
    parts.push('<div class="wb-section"><h3>证据与定位<span class="wb-count">' +
      evidence.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>来源</th><th>定位</th><th>关系</th><th>判定依据</th>' +
      '<th>可信度</th></tr>' +
      evidence.map((e: any) =>
        '<tr><td>' + wbEsc(e.title || e.source_id) +
          (e.excerpt ? '<div class="wb-note">' + wbEsc(e.excerpt.slice(0, 160)) + '</div>' : '') + '</td>' +
        '<td class="mono">' + wbEsc(e.locator || (e.page ? 'p' + e.page : '无定位')) + '</td>' +
        '<td>' + wbEsc(supportRelationLabel(e.support)) +
          (e.reviewer ? '<div class="wb-note">判定者: ' + wbEsc(e.reviewer) + '</div>' : '') + '</td>' +
        '<td>' + wbEsc(e.support_reason || '-') + '</td>' +
        '<td>' + wbEsc(e.credibility) +
          (e.existence_verified ? '<div class="wb-note">存在性已核</div>' : '') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 验证记录
  if (verifications.length) {
    parts.push('<div class="wb-section"><h3>验证记录<span class="wb-count">' +
      verifications.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>工具</th><th>结论</th><th>状态</th><th>覆盖</th>' +
      '<th>证书 / 反例</th></tr>' +
      verifications.map((v: any) => {
        const ce = v.counterexample && Object.keys(v.counterexample).length
          ? '<div class="wb-note" style="color:var(--danger)">反例 ' + wbEsc(JSON.stringify(v.counterexample)) + '</div>'
          : '';
        return '<tr><td>' + wbEsc(v.tool) + '</td>' +
          '<td class="mono">' + wbEsc(v.claim_id) + '</td>' +
          '<td>' + wbEsc(statusLabelRaw(v.validation_status)) +
            (v.stale ? ' <span class="tag t-blocked">已过期</span>' : '') + '</td>' +
          '<td>' + wbEsc(coverageLabel(v.scope)) + '</td>' +
          '<td>' + wbEsc(v.certificate || '-') + ce + '</td></tr>';
      }).join('') + '</table></div>');
  }

  // 实验/仿真建议
  parts.push('<div class="wb-section"><h3>实验/仿真建议<span class="wb-count">' +
    experiments.length + ' 条</span></h3>');
  if (!experiments.length) {
    parts.push('<div class="empty">尚未生成实验规格（未执行的建议才会出现在这里）</div>');
  } else {
    parts.push('<table class="wb"><tr><th>规格</th><th>关联结论</th><th>目的</th>' +
      '<th>执行状态</th><th>判据 / 指标</th></tr>' +
      experiments.map((s: any) =>
        '<tr><td>' + wbEsc(s.title || s.id) + '</td>' +
        '<td class="mono">' + wbEsc(s.claim_id) + '</td>' +
        '<td>' + wbEsc(s.purpose || '-') + '</td>' +
        '<td>' + wbEsc(executionLabel(s.execution_status)) + '</td>' +
        '<td>' + wbEsc(s.decision_rule || '-') +
          ((s.metrics || []).length ? '<div class="wb-note">指标: ' +
            wbEsc(s.metrics.join('、')) + '</div>' : '') +
          ((s.limitations || []).length ? '<div class="wb-note">局限: ' +
            wbEsc(s.limitations.join('；')) + '</div>' : '') +
        '</td></tr>').join('') + '</table>');
  }
  parts.push('</div>');

  // 研究路线与失败记忆
  if (routes.length) {
    parts.push('<div class="wb-section"><h3>研究路线<span class="wb-count">' +
      routes.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>策略</th><th>状态</th><th>目标</th>' +
      '<th>失败原因</th><th>恢复条件</th></tr>' +
      routes.map((r: any) =>
        '<tr><td class="mono">' + wbEsc(r.strategy) + '</td>' +
        '<td>' + wbEsc(routeLabel(r.status)) + '</td>' +
        '<td>' + wbEsc((r.goal || '').slice(0, 80)) + '</td>' +
        '<td>' + wbEsc(r.failure_reason || '-') + '</td>' +
        '<td>' + wbEsc(r.recovery_condition || '-') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 新颖性
  if (novelty.length) {
    parts.push('<div class="wb-section"><h3>新颖性审查</h3>' +
      '<table class="wb"><tr><th>结论</th><th>状态</th><th>结论说明</th><th>检索边界</th></tr>' +
      novelty.map((n: any) =>
        '<tr><td class="mono">' + wbEsc(n.claim_id) + '</td>' +
        '<td>' + wbEsc(statusLabelRaw(n.status)) + '</td>' +
        '<td>' + wbEsc(n.conclusion || '-') + '</td>' +
        '<td>' + wbEsc((n.covered_sources || []).join('、') || '-') +
          ((n.inaccessible || []).length ? '<div class="wb-note" style="color:var(--warn)">' +
            wbEsc(n.inaccessible.join('；')) + '</div>' : '') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 服务端给出的"还缺什么 / 日志契约异常 / 预算停止"说明。
  // 这些是**显式声明**而不是让前端从空数组猜: 计数为 0 可能是"确实没有",
  // 也可能是"尚未做", 二者在界面上的含义完全不同。
  const notes = d.coverage_notes || [];
  if (notes.length) {
    parts.push('<div class="wb-section"><h3>覆盖说明<span class="wb-count">' +
      notes.length + ' 条</span></h3>' +
      notes.map((n: any) => '<div class="wb-note">· ' + wbEsc(n) + '</div>').join('') +
      '</div>');
  }

  // 日志契约异常必须显眼 (统一日志键: 少字段不再是静默的 0)
  const anomalies = d.log_anomalies || [];
  if (anomalies.length) {
    parts.push('<div class="wb-section"><h3>日志契约异常<span class="wb-count">' +
      anomalies.length + ' 条</span></h3><div class="wb-note" style="color:var(--warn)">' +
      '这些事件缺少必需字段或类型未登记, 可能导致界面显示为 0。' +
      anomalies.map((a: any) => '<div>· ' + wbEsc(a.kind || '') + ': ' +
        wbEsc(a.missing || a.reason || '') + '</div>').join('') +
      '</div></div>');
  }

  // 对象级操作
  parts.push(
    '<div class="wb-section"><h3>人工介入与调整</h3>' +
    '<div class="adv-row"><label>调整范围 <select id="wbfeedbackscope" aria-label="人工调整范围">' +
    '<option value="object">修订指定研究对象</option>' +
    '<option value="sources">补充或调整文献检索</option>' +
    '<option value="method">调整建模方法与假设</option>' +
    '<option value="validation">调整仿真或实验建议</option>' +
    '<option value="writing">调整论文写作</option>' +
    '<option value="review">要求重新审阅</option>' +
    '</select></label></div>' +
    '<div class="adv-row">' +
    '<label>作用对象 <select id="wbobj">' +
    feedbackObjectOptions({
      assumptions: Object.fromEntries((d.assumptions || []).map((a: any) => [a.id, a.statement || ''])),
      claims: Object.fromEntries(claims.map((c: any) => [c.id, c.statement])),
      obligations: Object.fromEntries((d.obligations || []).map((o: any) => [o.id, o.statement || ''])),
      evidence: Object.fromEntries((d.evidence || []).map((e: any) => [e.id, e.title || e.excerpt || ''])),
      models: Object.fromEntries(((d.model_selection || {}).models || []).map((m: any) => [m.id, m.name || ''])),
      verifications: Object.fromEntries((d.verifications || []).map((v: any) => [v.id, v.status || ''])),
    }).map((o: any) => '<option value="' + wbEsc(o.value) + '">' + wbEsc(o.label) + '</option>').join('') +
    '</select></label>' +
    '</div>' +
    '<div class="adv-row">' +
    '<input type="text" id="wbfeedback" placeholder="反馈：如「不要假设 x ∈ real」/「这个结论请给反例」" style="flex:2">' +
    '<button class="ghost" data-action="submitWorkbenchFeedback">施加反馈</button>' +
    '</div>' +
    '<div class="adv-row">' +
    '<input type="text" id="wbsnapid" placeholder="快照 ID（留空取最新）">' +
    '<button class="ghost" data-action="forkFromWorkbench">从快照派生新问题</button>' +
    '<button class="ghost" data-action="refreshWorkbench">刷新工作台</button>' +
    '</div>' +
    '<div class="wb-note">修订具体对象时请选择对象；其他调整范围可不选对象。' +
    '改变研究问题本身请从快照派生新问题，系统不会覆盖原问题。</div>' +
    '</div>'
  );

  return parts.join('');
}
