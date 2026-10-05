/**
 * 会话事件流连接层 (合并计划 §9.5 `events/session-stream.ts`) 的行为测试。
 *
 * 迁出 `app.ts::connectSSE` 时最容易退化的三点, 这里逐条钉住:
 * 1. 只有收到过事件才带 `?last_event_id=` (URL 与旧版一致);
 * 2. 游标是**按会话**保存的 (旧版全局游标会让切换会话串号);
 * 3. 断线先查 `/state` 做补偿, 再按有上限的退避重连; 显式关闭后不再重连。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSessionStream, type SessionStreamDeps } from '../src/events/session-stream';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close() { this.closed = true; }

  emit(data: any, lastEventId: string) {
    this.onmessage?.({data: JSON.stringify(data), lastEventId});
  }
}

interface Harness {
  stream: ReturnType<typeof createSessionStream>;
  events: any[];
  states: string[];
  statuses: string[];
  offline: string[];
  finished: number;
}

function harness(overrides: Partial<SessionStreamDeps> = {}): Harness {
  const events: any[] = [];
  const states: string[] = [];
  const statuses: string[] = [];
  const offline: string[] = [];
  const out = { finished: 0 };
  const cursors: Record<string, number> = {};
  const deps: SessionStreamDeps = {
    handleEvent: (ev) => { events.push(ev); },
    onTransport: (state) => { statuses.push(state); },
    currentThreadId: () => 't-1',
    getCursor: (threadId) => cursors[threadId] || 0,
    recordCursor: (threadId, seq) => { cursors[threadId] = seq; },
    onCompensate: (status) => { states.push(status); },
    onOfflineStatus: (status) => { offline.push(status); },
    onFinished: () => { out.finished += 1; },
    maxReconnectAttempts: 3,
    ...overrides,
  };
  return {
    stream: createSessionStream(deps),
    events, states, statuses, offline,
    get finished() { return out.finished; },
  };
}

afterEach(() => {
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('连接与游标', () => {
  it('首次连接不带 last_event_id, 事件被交给页面处理', () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    const h = harness();
    h.stream.connect('t-1');
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/sessions/t-1/events');
    source.emit({type: 'node', text: 'x'}, '7');
    expect(h.events).toHaveLength(1);
    expect(h.stream.lastEventId('t-1')).toBe(7);
    expect(h.stream.isOpen()).toBe(true);
  });

  it('重连时带上该会话自己的游标 (不串到别的会话)', () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    const h = harness();
    h.stream.connect('t-1');
    FakeEventSource.instances[0].emit({type: 'node'}, '5');
    h.stream.connect('t-1');
    expect(FakeEventSource.instances[1].url).toBe('/api/sessions/t-1/events?last_event_id=5');
    // 另一个会话从 0 开始, 不受 t-1 游标影响
    h.stream.connect('t-2');
    expect(FakeEventSource.instances[2].url).toBe('/api/sessions/t-2/events');
    expect(h.stream.lastEventId('t-2')).toBe(0);
  });

  it('无法解析的事件载荷被跳过, 不影响后续事件', () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    const h = harness();
    h.stream.connect('t-1');
    const source = FakeEventSource.instances[0];
    source.onmessage?.({data: '{not json', lastEventId: '1'});
    source.emit({type: 'node'}, '2');
    expect(h.events).toHaveLength(1);
    expect(h.stream.lastEventId('t-1')).toBe(2);
  });

  it('切换线程后排队到达的旧事件不得污染新页面或游标', () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    let current = 't-1';
    const h = harness({ currentThreadId: () => current });
    h.stream.connect('t-1');
    const oldSource = FakeEventSource.instances[0];
    current = 't-2';
    h.stream.connect('t-2');
    oldSource.emit({type: 'node', text: '旧研究'}, '12');
    expect(h.events).toHaveLength(0);
    expect(h.stream.lastEventId('t-1')).toBe(0);
    FakeEventSource.instances[1].emit({type: 'node', text: '新研究'}, '1');
    expect(h.events).toEqual([{type: 'node', text: '新研究'}]);
  });
});

describe('断线补偿与重连', () => {
  it('断线先查会话状态补偿, 等待输入时如实提示', async () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({status: 'waiting'}),
    })));
    const h = harness();
    h.stream.connect('t-1');
    FakeEventSource.instances[0].onerror?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(h.states).toEqual(['waiting']);
    expect(h.offline).toEqual(['waiting']);
    expect(h.statuses).toContain('reconnecting');
  });

  it('服务端已完成时提示完成, 不把断线说成"还在跑"', async () => {
    vi.stubGlobal('EventSource', FakeEventSource as any);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({status: 'done'}),
    })));
    const h = harness();
    h.stream.connect('t-1');
    FakeEventSource.instances[0].onerror?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(h.states).toEqual(['done']);
    expect(h.finished).toBe(1);
    expect(h.offline).toEqual([]);
  });

  it('重连次数有上限: 达到上限后不再新建连接', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource as any);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({status: 'running'}),
    })));
    const h = harness();
    h.stream.connect('t-1');
    for (let i = 0; i < 4; i += 1) {
      FakeEventSource.instances[FakeEventSource.instances.length - 1].onerror?.();
      await vi.advanceTimersByTimeAsync(60000);
    }
    expect(FakeEventSource.instances.length).toBeLessThanOrEqual(4);
    expect(h.statuses).toContain('closed');
  });

  it('显式关闭后不再重连', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource as any);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({status: 'running'}),
    })));
    const h = harness();
    h.stream.connect('t-1');
    FakeEventSource.instances[0].onerror?.();
    h.stream.close();
    await vi.advanceTimersByTimeAsync(60000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(h.stream.isOpen()).toBe(false);
  });

  it('切换到别的线程后, 旧线程的断线不再重连', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource as any);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({status: 'running'}),
    })));
    let current = 't-1';
    const h = harness({ currentThreadId: () => current });
    h.stream.connect('t-1');
    const first = FakeEventSource.instances[0];
    first.onerror?.();
    current = 't-2';
    await vi.advanceTimersByTimeAsync(60000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
