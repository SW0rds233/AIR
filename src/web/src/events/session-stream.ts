/** 消费 SSE 会话事件，并在断线后恢复状态。 */

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
  /** 游标只存在于统一 research store；键为 SSE 端点的 thread_id。 */
  getCursor(threadId: string): number;
  recordCursor(threadId: string, seq: number): void;
  onGap?(threadId: string, previous: number, incoming: number): void;
  /** 断线补偿: 服务端会话状态 → 页面状态。 */
  onCompensate(status: string): void;
  /** 断线且等待输入时的提示/状态切换。 */
  onOfflineStatus(runStatus: string): void;
  /** 终止状态 (done) 的提示。 */
  onFinished(): void;
  /** 重连次数上限 (默认 6)。 */
  maxReconnectAttempts?: number;
  /** HTTP 出口: 断线补偿的状态查询也走 api/ 层, 不在事件层拼 URL。 */
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
    const cursor = deps.getCursor(tid) || 0;
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
      // EventSource.close() 后仍可能有一个已排队的旧会话回调；不得更新当前页。
      if (es !== source || current !== tid || deps.currentThreadId() !== tid) return;
      try {
        const payload = JSON.parse(e.data);
        const previous = deps.getCursor(tid) || 0;
        const next = Number.parseInt(e.lastEventId || '', 10) || 0;
        if (next && next <= previous) return; // 重连回放的重复事件
        if (previous && next > previous + 1) deps.onGap?.(tid, previous, next);
        payload._event_seq = next;
        payload._session_id = tid;
        deps.handleEvent(payload);
        if (next) deps.recordCursor(tid, next);
      } catch (err) {
        deps.onTransport?.('reconnecting', '事件无法解析，等待重连补偿');
      }
    };
    // F2: 断线不能静默 —— 先查会话状态做补偿, 再重连并回放缺失事件。
    // 补偿查询也走统一客户端: 但**不重试**(这里本身就是重连路径, 叠加重试
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
    lastEventId: (threadId: string) => deps.getCursor(String(threadId || '')) || 0,
    isOpen: () => Boolean(es),
  };
}
