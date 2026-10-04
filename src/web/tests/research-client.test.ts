/// <reference types="vite/client" />
/**
 * 研究 HTTP 客户端用例 (合并计划 §9.4 / §9.5)。
 *
 * 固定的是**语义**, 不是某次请求的字符串:
 * 1. GET 与带幂等键的变更才自动重试 —— 否则一次超时重试可能把同一动作施加两遍;
 * 2. 4xx 是语义错误, 立即抛出并如实带出后端 `detail` (界面要显示"为什么失败");
 * 3. 取消 (`AbortError`) 直接向上抛, 不当作失败重试, 也不吞掉;
 * 4. URL 编码与查询参数只在这里拼一次。
 */

import { describe, expect, it, vi } from 'vitest';

import {
  ENDPOINTS,
  buildUrl,
  createResearchClient,
  isRetryable,
  type ApiError,
} from '../src/api/research-client';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('重试语义', () => {
  it('GET 幂等可重试; 变更类必须有幂等键', () => {
    expect(isRetryable({})).toBe(true);
    expect(isRetryable({ method: 'GET' })).toBe(true);
    expect(isRetryable({ method: 'POST' })).toBe(false);
    expect(isRetryable({ method: 'POST', idempotencyKey: 'k' })).toBe(true);
    expect(isRetryable({ method: 'DELETE' })).toBe(false);
  });

  it('5xx 时 GET 重试一次; 无幂等键的 POST 不重试', async () => {
    const getCalls: string[] = [];
    const getFetch = vi.fn(async (url: string) => {
      getCalls.push(String(url));
      return getCalls.length === 1 ? jsonResponse({}, 503) : jsonResponse({ ok: 1 });
    });
    const client = createResearchClient({ fetchImpl: getFetch as never });
    await expect(client.json('/api/sources')).resolves.toEqual({ ok: 1 });
    expect(getFetch).toHaveBeenCalledTimes(2);

    const postFetch = vi.fn(async () => jsonResponse({}, 503));
    const client2 = createResearchClient({ fetchImpl: postFetch as never });
    await expect(client2.json('/api/uploads', { method: 'POST', body: {} }))
      .rejects.toMatchObject({ status: 503 });
    expect(postFetch).toHaveBeenCalledTimes(1);
  });

  it('带幂等键的变更会重试, 并把幂等键放进请求头', async () => {
    const calls: Array<RequestInit | undefined> = [];
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push(init);
      return calls.length === 1 ? jsonResponse({}, 502) : jsonResponse({ ok: true });
    });
    const client = createResearchClient({ fetchImpl: fetcher as never });
    await expect(client.json('/api/library/import', {
      method: 'POST', body: { paths: [] }, idempotencyKey: 'lib-1',
    })).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(2);
    const headers = (calls[1]?.headers ?? {}) as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe('lib-1');
  });
});

describe('错误与取消', () => {
  it('4xx 立即抛出后端 detail (不是 HTTP 400 了事)', async () => {
    const fetcher = vi.fn(async () => jsonResponse({ detail: '资料源不可用' }, 409));
    const client = createResearchClient({ fetchImpl: fetcher as never });
    await expect(client.json('/api/sessions', { method: 'POST', body: {} }))
      .rejects.toMatchObject({ status: 409, detail: '资料源不可用' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('取消不算失败: AbortError 原样抛出, 不重试', async () => {
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
    const fetcher = vi.fn(async () => { throw abort; });
    const client = createResearchClient({ fetchImpl: fetcher as never });
    await expect(client.json('/api/sources')).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('错误对象带 status 与 detail, 界面可直接显示', async () => {
    const fetcher = vi.fn(async () => jsonResponse({ detail: '' }, 500));
    const client = createResearchClient({ fetchImpl: fetcher as never, retries: 0 });
    const error = await client.json('/api/sources').catch((e) => e) as ApiError;
    expect(error.status).toBe(500);
    expect(error.detail).toBe('HTTP 500');
  });
});

describe('URL 与查询参数', () => {
  it('查询参数编码且空值被丢弃', async () => {
    const seen: string[] = [];
    const fetcher = vi.fn(async (url: string) => {
      seen.push(String(url));
      return jsonResponse({});
    });
    const client = createResearchClient({ fetchImpl: fetcher as never, base: 'http://x' });
    await client.json('/api/artifacts', {
      params: { project_id: 'p 1', run_id: '', _: 123 },
    });
    expect(seen[0]).toContain('project_id=p+1');
    expect(seen[0]).not.toContain('run_id=');
  });

  it('端点表对 id 做 URL 编码', () => {
    expect(ENDPOINTS.sessionState('a/b')).toBe('/api/sessions/a%2Fb/state');
    expect(ENDPOINTS.artifact('out/x y.png')).toBe('/api/artifacts/out/x%20y.png');
    expect(ENDPOINTS.teamProjection('p1', 'r 1')).toBe('/api/team/p1/r%201');
  });

  it('地址构造自动补 "?"、逐项编码并跳过空值 (曾出现 /state&_= 的真实缺陷)', () => {
    const url = buildUrl('/api/research/p/state', { problem_id: 'p 1', _: 123 },
                         'http://127.0.0.1:8000');
    expect(url).toContain('?');
    expect(url).toContain('problem_id=p+1');
    expect(url).toContain('_=123');
    expect(url).not.toContain('/state&');
    expect(buildUrl('/x', { a: '', b: 'v' }, 'http://h')).toBe('http://h/x?b=v');
  });
});

describe('会话动作的请求形状 (原 research-api 覆盖, 迁移过来)', () => {
  it('启动/续研的语义靠字段区分, 不靠端点不同', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body ?? '{}')));
      return jsonResponse({ thread_id: 't', session_id: 's' });
    });
    const client = createResearchClient({ fetchImpl: fetcher as never, base: 'http://h' });
    await client.json(ENDPOINTS.sessions, { method: 'POST', body: { request: 'x' } });
    await client.json(ENDPOINTS.sessionResume('s1'), { method: 'POST', body: {} });
    expect(bodies[0].request).toBe('x');
    // 续研用**独立端点**, 不再靠同一个端点上的 resume 布尔开关
    expect(String((fetcher.mock.calls[1] ?? [])[0])).toContain('/api/sessions/s1/resume');
  });

  it('respond 必须带 interrupt_id (幂等键)', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body ?? '{}')));
      return jsonResponse({ ok: true });
    });
    const client = createResearchClient({ fetchImpl: fetcher as never, base: 'http://h' });
    await client.json(ENDPOINTS.sessionRespond('t-1'), {
      method: 'POST',
      body: { response: '选 A', interrupt_id: 'int-1' },
      idempotencyKey: 'int-1',
    });
    expect(bodies[0].response).toBe('选 A');
    expect(bodies[0].interrupt_id).toBe('int-1');
  });

  it('工作台取数把问题与运行身份一起带上 (不串项目/问题)', async () => {
    const seen: string[] = [];
    const fetcher = vi.fn(async (url: string) => {
      seen.push(String(url));
      return jsonResponse({});
    });
    const client = createResearchClient({ fetchImpl: fetcher as never, base: 'http://h' });
    await client.json(ENDPOINTS.researchState('projA'), {
      params: { problem_id: 'p2', run_id: 'r2' },
    });
    expect(seen[0]).toContain('/api/research/projA/state?');
    expect(seen[0]).toContain('problem_id=p2');
    expect(seen[0]).toContain('run_id=r2');
    expect(seen[0]).not.toContain('/state&');
  });
});

describe('取数入口的收敛 (防回流)', () => {
  // Vite 在构建时把源码读成字符串: 不依赖 node 类型, 也不用 fs (测试跑在 jsdom 下)。
  const sources = import.meta.glob('../src/**/*.ts', {
    query: '?raw', import: 'default', eager: true,
  }) as Record<string, string>;
  const sourceAt = (suffix: string): string | undefined => {
    const key = Object.keys(sources).find((path) => path.endsWith(suffix));
    return key ? sources[key] : undefined;
  };

  it('控制器层不再自己调 fetch, 而是走统一客户端', () => {
    // `session-stream.ts` 只用 `EventSource` 建 SSE 长连接 (协议要求), 不在此列。
    for (const name of ['team-controller.ts', 'library-controller.ts',
                        'session-controller.ts', 'features/intake/controller.ts',
                        'events/session-stream.ts', 'app.ts']) {
      const source = sourceAt(`/${name}`);
      expect(source, `缺少 ${name}`).toBeTruthy();
      expect(source, name).not.toMatch(/\bfetch\s*\(/);
      expect(source, name).toContain('research-client');
    }
  });

  it('端点字符串只出现在 api/ 层 (页面与控制器都不再自己拼 URL)', () => {
    // 去掉注释后再判定: 注释里解释"某个 URL 属于哪个接口"是文档, 不是拼接。
    const withoutComments = (text: string) => text
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    const offenders = Object.entries(sources)
      .filter(([path, source]) => !path.includes('/api/')
        && /['"`]\/api\//.test(withoutComments(source)))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});
