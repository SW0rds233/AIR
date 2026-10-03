/**
 * 研究 HTTP 契约 (计划书 §2 F4)。
 *
 * 所有请求集中在这里: 输入、返回、错误与重试语义只有一份实现,
 * 视图层不再各自拼 URL。这样 F0-1 那类"手拼查询串缺 `?`"的问题不会在别处重现。
 */

import type { CurrentResearch } from './current-research';

export interface ResearchApi {
  startSession(body: Record<string, unknown>): Promise<Response>;
  resumeSession(body: Record<string, unknown>): Promise<Response>;
  getState(projectId: string, problemId: string): Promise<Response>;
  getProblems(projectId: string): Promise<Response>;
  listSources(): Promise<Response>;
  describeSource(sourceSetId: string): Promise<Response>;
  applyFeedback(projectId: string, body: Record<string, unknown>): Promise<Response>;
  fork(body: Record<string, unknown>): Promise<Response>;
  respond(threadId: string, response: string, interruptId?: string): Promise<Response>;
  sessionState(threadId: string): Promise<Response>;
  listArtifacts(state: CurrentResearch): Promise<Response>;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** 用 URL + searchParams 构造地址: 手拼字符串在参数缺失时会生成非法路径。 */
export function buildUrl(path: string, params: Record<string, string | number> = {},
                         base = 'http://127.0.0.1'): string {
  const url = new URL(path, base);
  Object.keys(params).forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null || value === '') return;
    url.searchParams.set(key, String(value));
  });
  return url.toString();
}

async function readJson(resp: Response): Promise<unknown> {
  try {
    return await resp.json();
  } catch (err) {
    return { detail: `响应不是 JSON (HTTP ${resp.status})` };
  }
}

/** 带重试的请求: 只在网络层失败或 5xx 时重试, 4xx 一律立即返回 (语义错误重试无用)。 */
export async function requestWithRetry(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit = {},
  options: { retries?: number; delayMs?: number } = {},
): Promise<Response> {
  const retries = options.retries === undefined ? 1 : options.retries;
  const delayMs = options.delayMs === undefined ? 0 : options.delayMs;
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const resp = await fetcher(url, init);
      if (resp.status >= 500 && attempt < retries) {
        lastError = new Error(`HTTP ${resp.status}`);
      } else {
        return resp;
      }
    } catch (err) {
      lastError = err;
      if (attempt >= retries) break;
    }
    if (delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

export function createResearchApi(fetcher?: typeof fetch,
                                  base = ''): ResearchApi {
  const doFetch: typeof fetch = fetcher || ((...args: Parameters<typeof fetch>) =>
    fetch(...args));

  const post = (path: string, body: Record<string, unknown>) =>
    requestWithRetry(doFetch, buildUrl(path, {}, base || defaultBase()), {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body),
    });

  const get = (path: string, params: Record<string, string | number> = {}) =>
    requestWithRetry(doFetch, buildUrl(path, params, base || defaultBase()), {
      method: 'GET',
    });

  return {
    startSession: (body) => post('/api/sessions', body),
    resumeSession: (body) => post('/api/sessions', Object.assign({ resume: true }, body)),
    getState: (projectId, problemId) => get(
      `/api/research/${encodeURIComponent(projectId)}/state`,
      { problem_id: problemId, _: Date.now() }),
    getProblems: (projectId) => get(
      `/api/research/${encodeURIComponent(projectId)}/problems`),
    listSources: () => get('/api/sources'),
    describeSource: (sourceSetId) => get(
      `/api/sources/${encodeURIComponent(sourceSetId)}`),
    applyFeedback: (projectId, body) => post(
      `/api/research/${encodeURIComponent(projectId)}/feedback`, body),
    fork: (body) => post('/api/research/fork', body),
    respond: (threadId, response, interruptId) => post(
      `/api/sessions/${encodeURIComponent(threadId)}/respond`,
      { response, interrupt_id: interruptId || '' }),
    sessionState: (threadId) => get(
      `/api/sessions/${encodeURIComponent(threadId)}/state`),
    listArtifacts: (state) => get('/api/artifacts', {
      project_id: state.projectId, problem_id: state.problemId,
      run_id: state.runId, _: Date.now(),
    }),
  };
}

function defaultBase(): string {
  if (typeof window !== 'undefined' && window.location && window.location.origin) {
    return window.location.origin;
  }
  return 'http://127.0.0.1';
}

export { readJson };
