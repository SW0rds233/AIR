/**
 * 渲染期编号面板: 只显示**后端给定**的编号。
 *
 * 核心约束 (§6.3): 引用/图/公式的编号只在最终渲染时分配。页面自行编号会与导出稿
 * 不一致 —— 因此没有编号数据时如实说"只在最终渲染时分配", 而不是从 1 开始数。
 *
 * 纯函数: 输入 manifest 里的 `numbering` 字段, 输出结构与 HTML。
 */

import { esc } from './types';

export interface NumberingView {
  available: boolean;
  citations: Record<string, string>;
  figures: Record<string, string>;
  equations: Record<string, string>;
  note: string;
}

/**
 * 渲染期编号 (§6.3)。
 *
 * **没有编号数据时不自己编号**: 公式/图号只在最终渲染时分配, 页面自行编号会与
 * 导出稿不一致 —— 这里如实返回 `available=false` 与原因。
 */
export function numberingView(raw: unknown): NumberingView {
  const data = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : null;
  const pick = (value: unknown): Record<string, string> => {
    const out: Record<string, string> = {};
    if (value && typeof value === 'object') {
      for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
        out[String(key)] = String(item);
      }
    }
    return out;
  };
  const citations = pick(data?.citations);
  const figures = pick(data?.figures);
  const equations = pick(data?.equations);
  const available = Object.keys(citations).length + Object.keys(figures).length
    + Object.keys(equations).length > 0;
  return {
    available, citations, figures, equations,
    note: available ? '编号来自渲染结果'
      : '编号只在最终渲染时分配（当前会话未提供编号数据，页面不自行编号）',
  };
}

export function renderNumbering(view: NumberingView): string {
  const lines: Array<[string, Record<string, string>]> = [
    ['引用', view.citations], ['图', view.figures], ['公式', view.equations],
  ];
  const body = lines
    .filter(([, map]) => Object.keys(map).length)
    .map(([label, map]) => '<div class="numbering-row">' +
      `${esc(label)}: ` + Object.entries(map)
        .map(([key, value]) => `<span class="mono">${esc(key)}=${esc(value)}</span>`)
        .join(' · ') + '</div>').join('');
  return '<div class="paper-section"><div class="paper-section-head">渲染期编号</div>' +
    `<div class="wb-note">${esc(view.note)}</div>` + body + '</div>';
}
