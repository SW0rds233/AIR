/**
 * 会话操作单一入口 (合并计划 §9.5 `session-controller.ts`, G19 后经唯一 store) 的行为测试。
 *
 * 迁移的关键风险是"会话切换串号", 因此这些用例盯的是语义而不是实现:
 * - 新建会话必须清空**所有**身份 (线程/会话/项目/问题) 并整体重置团队视图;
 * - 身份切换必须让在途的工作台请求作废 (迟到回包不得覆盖新会话) —— 迁移前靠页面上的
 *   `workbenchSeqBump()`, 现在由唯一状态的加载代号 (`ui.loadToken`) 承担;
 * - 切换历史会话必须整体恢复项目与问题 (**统一入口后不再有"模式恢复"**);
 * - 已完成的会话恢复后不得再连事件流 (查看不触发运行)。
 *
 * G19 迁移说明: 断言不再读 `deps.applyResearch` 捕获的补丁对象 (那个写入口已删除),
 * 而是读**唯一状态** `state/research-store.ts` 的 `selection` —— 判据逐条保留。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createSessionController,
  type SessionController,
  type SessionDeps,
} from '../src/session-controller';
import { createResearchClient } from '../src/api/research-client';
import { dispatch, getState, resetStore } from '../src/state/research-store';

interface Harness {
  controller: SessionController;
  calls: string[];
  deps: SessionDeps;
}

function selection(): Record<string, any> {
  const s = getState().selection;
  return {
    threadId: s.threadId, sessionId: s.sessionId, projectId: s.projectId,
    problemId: s.problemId, runId: s.runId, mode: s.mode, runStatus: s.runStatus,
  };
}

function harness(overrides: Partial<SessionDeps> = {}): Harness {
  // 注意: 这里刻意**没有** `#runmode` —— 模式选择器已删除, 保留一个不存在的元素
  // 会让"删除模式分支"变成假动作。
  document.body.innerHTML = `
    <div id="log"></div>
    <div id="workbench"></div>
    <div id="team-section"></div>
    <select id="hist"><option value="">+ 历史会话</option></select>
    <input id="projid"><input id="probid"><input id="wbfeedback"><input id="wbsnapid">
    <span id="runhint"></span>
    <div id="history-overlay"></div>
    <div id="history-list"></div>
    <div id="history-detail"></div>
    <span id="history-title"></span>
    <button id="btn-history-back"></button>
  `;
  const calls: string[] = [];
  const deps: SessionDeps = {
    connectStream: (tid) => { calls.push('connect:' + tid); },
    closeStream: () => { calls.push('close'); },
    addMsg: (text, cls) => {
      calls.push('msg:' + cls + ':' + text);
      const log = document.getElementById('log');
      if (!log) return;
      const row = document.createElement('div');
      row.className = 'msg ' + cls;
      row.textContent = text;
      log.appendChild(row);
    },
    setMode: (m) => { calls.push('mode:' + m); },
    setStatus: (text) => { calls.push('status:' + text); },
    switchTab: (name) => { calls.push('tab:' + name); },
    refreshWorkbench: () => { calls.push('refreshWorkbench'); },
    refreshArtifacts: () => { calls.push('refreshArtifacts'); },
    refreshContexts: () => { calls.push('refreshContexts'); },
    refreshHistorySelect: () => { calls.push('refreshHistorySelect'); },
    loadArtifact: (name) => { calls.push('loadArtifact:' + name); },
    resetTeam: () => { calls.push('resetTeam'); },
    // HTTP 出口走 `api/` 层 (§9.5): 控制器本身不拼 URL, 因此这里注入一个客户端。
    // `fetchImpl` 每次请求时才解析, 于是用例里的 `vi.stubGlobal('fetch', ...)`
    // 仍然能拦住请求 —— 断言还是"发了哪个 URL"。
    api: createResearchClient({
      base: 'http://127.0.0.1',
      retries: 0,
      fetchImpl: ((...args: Parameters<typeof fetch>) =>
        (globalThis.fetch as typeof fetch)(...args)) as typeof fetch,
    }),
    ...overrides,
  };
  return { controller: createSessionController(deps), calls, deps };
}

function jsonResponse(body: any, status = 200): Promise<any> {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  resetStore();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('新建会话 (newSession)', () => {
  it('清空全部身份、清空日志与工作台, 并整体重置团队视图', () => {
    const h = harness();
    // 上一个会话留下的身份与消息都必须消失 (新会话不能沿用旧上下文)
    dispatch({ type: 'selection/patch', patch: {
      threadId: 't-old', sessionId: 's-old', projectId: 'p-old', problemId: 'q-old', runId: 'r-old',
    } });
    document.getElementById('log')!.innerHTML = '<div class="msg msg-user">上一轮输入</div>';
    h.controller.newSession();
    expect(selection()).toEqual({
      threadId: '', sessionId: '', projectId: '', problemId: '', runId: '',
      mode: '', runStatus: 'idle',
    });
    expect(h.calls).toContain('close');
    expect(h.calls).toContain('resetTeam');
    expect(document.getElementById('team-section')).toBeNull();
    expect(document.getElementById('log')!.textContent).not.toContain('上一轮输入');
    expect((document.getElementById('probid') as HTMLInputElement).value).toBe('problem');
    expect(document.getElementById('workbench')!.innerHTML).toContain('新会话');
    expect(h.calls.filter((c) => c.startsWith('msg:msg-agent:欢迎'))).toHaveLength(1);
    expect(h.calls).toContain('mode:idle');
  });

  it('新建会话让在途的工作台请求作废 (加载代号前进)', () => {
    const h = harness();
    dispatch({ type: 'selection/patch', patch: { projectId: 'p-old' } });
    const before = getState().ui.loadToken;
    h.controller.newSession();
    // 迁移前这里靠页面 `workbenchSeqBump()`; 现在身份整体重置本身就推进代号,
    // 因此任何以旧代号发出的回包都会被丢弃。
    expect(getState().ui.loadToken).toBeGreaterThan(before);
  });

  it('欢迎语不再区分"模式", 而是说明统一入口', () => {
    const h = harness();
    h.controller.newSession();
    const welcome = h.calls.filter((c) => c.startsWith('msg:msg-agent:')).join('\n');
    expect(welcome).toContain('系统自行判断研究类型');
    expect(welcome).not.toContain('理论研究」模式');
    expect(welcome).not.toContain('综述模式');
  });
});

describe('切换历史会话 (switchToConversation)', () => {
  it('整体恢复项目/问题, 回到日志, 未完成则恢复线程', async () => {
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      requests.push(path);
      if (path.startsWith('/api/conversations/')) {
        return jsonResponse({
          session_id: 's1', topic: '历史主题', status: 'waiting',
          request: {mode: 'theory', project_id: 'proj-x', problem_id: 'q1'},
          messages: [
            {role: 'user', text: '之前的输入'},
            {role: 'interrupt', title: '等待确认', hint: 'hint'},
          ],
        });
      }
      if (path.endsWith('/resume')) return jsonResponse({thread_id: 't-9'});
      return jsonResponse({});
    }));
    const h = harness();
    h.controller.switchToConversation('s1');
    await flush();
    await flush();
    await flush();
    const sel = selection();
    expect(sel.sessionId).toBe('s1');
    expect(sel.projectId).toBe('proj-x');
    expect(sel.problemId).toBe('q1');
    expect(sel.threadId).toBe('t-9');
    // 服务端记录的引擎标识如实带入 (只用于显示)
    expect(sel.mode).toBe('theory');
    expect((document.getElementById('projid') as HTMLInputElement).value).toBe('proj-x');
    // 统一入口: 不再有模式回放 (onModeChange 已删除), 直接进入工作台
    expect(h.calls).not.toContain('onModeChange');
    expect(h.calls).toContain('tab:workbench');
    expect(h.calls).toContain('connect:t-9');
    // 日志里只有非 interrupt 的历史消息 (最后一条暂停点由恢复的会话重新给出)
    const logText = document.getElementById('log')!.textContent || '';
    expect(logText).toContain('历史主题');
    expect(logText).toContain('之前的输入');
    expect(logText).not.toContain('等待确认');
  });

  it('历史记录缺少 request 字段时按空上下文打开, 不抛异常', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path.startsWith('/api/conversations/')) {
        return jsonResponse({ session_id: 's9', topic: '无请求记录', status: 'done' });
      }
      return jsonResponse({});
    }));
    const h = harness();
    h.controller.switchToConversation('s9');
    await flush();
    await flush();
    expect(selection().projectId).toBe('');
    expect(selection().mode).toBe('');
    expect(selection().sessionId).toBe('s9');
  });

  it('已完成的会话只查看: 不连事件流, 状态标记为已完成', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path.startsWith('/api/conversations/')) {
        return jsonResponse({
          session_id: 's2', topic: 'T', status: 'done',
          request: {mode: 'survey'}, messages: [{role: 'assistant', text: '结论'}],
        });
      }
      return jsonResponse({});
    }));
    const h = harness();
    h.controller.switchToConversation('s2');
    await flush();
    await flush();
    expect(h.calls.some((c) => c.startsWith('connect:'))).toBe(false);
    expect(h.calls).toContain('status:已完成');
    // 统一入口: 不论历史会话是哪种引擎, 都进同一个工作台
    expect(h.calls).toContain('tab:workbench');
    expect(selection().threadId).toBe('');
  });
});

describe('停止与结束', () => {
  it('没有线程时不发停止请求; 有线程时如实说明在等当前节点', async () => {
    const fetchMock = vi.fn(() => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);
    const idle = harness();
    idle.controller.stopSession();
    expect(fetchMock).not.toHaveBeenCalled();
    dispatch({ type: 'selection/patch', patch: { threadId: 't-1' } });
    const running = harness();
    running.controller.stopSession();
    await flush();
    // 客户端把路径解析成绝对 URL (base), 因此断言**路径**而不是整串
    const firstCall = fetchMock.mock.calls[0] as unknown[] | undefined;
    const stopUrl = String(firstCall?.[0] ?? '');
    expect(stopUrl).toContain('/api/sessions/t-1/stop');
    expect(running.calls.some((c) => c.includes('已请求停止'))).toBe(true);
  });

  it('finishRun 清线程、关连接并刷新上下文 (不清项目/问题)', () => {
    const h = harness();
    dispatch({ type: 'selection/patch', patch: {
      threadId: 't-1', projectId: 'p7', problemId: 'q7',
    } });
    h.controller.finishRun();
    expect(selection().threadId).toBe('');
    // 结束的只是本次运行的线程: 项目与问题仍是当前上下文
    expect(selection().projectId).toBe('p7');
    expect(selection().problemId).toBe('q7');
    expect(h.calls).toContain('close');
    expect(h.calls).toContain('refreshContexts');
    expect(h.calls).toContain('refreshHistorySelect');
    // 统一入口: 结束时总是刷新工作台 (不再按模式判断)
    expect(h.calls).toContain('refreshWorkbench');
    expect(h.calls).toContain('mode:idle');
  });

  it('终止事件 onDone 说明交付级别与门槛, 并刷新产物', () => {
    const h = harness();
    h.controller.onDone({
      delivery_level: 'manuscript', gate_passed: false,
      snapshot_id: 'snap-1', package_dir: '/tmp/pkg', project_id: 'p7',
    });
    expect(selection().projectId).toBe('p7');
    expect(h.calls.some((c) => c.includes('交付门槛: 未通过'))).toBe(true);
    expect(h.calls).toContain('tab:workbench');
    expect(h.calls).toContain('refreshArtifacts');
  });

  it('综述完成时按既有规则给结论摘要并打开产物', () => {
    const h = harness();
    h.controller.onDone({
      literature_notes_path: 'E:\\AIR\\outputs\\run-1\\notes.md',
      figure_count: 3, review_score: '42', revision_count: 1,
      draft_path: 'E:\\AIR\\outputs\\run-1\\draft.md',
    });
    expect(h.calls.some((c) => c.includes('已生成图表: 3 张'))).toBe(true);
    expect(h.calls).toContain('loadArtifact:run-1/notes.md');
  });
});

describe('删除会话', () => {
  it('没有选中会话时提示先选择; 确认后清空身份并删除条目', async () => {
    const alerts: string[] = [];
    vi.stubGlobal('alert', (text: string) => { alerts.push(text); });
    const h = harness();
    h.controller.deleteSession();
    expect(alerts).toHaveLength(1);

    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      requests.push(String(url).replace(/^https?:\/\/[^/]+/, ""));
      return jsonResponse({removed: ['s3']});
    }));
    vi.stubGlobal('confirm', () => true);
    const sel = document.getElementById('hist') as HTMLSelectElement;
    const option = document.createElement('option');
    option.value = 's3';
    option.textContent = '会话三';
    sel.appendChild(option);
    sel.value = 's3';
    dispatch({ type: 'selection/patch', patch: { threadId: 't-3', sessionId: 's3' } });
    h.controller.deleteSession();
    await flush();
    expect(requests[0]).toBe('/api/sessions/s3');
    expect(selection().threadId).toBe('');
    expect(selection().sessionId).toBe('');
    expect(h.calls.some((c) => c.includes('已删除会话: s3'))).toBe(true);
    expect(sel.querySelector('option[value="s3"]')).toBeNull();
  });

  it('取消确认时不发删除请求', async () => {
    const fetchMock = vi.fn(() => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('confirm', () => false);
    dispatch({ type: 'selection/patch', patch: { sessionId: 's4' } });
    const h = harness();
    h.controller.deleteSession();
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('历史回看抽屉', () => {
  it('列表按 data-sid 精确挂事件, 已完成会话没有继续按钮', async () => {
    vi.stubGlobal('fetch', vi.fn(() => jsonResponse({
      conversations: [
        {session_id: 's1', topic: 'A', status: 'running'},
        {session_id: 's2', topic: 'B', status: 'done'},
      ],
    })));
    const h = harness();
    h.controller.showHistoryList();
    await flush();
    const list = document.getElementById('history-list')!;
    expect(list.querySelector('.history-item[data-sid="s1"]')).not.toBeNull();
    expect(list.querySelector('.history-item[data-sid="s1"] .hist-resume')).not.toBeNull();
    expect(list.querySelector('.history-item[data-sid="s2"] .hist-resume')).toBeNull();
  });

  it('回看详情渲染消息并显示返回按钮', async () => {
    vi.stubGlobal('fetch', vi.fn(() => jsonResponse({
      session_id: 's5', topic: '回看主题', status: 'done',
      messages: [{role: 'user', text: '问题'}, {role: 'assistant', text: '回答'}],
    })));
    const h = harness();
    h.controller.viewConversation('s5');
    await flush();
    expect(document.getElementById('history-title')!.textContent).toBe('回看主题');
    const detail = document.getElementById('history-detail')!;
    expect(detail.textContent).toContain('问题');
    expect(detail.textContent).toContain('回答');
    expect(detail.style.display).toBe('block');
  });

  it('关闭抽屉只隐藏, 不清空内容', () => {
    const h = harness();
    const overlay = document.getElementById('history-overlay')!;
    overlay.style.display = 'block';
    h.controller.closeHistory();
    expect(overlay.style.display).toBe('none');
  });
});
