/**
 * 团队状态条与任务详情 (合并计划 §9.2「团队状态条」/ §9.6)。
 *
 * 展示的是**后端产生的**任务与研究记录: 视图不推断科学结论, 也不在页面里另做一套
 * 主控调度。要点:
 * - 一个角色一行 (同一角色多个任务显示**任务数**, 而不是重复头像);
 * - 状态不能只靠颜色区分 —— 每行同时给出文字与图标;
 * - 未知/未同步状态显式显示"未知/待同步", 不假报百分比 (§9.4);
 * - 任务详情展示: 目标、要回答的子问题、预期新信息、依赖、验收标准、失败处理、
 *   实际用量与失败原因 (失败/受阻必须能看到原因, 而不是空白)。
 *
 * 本模块是纯函数 (输入 store/行数据, 输出 HTML 字符串), 可直接在测试里断言。
 */

import type {
  ResearchStore,
  RoleStatus,
  TeamRoleRow,
  TeamTaskRow,
} from '../state/research-store';
import {
  blockingIssues,
  connectionLabel,
  currentRunId,
  latestDecision,
  roleStatuses,
  TASK_STATUS_LABEL,
  visibleTasks,
} from '../state/research-store';

export function esc(text: unknown): string {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 状态 -> 图标 (与文字一起用, 不靠颜色单独区分)。 */
export const STATUS_ICON: Record<string, string> = {
  queued: '○', running: '◐', waiting: '⏸', completed: '✓',
  partial: '◑', failed: '✗', cancelled: '⊘', unknown: '?',
};

export function statusBadge(status: string): string {
  const key = String(status || 'unknown');
  const icon = STATUS_ICON[key] ?? '?';
  const label = TASK_STATUS_LABEL[key] ?? `未知状态 (${esc(key)})`;
  return `<span class="team-status team-status-${esc(key)}" data-status="${esc(key)}">` +
    `<span class="icon" aria-hidden="true">${icon}</span><span class="txt">${esc(label)}</span></span>`;
}

// ----------------------------------------------------------------------
// 角色能力卡 (来自 /api/team/roles)
// ----------------------------------------------------------------------
export function renderRoleCapability(role: TeamRoleRow): string {
  const unavailable = (role.unavailable ?? []);
  const available = (role.available ?? []);
  return (
    '<div class="role-card">' +
    `<div class="role-head"><b>${esc(role.label || role.agent)}</b>` +
    `<span class="mono">${esc(role.agent)}</span>` +
    (role.mayRequestDowngrade ? '<span class="tag">可请求降级</span>' : '') +
    '</div>' +
    (role.objective ? `<div class="role-objective">${esc(role.objective)}</div>` : '') +
    (role.deliverables?.length
      ? `<div class="role-line">交付: ${esc(role.deliverables.join('、'))}</div>` : '') +
    `<div class="role-line">可用: ${available.length ? esc(available.join('、')) : '无'}</div>` +
    (unavailable.length
      ? '<div class="role-line warn">不可用: ' +
        esc(unavailable.map((u) => `${u.capability} (${u.reason})`).join('；')) + '</div>'
      : '') +
    '</div>'
  );
}

export function renderRoleCapabilities(roles: TeamRoleRow[]): string {
  if (!roles.length) {
    return '<div class="empty">尚未读取团队能力 (连接后自动获取)</div>';
  }
  return `<div class="role-grid">${roles.map(renderRoleCapability).join('')}</div>`;
}

// ----------------------------------------------------------------------
// 团队状态条
// ----------------------------------------------------------------------
export function renderRoleStatusBar(statuses: RoleStatus[]): string {
  if (!statuses.length) {
    return '<div class="empty">当前运行还没有任务记录</div>';
  }
  const rows = statuses.map((status) => {
    const detail = Object.entries(status.byStatus)
      .map(([k, v]) => `${TASK_STATUS_LABEL[k] ?? k} ${v}`)
      .join(' / ');
    const blocked = status.failed > 0;
    return (
      `<div class="role-status${blocked ? ' blocked' : ''}" data-agent="${esc(status.agent)}">` +
      `<span class="name">${esc(status.label)}</span>` +
      `<span class="state">${esc(status.text)}</span>` +
      (status.total ? `<span class="count mono">任务 ${status.total}</span>` : '') +
      (detail ? `<span class="detail">${esc(detail)}</span>` : '') +
      '</div>'
    );
  });
  return `<div class="team-statusbar">${rows.join('')}</div>`;
}

// ----------------------------------------------------------------------
// 主控决策摘要
// ----------------------------------------------------------------------
export function renderSupervisorHeader(store: ResearchStore): string {
  const decision = latestDecision(store);
  const runId = currentRunId(store);
  const counts = store.entities.objectCountsByRun[runId] ?? {};
  const objectBits = Object.entries(counts)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => `${esc(k)} ${v}`)
    .join(' · ');
  const parts: string[] = [];
  parts.push(
    '<div class="wb-section"><h3>主控视角' +
    `<span class="wb-count">${esc(connectionLabel(store.transport))}</span></h3>`
  );
  if (!runId) {
    parts.push('<div class="empty">还没有运行: 提交研究任务后这里会显示主控的派工理由</div>');
  } else {
    if (decision) {
      parts.push(
        '<div class="wb-note" style="color:var(--text)">' +
        `第 ${esc(decision.round)} 轮决策: <b>${esc(decision.decision || '未知')}</b><br>` +
        `${esc(decision.reason || '')}` +
        (decision.note ? `<br><span class="mono">${esc(decision.note)}</span>` : '') +
        '</div>'
      );
    } else {
      parts.push('<div class="wb-note">主控尚未给出决策 (等待首个事件)</div>');
    }
    if (objectBits) {
      parts.push(`<div class="wb-note">已登记: ${objectBits}</div>`);
    }
    parts.push(`<div class="wb-note mono">run ${esc(runId)}</div>`);
  }
  if (store.ui.notice) {
    parts.push(`<div class="wb-note warn">${esc(store.ui.notice)}</div>`);
  }
  if (store.transport.needsResync) {
    parts.push(
      '<div class="wb-note warn">事件存在缺口或协议不兼容: 需要重新加载投影 ' +
      '(不会把缺口当作已完成)</div>'
    );
  }
  if (store.transport.lastError) {
    parts.push(`<div class="wb-note warn">连接错误: ${esc(store.transport.lastError)}</div>`);
  }
  parts.push('</div>');
  return parts.join('');
}

// ----------------------------------------------------------------------
// 任务列表与详情
// ----------------------------------------------------------------------
export function renderTaskRow(task: TeamTaskRow): string {
  const status = String(task.status || 'unknown');
  return (
    `<tr data-task="${esc(task.taskId)}" class="${status === 'failed' || status === 'cancelled' ? 'row-failed' : ''}">` +
    `<td>${esc(task.agent)}</td>` +
    `<td>${statusBadge(status)}</td>` +
    `<td>${esc(task.objective)}` +
    (task.subquestion ? `<div class="wb-note">子问题: ${esc(task.subquestion)}</div>` : '') +
    (task.failureReason ? `<div class="wb-note warn">原因: ${esc(task.failureReason)}</div>` : '') +
    '</td>' +
    `<td class="mono">${task.attempt > 1 ? `第 ${esc(task.attempt)} 次` : '首次'}</td>` +
    `<td class="mono">${task.dependsOn.length ? esc(task.dependsOn.length) + ' 个前置' : '无'}</td>` +
    '</tr>'
  );
}

export function renderTaskTable(tasks: TeamTaskRow[]): string {
  if (!tasks.length) {
    return '<div class="empty">没有符合条件的任务</div>';
  }
  return (
    '<table class="wb"><tr><th>角色</th><th>状态</th><th>目标 / 受阻原因</th>' +
    '<th>尝试</th><th>依赖</th></tr>' +
    tasks.map(renderTaskRow).join('') +
    '</table>'
  );
}

export function renderTaskDetail(task: TeamTaskRow): string {
  const rows: Array<[string, string]> = [
    ['任务 ID', task.taskId],
    ['角色', task.agent],
    ['目标', task.objective],
    ['要回答的子问题', task.subquestion || '(未声明)'],
    ['预期新信息', task.expectedGain || '(未声明)'],
    ['计划版本', String(task.planVersion)],
    ['尝试次数', String(task.attempt)],
    ['依赖', task.dependsOn.length ? task.dependsOn.join(', ') : '无'],
    ['结果', task.outcome || '(未回传)'],
    ['摘要', task.summary || '(无)'],
    ['失败原因', task.failureReason || '(无)'],
  ];
  return (
    `<div class="task-detail" data-task="${esc(task.taskId)}">` +
    `<div class="detail-head">${statusBadge(String(task.status))} ${esc(task.objective)}</div>` +
    '<table class="wb kv">' +
    rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('') +
    '</table></div>'
  );
}

// ----------------------------------------------------------------------
// 审阅与返工
// ----------------------------------------------------------------------
export function renderIssuePanel(store: ResearchStore): string {
  const issues = Object.values(store.entities.issues);
  const blocking = blockingIssues(store);
  if (!issues.length) {
    return '<div class="wb-section"><h3>审阅与返工</h3>' +
      '<div class="empty">还没有审阅问题</div></div>';
  }
  const rows = issues
    .slice()
    .sort((a, b) => Number(b.blocking) - Number(a.blocking))
    .map((issue) =>
      `<tr class="${issue.blocking ? 'row-failed' : ''}">` +
      `<td>${esc(issue.severity)}</td><td>${esc(issue.category)}</td>` +
      `<td>${esc(issue.summary)}</td>` +
      `<td>${issue.blocking ? '阻断交付' : '可排期'}</td></tr>`
    )
    .join('');
  return (
    '<div class="wb-section"><h3>审阅与返工' +
    `<span class="wb-count">${issues.length} 条, 阻断 ${blocking.length}</span></h3>` +
    '<table class="wb"><tr><th>严重度</th><th>类别</th><th>问题</th><th>影响</th></tr>' +
    rows + '</table></div>'
  );
}

// ----------------------------------------------------------------------
// 整块装配
// ----------------------------------------------------------------------
export function renderTeamBoard(store: ResearchStore): string {
  const statuses = roleStatuses(store);
  const tasks = visibleTasks(store);
  const filters = (
    '<div class="team-filters">' +
    '<label>角色 <select data-filter="agent">' +
    '<option value="">全部</option>' +
    statuses.map((s) => `<option value="${esc(s.agent)}"${store.ui.filterAgent === s.agent ? ' selected' : ''}>${esc(s.label)}</option>`).join('') +
    '</select></label>' +
    '<label>状态 <select data-filter="status">' +
    '<option value="">全部</option>' +
    Object.entries(TASK_STATUS_LABEL)
      .map(([k, v]) => `<option value="${esc(k)}"${store.ui.filterTaskStatus === k ? ' selected' : ''}>${esc(v)}</option>`)
      .join('') +
    '</select></label>' +
    `<span class="wb-count">显示 ${tasks.length} 个任务</span>` +
    '</div>'
  );
  return (
    renderSupervisorHeader(store) +
    '<div class="wb-section"><h3>团队状态</h3>' +
    renderRoleStatusBar(statuses) + filters + '</div>' +
    '<div class="wb-section"><h3>任务</h3>' + renderTaskTable(tasks) + '</div>' +
    renderRoleCapabilities(store.entities.roles) +
    renderIssuePanel(store)
  );
}
