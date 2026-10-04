/*!
 * 研究输入与附件视图 (合并计划 §9.5 `views/research-input.ts`)。
 *
 * 这里只放**纯渲染**: 附件登记行、附件/资料权限绑定说明、上传按钮状态所需的文案。
 * 取文件、发请求、写 DOM 都在 `features/intake/controller.ts` (流程状态) 与 `app.ts`
 * (装配) 里, 于是"界面显示了什么"可以单测, 不会与请求逻辑互相掩盖。
 *
 * 硬约束不变: 动态按钮仍然写 `data-action` (CSP 下内联处理器不执行),
 * 附件 id 经转义后进入 `data-id`。
 */

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
