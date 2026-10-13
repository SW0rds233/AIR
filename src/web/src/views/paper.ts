/** 论文与追溯视图 —— 分组入口。 */

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
