/**
 * 会话事件消费。
 *
 * 解决的问题 (逐条对应计划书表格)
 * -----------------------------
 * 1. `connectSSE()` 早期用**全局游标** (`lastEventId`): 切换会话后游标还指向上一个
 *    会话的位置, 于是要么丢事件, 要么把旧会话的事件补进新会话。这里改成
 *    **会话级游标** (`transport.cursorBySession`)。
 * 2. 断线后固定间隔重连且无上限: 这里用**有上限的退避 + 抖动**, 并在服务端已终止
 *    (§9.4: "终止状态/切换会话后取消重连") 时停止重连。
 * 3. 每个 node 事件都刷新整个工作台: 这里把事件分成**业务事件**与**辅助事件**,
 *    只有业务事件触发受影响面板的刷新 (§9.4: "避免每个 token/工具日志触发全工作台
 *    刷新")。
 * 4. 未知事件被静默忽略 -> 页面停在"完成": 这里把未知类型记成 `unknown` 并要求
 *    **重新加载投影**, 不允许静默跳过 (§9.4: "未知事件可记录并重新同步, 不能静默让
 *    页面变成'完成'")。
 *
 * 本模块是纯函数: 不创建 EventSource、不碰 DOM。连接本身由 controller 负责。
 */

import type { ResearchStore, TaskStatus } from '../state/research-store';
import { reduce, shouldResync } from '../state/research-store';

// ----------------------------------------------------------------------
// 事件契约 (§9.4)
// ----------------------------------------------------------------------
export type BusinessEventType =
  | 'task_dispatched' | 'task_started' | 'task_blocked' | 'task_completed'
  | 'object_changed' | 'manuscript_ready' | 'figure_ready'
  | 'review_issue' | 'waiting_user' | 'run_finished'
  | 'supervisor_decision' | 'brief_ready' | 'plan_ready'
  | 'change_proposed' | 'task_result' | 'task_finished';

/** 工具进度等只作辅助, 不触发整页刷新。 */
export const AUXILIARY_EVENT_TYPES: string[] = ['tool_run', 'llm_call', 'log', 'ping', 'heartbeat'];

export interface TeamEvent {
  /** 协议版本 (用于协议不兼容判断)。 */
  protocol?: number;
  eventId?: string;
  seq: number;
  sessionId: string;
  runId?: string;
  type: string;
  at?: string;
  taskId?: string;
  agent?: string;
  payload?: Record<string, unknown>;
}

export const PROTOCOL_VERSION = 1;

export type EventEffect =
  | { kind: 'apply'; store: ResearchStore; refresh: RefreshTarget[]; reason?: string }
  | { kind: 'duplicate'; store: ResearchStore; reason?: string }
  | { kind: 'resync'; store: ResearchStore; reason: string }
  | { kind: 'incompatible'; store: ResearchStore; reason: string };

/** 需要重新渲染的面板 (§9.4: 只重新渲染受影响面板)。 */
export type RefreshTarget = 'team' | 'timeline' | 'paper' | 'review' | 'sources' | 'overview';

export function refreshTargets(type: string): RefreshTarget[] {
  switch (type) {
    case 'task_dispatched':
    case 'task_started':
    case 'task_blocked':
    case 'task_completed':
    case 'task_result':
    case 'task_finished':
    case 'supervisor_decision':
    case 'plan_ready':
    case 'brief_ready':
      return ['team', 'timeline'];
    case 'manuscript_ready':
    case 'figure_ready':
      return ['paper'];
    case 'review_issue':
      return ['review', 'team'];
    case 'object_changed':
    case 'change_proposed':
      return ['team', 'overview', 'sources'];
    case 'waiting_user':
      return ['team'];
    case 'run_finished':
      return ['team', 'timeline', 'overview'];
    default:
      return [];
  }
}

// ----------------------------------------------------------------------
// 应用一个事件
// ----------------------------------------------------------------------
export function applyEvent(store: ResearchStore, event: TeamEvent): EventEffect {
  if (event.protocol !== undefined && event.protocol !== PROTOCOL_VERSION) {
    return {
      kind: 'incompatible', store,
      reason: `事件协议版本 ${event.protocol} 与前端 ${PROTOCOL_VERSION} 不兼容`,
    };
  }
  if (!Number.isFinite(event.seq) || event.seq <= 0) {
    return { kind: 'resync', store, reason: '事件缺少有效序号 (seq)' };
  }
  const known = isKnownEvent(event.type);
  if (!known) {
    // 未知类型**必须**触发重新同步, 不能静默跳过 (否则页面会停在"完成")
    const next = reduce(store, { type: 'transport/needsResync', needs: true });
    return { kind: 'resync', store: next, reason: `未知事件类型 ${event.type}` };
  }
  if (shouldResync(store, event.seq, event.sessionId)) {
    const next = reduce(store, { type: 'transport/needsResync', needs: true });
    return { kind: 'resync', store: next, reason: `事件序号出现缺口 (收到 ${event.seq})` };
  }
  const cursor = store.transport.cursorBySession[event.sessionId] ?? 0;
  if (event.seq <= cursor) {
    return { kind: 'duplicate', store };      // 重连重放导致的重复: 已应用过
  }

  let next = reduce(store, { type: 'transport/cursor', sessionId: event.sessionId, seq: event.seq });
  next = reduce(next, { type: 'transport/needsResync', needs: false });
  next = applyPayload(next, event);
  if (event.type === 'run_finished') {
    next = reduce(next, { type: 'transport/terminated', reason: '运行已结束' });
  }
  return { kind: 'apply', store: next, refresh: refreshTargets(event.type) };
}

function isKnownEvent(type: string): boolean {
  if (AUXILIARY_EVENT_TYPES.includes(type)) return true;
  return refreshTargets(type).length > 0 || type === 'brief_ready';
}

function applyPayload(store: ResearchStore, event: TeamEvent): ResearchStore {
  const runId = event.runId || store.selection.runId;
  const payload = event.payload ?? {};
  switch (event.type) {
    case 'supervisor_decision': {
      return reduce(store, {
        type: 'entities/decision', runId,
        decision: {
          round: Number(payload.round ?? 0),
          decision: String(payload.decision ?? ''),
          reason: String(payload.reason ?? ''),
          note: String(payload.note ?? ''),
          taskIds: Array.isArray(payload.tasks) ? (payload.tasks as string[]) : [],
        },
      });
    }
    case 'plan_ready': {
      let next = store;
      const rows = Array.isArray(payload.task_details) ? payload.task_details : [];
      for (const raw of rows as Array<Record<string, unknown>>) {
        const taskId = String(raw.task_id ?? raw.taskId ?? '');
        if (!taskId) continue;
        next = reduce(next, {
          type: 'entities/task', runId,
          task: {
            taskId,
            agent: String(raw.agent ?? ''),
            objective: String(raw.objective ?? ''),
            subquestion: String(raw.subquestion ?? ''),
            expectedGain: String(raw.expected_gain ?? ''),
            status: String(raw.status ?? 'queued'),
            outcome: '', summary: '', failureReason: '',
            dependsOn: Array.isArray(raw.depends_on) ? raw.depends_on as string[] : [],
            attempt: Number(raw.attempt ?? 1),
            needsHuman: Boolean(raw.needs_human ?? false),
            planVersion: Number(raw.plan_version ?? payload.version ?? 1),
          },
        });
      }
      return next;
    }
    case 'task_dispatched':
    case 'task_started':
    case 'task_blocked':
    case 'task_completed': {
      const taskId = String(event.taskId ?? payload.task_id ?? '');
      if (!taskId) return store;
      const existing = store.entities.tasksByRun[runId]?.[taskId];
      return reduce(store, {
        type: 'entities/task', runId,
        task: {
          taskId,
          agent: String(event.agent ?? payload.agent ?? existing?.agent ?? ''),
          objective: String(payload.objective ?? existing?.objective ?? ''),
          subquestion: String(payload.subquestion ?? existing?.subquestion ?? ''),
          expectedGain: String(payload.expected_gain ?? existing?.expectedGain ?? ''),
          status: String(payload.status ?? statusForType(event.type, existing?.status)),
          outcome: String(payload.outcome ?? existing?.outcome ?? ''),
          summary: String(payload.summary ?? existing?.summary ?? ''),
          failureReason: String(payload.failure_reason ?? existing?.failureReason ?? ''),
          dependsOn: (payload.depends_on as string[]) ?? existing?.dependsOn ?? [],
          attempt: Number(payload.attempt ?? existing?.attempt ?? 1),
          needsHuman: Boolean(payload.needs_human ?? existing?.needsHuman ?? false),
          planVersion: Number(payload.plan_version ?? existing?.planVersion ?? 1),
        },
      });
    }
    case 'task_result':
    case 'task_finished': {
      const taskId = String(event.taskId ?? payload.task_id ?? '');
      if (!taskId) return store;
      const existing = store.entities.tasksByRun[runId]?.[taskId];
      return reduce(store, {
        type: 'entities/task', runId,
        task: {
          taskId,
          agent: String(event.agent ?? payload.agent ?? existing?.agent ?? ''),
          objective: String(payload.objective ?? existing?.objective ?? ''),
          subquestion: String(payload.subquestion ?? existing?.subquestion ?? ''),
          expectedGain: String(payload.expected_gain ?? existing?.expectedGain ?? ''),
          status: String(payload.status ?? (payload.outcome === 'failed' ? 'failed'
            : payload.outcome === 'cancelled' ? 'cancelled'
            : payload.outcome === 'blocked' ? 'waiting'
            : payload.outcome === 'partial' ? 'partial' : 'completed')),
          outcome: String(payload.outcome ?? existing?.outcome ?? ''),
          summary: String(payload.summary ?? existing?.summary ?? ''),
          failureReason: String(payload.failure_reason ?? existing?.failureReason ?? ''),
          dependsOn: (payload.depends_on as string[]) ?? existing?.dependsOn ?? [],
          attempt: Number(payload.attempt ?? existing?.attempt ?? 1),
          needsHuman: Boolean(payload.needs_human ?? existing?.needsHuman ?? false),
          planVersion: Number(payload.plan_version ?? existing?.planVersion ?? 1),
        },
      });
    }
    case 'review_issue': {
      const issueId = String(payload.issue_id ?? '');
      if (!issueId) return store;
      return reduce(store, {
        type: 'entities/issue',
        issue: {
          issueId,
          severity: String(payload.severity ?? 'minor'),
          category: String(payload.category ?? ''),
          summary: String(payload.summary ?? ''),
          blocking: Boolean(payload.blocking ?? false),
        },
      });
    }
    case 'object_changed': {
      const counts = { ...(store.entities.objectCountsByRun[runId] ?? {}) };
      const kind = String(payload.kind ?? '');
      if (kind) counts[kind] = Number(payload.count ?? (counts[kind] ?? 0) + 1);
      return reduce(store, { type: 'entities/objectCounts', runId, counts });
    }
    case 'change_proposed': {
      const counts = { ...(store.entities.objectCountsByRun[runId] ?? {}) };
      const kind = String(payload.kind ?? '');
      if (kind && payload.committed !== false)
        counts[kind] = Number(counts[kind] ?? 0) + 1;
      return reduce(store, { type: 'entities/objectCounts', runId, counts });
    }
    case 'waiting_user':
      return reduce(store, { type: 'ui/notice', notice: String(payload.question ?? '需要你的确认') });
    case 'brief_ready': {
      const brief = payload.brief as Record<string, unknown> | undefined;
      const question = String(brief?.main_question ?? '');
      return reduce(store, { type: 'ui/notice',
        notice: question ? `主控已完成问题拆解：${question.slice(0, 300)}`
          : '主控已完成问题拆解，开始安排研究任务' });
    }
    case 'run_finished':
      return reduce(store, { type: 'ui/notice', notice: String(payload.stop_reason ?? '运行已结束') });
    default:
      return store;
  }
}

function statusForType(type: string, fallback?: string): string {
  const map: Record<string, TaskStatus> = {
    task_dispatched: 'queued',
    task_started: 'running',
    task_blocked: 'waiting',
    task_completed: 'completed',
  };
  return map[type] ?? fallback ?? 'running';
}

// ----------------------------------------------------------------------
// 重连策略 (§9.4: 有上限的退避与抖动)
// ----------------------------------------------------------------------
export interface BackoffOptions {
  baseMs?: number;
  maxMs?: number;
  maxAttempts?: number;
  /** 抖动比例 (0–1), 用于避免多标签同时重连。 */
  jitter?: number;
  random?: () => number;
}

export interface BackoffDecision {
  retry: boolean;
  delayMs: number;
  reason: string;
}

export function nextReconnectDelay(attempt: number, options: BackoffOptions = {}): BackoffDecision {
  const base = options.baseMs ?? 1000;
  const max = options.maxMs ?? 30000;
  const maxAttempts = options.maxAttempts ?? 8;
  const jitter = options.jitter ?? 0.25;
  const random = options.random ?? Math.random;
  if (attempt >= maxAttempts) {
    return { retry: false, delayMs: 0, reason: `重连次数已达上限 (${maxAttempts})` };
  }
  const raw = Math.min(max, base * 2 ** attempt);
  const spread = raw * jitter;
  const delayMs = Math.max(0, Math.round(raw - spread + random() * spread * 2));
  return { retry: true, delayMs, reason: `第 ${attempt + 1} 次重连` };
}

/** 是否还应继续重连 (§9.4: 终止状态/切换会话后取消重连)。 */
export function shouldReconnect(store: ResearchStore,
                                options: { sessionChanged?: boolean } = {}): BackoffDecision {
  if (store.transport.terminated) {
    return { retry: false, delayMs: 0, reason: '运行已终止' };
  }
  if (options.sessionChanged) {
    return { retry: false, delayMs: 0, reason: '已切换会话' };
  }
  return nextReconnectDelay(store.transport.reconnectAttempts);
}

// ----------------------------------------------------------------------
// 投影与事件的衔接 (§9.4: 先读投影, 再订阅其后的事件)
// ----------------------------------------------------------------------
export interface SnapshotCursor {
  revision?: number;
  eventSeq: number;
}

/**
 * 决定订阅起点。
 *
 * 规则: 用投影返回的 `eventSeq` 作为起点 —— 服务端负责重放这之后的事件。
 * **不能**沿用上一个会话的游标 (那是全局游标的旧缺陷)。
 */
export function subscribeFrom(store: ResearchStore, sessionId: string,
                              snapshot: SnapshotCursor | null): number {
  if (snapshot && Number.isFinite(snapshot.eventSeq)) return snapshot.eventSeq;
  return store.transport.cursorBySession[sessionId] ?? 0;
}
