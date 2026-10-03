/**
 * 研究 HTTP 契约的用例 (计划书 §2 F4 / F0-1)。
 *
 * 重点: 视图不再各自拼 URL。这里验证地址构造与重试语义, 包括曾经的真实缺陷
 * (`/state&_=...` 缺 `?`)。
 */
import { describe, expect, it, vi } from 'vitest';

import { buildUrl, createResearchApi, requestWithRetry } from '../src/research-api';
import { emptyResearch } from '../src/current-research';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}

describe('buildUrl', () => {
  it('自动补 "?" 并逐项编码参数', () => {
    const url = buildUrl('/api/research/p/state', { problem_id: 'p 1', _: 123 },
                         'http://127.0.0.1:8000');
    expect(url).toContain('?');
    expect(url).toContain('problem_id=p+1');
    expect(url).toContain('_=123');
    expect(url).not.toContain('/state&');
  });

  it('无参数时也生成合法路径 (不再出现 /state&_= 这种写法)', () => {
    const url = buildUrl('/api/research/demo/state', { _: 1 },
                         'http://127.0.0.1:8000');
    expect(url).toBe('http://127.0.0.1:8000/api/research/demo/state?_=1');
  });

  it('跳过空值参数', () => {
    const url = buildUrl('/x', { a: '', b: 'v' }, 'http://h');
    expect(url).toBe('http://h/x?b=v');
  });
});

describe('requestWithRetry', () => {
  it('5xx 会重试, 4xx 立即返回', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, 500))
      .mockResolvedValueOnce(jsonResponse({ ok: true }, 200));
    const resp = await requestWithRetry(fetcher as unknown as typeof fetch,
                                        'http://h/x', {}, { retries: 1 });
    expect(resp.status).toBe(200);
    expect(fetcher).toHaveBeenCalledTimes(2);

    const four = vi.fn().mockResolvedValue(jsonResponse({ detail: 'bad' }, 409));
    const r2 = await requestWithRetry(four as unknown as typeof fetch,
                                      'http://h/x', {}, { retries: 2 });
    expect(r2.status).toBe(409);
    expect(four).toHaveBeenCalledTimes(1);
  });

  it('网络异常在重试耗尽后抛出', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(requestWithRetry(fetcher as unknown as typeof fetch,
                                  'http://h/x', {}, { retries: 1 }))
      .rejects.toThrow('offline');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe('createResearchApi', () => {
  it('getState 绑定 problem_id 而不是拼错查询串', async () => {
    const calls: string[] = [];
    const fetcher = vi.fn(async (url: string) => {
      calls.push(String(url));
      return jsonResponse({ claims: [] });
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://127.0.0.1:8000');
    await api.getState('demo', 'p1');
    expect(calls[0]).toContain('/api/research/demo/state?');
    expect(calls[0]).toContain('problem_id=p1');
    expect(calls[0]).not.toContain('/state&');
  });

  it('listArtifacts 带上当前研究上下文', async () => {
    const calls: string[] = [];
    const fetcher = vi.fn(async (url: string) => {
      calls.push(String(url));
      return jsonResponse({ files: [] });
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://127.0.0.1:8000');
    await api.listArtifacts({ ...emptyResearch('theory'), projectId: 'projA',
                              problemId: 'p2', runId: 'r2' });
    expect(calls[0]).toContain('project_id=projA');
    expect(calls[0]).toContain('problem_id=p2');
    expect(calls[0]).toContain('run_id=r2');
  });

  it('startSession 与 resumeSession 的语义分开', async () => {
    const bodies: string[] = [];
    const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return jsonResponse({ thread_id: 't' });
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://h');
    await api.startSession({ request: 'x' });
    await api.resumeSession({ request: 'x', project_id: 'p', problem_id: 'q' });
    expect(JSON.parse(bodies[0]).resume).toBeUndefined();
    expect(JSON.parse(bodies[1]).resume).toBe(true);
  });

  it('respond 带 interrupt_id (幂等键)', async () => {
    const bodies: string[] = [];
    const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return jsonResponse({ ok: true });
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://h');
    await api.respond('t-1', '选 A', 'int-1');
    const body = JSON.parse(bodies[0]);
    expect(body.response).toBe('选 A');
    expect(body.interrupt_id).toBe('int-1');
  });

  it('反馈请求带上作用对象与问题', async () => {
    const bodies: string[] = [];
    const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return jsonResponse({ ok: true });
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://h');
    await api.applyFeedback('projA', { response: '改一下', object_id: 'clm-1',
                                       problem_id: 'p1' });
    const body = JSON.parse(bodies[0]);
    expect(body.object_id).toBe('clm-1');
    expect(body.problem_id).toBe('p1');
  });

  it('项目/问题 id 会被 URL 编码', async () => {
    const calls: string[] = [];
    const fetcher = vi.fn(async (url: string) => {
      calls.push(String(url));
      return jsonResponse({});
    });
    const api = createResearchApi(fetcher as unknown as typeof fetch, 'http://h');
    await api.getState('a/b', 'p 1');
    expect(calls[0]).toContain('/api/research/a%2Fb/state');
    expect(calls[0]).toContain('problem_id=p+1');
  });
});
