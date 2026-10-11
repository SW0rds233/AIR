/**
 * 资料库契约: 按用户指定本机路径构建人工文献库。
 *
 * 为什么单独成文件: `contracts.ts` 描述会话/研究/交付的载荷, 这里的载荷属于
 * **资料接入** (扫描预览、导入报告、库详情)。计划书 §9.5 要求传输类型按
 * session/team/research/publication 分组, 因此新契约不再往 `contracts.ts` 里堆。
 *
 * 服务端口径 (`src/team_api.py` + `src/kb/path_import.py`):
 * - 响应里**只有 basename** (`display_path`), 完整绝对路径不外发;
 * - `denied` 项单独列出并给原因, 界面不得把它显示成"已跳过";
 * - 截断必须显式 (`truncated=true`), 不能静默丢文件;
 * - 删除库只解除登记, 不动用户原文件与共享向量库。
 */

/** 单个扫描条目在响应里的形状 (`ScannedFile.to_dict`)。 */
export interface ScannedPathRow {
  /** 已脱敏的展示路径 (basename)。 */
  path: string;
  size: number;
  file_hash: string;
  kind: 'file' | 'dir';
  /** ok / skipped / denied / duplicate / unreadable (后端 FileStatus)。 */
  status: string;
  reason: string;
  mtime: number;
  authorized_root: string;
  symlink: boolean;
}

/** 一条导入结果 (`ImportedRecord.to_dict`), 新增/合并/跳过/拒绝/失败都走这个形状。 */
export interface ImportedPathRow extends ScannedPathRow {
  doc_id: string;
  title: string;
  pages: number;
  cards: number;
  parse_quality: string;
  /** False 表示命中既有身份并合并来源 (不是新文献)。 */
  created: boolean;
}

/** `POST /api/library/scan` 的返回 (只扫描不导入)。 */
export interface LibraryScanReport {
  files: ScannedPathRow[];
  denied: ScannedPathRow[];
  roots: string[];
  truncated: boolean;
  /** 逐条输入路径的处置说明 (输入不存在 / 通配无命中)。 */
  notes: string[];
  counts: Record<string, number>;
  total_bytes: number;
}

/** `POST /api/library/import` 的返回 (逐条结果, 部分失败不得当成整体成功)。 */
export interface LibraryImportReport {
  topic: string;
  label: string;
  batch_id: string;
  origin: string;
  roots: string[];
  imported: ImportedPathRow[];
  merged: ImportedPathRow[];
  skipped: ImportedPathRow[];
  denied: ImportedPathRow[];
  failed: ImportedPathRow[];
  duplicates: ImportedPathRow[];
  truncated: boolean;
  notes: string[];
  counts: Record<string, number>;
  /** 整体是否"完全成功": 有任何拒绝/失败/截断即为 false。 */
  ok: boolean;
  /**
   * 本次是否为**幂等缓存命中**（目录内容与上次导入完全一致，未重新导入）。
   * 服务端按「客户端键#内容签名」查缓存；签名含每个候选的 path+size+mtime。
   */
  cached?: boolean;
  /** 服务端实际使用的缓存键（客户端键#内容签名）。 */
  cache_key?: string;
  /** 同名库已存在时，本次会把新文献并入其中。 */
  library_existed?: boolean;
  /** 当前对话绑定成功后的会话 ID；未选历史会话时为空。 */
  bound_session_id?: string;
  binding_error?: string;
}

/** `GET /api/library/{source_set_id}` 的返回 (`library_summary`)。 */
export interface LibraryDetail {
  source_set_id: string;
  origin: string;
  readable: boolean;
  note?: string;
  /** 登记文件所在目录 (后端给的是文件名, 这里只用于分组显示)。 */
  roots: string[];
  documents: number;
  cards: number;
  files: number;
  /** 原件已被移动/删除的文件。 */
  stale_files: string[];
  hashes: Array<{ file: string; file_hash: string; doc_id: string; stale: boolean }>;
  source_set?: Record<string, unknown>;
}

/** `DELETE /api/library/{source_set_id}` 的返回。 */
export interface LibraryDeleteResult {
  ok: boolean;
  reason: string;
  removed_refs: number;
  removed_records: boolean;
  note: string;
}

/** 一次扫描/导入的请求体 (与后端 `ScanRequestModel` 同名同义)。 */
export interface LibraryPathRequest {
  paths: string[];
  recursive: boolean;
  label: string;
  max_files: number;
  max_bytes: number;
  /**
   * 授权根只能由服务端签发：本机文件夹选择窗口给出 `folder_grants` 令牌，
   * 或服务端配置 `DATA_READ_ROOTS`。页面**不发送** `allow_roots`
   * （后端保留该字段正是为了对非空值返回 400，避免"静默忽略"）。
   */
  folder_grants: string[];
}

/** 导入请求 = 扫描请求 + 库名与幂等键。 */
export interface LibraryImportRequest extends LibraryPathRequest {
  topic: string;
  embed: boolean;
  idempotency_key: string;
  session_id?: string;
}

/** 用户在多行文本框里逐行给出的路径 (兼容 Windows 换行与中英文分号)。 */
export function parsePaths(text: string): string[] {
  return String(text || '')
    .split(/[\r\n;；]+/)
    .map((line) => line.trim().replace(/^["']|["']$/g, '').trim())
    .filter(Boolean);
}

/** 幂等键: 同一次提交重复点击不产生二次解析/计费。 */
export function importKey(label: string, paths: string[]): string {
  const body = `${String(label || '').trim()}|${paths.join('\u0000')}`;
  let hash = 2166136261;
  for (let i = 0; i < body.length; i += 1) {
    hash ^= body.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `lib-${(hash >>> 0).toString(36)}-${paths.length}`;
}

/** 空请求 (视图与测试共用的默认值)。 */
export function emptyPathRequest(): LibraryPathRequest {
  return {
    paths: [], recursive: true, label: '', max_files: 0, max_bytes: 0,
    folder_grants: [],
  };
}
