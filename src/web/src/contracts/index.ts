/**
 * 前后端契约类型的**分组入口** (计划书 §9.5)。
 *
 * 为什么按域分文件而不是一个 `contracts.ts`:
 * 三类契约的后端来源、改动频率和消费者都不同 ——
 * - `session.ts`     会话身份/SSE 事件/状态补偿 (由 `sessions/` 与端点提供)
 * - `research.ts`    问题形式化与工作台行 (权威在 `research/schemas.py`)
 * - `publication.ts` 交付包清单与追溯 (权威在 `research/package.py`)
 * - `library.ts`     资料库路径导入与库详情 (§13)
 *
 * 混在一个文件里时, "改交付清单会不会影响会话类型"这种问题只能靠通读;
 * 分开之后每个域可以独立演进, 也让"这个类型该跟着哪个后端改"一目了然。
 */

export type {
  EngineId,
  RunStatus,
  SessionEvent,
  SessionState,
  StartSessionResponse,
} from './session';
export type {
  ClaimRow,
  EvidenceRow,
  ExperimentRow,
  FeedbackRequest,
  FeedbackResponse,
  ProblemContract,
  ResearchPath,
  RetrievalCoverage,
  RouteRow,
  SourcePolicy,
  TaskKind,
  WorkbenchState,
} from './research';
export type {
  ArtifactEntry,
  ArtifactListBody,
  DeliveryManifest,
  ManifestReadResult,
} from './publication';
export { pickManifestEntry, readManifestBody } from './publication';
export type {
  ImportedPathRow,
  LibraryDeleteResult,
  LibraryDetail,
  LibraryImportReport,
  LibraryImportRequest,
  LibraryPathRequest,
  LibraryScanReport,
  ScannedPathRow,
} from './library';
export {
  emptyPathRequest,
  importKey,
  parsePaths,
} from './library';
