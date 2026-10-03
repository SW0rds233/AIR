/*!
 * 会话历史与消息视图契约 (计划书 §2 F1 / §4 `views/*`)。
 *
 * 历史列表与回看曾经直接拼 HTML 且没有类型: "可继续"的判断、状态标签、
 * 时间格式各写一遍, 容易出现"列表说可继续、点进去却没有线程"这类不一致。
 * 这里把判定与格式化集中成纯函数, 页面只负责取数据与挂事件。
 *
 * 硬约束: 不发起请求、不读写全局状态; 所有外部文本经 `escapeHtml`。
 */

export interface ConversationSummary {
  session_id: string;
  topic?: string;
  status?: string;
  request?: { mode?: string } | null;
  message_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationMessage {
  role?: string;
  text?: string;
  title?: string;
  hint?: string;
}

export type ConversationState = 'running' | 'waiting' | 'done' | 'stopped' | 'error';

const STATE_LABEL: Record<string, string> = {
  running: '运行中', waiting: '等待中', done: '已完成', stopped: '已停止', error: '出错',
};

const MESSAGE_CLASS: Record<string, string> = {
  user: 'msg-user', node: 'msg-node', done: 'msg-done', error: 'msg-error',
  stopped: 'msg-agent',
};

function esc(raw: unknown): string {
  return String(raw === null || raw === undefined ? '' : raw)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 会话状态的中文标签 (未知状态原样返回, 不伪装成"已完成")。 */
export function statusLabel(status: unknown): string {
  const key = String(status === null || status === undefined ? '' : status);
  return STATE_LABEL[key] || key || '未知';
}

/** 只有**未收尾**的会话才提供"继续": 已完成的会话没有可恢复的线程。 */
export function isResumable(status: unknown): boolean {
  return String(status === null || status === undefined ? '' : status) !== 'done';
}

/** 消息角色的样式类。 */
export function messageClass(message: ConversationMessage | null): string {
  const role = String((message || {}).role || '');
  return MESSAGE_CLASS[role] || 'msg-agent';
}

/** 消息气泡里的角色显示。 */
export function messageAvatar(message: ConversationMessage | null): string {
  const role = String((message || {}).role || '');
  if (role === 'user') return '我';
  if (role === 'node') return '·';
  return 'AI';
}

/** ISO 时间 → `MM-DD HH:MM`; 无法解析时截断显示, 不抛错。 */
export function fmtTime(iso: unknown): string {
  const text = String(iso === null || iso === undefined ? '' : iso);
  if (!text) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(text);
  return m ? (m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5]) : text.slice(0, 16);
}

/** 历史列表里的一个会话条目。 */
export function historyItemHtml(summary: ConversationSummary): string {
  const status = String(summary.status || 'unknown');
  const modeTag = ((summary.request || {}).mode === 'theory')
    ? '<span class="tag t-neutral">理论</span> ' : '';
  let actions = modeTag +
    '<span class="hist-status st-' + esc(status) + '">' + esc(statusLabel(status)) + '</span>';
  if (isResumable(status)) {
    actions += '<button class="hist-resume" data-sid="' + esc(summary.session_id) +
      '">继续</button>';
  }
  return '<div class="hist-main">' +
    '<div class="hist-topic">' + esc(summary.topic || summary.session_id) + '</div>' +
    '<div class="hist-meta">' + esc(fmtTime(summary.updated_at || summary.created_at)) +
    ' · ' + (summary.message_count || 0) + ' 条消息</div>' +
    '</div>' + actions;
}

/** 历史条目是否可以点击回看 (始终可以; 显式函数便于日后加权限/空态)。 */
export function conversationTitle(summary: ConversationSummary): string {
  return String(summary.topic || summary.session_id || '会话');
}

export const ConversationView = {
  statusLabel,
  isResumable,
  messageClass,
  messageAvatar,
  fmtTime,
  historyItemHtml,
  conversationTitle,
};

export default ConversationView;
