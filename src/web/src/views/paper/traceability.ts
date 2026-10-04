/**
 * 稿件追溯面板: 论断 → 正文锚点 → 快照对象, 以及三个门槛的**独立**结论。
 *
 * 三条不可让步 (与 §9.6 一致):
 * - 门槛结论以 `manifest.json` 的字段为准, 页面不自己判断"通过"; 缺失显示"未知";
 * - 无法反查的论断必须**显式列出** (`unmapped_claims` / `missing_anchors` /
 *   `unlabeled_blocks`), 不能因为"看起来完整"就当作已追溯;
 * - 来源索引按交付包登记的 `source_set.documents` 显示, 不猜。
 *
 * 纯函数: 输入 manifest, 输出结构或 HTML; 取数与 DOM 装配在页面控制器。
 */

import { esc, type DeliveryManifestView } from './types';

// ----------------------------------------------------------------------
// 追溯表
// ----------------------------------------------------------------------
export interface TraceRow {
  claim_id: string;
  anchor: string;
  /** mapped / unmapped (快照里有结论但正文没有位置) / missing (锚点不在正文里)。 */
  status: 'mapped' | 'unmapped' | 'missing';
  note: string;
}

/** 逐条论断的追溯行: 三类状态都显式出现, 不把"缺失"当成"没有"。 */
export function claimTraceRows(manifest: DeliveryManifestView | null | undefined): TraceRow[] {
  if (!manifest) return [];
  const trace = manifest.manuscript_traceability ?? {};
  const rows = new Map<string, TraceRow>();
  for (const [claimId, anchor] of Object.entries(manifest.writing_map ?? {})) {
    rows.set(String(claimId), {
      claim_id: String(claimId), anchor: String(anchor || ''),
      status: 'mapped', note: '',
    });
  }
  for (const item of trace.mapped ?? []) {
    const claimId = String(item?.claim_id || '');
    if (!claimId) continue;
    rows.set(claimId, {
      claim_id: claimId, anchor: String(item?.anchor || ''),
      status: 'mapped', note: '',
    });
  }
  // 快照里有结论、正文里没有位置: 必须出现, 且原因写明
  for (const claimId of trace.unmapped_claims ?? []) {
    const key = String(claimId || '');
    if (!key) continue;
    rows.set(key, {
      claim_id: key, anchor: '',
      status: 'unmapped', note: '这条结论在正文里没有位置（写作阶段未映射）',
    });
  }
  // 正文有锚点、但锚点不在正文里 (写作映射与实际正文不一致)。
  // 注意: 保留已有的 anchor, 只把状态升级为 missing —— 否则会把"映射到哪儿"丢掉,
  // 而用户正是要看那个对不上的锚点在哪里。
  for (const anchor of trace.missing_anchors ?? []) {
    const key = String(anchor || '');
    if (!key) continue;
    const existing = rows.get(key);
    rows.set(key, {
      claim_id: key, anchor: existing?.anchor || '',
      status: 'missing', note: '映射锚点没有出现在正文里（映射与正文不一致）',
    });
  }
  return [...rows.values()].sort((a, b) => a.claim_id.localeCompare(b.claim_id));
}

export const TRACE_STATUS_LABEL: Record<TraceRow['status'], string> = {
  mapped: '可反查',
  unmapped: '正文缺位置',
  missing: '锚点缺失',
};

const TRACE_STATUS_ICON: Record<TraceRow['status'], string> = {
  mapped: '✓',
  unmapped: '⊘',
  missing: '⚠',
};

export function traceStatusBadge(status: TraceRow['status']): string {
  const icon = TRACE_STATUS_ICON[status] ?? '⚠';
  const label = TRACE_STATUS_LABEL[status] ?? status;
  return `<span class="trace-status trace-status-${esc(status)}" data-status="${esc(status)}">` +
    `<span class="icon" aria-hidden="true">${icon}</span>` +
    `<span class="txt">${esc(label)}</span></span>`;
}// ----------------------------------------------------------------------
// 来源索引 (从稿件句子回到来源)
// ----------------------------------------------------------------------
export interface SourceIndexRow {
  source_id: string;
  title: string;
  file_hash: string;
  locator: string;
}

export function sourceIndex(manifest: DeliveryManifestView | null | undefined): SourceIndexRow[] {
  const docs = manifest?.source_set?.documents ?? [];
  return docs.map((doc) => ({
    source_id: String(doc?.source_id || ''),
    title: String(doc?.title || doc?.source_id || ''),
    file_hash: String(doc?.file_hash || ''),
    locator: String(doc?.locator || ''),
  })).filter((row) => row.source_id || row.title);
}

// ----------------------------------------------------------------------
// 渲染
// ----------------------------------------------------------------------
/** 门槛结论: 三个字段各自独立, 缺失显示"未知", 不默认通过。 */
export function gateLabel(passed: boolean | null | undefined): string {
  if (passed === true) return '通过';
  if (passed === false) return '未通过';
  return '未知（未记录）';
}

export function gateClass(passed: boolean | null | undefined): string {
  if (passed === true) return 'ok';
  if (passed === false) return 'warn';
  return 'unknown';
}

export function renderPaperTraceability(manifest: DeliveryManifestView | null | undefined): string {
  if (!manifest) {
    return '<div class="empty">还没有交付包：研究完成后这里显示稿件与追溯信息。</div>';
  }
  const trace = manifest.manuscript_traceability ?? {};
  const rows = claimTraceRows(manifest);
  const head = '<div class="paper-head">' +
    `<div>交付等级: <b>${esc(manifest.delivery_level || '未知')}</b></div>` +
    `<div>出版门槛: <span class="gate gate-${gateClass(manifest.delivery_gate_passed)}">` +
    `${esc(gateLabel(manifest.delivery_gate_passed))}</span> · ` +
    `研究门槛: <span class="gate gate-${gateClass(manifest.gate_passed)}">` +
    `${esc(gateLabel(manifest.gate_passed))}</span> · ` +
    `理论门槛: <span class="gate gate-${gateClass(manifest.theory_gate_passed)}">` +
    `${esc(gateLabel(manifest.theory_gate_passed))}</span></div>` +
    `<div class="wb-note">快照 ${esc(manifest.snapshot_id || '-')} · 命题 ${manifest.claims ?? 0} · ` +
    `证据 ${manifest.evidence ?? 0}</div>` +
    '</div>';

  const verdict = trace.ok
    ? '<div class="paper-ok">可反查性检查通过：正文中的每条核心论断都能回到冻结快照对象。</div>'
    : '<div class="paper-warn">可反查性检查<b>未通过</b>：下面逐条列出无法反查的论断。' +
      (trace.note ? `<div class="wb-note">${esc(trace.note)}</div>` : '') + '</div>';

  const table = rows.length
    ? '<table class="wb"><thead><tr><th>论断 (快照对象)</th><th>正文锚点</th>' +
      '<th>状态</th><th>说明</th></tr></thead><tbody>' +
      rows.map((row) => '<tr>' +
        `<td class="mono">${esc(row.claim_id)}</td>` +
        `<td class="mono">${esc(row.anchor || '-')}</td>` +
        `<td>${traceStatusBadge(row.status)}</td>` +
        `<td>${esc(row.note || '')}</td></tr>`).join('') +
      '</tbody></table>'
    : '<div class="empty">交付包里没有写作映射（可能是研究备忘录或纯综述交付）。</div>';

  const unlabeled = (trace.unlabeled_blocks ?? []).length
    ? '<div class="paper-warn">以下正文段落没有对象标签（无法归属到快照对象）：' +
      esc((trace.unlabeled_blocks ?? []).join('、')) + '</div>'
    : '';

  const sources = sourceIndex(manifest);
  const sourceBlock = '<div class="paper-section"><div class="paper-section-head">' +
    `来源（${sources.length}）</div>` +
    (sources.length
      ? '<table class="wb"><thead><tr><th>来源</th><th>标题</th><th>hash</th>' +
        '<th>定位</th></tr></thead><tbody>' +
        sources.map((row) => '<tr>' +
          `<td class="mono">${esc(row.source_id)}</td><td>${esc(row.title)}</td>` +
          `<td class="mono">${esc(row.file_hash.slice(0, 12))}</td>` +
          `<td class="mono">${esc(row.locator || '-')}</td></tr>`).join('') +
        '</tbody></table>'
      : '<div class="empty">交付包没有登记来源（研究可能未使用外部资料）。</div>') +
    '</div>';

  return head + verdict + unlabeled + table + sourceBlock;
}
