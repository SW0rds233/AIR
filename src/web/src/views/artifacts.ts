/*!
 * 交付物（outputs/）文件清单的视图契约 (计划书 §2 F3 / §4 `views/*`)。
 *
 * 为什么单独一个模块: 文件清单的归属显示曾散在页面逻辑里, 于是"未归属产物"
 * 与"这次运行的交付物"在界面上看起来一样。这里把**纯函数**抽出来 (类型 + 单测),
 * 页面只负责取数据与挂事件。
 *
 * 硬约束: 不发起请求、不读写全局状态; 所有外部文本经 `escapeHtml`。
 */

export interface ArtifactEntry {
  name: string;
  size?: number;
  mtime?: number;
  /** 归属: 由服务端按 manifest/交付包计算, 页面不得自行推断 */
  project_id?: string;
  problem_id?: string;
  run_id?: string;
  delivery_level?: string;
  package_dir?: string;
  unattributed?: boolean;
}

export interface ArtifactPayload {
  files?: ArtifactEntry[];
  roots?: Record<string, unknown>;
}

export interface ArtifactBinding {
  projectId?: string;
  problemId?: string;
  runId?: string;
}

function esc(raw: unknown): string {
  return String(raw === null || raw === undefined ? '' : raw)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 路径最后一段 (兼容 Windows 反斜杠)。 */
export function basename(path: unknown): string {
  const norm = String(path === null || path === undefined ? '' : path).replace(/\\/g, '/');
  return norm.split('/').pop() || '';
}

/** 把后端返回的绝对路径转成 outputs/ 下的相对名 (用于请求 /api/artifacts/<name>)。 */
export function artifactNameFromPath(path: unknown): string {
  const norm = String(path === null || path === undefined ? '' : path).replace(/\\/g, '/');
  const marker = '/outputs/';
  const idx = norm.lastIndexOf(marker);
  if (idx >= 0) return norm.slice(idx + marker.length);
  return basename(norm);
}

/** 人可读体积; 未知大小显示 `-` 而不是 0 B。 */
export function formatSize(size: unknown): string {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  if (bytes > 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  if (bytes > 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

/**
 * 文件清单的绑定说明 (F3): 明确"当前显示的是谁的交付物"。
 *
 * 未绑定问题时必须说清这是 outputs/ 全部文件（含未归属产物）,
 * 不能让用户以为看到的就是当前研究问题的产出。
 */
export function bindingScope(payload: ArtifactPayload | null,
                              binding: ArtifactBinding = {}): string {
  const roots = Object.values((payload || {}).roots || {});
  const scope = [
    binding.projectId && ('项目 ' + binding.projectId),
    binding.problemId && ('问题 ' + binding.problemId),
    binding.runId && ('运行 ' + binding.runId),
  ].filter(Boolean).join(' · ');
  if (!scope) return '未绑定研究问题：显示 outputs/ 全部文件（含未归属产物）';
  return '仅显示 ' + scope + ' 的交付物 (匹配交付包 ' + roots.length + ' 个)';
}

/** 文件条目的右侧说明: 未归属 / 交付级别 (不得把未归属文件显示成当前问题的产物)。 */
export function artifactNote(entry: ArtifactEntry): string {
  if (entry.unattributed) return ' · 未归属';
  if (entry.delivery_level) return ' · ' + esc(entry.delivery_level);
  return '';
}

/** 文件清单里一行的 HTML (转义 + 目录前缀)。 */
export function artifactRowHtml(entry: ArtifactEntry): string {
  const parts = String(entry.name || '').split('/');
  const base = parts[parts.length - 1];
  const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
  return '<span class="fname">' + esc(dir ? dir + '/' + base : base) + '</span>' +
    '<span class="fsize">' + esc(formatSize(entry.size)) + artifactNote(entry) + '</span>';
}

/** 该行是否应标记为"未归属"。 */
export function artifactClass(entry: ArtifactEntry): string {
  return 'file-item' + (entry.unattributed ? ' unattributed' : '');
}

/** 文件清单查询串 (只带真实存在的身份; 不猜 run)。 */
export function artifactQuery(binding: ArtifactBinding): string {
  const params = new URLSearchParams();
  if (binding.projectId) params.set('project_id', binding.projectId);
  if (binding.problemId) params.set('problem_id', binding.problemId);
  if (binding.runId) params.set('run_id', binding.runId);
  return params.toString();
}

export const ArtifactsView = {
  basename,
  artifactNameFromPath,
  formatSize,
  bindingScope,
  artifactNote,
  artifactRowHtml,
  artifactClass,
  artifactQuery,
};

export default ArtifactsView;
