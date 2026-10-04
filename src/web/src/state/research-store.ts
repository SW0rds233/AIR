/**
 * 前端唯一状态入口 (合并计划 §9.3)。
 *
 * 解决的问题 (合并计划 §9.3 的表格逐条)
 * ----------------------------------
 * 1. 一份状态: 早期实现同时存在 `currentThreadId`/`currentProjectId`/
 *    `lastEventId`/`workbenchData` 与 `window.AIR.research` 两套可写状态, 切换会话时
 *    必然有一边没跟上。这里把状态按**归属**切成四片:
 *      - `selection`: 当前 project/problem/session/run 与所选对象; 切换时整体替换;
 *      - `entities`: 按 ID/版本保存的任务、证据、结论、稿件与审阅结果 (分 run 缓存);
 *      - `transport`: 连接状态与事件游标 —— **与后台运行状态分开**
 *        (计划书明确要求: "离线不等于后台已停止");
 *      - `ui`: 当前标签、筛选、展开项、草稿; **不能回写命题状态**。
 * 2. 迟到响应丢弃: 每次加载带 `loadToken`, 只有 token 最新的响应才被应用
 *    (§9.3: "所有异步响应携带会话/run key 与加载代号, 迟到结果直接丢弃")。
 * 3. 事件去重与缺口检测: 按 `seq` 去重, 发现缺口时要求**重新加载投影**
 *    而不是把缺口当成"已完成"。
 *
 * 本模块是纯函数 + 不可变数据: 不访问 `document`/`window`/网络, 因此可以直接在
 * 测试里驱动 (见 `tests/research-store.test.ts`)。
 */

// ----------------------------------------------------------------------
// 类型
// ----------------------------------------------------------------------
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

export interface Selection {
  projectId: string;
  problemId: string;
  sessionId: string;
  threadId: string;
  runId: string;
  /** 当前在详情面板里查看的对象 (`kind:id`)。 */
  objectKey: string;
  /** 当前标签页。 */
  tab: 'overview' | 'sources' | 'research' | 'paper' | 'review';
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
  /** `runId -> 决策序列`。 */
  decisionsByRun: Record<string, DecisionRow[]>;
  /** `runId -> {kind: count}` —— 只放数量, 对象正文按需取。 */
  objectCountsByRun: Record<string, Record<string, number>>;
  /** 已记录的问题账本 (`issueId -> 摘要`)。 */
  issues: Record<string, { issueId: string; severity: string; category: string; summary: string; blocking: boolean }>;
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
    objectKey: '', tab: 'overview',
  };
}

export function emptyStore(): ResearchStore {
  return {
    selection: emptySelection(),
    entities: { tasksByRun: {}, roles: [], decisionsByRun: {}, objectCountsByRun: {}, issues: {} },
    transport: {
      status: 'offline', cursorBySession: {}, lastError: '',
      reconnectAttempts: 0, terminated: false, needsResync: false,
    },
    ui: { expanded: {}, filterAgent: '', filterTaskStatus: '', draftBySession: {}, loadToken: 0, notice: '' },
  };
}

// ----------------------------------------------------------------------
// 动作
// ----------------------------------------------------------------------
export type Action =
  | { type: 'selection/open'; selection: Partial<Selection>; sessionKey?: string }
  | { type: 'selection/clearObjects' }
  | { type: 'transport/status'; status: TransportStatus; error?: string }
  | { type: 'transport/cursor'; sessionId: string; seq: number }
  | { type: 'transport/reconnectAttempt' }
  | { type: 'transport/terminated'; reason?: string }
  | { type: 'transport/needsResync'; needs: boolean }
  | { type: 'entities/roles'; roles: TeamRoleRow[] }
  | { type: 'entities/task'; runId: string; task: TeamTaskRow }
  | { type: 'entities/tasks'; runId: string; tasks: TeamTaskRow[] }
  | { type: 'entities/decision'; runId: string; decision: DecisionRow }
  | { type: 'entities/objectCounts'; runId: string; counts: Record<string, number> }
  | { type: 'entities/issue'; issue: Entities['issues'][string] }
  | { type: 'ui/loadStarted' }
  | { type: 'ui/notice'; notice: string }
  | { type: 'ui/toggle'; key: string }
  | { type: 'ui/filter'; agent?: TeamRole | ''; taskStatus?: TaskStatus | '' }
  | { type: 'ui/draft'; sessionId: string; text: string };

// ----------------------------------------------------------------------
// Reducer
// ----------------------------------------------------------------------
export function reduce(state: ResearchStore, action: Action): ResearchStore {
  switch (action.type) {
    case 'selection/open': {
      // 整体替换身份: **不**保留旧 run 的对象 (否则会把上一个研究的问题显示成当前的)
      const selection: Selection = {
        ...state.selection,
        ...action.selection,
        objectKey: action.selection.objectKey ?? '',
      };
      const ui: UiState = { ...state.ui, loadToken: state.ui.loadToken + 1, notice: '' };
      return { ...state, selection, ui };
    }
    case 'selection/clearObjects':
      return { ...state, selection: { ...state.selection, objectKey: '' } };
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
    case 'entities/roles':
      return { ...state, entities: { ...state.entities, roles: action.roles } };
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
    case 'ui/loadStarted':
      return { ...state, ui: { ...state.ui, loadToken: state.ui.loadToken + 1 } };
    case 'ui/notice':
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
    default:
      return state;
  }
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

