/**
 * 统一输入与附件控制器 (合并计划 §9.5 `features/intake/controller.ts`) 的行为测试。
 *
 * 关注点是**统一入口后不允许退化的语义**:
 * - 只有一个启动入口, 载荷里**不再有 `mode`** (服务端决定引擎);
 * - 项目/问题/预算/资料授权/附件等字段**始终**随请求发出;
 * - 附件按项目归属校验 (R3): 项目 ID 与上传时不同必须拦下并如实告知, 不能静默丢附件;
 * - 稳定草稿身份 (R2): 启动前必须有 project_id;
 * - 附件列表为空时清空绑定说明, 不留下上一个会话的字样。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createResearchClient } from '../src/api/research-client';
import {
  createIntakeController,
  type IntakeController,
  type IntakeDeps,
} from '../src/features/intake/controller';

interface Harness {
  controller: IntakeController;
  deps: IntakeDeps;
  messages: Array<{ text: string; cls: string }>;
  research: Record<string, any>;
  projectField: { value: string };
}

function harness(overrides: Partial<IntakeDeps> = {}): Harness {
  // 注意: 这里刻意**没有** `#runmode` —— 模式选择器已从模板与控制器中删除,
  // 给测试 DOM 放一个不存在的元素会让"删除"变成假动作。
  document.body.innerHTML = `
    <div id="log"></div>
    <textarea id="reply"></textarea>
    <button id="btn-send"></button>
    <button id="btn-stop"></button>
    <input id="projid">
    <input id="probid">
    <input id="topic">
    <input id="keywords">
    <span id="attachlist"></span>
    <span id="filebinding"></span>
  `;
  const messages: Array<{ text: string; cls: string }> = [];
  const research: Record<string, any> = {};
  const deps: IntakeDeps = {
    threadId: () => '',
    setThreadId: () => {},
    applyResearch: (patch) => { Object.assign(research, patch); },
    problemId: () => '',
    projectId: () => '',
    projectField: () => '',
    boundProjectId: () => '',
    workbenchTargetFor: (pid) => pid,
    bindRun: () => {},
    markDraftProject: () => {},
    ensureDraftProjectId: () => 'proj-draft',
    researchContext: () => '',
    addMsg: (text, cls) => { messages.push({ text, cls }); },
    setMode: () => {},
    mode: () => 'idle',
    setStatus: () => {},
    clearReply: () => {},
    replyInput: () => document.getElementById('reply') as HTMLTextAreaElement,
    setPlaceholder: () => {},
    pendingInterruptId: () => '',
    currentCandidateId: () => '',
    onCandidateChosen: () => {},
    refreshSourceSets: () => {},
    refreshHistorySelect: () => {},
    refreshWorkbench: () => {},
    refreshArtifacts: () => {},
    refreshContexts: () => {},
    switchTab: () => {},
    connectStream: () => {},
    setPendingInterruptId: () => {},
    submitFeedback: () => {},
    // HTTP 出口走 `api/` 层 (§9.5): 控制器不拼 URL, 因此这里注入一个客户端;
    // `fetchImpl` 每次请求时才解析, 用例里的 `vi.stubGlobal('fetch', ...)` 仍然生效。
    api: createResearchClient({
      base: 'http://127.0.0.1',
      retries: 0,
      fetchImpl: ((...args: unknown[]) => (globalThis.fetch as any)(...args)) as typeof fetch,
    }),
    ...overrides,
  };
  return {
    controller: createIntakeController(deps),
    deps,
    messages,
    research,
    projectField: document.getElementById('projid') as HTMLInputElement,
  };
}

function jsonResponse(body: any, status = 200): Promise<any> {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// 每个用例都必须用**自己的** fetch 桩: 否则后一个用例会打到真实网络,
// 断言就变成了"发没发请求"这种假阳性/假阴性的来源。
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('测试未设置 fetch 桩'))));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('统一入口 (start)', () => {
  it('载荷不带 mode, 且项目/预算/资料授权等字段始终随请求发出', async () => {
    const calls: Array<{ url: string; init: any }> = [];
    vi.stubGlobal('fetch', vi.fn((url: string, init: any) => {
      calls.push({ url: String(url).replace(/^https?:\/\/[^/]+/, ''), init });
      return jsonResponse({ thread_id: 't-1', session_id: 's-1' });
    }));
    const h = harness();
    h.controller.start('研究主题');
    await flush();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('/api/sessions');
    const payload = JSON.parse(calls[0].init.body);
    expect(payload.request).toBe('研究主题');
    // 统一入口的核心契约: 前端不再发送 mode
    expect(payload.mode).toBeUndefined();
    expect('mode' in payload).toBe(false);
    // 统一载荷: 这些字段不再取决于"用户选了哪种模式"
    expect(payload.project_id).toBe('proj-draft');
    expect(payload.problem_id).toBe('problem');
    expect(payload.source_policy).toBe('user_kb');
    expect(payload.attachment_ids).toEqual([]);
    expect(payload.max_actions).toBe(40);
    expect(payload.max_tool_calls).toBe(60);
    expect(h.deps.threadId).toBeDefined();
  });

  it('表单里的精确主题优先, 否则用资料库上下文主题, 最后才用输入本身', async () => {
    const calls: any[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, init: any) => {
      calls.push(JSON.parse(init.body));
      return jsonResponse({ thread_id: 't-2' });
    }));
    const h = harness({ researchContext: () => '库主题' });
    h.controller.start('我的请求');
    await flush();
    expect(calls[0].topic).toBe('库主题');
    (document.getElementById('topic') as HTMLInputElement).value = '表单主题';
    h.controller.start('我的请求');
    await flush();
    expect(calls[1].topic).toBe('表单主题');
    (document.getElementById('topic') as HTMLInputElement).value = '';
    const noContext = harness();
    noContext.controller.start('只有输入');
    await flush();
    expect(calls[2].topic).toBe('只有输入');
  });

  it('服务端返回的引擎标识只写入状态用于显示, 不再回显到请求里', async () => {
    const calls: any[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, init: any) => {
      calls.push(JSON.parse(init.body));
      return jsonResponse({ thread_id: 't-3', mode: 'theory' });
    }));
    const h = harness();
    h.controller.start('问题');
    await flush();
    expect(h.research.mode).toBe('theory');
    expect(calls[0].mode).toBeUndefined();
  });

  it('没有请求也没有主题时不发请求, 如实提示', async () => {
    const fetchMock = vi.fn(() => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('alert', vi.fn());
    const h = harness();
    h.controller.start('   ');
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(globalThis.alert).toHaveBeenCalled();
  });
});

describe('附件归属自检 (R3)', () => {
  it('问题附件属于另一个项目时拦下启动并说明原因', async () => {
    const fetchMock = vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path.startsWith('/api/uploads?')) {
        return jsonResponse({
          attachments: [{
            attachment_id: 'att-1', kind: 'problem', filename: 'notes.md',
            size: 12, sha256: 'a'.repeat(64),
          }],
        });
      }
      return jsonResponse({ thread_id: 't-3' });
    });
    vi.stubGlobal('fetch', fetchMock);
    let projectId = 'proj-old';
    const h = harness({
      projectId: () => projectId,
      projectField: () => projectId,
    });
    // 上传时表单里的项目 id 也是 proj-old: 附件据此登记归属
    h.projectField.value = 'proj-old';
    await h.controller.refreshAttachments();
    await flush();
    expect(h.controller.pendingAttachmentIds()).toEqual(['att-1']);
    // 之后项目被改成 proj-new (表单与状态一起变)
    projectId = 'proj-new';
    h.projectField.value = 'proj-new';
    h.controller.start('问题');
    await flush();
    await flush();
    const sessionCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).replace(/^https?:\/\/[^/]+/, '') === '/api/sessions');
    expect(sessionCalls).toHaveLength(0);
    expect(h.messages.some((m) => m.cls === 'msg-error' && m.text.includes('会被归属校验拒绝')))
      .toBe(true);
    expect(h.controller.pendingAttachmentIds()).toEqual(['att-1']);
  });

  it('附件列表为空时清空绑定说明, 不残留上一个会话的字样', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path.startsWith('/api/uploads?')) return jsonResponse({ attachments: [] });
      return jsonResponse({});
    }));
    const h = harness();
    await h.controller.refreshAttachments();
    expect(document.getElementById('filebinding')!.textContent).toBe('');
    expect(document.getElementById('attachlist')!.textContent).toContain('还没有附件');
  });

  it('有附件时绑定说明列出文件名与用途', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '');
      if (path.startsWith('/api/uploads?')) {
        return jsonResponse({
          attachments: [
            { attachment_id: 'a1', kind: 'problem', filename: 'q.md', size: 1, sha256: 'x' },
            { attachment_id: 'a2', kind: 'literature', filename: 'ref.pdf', size: 2, sha256: 'y' },
          ],
        });
      }
      return jsonResponse({});
    }));
    const h = harness({ projectId: () => 'p1' });
    await h.controller.refreshAttachments();
    const text = document.getElementById('filebinding')!.textContent || '';
    expect(text).toContain('q.md');
    expect(text).toContain('ref.pdf');
    expect(text).toContain('1 份问题说明将并入问题陈述');
    expect(document.getElementById('attachlist')!.innerHTML)
      .toContain('data-action="deleteAttachment"');
    expect(h.controller.pendingAttachmentIds()).toEqual(['a1']);
  });
});

describe('草稿身份的查询门禁 (R2)', () => {
  it('草稿项目不查工作台, 换成别的 id 一律放行', () => {
    const h = harness({
      projectField: () => 'proj-draft',
      boundProjectId: () => '',
      workbenchTargetFor: (pid) => (pid === 'proj-draft' ? '' : pid),
    });
    h.controller.markDraftProject('proj-draft');
    expect(h.controller.workbenchTargetPid()).toBe('');
    expect(h.deps.workbenchTargetFor('proj-other')).toBe('proj-other');
  });
});




