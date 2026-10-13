/** 研究输入表单的纯视图逻辑。 */

import { escapeHtml } from './research-workbench';
import {
  formatSize, kindLabel, parseLabel, problemAttachmentIds, shortHash,
  type AttachmentView,
} from '../uploads';

/** 附件登记列表 HTML (空态显示"还没有附件")。 */
export function attachmentListHtml(items: AttachmentView[]): string {
  const rows = items.map((item) => (
    '<div class="attach-item">' +
    '<span class="mono">' + escapeHtml(item.filename) + '</span> · ' +
    escapeHtml(kindLabel(item.kind)) + ' · ' + escapeHtml(formatSize(item.size)) +
    ' · <span class="mono">' + escapeHtml(shortHash(item.sha256)) + '</span> · ' +
    escapeHtml(parseLabel(item)) +
    ' <button class="ghost" data-action="deleteAttachment" data-id="' +
    escapeHtml(item.attachment_id) + '">移除</button></div>'
  ));
  return rows.join('') || '<span class="wb-note">还没有附件</span>';
}

/**
 * 本次研究绑定的附件说明。
 *
 * 必须如实说明"哪几份会并入问题陈述", 而不是让用户以为所有附件都会进问题;
 * 没有附件时清空该区域 (与迁移前一致)。
 */
export function attachmentBindingHtml(items: AttachmentView[]): string {
  if (!items.length) return '';
  const problemIds = problemAttachmentIds(items);
  return '本次研究绑定的附件: ' + escapeHtml(items.map(
    (i) => `${i.filename}（${kindLabel(i.kind)}）`).join('、')) +
    (problemIds.length ? '；' + problemIds.length + ' 份问题说明将并入问题陈述' : '');
}
