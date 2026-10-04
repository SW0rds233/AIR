/**
 * 会话事件流 (SSE) 的独立消费 (合并计划 §9.5: `app.ts::connectSSE/handleEvent/lastEventId`
 * 迁到 `events/` 之上)。
 *
 * 与 `events/session-events.ts` 的分工
 * --------------------------------
 * - `session-events.ts` 是**协议层**: 事件类型、解析、去重、缺口与退避策略 (已有单测);
 * - 本模块是**连接层**: EventSource 的建立/关闭、按会话游标续订、断线补偿、把事件
 *   交给页面的 `handleEvent`, 并且**不直接操作 DOM** (页面出口经 `deps` 注入)。
 *
 * 迁移时保持的行为 (逐条对应, 不允许"顺手改"):
 * 1. 只有收到过事件(`last_event_id > 0`)才带 `?last_event_id=`, 否则 URL 与旧版一致;
 * 2. 游标是**每个会话一份** (旧版是全局 `lastEventId`, 切换会话会串号);
 * 3. 断线先查 `/state` 补偿, 再重连; 连接被显式关闭(silent)或换了线程就不再重连;
 * 4. 重连不再无限: 用 `nextReconnectDelay` 的上限退避 (用户可手动刷新继续)。
 */

import { nextReconnectDelay } from './session-events';
import {
  ENDPOINTS,
  client as defaultApi,
  type ResearchClient,
} from '../api/research-client';

export interface SessionStreamDeps {
  /** 把事件交给页面处理 (迁移期仍是 `app.ts::handleEvent`, 不在这里操作 DOM)。 */
  handleEvent(ev: any): void;
  /** 连接状态变化 (用于状态条/提示)。 */
  onTransport?(state: 'connecting' | 'online' | 'reconnecting' | 'closed', detail?: string): void;
  /** 当前线程 (断线补偿与重连只对它生效)。 */
  currentThreadId(): string;
  /** 断线补偿: 服务端会话状态 → 页面状态。 */
  onCompensate(status: string): void;
  /** 断线且等待输入时的提示/状态切换。 */
  onOfflineStatus(runStatus: string): void;
  /** 终止状态 (done) 的提示。 */
  onFinished(): void;
  /** 重连次数上限 (默认 6)。 */
  maxReconnectAttempts?: number;
  /** HTTP 出口 (§9.5): 断线补偿的状态查询也走 api/ 层, 不在事件层拼 URL。 */
  api?: ResearchClient;
}

export interface SessionStream {
  connect(threadId: string): void;
  close(): void;
  /** 按会话读取已收到的事件序号 (断线重连据此补齐)。 */
  lastEventId(threadId: string): number;
  /** 当前是否已建立连接 (供调试/验收使用)。 */
  isOpen(): boolean;
}

export function createSessionStream(deps: SessionStreamDeps): SessionStream {
  let es: EventSource | null = null;
  let current: string | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;
  /** 会话级游标 (旧实现是全局游标: 切换会话会串号, 见 §9.4)。 */
  const cursors: Record<string, number> = {};

  function clearTimer() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function close() {
    clearTimer();
    if (es) { es.close(); es = null; }
    current = null;
    attempts = 0;
  }

  function scheduleReconnect(threadId: string) {
    const decision = nextReconnectDelay(attempts, {
      maxAttempts: deps.maxReconnectAttempts ?? 6,
    });
    if (!decision.retry) {
      deps.onTransport?.('closed', decision.reason);
      return;
    }
    attempts += 1;
    deps.onTransport?.('reconnecting', decision.reason);
    clearTimer();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (current === threadId && deps.currentThreadId() === threadId) connect(threadId);
    }, decision.delayMs);
  }

  function connect(threadId: string) {
    const tid = String(threadId || '');
    if (!tid) return;
    clearTimer();
    if (es) { es.close(); es = null; }
    current = tid;
    const cursor = cursors[tid] || 0;
    // SSE 端点同样取自 api/ 层的端点表 (游标作为查询参数), 事件层不自己拼 URL。
    const endpoint = new URL(ENDPOINTS.sessionEvents(tid), 'http://local');
    if (cursor) endpoint.searchParams.set('last_event_id', String(cursor));
    const url = endpoint.pathname + endpoint.search;
    deps.onTransport?.('connecting');
    const source = new EventSource(url);
    es = source;
    source.onopen = () => {
      if (es !== source) return;
      attempts = 0;
      deps.onTransport?.('online');
    };
    source.onmessage = (e: any) => {
      if (e.lastEventId) {
        const next = parseInt(e.lastEventId) || 0;
        if (next) cursors[tid] = next;
      }
      try { deps.handleEvent(JSON.parse(e.data)); } catch (err) {}
    };
    // F2: 断线不能静默 —— 先查会话状态做补偿, 再重连并回放缺失事件。
    // 补偿查询也走统一客户端 (§9.5): 但**不重试**(这里本身就是重连路径, 叠加重试
    // 会让补偿请求成倍增长); 失败也继续按退避重连。
    source.onerror = () => {
      if (es !== source) return;
      source.close();
      es = null;
      deps.onTransport?.('reconnecting', '连接中断');
      const api = deps.api ?? defaultApi();
      api.json(ENDPOINTS.sessionState(tid), { signal: undefined })
        .then((st: any) => {
          if (st && st.status) {
            deps.onCompensate(String(st.status));
            if (st.status === 'waiting') deps.onOfflineStatus('waiting');
            else if (st.status === 'done') deps.onFinished();
          }
        }).catch(() => {}).finally(() => {
          if (current === tid && deps.currentThreadId() === tid) scheduleReconnect(tid);
        });
    };
  }

  return {
    connect,
    close,
    lastEventId: (threadId: string) => cursors[String(threadId || '')] || 0,
    isOpen: () => Boolean(es),
  };
}
