/**
 * 论文/公式/图表追溯视图用例 (合并计划 §9.5 / §9.6, F3–F5)。
 *
 * 重点在"诚实": 交付门槛结论以 manifest 为准、缺位置与缺锚点的论断必须显式列出、
 * 图表归属不能冒充当前运行、公式编号没有数据时不自己编号。
 */

import { describe, expect, it } from 'vitest';

import {
  claimTraceRows,
  figureRows,
  gateLabel,
  numberingView,
  renderFigureGallery,
  renderPaperTraceability,
  sourceIndex,
  type DeliveryManifestView,
} from '../src/views/paper';

function manifest(over: Partial<DeliveryManifestView> = {}): DeliveryManifestView {
  return {
    project_id: 'proj-1',
    problem_id: 'p1',
    run_id: 'run-1',
    snapshot_id: 'snap-1',
    claims: 2,
    evidence: 1,
    writing_map: { 'clm-a': 'proposition-clm-a' },
    manuscript_traceability: {
      ok: false,
      mapped: [{ claim_id: 'clm-a', anchor: 'proposition-clm-a' }],
      unmapped_claims: ['clm-b'],
      missing_anchors: ['clm-c'],
      unlabeled_blocks: ['某某结论'],
      core_claims: 3,
      note: '正文与快照不一致',
    },
    delivery_level: '论文草稿',
    delivery_gate_passed: false,
    gate_passed: true,
    theory_gate_passed: null,
    source_set: {
      source_set_id: 'kb-1',
      source_policy: 'user_kb',
      queries: ['2-(211,15,1)'],
      uncovered: [],
      note: '',
      documents: [{ source_id: 'src-1', title: 'Ternary codes', file_hash: 'abcdef0123456789', locator: 'arxiv:1234.5678' }],
    },
    ...over,
  };
}

describe('交付门槛', () => {
  it('未记录时显示未知, 不默认通过', () => {
    expect(gateLabel(null)).toContain('未知');
    expect(gateLabel(undefined)).toContain('未知');
    expect(gateLabel(false)).toBe('未通过');
    expect(gateLabel(true)).toBe('通过');
  });

  it('三个门槛字段各自独立显示 (不得互相覆盖)', () => {
    const html = renderPaperTraceability(manifest());
    expect(html).toContain('出版门槛');
    expect(html).toContain('研究门槛');
    expect(html).toContain('理论门槛');
    // 出版门槛未通过而研究门槛通过: 两个结论都要出现, 不能合成一个
    expect(html).toContain('gate-warn');
    expect(html).toContain('gate-ok');
  });
});

describe('论断追溯', () => {
  it('可反查 / 正文缺位置 / 锚点缺失三类都显式出现', () => {
    const rows = claimTraceRows(manifest());
    const byId = Object.fromEntries(rows.map((row) => [row.claim_id, row]));
    expect(byId['clm-a'].status).toBe('mapped');
    expect(byId['clm-a'].anchor).toBe('proposition-clm-a');
    expect(byId['clm-b'].status).toBe('unmapped');
    expect(byId['clm-c'].status).toBe('missing');
  });

  it('缺位置与缺锚点必须出现在渲染结果里, 不能被"看起来完整"掩盖', () => {
    const html = renderPaperTraceability(manifest());
    expect(html).toContain('未通过');
    expect(html).toContain('clm-b');
    expect(html).toContain('clm-c');
    expect(html).toContain('正文缺位置');
    expect(html).toContain('锚点缺失');
    expect(html).toContain('某某结论');
  });

  it('可反查性通过时给出明确结论, 不显示告警', () => {
    const html = renderPaperTraceability(manifest({
      manuscript_traceability: { ok: true, mapped: [{ claim_id: 'clm-a', anchor: 'a' }] },
    }));
    expect(html).toContain('可反查性检查通过');
    // 断言的是**可反查性**那一段的结论, 不是页面上其它门槛的措辞
    // (出版门槛可能如实显示"未通过", 那是另一件事, 不得混为一谈)
    expect(html).not.toContain('可反查性检查<b>未通过</b>');
    expect(html).not.toContain('paper-warn');
  });

  it('没有交付包时说明原因, 不显示空表冒充结果', () => {
    expect(renderPaperTraceability(null)).toContain('还没有交付包');
  });
});

describe('来源索引', () => {
  it('逐条给出 source_id / hash / 定位', () => {
    const rows = sourceIndex(manifest());
    expect(rows).toHaveLength(1);
    expect(rows[0].source_id).toBe('src-1');
    expect(rows[0].locator).toBe('arxiv:1234.5678');
  });

  it('没有来源时如实说明', () => {
    expect(renderPaperTraceability(manifest({ source_set: {
      source_set_id: '', source_policy: '', queries: [], uncovered: [],
      documents: [], note: '',
    } })))
      .toContain('没有登记来源');
  });
});

describe('图表追溯', () => {
  const entries = [
    { name: 'figures/run-1/trend_0.png', size: 100, project_id: 'proj-1', run_id: 'run-1' },
    { name: 'figures/run-2/trend_0.png', size: 120, project_id: 'proj-2', run_id: 'run-2' },
    { name: 'paper.pdf', size: 999, project_id: 'proj-1', run_id: 'run-1' },
    { name: 'notes/logo.png', size: 5, project_id: 'proj-1', run_id: 'run-1' },
  ];

  it('只把 figures/ 下的图片当图表, 并标出归属', () => {
    const rows = figureRows(entries, { projectId: 'proj-1', runId: 'run-1' });
    expect(rows.map((row) => row.path)).toEqual([
      'figures/run-1/trend_0.png', 'figures/run-2/trend_0.png',
    ]);
    expect(rows[0].owned).toBe(true);
    expect(rows[1].owned).toBe(false);
  });

  it('别的运行的图不得显示成当前成果', () => {
    const html = renderFigureGallery(entries, { projectId: 'proj-1', runId: 'run-1' });
    expect(html).toContain('属于当前运行');
    expect(html).toContain('不属于当前运行');
  });

  it('没有图表时给出可执行说明', () => {
    expect(renderFigureGallery([])).toContain('还没有图表产物');
  });
});

describe('渲染期编号', () => {
  it('没有编号数据时不自行编号, 并说明原因', () => {
    const view = numberingView(null);
    expect(view.available).toBe(false);
    expect(view.note).toContain('只在最终渲染时分配');
  });

  it('有编号数据时按后端给定值显示', () => {
    const view = numberingView({
      citations: { 'clm-a': '1' }, figures: { 'fig-1': '1' }, equations: {},
    });
    expect(view.available).toBe(true);
    expect(view.citations['clm-a']).toBe('1');
    expect(view.figures['fig-1']).toBe('1');
  });
});
