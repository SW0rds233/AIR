/**
 * 图表面板: 从交付物清单挑出**本次运行的**成图, 并如实标出归属。
 *
 * 两条不可让步 (§9.6 F4):
 * - 只把 `figures/` 下的图片当正文配图 —— 别处的图片 (例如编辑残留) 不冒充图表;
 * - 归属不明的图**不冒充**当前成果: 卡片上直接写"归属未知/不属于当前运行",
 *   而不是默认当成自己的。
 *
 * 纯函数: 输入交付物清单, 输出结构或 HTML。
 */

import { esc, type ArtifactEntryView } from './types';

export interface FigureRow {
  name: string;
  /** 相对 outputs/ 的路径 (可直接用于预览)。 */
  path: string;
  size: number;
  /** 是否由本次运行产出 (归属校验: 不能把别的项目产物当成当前成果)。 */
  owned: boolean;
}

const FIGURE_SUFFIXES = ['.png', '.jpg', '.jpeg', '.svg', '.webp', '.pdf'];

/** 交付物清单里的成图 (含归属标记)。 */
export function figureRows(entries: Array<ArtifactEntryView> | null | undefined,
                           identity: { projectId?: string; runId?: string } = {}): FigureRow[] {
  const rows: FigureRow[] = [];
  for (const entry of entries ?? []) {
    const name = String(entry?.name || '');
    if (!name) continue;
    const lower = name.toLowerCase();
    if (!FIGURE_SUFFIXES.some((suffix) => lower.endsWith(suffix))) continue;
    // 只把 figures/ 下的当正文配图; 其它图片 (例如编辑残留) 不冒充图表
    if (!lower.includes('figures/')) continue;
    const owned = (!identity.projectId || entry.project_id === identity.projectId)
      && (!identity.runId || entry.run_id === identity.runId);
    rows.push({
      name: name.split('/').pop() || name,
      path: name,
      size: Number(entry.size || 0),
      owned: Boolean(owned),
    });
  }
  return rows.sort((a, b) => a.path.localeCompare(b.path));
}

export function renderFigureGallery(entries: Array<ArtifactEntryView> | null | undefined,
                                    identity: { projectId?: string; runId?: string } = {},
                                    base = ''): string {
  const rows = figureRows(entries, identity);
  if (!rows.length) {
    return '<div class="empty">还没有图表产物。配图智能体产出后会出现在这里。</div>';
  }
  return '<div class="figure-grid">' + rows.map((row) => (
    '<figure class="figure-card">' +
    `<img src="${esc(base)}/api/artifacts/${esc(row.path)}" alt="${esc(row.name)}" loading="lazy">` +
    `<figcaption><span class="mono">${esc(row.path)}</span></figcaption>` +
    `<div class="wb-note">${esc(row.name)} · ${row.size} B · ` +
    (row.owned ? '属于当前运行' : '<span class="warn">归属未知/不属于当前运行</span>') +
    '</div></figure>'
  )).join('') + '</div>';
}
