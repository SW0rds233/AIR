/**
 * 会话事件消费的用例 (合并计划 §9.3 / §9.4)。
 *
 * 固定的是**事件契约与重连语义**:
 * - 会话级游标 (不是全局游标);
 * - 按 seq 去重, 缺口要求重新加载投影;
 * - 未知事件类型不得被静默忽略 (否则页面停在"完成");
 * - 终止/切换会话后取消重连; 退避有上限。
 */
import { describe, expect, it } from 'vitest';

import {
  applyEvent,
  nextReconnectDelay,
  PROTOCOL_VERSION,
  refreshTargets,
  shouldReconnect,
  subscribeFrom,
  type TeamEvent,
} from '../src/events/session-events';
import { emptyStore, reduce, type ResearchStore } from '../src/state/research-store';

function event(over: Partial<TeamEvent> = {}): TeamEvent {
  return {
    protocol: PROTOCOL_VERSION, seq: 1, sessionId: 's-1', runId: 'r-1',
    type: 'task_started', taskId: 't-1', agent: 'evidence',
    payload: { objective: '检索' }, ...over,
  };
}

function storeWithRun(): ResearchStore {
  return reduce(emptyStore(), {
    type: 'selection/open', selection: { runId: 'r-1', sessionId: 's-1' },
  });
}

describe('applyEvent', () => {
  /** 事件游标是会话级的: 测试里先对齐起点, 避免把"首条事件"误判成缺口。 */
  function atCursor(seq: number): ResearchStore {
    return reduce(storeWithRun(), {
      type: 'transport/cursor', sessionId: 's-1', seq,
    });
  }

  it('正常事件被应用并推进会话游标', () => {
    const result = applyEvent(storeWithRun(), event());
    expect(result.kind).toBe('apply');
    expect(result.store.transport.cursorBySession['s-1']).toBe(1);
    if (result.kind === 'apply') {
      expect(result.store.entities.tasksByRun['r-1']['t-1'].status).toBe('running');
      expect(result.refresh).toContain('team');
    }
  });

  it('重复序号被丢弃 (重连重放不重复应用)', () => {
    const first = applyEvent(storeWithRun(), event());
    const second = applyEvent(first.store, event());
    expect(second.kind).toBe('duplicate');
    expect(second.store).toBe(first.store);
  });

  it('序号缺口触发重新同步, 而不是当作已完成', () => {
    const first = applyEvent(storeWithRun(), event({ seq: 1 }));
    const gap = applyEvent(first.store, event({ seq: 5 }));
    expect(gap.kind).toBe('resync');
    expect(gap.store.transport.needsResync).toBe(true);
    expect(gap.reason).toContain('缺口');
  });

  it('未知事件类型触发重新同步 (不能静默让页面变成完成)', () => {
    const result = applyEvent(storeWithRun(), event({ type: 'brand_new_event' }));
    expect(result.kind).toBe('resync');
    expect(result.store.transport.needsResync).toBe(true);
    expect(result.reason).toContain('未知事件');
  });

  it('缺少有效序号时要求重新同步', () => {
    const result = applyEvent(storeWithRun(), event({ seq: 0 }));
    expect(result.kind).toBe('resync');
  });

  it('协议版本不兼容时明确报错并要求重新加载', () => {
    const result = applyEvent(storeWithRun(), event({ protocol: PROTOCOL_VERSION + 1 }));
    expect(result.kind).toBe('incompatible');
    expect(result.reason).toContain('协议版本');
  });

  it('辅助事件 (工具进度) 不触发整页刷新', () => {
    const result = applyEvent(atCursor(1), event({ type: 'tool_run', seq: 2 }));
    expect(result.kind).toBe('apply');
    if (result.kind === 'apply') expect(result.refresh).toEqual([]);
  });

  it('run_finished 之后不再重连', () => {
    const result = applyEvent(atCursor(2), event({
      type: 'run_finished', seq: 3, payload: { stop_reason: '交付形态齐备' } }));
    expect(result.kind).toBe('apply');
    expect(result.store.transport.terminated).toBe(true);
    expect(shouldReconnect(result.store).retry).toBe(false);
  });

  it('决策事件进入决策序列, 事件里缺字段也不崩', () => {
    const result = applyEvent(atCursor(3), event({
      type: 'supervisor_decision', seq: 4,
      payload: { round: 1, decision: 'dispatch', reason: '派 3 个子任务' } }));
    expect(result.kind).toBe('apply');
    expect(result.store.entities.decisionsByRun['r-1'][0].decision).toBe('dispatch');
  });

  it('任务事件保留失败原因 (受阻必须能看到原因)', () => {
    const result = applyEvent(atCursor(1), event({
      type: 'task_blocked', seq: 2,
      payload: { status: 'waiting', failure_reason: '资料范围不可用' } }));
    const row = result.store.entities.tasksByRun['r-1']['t-1'];
    expect(row.status).toBe('waiting');
    expect(row.failureReason).toBe('资料范围不可用');
  });

  it('审阅问题事件写入问题账本', () => {
    const result = applyEvent(atCursor(1), event({
      type: 'review_issue', seq: 2,
      payload: { issue_id: 'i1', severity: 'blocking', category: 'science',
                 summary: '结论无依据', blocking: true } }));
    expect(result.store.entities.issues['i1'].blocking).toBe(true);
  });

  it('对象变更事件更新计数', () => {
    const first = applyEvent(atCursor(1), event({
      type: 'object_changed', seq: 2, payload: { kind: 'evidence' } }));
    const second = applyEvent(first.store, event({
      type: 'object_changed', seq: 3, payload: { kind: 'evidence' } }));
    expect(second.store.entities.objectCountsByRun['r-1'].evidence).toBe(2);
  });

  it('按事件类型只刷新受影响的面板', () => {
    expect(refreshTargets('manuscript_ready')).toEqual(['paper']);
    expect(refreshTargets('review_issue')).toContain('review');
    expect(refreshTargets('task_started')).toEqual(['team', 'timeline']);
    expect(refreshTargets('unknown_type')).toEqual([]);
  });
});

describe('重连策略', () => {
  it('退避随尝试次数增长, 且有上限', () => {
    const first = nextReconnectDelay(0, { random: () => 0.5 });
    const third = nextReconnectDelay(2, { random: () => 0.5 });
    expect(first.retry).toBe(true);
    expect(third.delayMs).toBeGreaterThan(first.delayMs);
    expect(third.delayMs).toBeLessThanOrEqual(30000);
  });

  it('达到上限后不再重连', () => {
    const decision = nextReconnectDelay(8, { maxAttempts: 8 });
    expect(decision.retry).toBe(false);
    expect(decision.reason).toContain('上限');
  });

  it('抖动让延迟落在区间内 (多标签不同时重连)', () => {
    const low = nextReconnectDelay(1, { baseMs: 1000, jitter: 0.25, random: () => 0 });
    const high = nextReconnectDelay(1, { baseMs: 1000, jitter: 0.25, random: () => 1 });
    expect(low.delayMs).toBeLessThan(high.delayMs);
  });

  it('切换会话后不再为旧会话重连', () => {
    const store = storeWithRun();
    expect(shouldReconnect(store, { sessionChanged: true }).retry).toBe(false);
  });

  it('离线不等于后台已停止: 仍允许重连', () => {
    const store = reduce(storeWithRun(), { type: 'transport/status', status: 'offline' });
    expect(store.transport.terminated).toBe(false);
    expect(shouldReconnect(store).retry).toBe(true);
  });
});

describe('订阅起点', () => {
  it('用投影返回的 eventSeq 作为起点, 不沿用别的会话的游标', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-old', seq: 99 });
    // 新会话没有游标 -> 从 0 开始; 服务端按投影的 eventSeq 重放
    expect(subscribeFrom(store, 's-new', null)).toBe(0);
    expect(subscribeFrom(store, 's-new', { eventSeq: 42 })).toBe(42);
    // 有本会话游标时用它
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-new', seq: 7 });
    expect(subscribeFrom(store, 's-new', null)).toBe(7);
  });
});
