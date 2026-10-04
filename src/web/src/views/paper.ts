/**
 * 论文与追溯视图 (合并计划 §9.5 / §9.6, F3–F5) —— **分组入口**。
 *
 * 三个子视图各有自己的硬约束, 因此各自成模块 (§9.6 里点名的三块):
 * - `paper/traceability.ts`   论断 → 正文锚点 → 快照对象, 三个门槛各自独立
 * - `paper/figure-gallery.ts` 只认 `figures/` 下的图, 归属不明的**如实标注**
 * - `paper/numbering.ts`      只显示后端给定的渲染期编号, 不自行编号
 * - `paper/types.ts`          公共类型与转义 (避免子视图之间反向依赖)
 *
 * 这里只做转发: 调用方 (页面装配与用例) 的导入路径保持稳定, 而"某一类追溯逻辑
 * 改在哪里"由文件名直接回答。
 */

export { esc } from './paper/types';
export type {
  ArtifactEntryView,
  DeliveryManifestView,
  ManuscriptTraceability,
} from './paper/types';

export {
  claimTraceRows,
  gateClass,
  gateLabel,
  renderPaperTraceability,
  sourceIndex,
  traceStatusBadge,
  TRACE_STATUS_LABEL,
} from './paper/traceability';
export type { SourceIndexRow, TraceRow } from './paper/traceability';

export { figureRows, renderFigureGallery } from './paper/figure-gallery';
export type { FigureRow } from './paper/figure-gallery';

export { numberingView, renderNumbering } from './paper/numbering';
export type { NumberingView } from './paper/numbering';
