/** 会话历史和消息列表的纯视图逻辑。 */
import { render as renderMarkdown } from '../markdown';

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
  stopped: 'msg-agent', llm: 'msg-llm', tool: 'msg-tool',
  system: 'msg-system',
};

function esc(raw: unknown): string {
  return String(raw === null || raw === undefined ? '' : raw)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** 实时与历史共用同一渲染，外部文本只经安全 Markdown 渲染器进入 DOM。 */
export function messageElement(message: ConversationMessage): HTMLElement {
  const cls = messageClass(message);
  const labels: Record<string, string> = {
    user: '你', node: '研究进展', llm: '模型活动', tool: '工具记录',
    done: '研究结果', error: '需要关注', system: '系统提示', stopped: '已停止',
  };
  const row = document.createElement('article');
  row.className = 'msg ' + cls;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = messageAvatar(message);
  avatar.setAttribute('aria-hidden', 'true');
  const body = document.createElement('div');
  body.className = 'message-body';
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = labels[message.role || ''] || 'AIR 助手';
  const text = String(message.text || '');
  const bubble = renderMarkdown(text, { className: 'bubble message-content' });
  body.append(label);
  if (['tool', 'llm', 'node'].includes(message.role || '') && text.length > 280) {
    const details = document.createElement('details');
    details.className = 'message-details';
    const summary = document.createElement('summary');
    summary.textContent = text.split('\n')[0].slice(0, 100) + ' · 展开详情';
    details.append(summary, bubble);
    body.append(details);
  } else {
    body.append(bubble);
  }
  row.append(avatar, body);
  return row;
}

/** 用户回看历史时保留滚动位置，并提供跳回最新消息的入口。 */
export function appendMessage(log: HTMLElement, message: ConversationMessage): void {
  const follow = message.role === 'user' || log.scrollHeight - log.clientHeight - log.scrollTop < 80;
  let jump = log.nextElementSibling as HTMLButtonElement | null;
  if (!jump?.classList.contains('conversation-jump')) {
    jump = document.createElement('button');
    jump.className = 'conversation-jump';
    jump.type = 'button';
    jump.hidden = true;
    jump.textContent = '有新消息 · 回到最新';
    log.insertAdjacentElement('afterend', jump);
    const button = jump;
    button.addEventListener('click', () => {
      log.scrollTop = log.scrollHeight;
      button.hidden = true;
    });
    log.addEventListener('scroll', () => {
      if (log.scrollHeight - log.clientHeight - log.scrollTop < 80) button.hidden = true;
    });
  }
  log.append(messageElement(message));
  if (follow) log.scrollTop = log.scrollHeight;
  jump.hidden = follow;
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
  if (role === 'llm') return '思';
  if (role === 'tool') return '工';
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
  // 引擎标识只做**显示**: 有就如实标出 (服务端返回什么就显示什么), 不据它分支行为。
  const engine = String(((summary.request || {}) as { mode?: unknown }).mode || '').trim();
  const modeTag = engine
    ? '<span class="tag t-neutral">' + esc(engine) + '</span> ' : '';
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
