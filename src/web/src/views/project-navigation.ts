/*!
 * 项目 / 问题 / run 导航与历史回看视图 (合并计划 §9.5: `renderHistory*` 迁出)。
 *
 * 迁移前的判定散在 `app.ts` 里各写一遍: "可继续"的判断、最后一条 interrupt 要不要
 * 跳过、模式标签、状态标签。两处写法一旦分叉, 就会出现"列表说能继续、点进去却是
 * 空的"这类不一致。这里把判定与条目 HTML 集中成**纯函数**:
 *
 * - 只消费 `rec` / `items`, 不发起请求、不读写全局状态、不碰 DOM (由页面挂载);
 * - 外部文本一律转义 (与迁移前 `textContent` 的显示结果一致);
 * - 已有 `views/conversation.ts` 的导出**不动** (它有单独的 19 个单测,
 *   `historyItemHtml` 仍由这里继续使用)。
 */

import {
  fmtTime,
  historyItemHtml,
  messageAvatar,
  messageClass,
  statusLabel,
  type ConversationMessage,
  type ConversationSummary,
} from './conversation';

function esc(raw: unknown): string {
  return String(raw === null || raw === undefined ? '' : raw)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 历史回看里的一条消息: 要么是暂停点卡片, 要么是普通气泡。 */
export type HistoryLogEntry =
  | { kind: 'interrupt'; html: string }
  | { kind: 'message'; html: string };

/** 历史会话下拉框里的一个 `<option>` 文案。 */
export function historyOption(c: ConversationSummary): { value: string; label: string } {
  const sessionId = String((c && c.session_id) || '');
  return {
    value: sessionId,
    label: String((c && c.topic) || sessionId) + ' · ' +
      fmtTime((c && (c.updated_at || c.created_at)) || '') + ' · ' +
      statusLabel(c && c.status),
  };
}

/**
 * 历史列表容器里的一条 HTML。
 *
 * 额外写 `data-sid`: 页面据此把点击/继续事件挂到**这一条**上, 不再依赖"按顺序
 * querySelector"的脆弱对应 (顺序一旦与数据不一致就会把点击挂到别的会话)。
 */
export function historyListItemHtml(c: ConversationSummary): string {
  return '<div class="history-item" data-sid="' + esc((c && c.session_id) || '') + '">' +
    historyItemHtml(c) + '</div>';
}

/** 历史列表容器整体 HTML。 */
export function historyListHtml(items: ConversationSummary[]): string {
  if (!items.length) return '<div class="empty">暂无历史会话</div>';
  return items.map((c) => historyListItemHtml(c)).join('');
}

/** 回看抽屉的标题 (无主题时回退到固定文案)。 */
export function historyDetailTitle(rec: any): string {
  return String((rec && rec.topic) || '会话回看');
}

/**
 * 会话消息 → 条目 HTML 列表。
 *
 * 关键判定 (与迁移前 `renderHistoryToLog` 完全一致): 只有**未收尾**(非 done)的会话
 * 才跳过最后一条 `interrupt` —— 那条暂停点正是"继续"要恢复的对象, 先渲染出来会与
 * 随后恢复的暂停点卡片重复。
 */
export function conversationEntries(rec: any, skipLastInterrupt = false): HistoryLogEntry[] {
  const msgs: ConversationMessage[] = (rec && rec.messages) || [];
  const resumable = String((rec && rec.status) || '') !== 'done';
  const skipLast = skipLastInterrupt && resumable && msgs.length > 0
    && msgs[msgs.length - 1].role === 'interrupt';
  const toRender = skipLast ? msgs.slice(0, -1) : msgs;
  return toRender.map(toLogEntry);
}

function toLogEntry(m: ConversationMessage): HistoryLogEntry {
  if (m.role === 'interrupt') {
    return {
      kind: 'interrupt',
      html: '<div class="interrupt-card"><div class="it-title">' + esc(m.title || '') + '</div>' +
        '<div class="it-hint">' + esc(m.hint || '') + '</div></div>',
    };
  }
  return {
    kind: 'message',
    html: '<div class="msg ' + esc(messageClass(m)) + '">' +
      '<div class="avatar">' + esc(messageAvatar(m)) + '</div>' +
      '<div class="bubble">' + esc(m.text || '') + '</div></div>',
  };
}

/** 综述上下文的阶段标签 (无阶段时不显示成"已完成")。 */
export function stageLabel(stages: unknown): string {
  const list = Array.isArray(stages) ? stages.map((s) => String(s)) : [];
  if (!list.length) return '无';
  if (list.includes('write')) return '已撰写';
  if (list.includes('research')) return '已检索';
  return list.join('+');
}
