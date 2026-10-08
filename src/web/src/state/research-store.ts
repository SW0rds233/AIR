/**
 * 前端唯一可写状态。
 *
 * 解决的问题 (G19: "前端仍同步多份状态")
 * -------------------------------------
 * 早期实现同时存在 `window.AIR.research` (current-research.ts 的状态机)、
 * `app.ts::currentResearch` 与一组 ID 镜像 (`currentProjectId`/`currentProblemId`/
 * `currentThreadId`/`currentSessionId`/`currentContext`/`mode`)、以及
 * `team-controller.ts` 里另一份 reducer store。三者并行时, 只要有**一条**路径忘了同步,
 * 界面就会出现"标题/任务/稿件属于上一个会话"这类串号问题 —— 那正是
 * `syncSelectionFromLegacy()` 存在的理由, 也正说明新 store 不是唯一权威。
 *
 * 现在只有这一份状态, 按**归属**切成四片:
 *   - `selection`: 当前 project/problem/session/run 身份、服务端返回的引擎标识、后台运行
 *     状态、当前查看的对象与标签页; 身份切换时整体替换;
 *   - `entities`: 按 ID/版本保存的任务、决策、角色、对象计数与审阅问题 (分 run 缓存);
 *   - `transport`: 连接状态与**每会话**事件游标 —— **与后台运行状态分开**
 *     (计划书明确要求: "离线不等于后台已停止");
 *   - `ui`: 标签筛选、展开项、未发送草稿、主题偏好 (=资料库主题)、暂停点/候选
 *     回显、草稿项目身份与加载代号; **不能回写命题状态**。
 *
 * 写路径只有一条: `dispatch(action)` → `reduce` → 通知订阅者。
 * 读路径只有一条: `getState()` / 导出 selector (视图不得自己再算一套)。
 * 兼容对象 (`window.AIR.research`) 只是这里的**只读投影**, 见 `current-research.ts`。
 *
 * `reduce`/selector 仍是纯函数 + 不可变数据 (不访问 `document`/`window`/网络),
 * 因此可以直接在测试里驱动; 进程级单例是模块内的一层薄封装 (与
 * `api/research-client.ts` 的 `client()`/`resetClient()` 同一模式)。
 */

// ----------------------------------------------------------------------
// 类型
// ----------------------------------------------------------------------
// **类型**导入 (构建时被擦除, 不产生运行期依赖): 工作台投影的形状定义在
// `views/research-workbench.ts`, 而"最近一次投影"这份服务端结论属于 `entities`。
// 把 WorkbenchData 移到 `contracts/` 是另一件事 (§8.1 item 5 的后续切片)。
import type { WorkbenchData } from '../views/research-workbench';

export type TransportStatus = 'offline' | 'connecting' | 'online' | 'reconnecting';

/** 后台运行状态。**与连接状态分开** —— 断线不代表后台停了。
 *
 * 取值来自传输契约 (`contracts.ts`), 这里只补一个前端专有的 `queued`
 * (团队任务已排入但后端运行状态还没进入 `running`)。**不再另写一套取值** ——
 * 三份 `RunStatus` 曾经各自少一两个状态, 于是"已停止/出错"在部分视图里不存在。
 */
export type RunStatus = import('../contracts/session').RunStatus | 'queued';

/** 任务状态 (与后端 `TaskStatus` 一致)。 */
export type TaskStatus =
  | 'queued' | 'running' | 'waiting' | 'completed' | 'partial' | 'failed' | 'cancelled';

export type TeamRole =
  | 'supervisor' | 'evidence' | 'modeling' | 'reasoning'
  | 'validation' | 'writing' | 'figures' | 'review';

/** 输出区标签页 (= 模板里的 `data-tab`, 不再另立一套名字)。 */
export type TabName = 'preview' | 'workbench' | 'library' | 'paper' | 'files';

export interface Selection {
  projectId: string;
  problemId: string;
  sessionId: string;
  threadId: string;
  runId: string;
  /**
   * 服务端返回的引擎标识 (`theory` / `survey` / ...), **只用于显示**。
   *
   * 刻意声明为 `string` 而不是字面量联合: 前端不再理解它的取值含义, 也就不再有
   * `mode === 'theory'` 这类界面分支。
   */
  mode: string;
  /**
   * 当前 run 的后台状态 (idle/running/waiting/done/stopped/error)。
   *
   * 与 `transport.status` 是**两件事**: 断线时这里仍是"运行中"。
   */
  runStatus: RunStatus;
  /** 当前在详情面板里查看的对象 (`kind:id`)。 */
  objectKey: string;
  /** 当前标签页。 */
  tab: TabName;
}

export interface TeamTaskRow {
  taskId: string;
  agent: TeamRole | string;
  objective: string;
  subquestion: string;
  expectedGain: string;
  status: TaskStatus | string;
  outcome: string;
  summary: string;
  failureReason: string;
  dependsOn: string[];
  attempt: number;
  needsHuman: boolean;
  planVersion: number;
}

export interface TeamRoleRow {
  agent: TeamRole | string;
  label: string;
  objective: string;
  deliverables: string[];
  available: string[];
  unavailable: Array<{ capability: string; reason: string }>;
  mayRequestDowngrade: boolean;
}

export interface DecisionRow {
  round: number;
  decision: string;
  reason: string;
  note: string;
  taskIds: string[];
}

export interface Entities {
  /** `runId -> taskId -> row` (不同 run 分区缓存, 不互相覆盖)。 */
  tasksByRun: Record<string, Record<string, TeamTaskRow>>;
  roles: TeamRoleRow[];
  /** 角色能力是否已从服务端读过 (**不靠 `roles.length` 推断**: 空列表也是有效结果)。 */
  rolesLoaded: boolean;
  /** `runId -> 决策序列`。 */
  decisionsByRun: Record<string, DecisionRow[]>;
  /** `runId -> {kind: count}` —— 只放数量, 对象正文按需取。 */
  objectCountsByRun: Record<string, Record<string, number>>;
  /** 已记录的问题账本 (`issueId -> 摘要`)。 */
  issues: Record<string, { issueId: string; severity: string; category: string; summary: string; blocking: boolean }>;
  /**
   * 最近一次服务端工作台投影 (只读结论)。
   *
   * 放在这里而不是 `app.ts` 的局部变量: 工作台**不自己持有工作对象**
   * (§8.1 item 4/5), 结论详情导航也按这份投影渲染。
   */
  workbench: WorkbenchData | null;
}

export interface Transport {
  status: TransportStatus;
  /** 每个会话自己的游标 (**不是全局游标**)。 */
  cursorBySession: Record<string, number>;
  lastError: string;
  /** 重连尝试次数 (有上限退避)。 */
  reconnectAttempts: number;
  /** 服务端已终止 -> 不再重连。 */
  terminated: boolean;
  /** 发现事件缺口 -> 需要重新加载投影。 */
  needsResync: boolean;
}

export interface UiState {
  expanded: Record<string, boolean>;
  filterAgent: TeamRole | '' ;
  filterTaskStatus: TaskStatus | '';
  /** 未发送的输入草稿 (按会话保存, 刷新后可恢复)。 */
  draftBySession: Record<string, string>;
  /** 已应用的最高加载代号: 迟到响应据此丢弃。 */
  loadToken: number;
  notice: string;
  /** 资料库/主题偏好 —— 与项目 id **不是**同一个字段, 切换会话不得覆盖它。 */
  contextTopic: string;
  /** 当前等待答复的暂停点 ID (F2 幂等键)。 */
  pendingInterruptId: string;
  /** 用户点选的候选路线 ID (仅用于卡片回显)。 */
  candidateId: string;
  /** 草稿项目身份 (还没有研究记录的项目)。 */
  draftProjectId: string;
  /** 已确认建立过运行的项目 id (空 = 尚未确认)。 */
  boundProjectId: string;
}

export interface ResearchStore {
  selection: Selection;
  entities: Entities;
  transport: Transport;
  ui: UiState;
}

// ----------------------------------------------------------------------
// 初始状态
// ----------------------------------------------------------------------
export function emptySelection(): Selection {
  return {
    projectId: '', problemId: '', sessionId: '', threadId: '', runId: '',
    mode: '', runStatus: 'idle', objectKey: '', tab: 'preview',
  };
}

export function emptyEntities(): Entities {
  return {
    tasksByRun: {}, roles: [], rolesLoaded: false,
    decisionsByRun: {}, objectCountsByRun: {}, issues: {}, workbench: null,
  };
}

export function emptyUi(): UiState {
  return {
    expanded: {}, filterAgent: '', filterTaskStatus: '', draftBySession: {},
    loadToken: 0, notice: '', contextTopic: '', pendingInterruptId: '', candidateId: '',
    draftProjectId: '', boundProjectId: '',
  };
}

export function emptyTransport(): Transport {
  return {
    status: 'offline', cursorBySession: {}, lastError: '',
    reconnectAttempts: 0, terminated: false, needsResync: false,
  };
}

export function emptyStore(): ResearchStore {
  return {
    selection: emptySelection(),
    entities: emptyEntities(),
    transport: emptyTransport(),
    ui: emptyUi(),
  };
}

/** 生成新的草稿项目 id (R2: 让附件在稳定身份下上传)。 */
export function newDraftProjectId(): string {
  return 'proj-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// ----------------------------------------------------------------------
// 动作
// ----------------------------------------------------------------------
/** 身份字段: 整体切换 (`selection/open`) 与逐字段更新 (`selection/patch`) 都作用于它们。 */
export const IDENTITY_KEYS: Array<keyof Selection> = [
  'projectId', 'problemId', 'sessionId', 'threadId', 'runId',
];

export type Action =
  /** 整体切换身份: **清掉**查看中的对象并推进加载代号 (旧 run 的任务/结论不得留下)。 */
  | { type: 'selection/open'; selection: Partial<Selection>; sessionKey?: string }
  /** 逐字段更新 (不做整体切换): 用于服务端解析出的问题 id、线程 id 等回写。 */
  | { type: 'selection/patch'; patch: Partial<Selection> }
  /** 后台运行状态 (与连接状态分开)。 */
  | { type: 'selection/status'; status: RunStatus }
  /** 新会话: 清空全部身份与运行状态 (保留引擎标识与主题偏好)。 */
  | { type: 'selection/reset' }
  | { type: 'selection/clearObjects' }
  | { type: 'selection/object'; objectKey: string }
  | { type: 'selection/tab'; tab: TabName }
  | { type: 'transport/status'; status: TransportStatus; error?: string }
  | { type: 'transport/cursor'; sessionId: string; seq: number }
  | { type: 'transport/reconnectAttempt' }
  | { type: 'transport/terminated'; reason?: string }
  | { type: 'transport/needsResync'; needs: boolean }
  | { type: 'transport/reset' }
  | { type: 'entities/roles'; roles: TeamRoleRow[] }
  | { type: 'entities/task'; runId: string; task: TeamTaskRow }
  | { type: 'entities/tasks'; runId: string; tasks: TeamTaskRow[] }
  | { type: 'entities/decision'; runId: string; decision: DecisionRow }
  | { type: 'entities/objectCounts'; runId: string; counts: Record<string, number> }
  | { type: 'entities/issue'; issue: Entities['issues'][string] }
  /** 服务端工作台投影 (只读结论) —— 工作台不自己持有工作对象。 */
  | { type: 'entities/workbench'; data: WorkbenchData }
  /** 会话级整体重置 (团队视图/事件投影): 会话切换后不得显示上一个 run 的任务。 */
  | { type: 'entities/clear' }
  | { type: 'ui/loadStarted' }
  | { type: 'ui/notice'; notice: string }
  | { type: 'ui/toggle'; key: string }
  | { type: 'ui/filter'; agent?: TeamRole | ''; taskStatus?: TaskStatus | '' }
  | { type: 'ui/draft'; sessionId: string; text: string }
  | { type: 'ui/context'; topic: string }
  | { type: 'ui/interrupt'; interruptId: string }
  | { type: 'ui/candidate'; candidateId: string }
  | { type: 'ui/draftProject'; projectId: string }
  | { type: 'ui/boundProject'; projectId: string }
  /**
   * 把**纯函数层**算出的新状态整体装入唯一 store。
   *
   * 用途: 事件消费 (`events/session-events.ts::applyEvent`) 是纯函数, 它返回新状态而不是
   * 自己持有状态; 页面把结果装回来时走这个动作, 而不是再留一个模块级 store 变量。
   */
  | { type: 'state/replace'; state: ResearchStore };

/**
 * 把关身份字段 (把 `undefined`/`null` 归一化为空串)。
 *
 * 判据是 `key in patch` 而不是"值不为 undefined": 显式传 `{problemId: undefined}`
 * 表示"要清空这个字段", 与"没提及"不是一回事 (旧 `applyPatch` 就是这个语义)。
 */
function identityPatch(patch: Partial<Selection>): Partial<Selection> {
  const next: Record<string, unknown> = {};
  for (const key of IDENTITY_KEYS) {
    if (!(key in patch)) continue;
    next[key] = String(patch[key] ?? '') || '';
  }
  if ('mode' in patch) next.mode = String(patch.mode ?? '') || '';
  if ('runStatus' in patch) next.runStatus = (patch.runStatus as RunStatus) || 'idle';
  if ('objectKey' in patch) next.objectKey = String(patch.objectKey ?? '') || '';
  if ('tab' in patch && patch.tab) next.tab = patch.tab;
  return next as Partial<Selection>;
}

// ----------------------------------------------------------------------
// Reducer
// ----------------------------------------------------------------------
export function reduce(state: ResearchStore, action: Action): ResearchStore {
  switch (action.type) {
    case 'selection/open': {
      // 整体替换身份: **不**保留旧 run 的对象 (否则会把上一个研究的问题显示成当前的)
      const selection: Selection = {
        ...state.selection,
        ...identityPatch(action.selection),
        objectKey: action.selection.objectKey ?? '',
      };
      const ui: UiState = { ...state.ui, loadToken: state.ui.loadToken + 1, notice: '' };
      return { ...state, selection, ui };
    }
    case 'selection/patch': {
      const patch = identityPatch(action.patch);
      if (!Object.keys(patch).length) return state;
      return { ...state, selection: { ...state.selection, ...patch } };
    }
    case 'selection/status': {
      if (action.status === state.selection.runStatus) return state;
      return { ...state, selection: { ...state.selection, runStatus: action.status } };
    }
    case 'selection/reset':
      // 新会话必须清空旧项目/问题与运行绑定 (否则新研究会沿用上一个问题),
      // 但引擎标识只用于显示、主题偏好属于资料库选择: 都不清。
      return {
        ...state,
        selection: {
          ...state.selection,
          ...identityPatch({ projectId: '', problemId: '', sessionId: '', threadId: '', runId: '' }),
          runStatus: 'idle',
          objectKey: '',
        },
        ui: { ...state.ui, loadToken: state.ui.loadToken + 1, notice: '' },
      };
    case 'selection/clearObjects':
      return { ...state, selection: { ...state.selection, objectKey: '' } };
    case 'selection/object':
      return { ...state, selection: { ...state.selection, objectKey: action.objectKey } };
    case 'selection/tab':
      return { ...state, selection: { ...state.selection, tab: action.tab } };
    case 'transport/status':
      return {
        ...state,
        transport: {
          ...state.transport,
          status: action.status,
          lastError: action.error ?? (action.status === 'online' ? '' : state.transport.lastError),
          reconnectAttempts: action.status === 'online' ? 0 : state.transport.reconnectAttempts,
        },
      };
    case 'transport/cursor': {
      const current = state.transport.cursorBySession[action.sessionId] ?? 0;
      if (action.seq <= current) return state;         // 去重: 游标只前进
      return {
        ...state,
        transport: {
          ...state.transport,
          cursorBySession: { ...state.transport.cursorBySession, [action.sessionId]: action.seq },
        },
      };
    }
    case 'transport/reconnectAttempt':
      return { ...state, transport: { ...state.transport, reconnectAttempts: state.transport.reconnectAttempts + 1 } };
    case 'transport/terminated':
      return {
        ...state,
        transport: { ...state.transport, terminated: true, status: 'offline', lastError: action.reason ?? '' },
      };
    case 'transport/needsResync':
      return { ...state, transport: { ...state.transport, needsResync: action.needs } };
    case 'transport/reset':
      return { ...state, transport: emptyTransport() };
    case 'entities/roles':
      return { ...state, entities: { ...state.entities, roles: action.roles, rolesLoaded: true } };
    case 'entities/task': {
      const bucket = state.entities.tasksByRun[action.runId] ?? {};
      return {
        ...state,
        entities: {
          ...state.entities,
          tasksByRun: {
            ...state.entities.tasksByRun,
            [action.runId]: { ...bucket, [action.task.taskId]: action.task },
          },
        },
      };
    }
    case 'entities/tasks': {
      const bucket: Record<string, TeamTaskRow> = {};
      for (const task of action.tasks) bucket[task.taskId] = task;
      return {
        ...state,
        entities: {
          ...state.entities,
          tasksByRun: { ...state.entities.tasksByRun, [action.runId]: bucket },
        },
      };
    }
    case 'entities/decision': {
      const rows = state.entities.decisionsByRun[action.runId] ?? [];
      return {
        ...state,
        entities: {
          ...state.entities,
          decisionsByRun: {
            ...state.entities.decisionsByRun,
            [action.runId]: [...rows, action.decision],
          },
        },
      };
    }
    case 'entities/objectCounts':
      return {
        ...state,
        entities: {
          ...state.entities,
          objectCountsByRun: { ...state.entities.objectCountsByRun, [action.runId]: action.counts },
        },
      };
    case 'entities/issue':
      return {
        ...state,
        entities: { ...state.entities, issues: { ...state.entities.issues, [action.issue.issueId]: action.issue } },
      };
    case 'entities/workbench':
      return { ...state, entities: { ...state.entities, workbench: action.data } };
    case 'entities/clear':
      return { ...state, entities: emptyEntities() };
    case 'ui/loadStarted':
      return { ...state, ui: { ...state.ui, loadToken: state.ui.loadToken + 1 } };
    case 'ui/notice':
      if (action.notice === state.ui.notice) return state;
      return { ...state, ui: { ...state.ui, notice: action.notice } };
    case 'ui/toggle':
      return { ...state, ui: { ...state.ui, expanded: { ...state.ui.expanded, [action.key]: !state.ui.expanded[action.key] } } };
    case 'ui/filter':
      return {
        ...state,
        ui: {
          ...state.ui,
          filterAgent: action.agent ?? state.ui.filterAgent,
          filterTaskStatus: action.taskStatus ?? state.ui.filterTaskStatus,
        },
      };
    case 'ui/draft':
      return {
        ...state,
        ui: { ...state.ui, draftBySession: { ...state.ui.draftBySession, [action.sessionId]: action.text } },
      };
    case 'ui/context': {
      const topic = String(action.topic || '');
      if (topic === state.ui.contextTopic) return state;
      return { ...state, ui: { ...state.ui, contextTopic: topic } };
    }
    case 'ui/interrupt': {
      const interruptId = String(action.interruptId || '');
      if (interruptId === state.ui.pendingInterruptId) return state;
      return { ...state, ui: { ...state.ui, pendingInterruptId: interruptId } };
    }
    case 'ui/candidate': {
      const candidateId = String(action.candidateId || '');
      if (candidateId === state.ui.candidateId) return state;
      return { ...state, ui: { ...state.ui, candidateId } };
    }
    case 'ui/draftProject': {
      // 草稿身份**不是**运行绑定: 必须显式记下来, 否则刷新工作台只能靠
      // "项目 id 恰好不等于状态里的项目 id"这条间接判据。
      const projectId = String(action.projectId || '');
      if (projectId === state.ui.draftProjectId && !state.ui.boundProjectId) return state;
      return { ...state, ui: { ...state.ui, draftProjectId: projectId, boundProjectId: '' } };
    }
    case 'ui/boundProject': {
      const projectId = String(action.projectId || '');
      if (projectId === state.ui.boundProjectId && !state.ui.draftProjectId) return state;
      return { ...state, ui: { ...state.ui, boundProjectId: projectId, draftProjectId: '' } };
    }
    case 'state/replace':
      return action.state;
    default:
      return state;
  }
}

// ----------------------------------------------------------------------
// 进程级唯一实例 (§8.1: 页面、控制器、团队视图都读写**这一份**)
// ----------------------------------------------------------------------
let current: ResearchStore = emptyStore();
const listeners = new Set<(state: ResearchStore) => void>();

/** 唯一可写状态 (只读使用; 写入必须经 `dispatch`)。 */
export function getState(): ResearchStore {
  return current;
}

/** 唯一写入口: `reduce` + 通知 (状态未变化时不通知)。 */
export function dispatch(action: Action): ResearchStore {
  const next = reduce(current, action);
  if (next === current) return current;
  current = next;
  for (const listener of Array.from(listeners)) listener(current);
  return current;
}

/** 订阅状态变化 (视图只需重渲染受影响的部分)。 */
export function subscribe(listener: (state: ResearchStore) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** 供测试重置单例 (与 `api/research-client.ts::resetClient` 同一模式)。 */
export function resetStore(): ResearchStore {
  current = emptyStore();
  for (const listener of Array.from(listeners)) listener(current);
  return current;
}

/**
 * 身份**整体切换**, 但只有身份真的变了才切。
 *
 * 为什么需要它: 每次刷新工作台/重挂团队视图都会把 (project, run) 交给 store; 若无条件
 * 走 `selection/open`, 每次都会清掉"当前查看的对象"并把加载代号推进一格 —— 于是用户
 * 点开的结论详情会被一次普通刷新关掉。
 */
export function switchSelection(selection: Partial<Selection>): ResearchStore {
  const currentSelection = current.selection;
  const changed = IDENTITY_KEYS.some((key) => key in selection
    && String(selection[key] ?? '') !== String(currentSelection[key] ?? ''));
  if (!changed) return current;
  return dispatch({ type: 'selection/open', selection });
}

/** 会话级整体重置: 身份 + 团队投影 + 连接状态一起清 (新会话/删除会话)。 */
export function resetSession(): ResearchStore {
  dispatch({ type: 'selection/reset' });
  dispatch({ type: 'entities/clear' });
  dispatch({ type: 'transport/reset' });
  return current;
}

// ----------------------------------------------------------------------
// Selectors (只读派生; 视图不得自己再算一套)
// ----------------------------------------------------------------------
export const ROLE_LABEL: Record<string, string> = {
  supervisor: '主控',
  evidence: '检索与证据整理',
  modeling: '问题建模',
  reasoning: '推理与结论综合',
  validation: '验证方案',
  writing: '写作',
  figures: '配图',
  review: '独立审阅',
};

export const TASK_STATUS_LABEL: Record<string, string> = {
  queued: '待派', running: '执行中', waiting: '等依赖/等用户',
  completed: '完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
};

/** 后台运行状态的中文标签 (纯显示; 未识别的取值如实返回原文)。 */
export const STATUS_LABEL: Record<string, string> = {
  idle: '就绪',
  running: '运行中',
  waiting: '等待输入',
  done: '已完成',
  stopped: '已停止',
  error: '出错',
};

/** 后台运行状态与连接状态**分开**展示: 离线 ≠ 已停止。 */
export function connectionLabel(transport: Transport): string {
  switch (transport.status) {
    case 'online': return '已连接';
    case 'connecting': return '连接中';
    case 'reconnecting': return `重连中 (第 ${transport.reconnectAttempts} 次)`;
    default: return transport.terminated ? '已离线 (运行已终止)' : '离线 (后台状态未知)';
  }
}

export function currentRunId(state: ResearchStore): string {
  return state.selection.runId;
}

/**
 * 输入区的"阶段" (原来 `app.ts` 另存一份 `mode` 变量)。
 *
 * 由唯一的运行状态派生, 不再有第二个可写副本: 运行中/已结束都**不接受**新输入,
 * 只有 `idle` 可以启动、`waiting` 可以答复暂停点。
 */
export function inputPhase(state: ResearchStore): 'idle' | 'running' | 'waiting' {
  switch (state.selection.runStatus) {
    case 'idle': return 'idle';
    case 'waiting': return 'waiting';
    default: return 'running';
  }
}

/** 资料库/主题偏好 (与项目 id 分开保存, 互不覆盖)。 */
export function contextTopic(state: ResearchStore): string {
  return state.ui.contextTopic;
}

/**
 * 工作台可查询的目标项目 id (空串 = 不要查询)。
 *
 * 草稿身份 (还没有运行记录) 不查 —— 否则每次打开页面都会打一个注定 404 的请求。
 */
export function workbenchQueryTarget(state: ResearchStore, projectId: string): string {
  const pid = String(projectId || '');
  if (!pid) return '';
  if (!state.ui.boundProjectId && pid === state.ui.draftProjectId) return '';
  return pid;
}

/** 最近一次服务端工作台投影 (只读; 结论详情导航按它渲染)。 */
export function workbenchFrom(state: ResearchStore): WorkbenchData | null {
  return state.entities.workbench;
}

export function tasksForCurrentRun(state: ResearchStore): TeamTaskRow[] {
  const runId = currentRunId(state);
  if (!runId) return [];
  return Object.values(state.entities.tasksByRun[runId] ?? {});
}

/** 团队状态条的投影: 一个角色一行 (同一角色多个任务显示**任务数**而非重复头像)。 */
export interface RoleStatus {  agent: string;
  label: string;
  total: number;
  byStatus: Record<string, number>;
  running: number;
  failed: number;
  waiting: number;
  /** 状态条上的显示文本 (不靠颜色区分, 同时给文字)。 */
  text: string;
}

export function roleStatuses(state: ResearchStore): RoleStatus[] {
  const tasks = tasksForCurrentRun(state);
  const order: string[] = ['supervisor', 'evidence', 'modeling', 'reasoning',
    'validation', 'writing', 'figures', 'review'];
  const known = new Set(state.entities.roles.map((r) => String(r.agent)));
  const agents = order.filter((a) => known.size === 0 || known.has(a));
  const extra = tasks.map((t) => String(t.agent)).filter((a) => !agents.includes(a));
  const all = [...agents, ...Array.from(new Set(extra))];

  return all.map((agent) => {
    const rows = tasks.filter((t) => String(t.agent) === agent);
    const byStatus: Record<string, number> = {};
    for (const row of rows) {
      const key = String(row.status || 'unknown');
      byStatus[key] = (byStatus[key] ?? 0) + 1;
    }
    const running = (byStatus.running ?? 0);
    const failed = (byStatus.failed ?? 0) + (byStatus.cancelled ?? 0);
    const waiting = (byStatus.waiting ?? 0) + (byStatus.queued ?? 0);
    let text = '待命';
    if (running) text = `执行中 ×${running}`;
    else if (failed) text = `失败 ×${failed}`;
    else if (waiting) text = `等待 ×${waiting}`;
    else if (rows.length) text = `完成 ×${rows.length}`;
    return {
      agent,
      label: ROLE_LABEL[agent] ?? agent,
      total: rows.length,
      byStatus,
      running,
      failed,
      waiting,
      text,
    };
  });
}

/** 团队决策摘要 (主控对研究问题的理解与当前目标)。 */
export function latestDecision(state: ResearchStore): DecisionRow | null {
  const runId = currentRunId(state);
  const rows = state.entities.decisionsByRun[runId] ?? [];
  return rows.length ? rows[rows.length - 1] : null;
}

export function visibleTasks(state: ResearchStore): TeamTaskRow[] {
  const { filterAgent, filterTaskStatus } = state.ui;
  return tasksForCurrentRun(state).filter((task) => {
    if (filterAgent && String(task.agent) !== filterAgent) return false;
    if (filterTaskStatus && String(task.status) !== filterTaskStatus) return false;
    return true;
  });
}

/** 阻断交付的问题 (审阅 blocking) —— 界面必须把它排在前面。 */
export function blockingIssues(state: ResearchStore) {
  return Object.values(state.entities.issues).filter((issue) => issue.blocking);
}

/**
 * 是否应当请求"重新加载投影"。
 *
 * 触发条件: 连接层发现事件缺口, 或收到未知事件类型后无法推进游标。
 * 计划书要求: "发现缺口、日志截断或协议不兼容时重新加载投影"。
 */
export function shouldResync(state: ResearchStore, incomingSeq: number, sessionId: string): boolean {
  if (state.transport.needsResync) return true;
  const cursor = state.transport.cursorBySession[sessionId] ?? 0;
  return incomingSeq > cursor + 1;      // 中间有洞
}

export function draftFor(state: ResearchStore, sessionId: string): string {
  return state.ui.draftBySession[sessionId] ?? '';
}
