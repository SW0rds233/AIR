/**
 * 资料工作区视图 (合并计划 §13.4「接口与前端」)。
 *
 * 两条必须守住的要求:
 * - **不显示系统绝对路径**: 后端只回 basename, 视图也只渲染它, 不做补全;
 * - **不把部分失败显示成成功**: `denied`/`failed`/截断必须出现在清单里, 并给出
 *   可执行的下一步, 而不是一句"导入完成"。
 *
 * 本模块是纯函数 (输入报告/登记表, 输出 HTML 与分组结构), 可直接在测试里断言;
 * 网络调用留在页面控制器里 (与 `refreshSourceSets` 同一分工)。
 */

import type {
  ImportedPathRow,
  LibraryDetail,
  LibraryImportReport,
  LibraryScanReport,
  ScannedPathRow,
} from '../contracts/library';

export function esc(text: unknown): string {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 逐状态图标 + 文字 (状态不能只靠颜色区分, §9.6)。 */
export const PATH_STATUS: Record<string, { icon: string; label: string }> = {
  ok: { icon: '✓', label: '将入库' },
  imported: { icon: '＋', label: '新增入库' },
  merged: { icon: '↔', label: '合并到既有文献' },
  duplicate: { icon: '=', label: '已在库中（幂等命中）' },
  skipped: { icon: '⊘', label: '跳过（类型不在允许范围）' },
  denied: { icon: '⛔', label: '拒绝（未授权或敏感）' },
  unreadable: { icon: '⚠', label: '无法读取' },
  failed: { icon: '✗', label: '失败' },
  dir: { icon: '▤', label: '目录' },
};

export function pathStatusBadge(status: string): string {
  const key = String(status || 'unknown');
  const info = PATH_STATUS[key] ?? { icon: '?', label: `未知状态 (${key})` };
  return `<span class="lib-status lib-status-${esc(key)}" data-status="${esc(key)}">` +
    `<span class="icon" aria-hidden="true">${info.icon}</span>` +
    `<span class="txt">${esc(info.label)}</span></span>`;
}

export function formatBytes(size: number): string {
  const bytes = Number(size) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
}

/** 展示用文件名 (后端已脱敏, 这里只兜底去掉空值)。 */
export function fileName(row: { path?: string }): string {
  const text = String(row?.path || '');
  return text || '(未命名)';
}

// ----------------------------------------------------------------------
// 扫描预览 (先预览再确认)
// ----------------------------------------------------------------------
export interface ScanCounts {
  ok: number;
  skipped: number;
  denied: number;
  duplicate: number;
  unreadable: number;
  total: number;
  bytes: number;
}

/** 逐状态计数: 优先用后端给的 counts, 缺失时按行统计 (而不是补零糊过去)。 */
export function scanCounts(report: LibraryScanReport | null | undefined): ScanCounts {
  const rows = [...(report?.files ?? []), ...(report?.denied ?? [])];
  const counts: ScanCounts = {
    ok: 0, skipped: 0, denied: (report?.denied ?? []).length, duplicate: 0,
    unreadable: 0, total: rows.length, bytes: Number(report?.total_bytes ?? 0),
  };
  // 后端缺失 counts 时按**行**统计 (拒绝项不在 files 里, 因此单独数 denied)
  const derived: Record<string, number> = { denied: (report?.denied ?? []).length };
  for (const row of report?.files ?? []) {
    derived[row.status] = (derived[row.status] ?? 0) + 1;
  }
  const server = report?.counts ?? {};
  const keys: Array<keyof ScanCounts> = ['ok', 'skipped', 'denied', 'duplicate', 'unreadable'];
  for (const key of keys) {
    counts[key] = typeof server[key] === 'number' ? Number(server[key]) : (derived[key] ?? 0);
  }
  return counts;
}

function pathRowHtml(row: ScannedPathRow): string {
  const detail = [
    formatBytes(row.size),
    row.authorized_root ? '授权根 ' + esc(row.authorized_root) : '',
    row.symlink ? '符号链接' : '',
  ].filter(Boolean).join(' · ');
  return '<div class="lib-item">' +
    `<span class="mono">${esc(fileName(row))}</span> · ${pathStatusBadge(row.status)}` +
    (detail ? ` · <span class="wb-note">${detail}</span>` : '') +
    (row.reason ? `<div class="lib-reason">${esc(row.reason)}</div>` : '') +
    '</div>';
}

/**
 * 预览清单: 将入库 / 跳过 / 拒绝 / 截断提示。
 *
 * 拒绝项**单独成组并置顶提示**, 因为它们与"跳过"的处置完全不同 (前者需要维护者
 * 放行授权根, 后者只需换文件格式)。截断必须显式说明数量上限。
 */
export function renderScanPreview(report: LibraryScanReport | null | undefined): string {
  if (!report) return '<div class="empty">尚未扫描。填入本机路径后先「扫描预览」。</div>';
  const rows = report.files ?? [];
  const denied = report.denied ?? [];
  if (!rows.length && !denied.length) {
    return '<div class="empty">没有命中任何文件。' +
      (report.notes?.length ? esc(report.notes.join('；')) : '请检查路径是否正确。') +
      '</div>';
  }
  const counts = scanCounts(report);
  const head = '<div class="lib-summary">' +
    `将入库 <b>${counts.ok}</b> · 跳过 <b>${counts.skipped}</b> · ` +
    `已在库 <b>${counts.duplicate}</b> · 拒绝 <b class="${counts.denied ? 'warn' : ''}">${counts.denied}</b> · ` +
    `无法读取 <b>${counts.unreadable}</b> · 合计 <b>${formatBytes(counts.bytes)}</b>` +
    '</div>';
  const truncated = report.truncated
    ? '<div class="lib-warn">已达到数量/体量上限，清单被<b>截断</b>：' +
      '本次只处理上面列出的文件，其余未扫描。请缩小范围后再次扫描。</div>'
    : '';
  const notes = (report.notes ?? []).length
    ? '<div class="lib-notes">' + esc(report.notes.join('；')) + '</div>' : '';
  const deniedBlock = denied.length
    ? '<div class="lib-group lib-group-denied"><div class="lib-group-head">' +
      `拒绝 ${denied.length} 项（未授权根或敏感文件）</div>` +
      denied.map(pathRowHtml).join('') +
      '<div class="wb-note">如需读取，请由维护者在 DATA_READ_ROOTS 显式放行该目录；' +
      '凭据与私钥类文件不会被放行。</div></div>'
    : '';
  const okBlock = rows.length
    ? '<div class="lib-group"><div class="lib-group-head">清单</div>' +
      rows.map(pathRowHtml).join('') + '</div>'
    : '';
  return head + truncated + notes + deniedBlock + okBlock;
}

// ----------------------------------------------------------------------
// 导入报告
// ----------------------------------------------------------------------
function importedRowHtml(row: ImportedPathRow): string {
  const bits = [
    row.pages ? `${row.pages} 页` : '',
    row.cards ? `${row.cards} 卡` : '',
    row.file_hash ? 'sha256 ' + esc(String(row.file_hash).slice(0, 12)) : '',
  ].filter(Boolean).join(' · ');
  return pathRowHtml(row as ScannedPathRow) +
    (bits ? `<div class="wb-note">${bits}</div>` : '') +
    (row.title ? `<div class="lib-title">${esc(row.title)}</div>` : '');
}

/** 导入报告: 逐条结果 + 明确的整体结论 (部分失败**不报成功**)。 */
export function renderImportReport(report: LibraryImportReport | null | undefined): string {
  if (!report) return '';
  const counts = report.counts ?? {};
  const head = '<div class="lib-summary">' +
    `新增 <b>${counts.imported ?? (report.imported ?? []).length}</b> · ` +
    `合并 <b>${counts.merged ?? (report.merged ?? []).length}</b> · ` +
    `已在库 <b>${counts.duplicates ?? (report.duplicates ?? []).length}</b> · ` +
    `跳过 <b>${counts.skipped ?? (report.skipped ?? []).length}</b> · ` +
    `拒绝 <b>${counts.denied ?? (report.denied ?? []).length}</b> · ` +
    `失败 <b>${counts.failed ?? (report.failed ?? []).length}</b>` +
    '</div>';
  const verdict = report.ok
    ? '<div class="lib-ok">导入完成：所有命中文件都已入库（幂等键防止重复计费）。</div>'
    : '<div class="lib-warn">导入<b>未全部成功</b>：下面逐条列出被拒绝、失败或截断的文件。' +
      '已入库的部分保留，可修正路径后再次导入（同一文件不会重复入库）。</div>';
  const groups: Array<[string, ImportedPathRow[]]> = [
    ['新增入库', report.imported ?? []],
    ['合并到既有文献', report.merged ?? []],
    ['已在库（幂等命中）', report.duplicates ?? []],
    ['跳过', report.skipped ?? []],
    ['拒绝', report.denied ?? []],
    ['失败', report.failed ?? []],
  ];
  const blocks = groups
    .filter(([, rows]) => rows.length)
    .map(([title, rows]) => '<div class="lib-group"><div class="lib-group-head">' +
      `${esc(title)} ${rows.length}</div>` + rows.map(importedRowHtml).join('') + '</div>')
    .join('');
  const truncated = report.truncated
    ? '<div class="lib-warn">本次导入被<b>截断</b>（达到数量/体量上限）。</div>' : '';
  const notes = (report.notes ?? []).length
    ? '<div class="lib-notes">' + esc(report.notes.join('；')) + '</div>' : '';
  return head + verdict + truncated + notes + blocks;
}

// ----------------------------------------------------------------------
// 资料工作区 (库分组)
// ----------------------------------------------------------------------
export interface LibraryGroup {
  /** 来源类型: path_import / managed / upload (§13.3 数据模型扩展)。 */
  origin: string;
  label: string;
  items: LibraryDetail[];
}

export const ORIGIN_LABEL: Record<string, string> = {
  path_import: '本机路径导入',
  managed: '系统管理库',
  upload: '上传资料',
};

/**
 * 按来源类型分组。
 *
 * 输入是 `SourceSet` 摘要 (`/api/sources`) 加上**已加载详情**的映射: 只对已展开的
 * 库显示文件与失效标记, 未展开的不编造文件数。缺失 `origin` 的库按 `managed` 处理
 * 并在标签里写明"来源未知", 不假装它一定是路径导入。
 */
export function groupLibraries(
  sources: Array<Record<string, unknown>>,
  details: Record<string, LibraryDetail> = {},
): LibraryGroup[] {
  const groups = new Map<string, LibraryGroup>();
  for (const source of sources ?? []) {
    const id = String((source as any).source_set_id ?? (source as any).id ?? '');
    if (!id) continue;
    const detail = details[id];
    const origin = String(detail?.origin || (source as any).origin || 'managed');
    const key = ORIGIN_LABEL[origin] ? origin : 'unknown';
    if (!groups.has(key)) {
      groups.set(key, {
        origin: key,
        label: ORIGIN_LABEL[key] ?? '来源未知',
        items: [],
      });
    }
    groups.get(key)!.items.push({
      source_set_id: id,
      origin,
      readable: detail?.readable ?? true,
      roots: detail?.roots ?? [],
      documents: Number(detail?.documents ?? (source as any).documents ?? 0),
      cards: Number(detail?.cards ?? (source as any).cards ?? 0),
      files: Number(detail?.files ?? 0),
      stale_files: detail?.stale_files ?? [],
      hashes: detail?.hashes ?? [],
      note: detail?.note,
    });
  }
  const order = ['path_import', 'upload', 'managed', 'unknown'];
  return [...groups.values()].sort(
    (a, b) => order.indexOf(a.origin) - order.indexOf(b.origin));
}

/** 资料工作区 HTML: 来源类型 / 授权根 / 文件状态（在库 / 已失效）。 */
export function renderLibraryWorkspace(
  sources: Array<Record<string, unknown>>,
  details: Record<string, LibraryDetail> = {},
): string {
  const groups = groupLibraries(sources, details);
  if (!groups.length) {
    return '<div class="empty">还没有资料库。可以在「从本机路径添加资料」里指定本机目录。</div>';
  }
  return groups.map((group) => {
    const items = group.items.map((item) => {
      const stale = item.stale_files ?? [];
      const roots = (item.roots ?? []).length
        ? '<div class="wb-note">授权根 ' + esc(item.roots.join('、')) + '</div>' : '';
      const files = item.files
        ? `<div class="wb-note">已登记 ${item.files} 个本机文件` +
          (stale.length
            ? ` · <span class="warn">已失效 ${stale.length}</span>：${esc(stale.join('、'))}`
            : ' · 全部在用') + '</div>'
        : '';
      const missing = stale.length
        ? '<div class="lib-warn">原件已被移动或删除：' + esc(stale.join('、')) +
          '。重新导入可恢复；旧引用不会静默保留。</div>'
        : '';
      return '<div class="lib-lib" data-source-set="' + esc(item.source_set_id) + '">' +
        `<div class="lib-lib-head"><b>${esc(item.source_set_id)}</b> · ` +
        `${item.documents} 篇 · ${item.cards} 卡` +
        (item.readable ? '' : ' · <span class="warn">不可读</span>') + '</div>' +
        roots + files + missing +
        (item.note ? `<div class="wb-note">${esc(item.note)}</div>` : '') +
        '<button class="ghost" data-action="deleteLibrary" data-id="' +
        esc(item.source_set_id) + '">解除登记</button>' +
        '<span class="wb-note">只解除库登记，不删除你的原文件。</span>' +
        '</div>';
    }).join('');
    return '<div class="lib-group"><div class="lib-group-head">' +
      `${esc(group.label)} ${group.items.length}</div>${items}</div>`;
  }).join('');
}
