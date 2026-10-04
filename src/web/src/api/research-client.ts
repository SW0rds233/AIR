/**
 * 研究 HTTP 客户端: 前端与后端之间的**唯一**取数入口 (合并计划 §9.5)。
 *
 * 为什么要有这一层
 * ----------------
 * 页面装配层此前到处直连 `fetch('/api/...')`, 于是同一件事 (取数、判错、取消、重试)
 * 在每个调用点各写一遍。计划书 §9.5 明确要求把"URL、错误、取消、幂等与兼容"集中到
 * `api/` 下, 因此这里成为唯一出口; 视图与控制器只管**语义**, 不再拼 URL。
 *
 * 三条刻意写死的语义 (§9.4)
 * -------------------------
 * 1. **自动重试只限幂等请求**: GET 与"带幂等键的变更"才重试。无幂等键的 POST/DELETE
 *    一律不重试 —— 否则一次超时重试就可能把同一个动作施加两遍 (例如重复导入)。
 * 2. **取消**: 所有请求都接受 `AbortSignal`; 切换会话时由调用方 abort 旧请求,
 *    迟到响应由调用方按加载代号丢弃 (见 `team-controller` / `session-controller`)。
 * 3. **错误可读**: 4xx 把后端的 `detail` 如实带出来 (界面要显示"为什么失败"),
 *    而不是抛一个 `HTTP 400` 了事。
 */

/** 用 URL + searchParams 构造地址: 手拼字符串在参数缺失时会生成非法路径
 *  (曾经的真实缺陷: `/state&_=...` 少了 `?`)。 */
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

/** 读 JSON: 非 JSON 响应也给出可读原因, 不让调用方拿到 `Unexpected token`。 */
export async function readJson(resp: Response): Promise<unknown> {
  try {
    return await resp.json();
  } catch (err) {
    return { detail: `响应不是 JSON (HTTP ${resp.status})` };
  }
}

/** 请求选项: 取消信号、是否允许重试、查询参数。 */
export interface RequestOptions {
  signal?: AbortSignal;
  /** 幂等键: 给了它才允许对变更类请求自动重试 (见文件头第 1 条)。 */
  idempotencyKey?: string;
  params?: Record<string, string | number>;
  method?: 'GET' | 'POST' | 'DELETE';
  body?: unknown;
  /** 表单上传 (文件): 与 `body` 互斥 —— 浏览器自己设置 multipart 边界。 */
  form?: FormData;
}

export interface ApiError extends Error {
  status: number;
  detail: string;
}

/** 后端返回的错误: 带上状态码与 `detail`, 供界面如实显示。 */
export function apiError(status: number, detail: string): ApiError {
  const error = new Error(detail || `HTTP ${status}`) as ApiError;
  error.status = status;
  error.detail = detail || `HTTP ${status}`;
  return error;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** 幂等: GET 天然幂等; 变更类必须有幂等键。 */
export function isRetryable(options: RequestOptions): boolean {
  if ((options.method ?? 'GET') === 'GET') return true;
  return Boolean(options.idempotencyKey);
}

export interface ResearchClientDeps {
  fetchImpl?: typeof fetch;
  base?: string;
  /** 每次尝试之间的等待 (毫秒); 测试里设为 0。 */
  retryDelayMs?: number;
  retries?: number;
}

export interface ResearchClient {
  /** 取 JSON (失败抛 `ApiError`)。 */
  json<T = unknown>(path: string, options?: RequestOptions): Promise<T>;
  /** 取原始 Response (需要看状态码/文本时用)。 */
  raw(path: string, options?: RequestOptions): Promise<Response>;
  readonly base: string;
}

function defaultBase(): string {
  if (typeof window !== 'undefined' && window.location && window.location.origin) {
    return window.location.origin;
  }
  return 'http://127.0.0.1';
}

export function createResearchClient(deps: ResearchClientDeps = {}): ResearchClient {
  const doFetch: typeof fetch = deps.fetchImpl
    || ((...args: Parameters<typeof fetch>) => fetch(...args));
  const base = deps.base || defaultBase();
  const delayMs = deps.retryDelayMs === undefined ? 0 : deps.retryDelayMs;
  const retries = deps.retries === undefined ? 1 : deps.retries;

  async function raw(path: string, options: RequestOptions = {}): Promise<Response> {
    const url = buildUrl(path, options.params ?? {}, base);
    const method = options.method ?? 'GET';
    const init: RequestInit = { method, signal: options.signal };
    if (options.idempotencyKey) {
      init.headers = { ...JSON_HEADERS, 'Idempotency-Key': options.idempotencyKey };
    } else if (method !== 'GET' && !options.form) {
      // 表单请求**不能**手写 Content-Type: multipart 的边界由浏览器生成。
      init.headers = JSON_HEADERS;
    }
    if (options.form) {
      init.body = options.form;
    } else if (options.body !== undefined) {
      init.body = JSON.stringify(options.body);
    }
    const attempts = isRetryable(options) ? retries : 0;
    let lastError: unknown = null;
    for (let attempt = 0; attempt <= attempts; attempt += 1) {
      try {
        const response = await doFetch(url, init);
        // 4xx 是语义错误: 重试无用, 立即如实返回/抛出
        if (response.status < 500 || attempt >= attempts) return response;
        lastError = apiError(response.status, `HTTP ${response.status}`);
      } catch (error) {
        // 取消不是失败: 直接向上抛, 由调用方按加载代号丢弃
        if ((error as { name?: string } | null)?.name === 'AbortError') throw error;
        lastError = error;
        if (attempt >= attempts) break;
      }
      if (delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
    }
    throw lastError instanceof Error ? lastError : apiError(0, String(lastError));
  }

  async function json<T = unknown>(path: string, options: RequestOptions = {})
    : Promise<T> {
    const response = await raw(path, options);
    const payload = (await readJson(response)) as Record<string, unknown> | null;
    if (!response.ok) {
      const detail = payload && typeof payload.detail === 'string'
        ? payload.detail
        : `HTTP ${response.status}`;
      throw apiError(response.status, detail);
    }
    return payload as T;
  }

  return { json, raw, base };
}

// ----------------------------------------------------------------------
// 端点: URL 只在这里出现 (§9.5 "URL/错误/取消/幂等集中管理")
// ----------------------------------------------------------------------

/**
 * 非 GET 端点的方法表: 这是**契约**, 不是文档。
 *
 * 为什么不靠"后端契约测试去猜调用点用了哪个方法": 猜法很脆 (同一端点在多个文件里
 * 被调用、`ENDPOINTS.upload` 还是 `ENDPOINTS.uploads` 的前缀……), 一旦猜不中就退化
 * 成"这条判据永远通过"。显式写在这里之后, `tests/test_web_api_contract.py` 可以直接
 * 比对: 表里每个端点都必须在后端存在, 且后端必须接受这个方法。
 *
 * 未列入的都是只读 GET。
 */
export const VERBS = {
  sessions: 'POST',
  sessionStop: 'POST',
  sessionRespond: 'POST',
  sessionResume: 'POST',
  sessionDelete: 'DELETE',
  researchFeedback: 'POST',
  fork: 'POST',
  uploads: 'POST',
  upload: 'DELETE',
  libraryScan: 'POST',
  libraryImport: 'POST',
  library: 'DELETE',
} as const;

/** 端点名 → 方法 (未列入 `VERBS` 的都是 GET)。 */
export function endpointMethod(name: string): string {
  return (VERBS as Record<string, string>)[name] ?? 'GET';
}

export const ENDPOINTS = {
  sessions: '/api/sessions',
  sessionState: (threadId: string) =>
    `/api/sessions/${encodeURIComponent(threadId)}/state`,
  sessionEvents: (threadId: string) =>
    `/api/sessions/${encodeURIComponent(threadId)}/events`,
  sessionStop: (threadId: string) =>
    `/api/sessions/${encodeURIComponent(threadId)}/stop`,
  sessionRespond: (threadId: string) =>
    `/api/sessions/${encodeURIComponent(threadId)}/respond`,
  sessionResume: (sessionId: string) =>
    `/api/sessions/${encodeURIComponent(sessionId)}/resume`,
  sessionDelete: (sessionId: string) =>
    `/api/sessions/${encodeURIComponent(sessionId)}`,
  conversations: '/api/conversations',
  conversation: (sessionId: string) =>
    `/api/conversations/${encodeURIComponent(sessionId)}`,
  sources: '/api/sources',
  source: (sourceSetId: string) => `/api/sources/${encodeURIComponent(sourceSetId)}`,
  contexts: '/api/contexts',
  contextNotes: (topic: string) =>
    `/api/contexts/${encodeURIComponent(topic)}/notes`,
  researchState: (projectId: string) =>
    `/api/research/${encodeURIComponent(projectId)}/state`,
  researchProblems: (projectId: string) =>
    `/api/research/${encodeURIComponent(projectId)}/problems`,
  researchFeedback: (projectId: string) =>
    `/api/research/${encodeURIComponent(projectId)}/feedback`,
  fork: '/api/research/fork',
  artifacts: '/api/artifacts',
  artifact: (name: string) => `/api/artifacts/${encodeURI(name)}`,
  uploads: '/api/uploads',
  upload: (attachmentId: string) =>
    `/api/uploads/${encodeURIComponent(attachmentId)}`,
  teamRoles: '/api/team/roles',
  teamProjection: (projectId: string, runId: string) =>
    `/api/team/${encodeURIComponent(projectId)}/${encodeURIComponent(runId)}`,
  // 资料库接入 (§13.4): 扫描 / 导入 / 详情 / 解除登记
  libraryScan: '/api/library/scan',
  libraryImport: '/api/library/import',
  library: (sourceSetId: string) =>
    `/api/library/${encodeURIComponent(sourceSetId)}`,
} as const;

/** 进程级默认客户端 (页面使用; 测试注入自己的 `fetchImpl`)。 */
let shared: ResearchClient | null = null;

export function client(): ResearchClient {
  if (shared === null) shared = createResearchClient();
  return shared;
}

/** 供测试重置单例 (避免用例之间互相影响)。 */
export function resetClient(next: ResearchClient | null = null): void {
  shared = next;
}
