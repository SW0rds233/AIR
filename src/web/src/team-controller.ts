/**
 * 团队工作台接入层 (合并计划 §9.2 / §9.3)。
 *
 * 定位: 把**已有的**理论工作台 (`app.ts` 的 `renderWorkbench`) 与新的团队视图拼在
 * 一起, 而不把两套状态再复制一份。做法:
 *
 * - 页面只有**一份**团队状态: `ResearchStore` 的 `entities`/`transport`/`ui` 三片;
 * - 数据来源是后端已有的投影接口 (`/api/team/roles`、`/api/team/{project}/{run}`),
 *   视图**不推断**科学结论, 也不在页面里另做一套主控调度;
 * - 旧的 `window.AIR.research` 在这一层被**单向映射**到 `selection`
 *   (计划书 §9.3: "不能在兼容对象与新 store 之间双向同步");
 * - 每次加载都带加载代号, 迟到响应直接丢弃。
 *
 * 迁移期它渲染进 `#workbench` 的同一个容器: 团队视图在前 (主控理解与派工), 旧工作台
 * 在后 (研究对象与产物)。旧模式被标记为兼容层, 界面如实说明它来自旧流程。
 */

import { applyEvent, type EventEffect, type TeamEvent } from './events/session-events';
import { ENDPOINTS } from './api/research-client';
import {
  emptyStore,
  reduce,
  type ResearchStore,
  type TeamRoleRow,
} from './state/research-store';
import { renderTeamBoard } from './views/team-board';

// 端点集中在 api/ 层 (§9.5): 这里只做语义命名, 不重复写 URL。
export const TEAM_API = {
  roles: ENDPOINTS.teamRoles,
  projection: (projectId: string, runId: string) =>
    ENDPOINTS.teamProjection(projectId, runId),
} as const;

let store: ResearchStore = emptyStore();
let rolesLoaded = false;

export function getStore(): ResearchStore {
  return store;
}

export function dispatch(action: Parameters<typeof reduce>[1]): ResearchStore {
  store = reduce(store, action);
  return store;
}

/** 兼容层: 只把旧 `window.AIR.research` 的身份**单向**同步进 selection。 */
export function syncSelectionFromLegacy(research: {
  projectId?: string; problemId?: string; sessionId?: string;
  threadId?: string; runId?: string;
} | null | undefined): void {
  if (!research) return;
  const next = {
    projectId: String(research.projectId || ''),
    problemId: String(research.problemId || ''),
    sessionId: String(research.sessionId || ''),
    threadId: String(research.threadId || ''),
    runId: String(research.runId || ''),
  };
  const current = store.selection;
  const changed = next.projectId !== current.projectId
    || next.problemId !== current.problemId
    || next.sessionId !== current.sessionId
    || next.runId !== current.runId;
  if (!changed) return;
  dispatch({ type: 'selection/open', selection: next });
}

// ----------------------------------------------------------------------
// 数据加载 (带加载代号; 迟到响应丢弃)
// ----------------------------------------------------------------------
export interface LoadDeps {
  fetchJson?: (url: string) => Promise<any>;
  onNotice?: (text: string) => void;
}

async function defaultFetchJson(url: string): Promise<any> {
  // 统一走 HTTP 客户端: URL/错误/取消/重试语义只在一处 (§9.5)。
  // 延迟取单例, 这样用例替换 `fetchImpl` 之后仍然生效。
  const { client } = await import('./api/research-client');

  return client().json(url);
}

const ROLE_KEYS: Array<keyof TeamRoleRow> = [
  'agent', 'label', 'objective', 'deliverables', 'available', 'unavailable',
  'mayRequestDowngrade',
];

function toRoleRow(raw: any): TeamRoleRow {
  const row: any = {};
  for (const key of ROLE_KEYS) row[key] = raw?.[key];
  return {
    agent: String(row.agent ?? ''),
    label: String(row.label ?? row.agent ?? ''),
    objective: String(row.objective ?? ''),
    deliverables: Array.isArray(row.deliverables) ? row.deliverables : [],
    available: Array.isArray(row.available) ? row.available : [],
    unavailable: Array.isArray(row.unavailable) ? row.unavailable : [],
    mayRequestDowngrade: Boolean(row.mayRequestDowngrade),
  };
}

/** 读取团队角色与真实可用能力 (只读)。 */
export async function loadRoles(deps: LoadDeps = {}): Promise<TeamRoleRow[]> {
  const fetchJson = deps.fetchJson ?? defaultFetchJson;
  const data = await fetchJson(TEAM_API.roles);
  const roles = (data?.roles ?? []).map(toRoleRow);
  dispatch({ type: 'entities/roles', roles });
  rolesLoaded = true;
  return roles;
}

export function rolesAreLoaded(): boolean {
  return rolesLoaded;
}

/**
 * 读取某 run 的任务投影并合并进状态。
 *
 * **只读**: 这个函数不会启动/恢复任何任务 —— 计划书要求"查看不触发执行"。
 * 返回 `false` 表示迟到的响应被丢弃 (身份已经切走)。
 */
export async function loadRunProjection(
  projectId: string, runId: string, deps: LoadDeps = {},
): Promise<boolean> {
  const token = store.ui.loadToken;
  const fetchJson = deps.fetchJson ?? defaultFetchJson;
  let data: any;
  try {
    data = await fetchJson(TEAM_API.projection(projectId, runId));
  } catch (error) {
    if (token !== store.ui.loadToken) return false;
    dispatch({ type: 'transport/status', status: 'offline', error: String(error) });
    deps.onNotice?.(`读取团队状态失败: ${String(error)}`);
    return false;
  }
  // 加载期间身份被切走 -> 丢弃这份迟到结果 (§9.3)
  if (token !== store.ui.loadToken
      || store.selection.projectId !== projectId
      || store.selection.runId !== runId) {
    return false;
  }
  const tasks = (data?.tasks ?? []).map((raw: any) => ({
    taskId: String(raw.task_id ?? raw.taskId ?? ''),
    agent: String(raw.agent ?? ''),
    objective: String(raw.objective ?? ''),
    subquestion: String(raw.subquestion ?? ''),
    expectedGain: String(raw.expected_gain ?? raw.expectedGain ?? ''),
    status: String(raw.status ?? 'unknown'),
    outcome: String(raw.outcome ?? ''),
    summary: String(raw.summary ?? ''),
    failureReason: String(raw.failure_reason ?? raw.failureReason ?? ''),
    dependsOn: Array.isArray(raw.depends_on) ? raw.depends_on
      : (Array.isArray(raw.dependsOn) ? raw.dependsOn : []),
    attempt: Number(raw.attempt ?? 1),
    needsHuman: Boolean(raw.needs_human ?? raw.needsHuman ?? false),
    planVersion: Number(raw.plan_version ?? raw.planVersion ?? 1),
  })).filter((task: any) => task.taskId);
  dispatch({ type: 'entities/tasks', runId, tasks });
  dispatch({
    type: 'entities/objectCounts', runId,
    counts: (data?.objects && typeof data.objects === 'object') ? data.objects : {},
  });
  for (const raw of data?.decisions ?? []) {
    dispatch({
      type: 'entities/decision', runId,
      decision: {
        round: Number(raw?.round ?? 0),
        decision: String(raw?.decision ?? ''),
        reason: String(raw?.reason ?? ''),
        note: String(raw?.note ?? ''),
        taskIds: Array.isArray(raw?.tasks) ? raw.tasks : [],
      },
    });
  }
  dispatch({ type: 'transport/status', status: 'online' });
  return true;
}

// ----------------------------------------------------------------------
// 渲染
// ----------------------------------------------------------------------
/** 需要重新渲染的面板 (供 controller 调用; 未传时渲染全部)。 */
export function renderTeamSection(targets?: string[]): string {
  if (targets && targets.length && !targets.includes('team')
      && !targets.includes('timeline') && !targets.includes('review')) {
    return '';
  }
  return renderTeamBoard(store);
}

/** 事件接入: 把后端事件交给状态层 (纯函数, 不改 DOM)。 */
export function applyTeamEvent(event: TeamEvent): EventEffect {
  const effect = applyEvent(store, event);
  store = effect.store;
  return effect;
}

export function resetTeamStore(): void {
  store = emptyStore();
  rolesLoaded = false;
}

// ----------------------------------------------------------------------
// 页面接入 (§9.5: 把团队区块挂进 `#workbench`; `app.ts` 只调用)
// ----------------------------------------------------------------------
export interface TeamMountDeps {
  /** 只读兼容层状态 (`window.AIR.research`), 只用于**单向**填 selection。 */
  legacyResearch(): { projectId?: string; problemId?: string; sessionId?: string;
                      threadId?: string; runId?: string } | null;
  /** 当前研究问题 id (selection 的一部分)。 */
  problemId(): string;
  /** 用户可见提示 (读取失败不阻断旧工作台)。 */
  notify(text: string, cls?: string): void;
  /** 更新连接/加载状态 (状态条)。 */
  status?(state: 'loading' | 'ready' | 'failed'): void;
}

export interface TeamMount {
  /** 加载团队角色 + 该 run 的投影, 并把区块插到 `#workbench` 最前。 */
  mount(projectId: string, runId: string): Promise<void>;
  /** 只看团队角色与真实能力 (页面打开即可用, 不查不存在的投影)。 */
  rolesOnly(): Promise<void>;
}

/**
 * 团队区块挂载 (合并计划 §9.2)。
 *
 * 与旧工作台的关系: 团队视图插在 `#workbench` 容器的**前面** —— 先给"主控如何理解
 * 问题、派了哪些任务、谁在跑", 再给旧工作台的"研究对象与产物"。旧流程如实标注为
 * 兼容层, 不伪装成团队任务。
 */
export function createTeamMount(deps: TeamMountDeps): TeamMount {
  async function ensureRoles() {
    if (rolesLoaded) return;
    try {
      await loadRoles();
    } catch (error) {
      // 读取团队能力失败不阻断旧工作台: 如实提示即可
      deps.notify('读取团队能力失败: ' + String(error), 'msg-error');
    }
  }

  function insertSection(host: HTMLElement) {
    const section = document.createElement('div');
    section.id = 'team-section';
    section.className = 'team-section';
    section.innerHTML = renderTeamSection();
    // 保留既有锚点: 团队视图永远在旧工作台之前, 因此插在容器**最前**
    const existing = document.getElementById('team-section');
    if (existing && existing.parentNode === host) existing.remove();
    host.insertBefore(section, host.firstChild);
  }

  async function rolesOnly() {
    const host = document.getElementById('workbench');
    if (!host) return;
    deps.status?.('loading');
    await ensureRoles();
    insertSection(host);
    deps.status?.('ready');
  }

  async function mount(projectId: string, runId: string) {
    const host = document.getElementById('workbench');
    if (!host) return;
    if (!projectId && !runId) {
      // 还没有运行: 只显示团队角色与真实能力, 不去查不存在的投影
      await rolesOnly();
      return;
    }
    deps.status?.('loading');
    await ensureRoles();
    const legacy = deps.legacyResearch();
    syncSelectionFromLegacy({
      projectId,
      problemId: deps.problemId(),
      sessionId: (legacy && legacy.sessionId) || '',
      threadId: (legacy && legacy.threadId) || '',
      runId: runId || (legacy && legacy.runId) || '',
    });
    const target = String(runId || (legacy && legacy.runId) || '');
    if (projectId && target) {
      try {
        await loadRunProjection(projectId, target);
      } catch (error) {
        deps.notify('读取团队任务失败: ' + String(error), 'msg-error');
      }
    }
    insertSection(host);
    deps.status?.('ready');
  }

  return { mount, rolesOnly };
}

