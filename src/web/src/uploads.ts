/**
 * 附件上传的前端契约与纯函数 (P2)。校验/格式化集中在这里, 便于单测;
 * 页面只负责取文件、调用接口与渲染。允许类型与大小上限必须与服务端一致:
 * `src/utils/uploads.py` 的 ALLOWED_SUFFIXES / MAX_BYTES。
 */

export type UploadKind = 'problem' | 'literature';

export const ALLOWED_SUFFIXES = ['.pdf', '.txt', '.md', '.markdown', '.docx'];
export const ACCEPT_ATTR = ALLOWED_SUFFIXES.join(',');
export const MAX_BYTES = 50 * 1024 * 1024;

export interface UploadCandidate {
  name: string;
  size: number;
}

export interface AttachmentView {
  attachment_id: string;
  kind: UploadKind;
  filename: string;
  size: number;
  sha256: string;
  parse_quality?: string;
  chars?: number;
  topic?: string;
  doc_id?: string;
}

export function suffixOf(name: string): string {
  const dot = (name || '').lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot).toLowerCase();
}

/** 返回错误说明; 空串表示通过。文学用途必须给出资料库主题。 */
export function validateFiles(files: UploadCandidate[], kind: UploadKind,
                             topic = ''): string {
  if (!files.length) return '请选择文件';
  if (kind === 'literature' && !String(topic || '').trim()) {
    return '补充文献需要先填写资料库主题 (或用「精确主题」字段)';
  }
  for (const file of files) {
    const suffix = suffixOf(file.name);
    if (!ALLOWED_SUFFIXES.includes(suffix)) {
      return `不支持的文件类型 ${suffix || '(无扩展名)'}: ${file.name}（仅支持 ${ALLOWED_SUFFIXES.join(' / ')}）`;
    }
    if (!file.size) return `空文件: ${file.name}`;
    if (file.size > MAX_BYTES) {
      return `文件过大: ${file.name}（${formatSize(file.size)}，上限 ${formatSize(MAX_BYTES)}）`;
    }
  }
  return '';
}

export function formatSize(bytes: number): string {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function shortHash(sha: string): string {
  return String(sha || '').slice(0, 12);
}

export function kindLabel(kind: UploadKind | string): string {
  return kind === 'literature' ? '补充文献' : '补充问题说明';
}

/** 解析状态的可读说明 (解析失败必须显示出来, 不能看起来"已成功")。 */
export function parseLabel(item: AttachmentView): string {
  const quality = String(item.parse_quality || '');
  if (!quality) return item.kind === 'literature' ? '已入库' : '已登记';
  if (quality === 'ok') return `解析正常（${item.chars || 0} 字）`;
  if (quality === 'short') return `内容偏短（${item.chars || 0} 字）`;
  return `解析失败（原件已保留: ${quality}）`;
}

/** 上传接口返回的逐文件结果 → 一行可读摘要。 */
export function summarizeResults(body: any): string {
  const results: any[] = (body && body.results) || [];
  if (!results.length) return '没有文件被处理';
  const parts = results.map((item: any) => {
    const name = item.filename || '未命名';
    if (!item.ok) return `${name}: 失败（${item.error || '未知原因'}）`;
    if (item.deduplicated) return `${name}: 已存在，跳过重复入库`;
    const doc = item.document || {};
    if (doc.doc_id) return `${name}: 已入库（${doc.doc_id}）`;
    const quality = String((item.attachment || {}).parse_quality || '');
    return `${name}: 已登记（${quality === 'ok' ? '解析正常' : quality || '待解析'}）`;
  });
  const failed = Number(body && body.failed) || 0;
  return parts.join('；') + (failed ? `（${failed} 个失败）` : '');
}

/** 本次研究要并入问题陈述的附件 id (仅"补充问题说明"用途)。 */
export function problemAttachmentIds(items: AttachmentView[]): string[] {
  return items.filter((i) => i.kind === 'problem').map((i) => i.attachment_id);
}
