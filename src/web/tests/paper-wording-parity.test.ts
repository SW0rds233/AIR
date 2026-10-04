/**
 * `views/paper.ts` 拆分后的**文案与标记平价** (回归).
 *
 * 背景: 把 340 行拆成 `paper/{types,traceability,figure-gallery,numbering}.ts` 时,
 * 我改坏过两处**用户可见**的东西, 而当时的用例只断言了其中一句, 于是"通过"了:
 * 1. 状态文案被换掉 (`可反查` → `已追溯`, `正文缺位置` → `未追溯（快照有、正文无）`);
 * 2. 缺失锚点那条记录把 anchor 丢成空串 —— 用户恰恰要看"对不上的锚点在哪"。
 *
 * 期望值**逐字取自拆分前的构建产物** (`vite build` 出来的单文件, 拆分时导出核对过):
 * 状态文案表 `{mapped:"可反查",unmapped:"正文缺位置",missing:"锚点缺失"}`、徽章标记
 * `trace-status trace-status-<状态>` + `data-status`、两条说明文案、门槛的
 * "通过 / 未通过 / 未知（未记录）"、以及图库与编号的空态文案。
 *
 * 为什么不做"拿旧产物跑差异测试": 我试过, **六次都没做成** —— 压缩后的单文件里
 * 同形状的小函数太多, 按特征串定位老撞错一个 (把 `H` 认成转义函数 `A`), 拼出来的
 * 代码报 `A is not defined`。与其留一个把自己绕进去的装置, 不如把**真的被改坏过的那几处**
 * 写成窄而可靠的红线。
 */

import { describe, expect, it } from 'vitest';

import {
  TRACE_STATUS_LABEL,
  claimTraceRows,
  numberingView,
  renderFigureGallery,
  renderNumbering,
  renderPaperTraceability,
} from '../src/views/paper';

const MANIFEST = {
  delivery_level: '完整论文',
  snapshot_id: 'snap-1',
  claims: 4,
  evidence: 7,
  delivery_gate_passed: true,
  gate_passed: false,
  theory_gate_passed: null,
  writing_map: { 'clm-1': 'sec-2.1', 'clm-2': 'sec-3' },
  manuscript_traceability: {
    ok: false,
    note: '正文与快照不一致',
    unlabeled_blocks: ['第 3 段'],
    mapped: [{ claim_id: 'clm-1', anchor: 'sec-2.1' }],
    unmapped_claims: ['clm-2'],
    missing_anchors: ['clm-1'],
    core_claims: 3,
  },
  source_set: {
    source_set_id: 'kb-1', source_policy: 'user_kb', queries: [], uncovered: [],
    note: '',
    documents: [
      { source_id: 'src-1', title: 'Ternary codes', file_hash: 'abcdef0123456789',
        locator: 'arxiv:1' },
    ],
  },
};

describe('paper 视图拆分后的文案与标记平价', () => {
  it('三类状态文案逐字不变 (拆分前的三个字面量)', () => {
    expect(TRACE_STATUS_LABEL.mapped).toBe('可反查');
    expect(TRACE_STATUS_LABEL.unmapped).toBe('正文缺位置');
    expect(TRACE_STATUS_LABEL.missing).toBe('锚点缺失');
    const html = renderPaperTraceability(MANIFEST as never);
    for (const label of Object.values(TRACE_STATUS_LABEL)) {
      expect(html, `HTML 里丢了状态文案 ${label}`).toContain(label);
    }
  });

  it('徽章标记不变 (类名 + data-status + 图标是样式与可访问性的依赖)', () => {
    const html = renderPaperTraceability(MANIFEST as never);
    expect(html).toContain('class="trace-status trace-status-missing"');
    expect(html).toContain('data-status="missing"');
    expect(html).toContain('class="trace-status trace-status-unmapped"');
    expect(html).toContain('data-status="unmapped"');
    // 三类图标分别为 ✓ / ⊘ / ⚠
    expect(html).toContain('aria-hidden="true">⚠<');
    expect(html).toContain('aria-hidden="true">⊘<');
  });

  it('缺失锚点那条记录保留 anchor (不能丢成空串)', () => {
    const rows = claimTraceRows(MANIFEST as never);
    const missing = rows.find((row) => row.status === 'missing');
    expect(missing).toBeDefined();
    expect(missing?.anchor, '把"对不上的锚点在哪"丢了').toBe('sec-2.1');
    const html = renderPaperTraceability(MANIFEST as never);
    expect(html).toContain('映射锚点没有出现在正文里（映射与正文不一致）');
    expect(html).toContain('这条结论在正文里没有位置（写作阶段未映射）');
  });

  it('门槛文案与未知态: 三个字段各自独立, 缺失不默认通过', () => {
    const html = renderPaperTraceability(MANIFEST as never);
    expect(html).toContain('交付等级: '); expect(html).toContain('快照 snap-1');
    expect(html).toContain('出版门槛'); expect(html).toContain('>通过<');
    expect(html).toContain('研究门槛'); expect(html).toContain('>未通过<');
    expect(html).toContain('理论门槛'); expect(html).toContain('未知（未记录）');
  });

  it('图库与编号的空态文案不变', () => {
    expect(renderFigureGallery([], { projectId: 'p', runId: 'r' }))
      .toContain('还没有图表产物');
    const view = numberingView(null);
    expect(view.available).toBe(false);
    expect(renderNumbering(view)).toContain('渲染期编号');
    expect(view.note).toContain('编号只在最终渲染时分配');
  });
});
