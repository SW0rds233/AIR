/**
 * 前端状态的用例 (合并计划 §9.3)。
 *
 * 固定的是**归属与不变量**, 而不是某个页面的渲染细节:
 * - 一份状态: selection/entities/transport/ui 各有明确归属, 切换会话整体替换身份;
 * - 连接状态与运行状态分开 (离线 ≠ 后台已停止);
 * - 迟到响应按加载代号丢弃;
 * - 事件按 seq 去重、发现缺口要求重新加载投影 (不把缺口当"完成")。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  blockingIssues,
  connectionLabel,
  dispatch,
  draftFor,
  emptyStore,
  getState,
  inputPhase,
  latestDecision,
  reduce,
  resetSession,
  resetStore,
  roleStatuses,
  shouldResync,
  subscribe,
  switchSelection,
  tasksForCurrentRun,
  visibleTasks,
  workbenchFrom,
  workbenchQueryTarget,
  type ResearchStore,
  type TeamTaskRow,
} from '../src/state/research-store';

function task(over: Partial<TeamTaskRow> = {}): TeamTaskRow {
  return {
    taskId: 't-1', agent: 'evidence', objective: '检索', subquestion: '',
    expectedGain: '', status: 'running', outcome: '', summary: '',
    failureReason: '', dependsOn: [], attempt: 1, needsHuman: false,
    planVersion: 1, ...over,
  };
}

function withRun(store: ResearchStore, runId = 'run-1'): ResearchStore {
  return reduce(store, { type: 'selection/open', selection: { runId, sessionId: 's-1' } });
}

describe('selection', () => {
  it('切换会话时整体替换身份, 并清掉查看中的对象', () => {
    let store = emptyStore();
    store = reduce(store, {
      type: 'selection/open',
      selection: { projectId: 'pA', problemId: 'qA', runId: 'rA', objectKey: 'claim:c1' },
    });
    store = reduce(store, {
      type: 'selection/open',
      selection: { projectId: 'pB', problemId: 'qB', runId: 'rB' },
    });
    expect(store.selection.projectId).toBe('pB');
    expect(store.selection.runId).toBe('rB');
    // 旧对象的选中不能留在新会话上
    expect(store.selection.objectKey).toBe('');
  });

  it('每次打开会话都推进加载代号 (迟到响应据此丢弃)', () => {
    let store = emptyStore();
    const before = store.ui.loadToken;
    store = reduce(store, { type: 'selection/open', selection: { sessionId: 's-2' } });
    expect(store.ui.loadToken).toBe(before + 1);
  });
});

describe('transport', () => {
  it('连接状态与运行状态分开: 离线不表示运行已停止', () => {
    const store = reduce(emptyStore(), { type: 'transport/status', status: 'offline' });
    expect(store.transport.status).toBe('offline');
    expect(store.transport.terminated).toBe(false);
    expect(connectionLabel(store.transport)).toContain('后台状态未知');
  });

  it('终止后显示"运行已终止", 不再重连', () => {
    let store = reduce(emptyStore(), { type: 'transport/status', status: 'online' });
    store = reduce(store, { type: 'transport/terminated', reason: '运行已结束' });
    expect(store.transport.terminated).toBe(true);
    expect(connectionLabel(store.transport)).toContain('已终止');
  });

  it('游标按会话分别记录, 且只前进', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-1', seq: 5 });
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-2', seq: 2 });
    expect(store.transport.cursorBySession['s-1']).toBe(5);
    expect(store.transport.cursorBySession['s-2']).toBe(2);
    // 回退或重复的序号不覆盖
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-1', seq: 3 });
    expect(store.transport.cursorBySession['s-1']).toBe(5);
  });

  it('重连成功后退避计数清零', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'transport/reconnectAttempt' });
    store = reduce(store, { type: 'transport/reconnectAttempt' });
    expect(store.transport.reconnectAttempts).toBe(2);
    store = reduce(store, { type: 'transport/status', status: 'online' });
    expect(store.transport.reconnectAttempts).toBe(0);
  });

  it('发现事件缺口时要求重新同步', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'transport/cursor', sessionId: 's-1', seq: 4 });
    expect(shouldResync(store, 5, 's-1')).toBe(false);
    expect(shouldResync(store, 7, 's-1')).toBe(true);   // 中间少了 6
    store = reduce(store, { type: 'transport/needsResync', needs: true });
    expect(shouldResync(store, 5, 's-1')).toBe(true);
  });
});

describe('entities', () => {
  it('任务按 run 分区缓存, 不同运行不互相覆盖', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/task', runId: 'rA', task: task({ taskId: 'a' }) });
    store = reduce(store, { type: 'entities/task', runId: 'rB', task: task({ taskId: 'b' }) });
    expect(Object.keys(store.entities.tasksByRun['rA'])).toEqual(['a']);
    expect(Object.keys(store.entities.tasksByRun['rB'])).toEqual(['b']);
  });

  it('只显示当前 run 的任务 (不把历史运行的任务混进当前)', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/task', runId: 'rA', task: task({ taskId: 'a' }) });
    store = reduce(store, { type: 'entities/task', runId: 'rB', task: task({ taskId: 'b' }) });
    store = withRun(store, 'rB');
    expect(tasksForCurrentRun(store).map((t) => t.taskId)).toEqual(['b']);
  });

  it('角色状态按任务数聚合, 同一角色多个任务只显示一行', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/tasks', runId: 'r1', tasks: [
      task({ taskId: 'a', agent: 'evidence', status: 'running' }),
      task({ taskId: 'b', agent: 'evidence', status: 'running' }),
      task({ taskId: 'c', agent: 'writing', status: 'completed' }),
    ] });
    store = withRun(store, 'r1');
    const statuses = roleStatuses(store);
    const evidence = statuses.find((s) => s.agent === 'evidence')!;
    expect(evidence.total).toBe(2);
    expect(evidence.running).toBe(2);
    expect(evidence.text).toContain('执行中');
    const writing = statuses.find((s) => s.agent === 'writing')!;
    expect(writing.text).toContain('完成');
    // 没有任务的角色显示"待命", 而不是空行
    const review = statuses.find((s) => s.agent === 'review')!;
    expect(review.text).toBe('待命');
  });

  it('阻断问题被排在最前并单独可查', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/issue', issue: {
      issueId: 'i1', severity: 'minor', category: 'readability',
      summary: '句子太长', blocking: false } });
    store = reduce(store, { type: 'entities/issue', issue: {
      issueId: 'i2', severity: 'blocking', category: 'science',
      summary: '结论无依据', blocking: true } });
    expect(blockingIssues(store).map((i) => i.issueId)).toEqual(['i2']);
  });
});

describe('ui', () => {
  it('筛选只影响显示, 不改动任何对象状态', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/tasks', runId: 'r1', tasks: [
      task({ taskId: 'a', agent: 'evidence' }),
      task({ taskId: 'b', agent: 'writing' }),
    ] });
    store = withRun(store, 'r1');
    expect(visibleTasks(store).length).toBe(2);
    store = reduce(store, { type: 'ui/filter', agent: 'writing' });
    expect(visibleTasks(store).map((t) => t.taskId)).toEqual(['b']);
    // 全部任务仍在状态里 (筛选不是删除)
    expect(tasksForCurrentRun(store).length).toBe(2);
  });

  it('草稿按会话保存, 互不串台', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'ui/draft', sessionId: 's-1', text: '研究 A' });
    store = reduce(store, { type: 'ui/draft', sessionId: 's-2', text: '研究 B' });
    expect(draftFor(store, 's-1')).toBe('研究 A');
    expect(draftFor(store, 's-2')).toBe('研究 B');
    expect(draftFor(store, 's-3')).toBe('');
  });

  it('展开项可切换且可关闭', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'ui/toggle', key: 'task-a' });
    expect(store.ui.expanded['task-a']).toBe(true);
    store = reduce(store, { type: 'ui/toggle', key: 'task-a' });
    expect(store.ui.expanded['task-a']).toBe(false);
  });
});

describe('decisions', () => {
  it('决策按 run 记录, 最新一条作为主控摘要', () => {
    let store = emptyStore();
    store = reduce(store, { type: 'entities/decision', runId: 'r1', decision: {
      round: 1, decision: 'dispatch', reason: '派 3 个子任务', note: '', taskIds: ['a'] } });
    store = reduce(store, { type: 'entities/decision', runId: 'r1', decision: {
      round: 2, decision: 'stop_with_report', reason: '未满足交付形态', note: '缺审阅', taskIds: [] } });
    store = withRun(store, 'r1');
    const latest = latestDecision(store)!;
    expect(latest.round).toBe(2);
    expect(latest.decision).toBe('stop_with_report');
  });
});

/* ---------------------------------------------------------------------
 * G19: 运行状态 / 主题 / 草稿身份 / 加载代号都归唯一状态, 页面不再各存一份
 * ------------------------------------------------------------------- */
describe('后台运行状态 (与连接状态分开)', () => {
  it('运行状态改变不动连接状态, 连接状态改变不动运行状态', () => {
    let store = reduce(emptyStore(), { type: 'selection/status', status: 'running' });
    store = reduce(store, { type: 'transport/status', status: 'offline' });
    // 断线 ≠ 后台已停止: 这正是"离线 (后台状态未知)"与"已停止"必须分开的原因
    expect(store.selection.runStatus).toBe('running');
    expect(store.transport.status).toBe('offline');
    expect(connectionLabel(store.transport)).toContain('后台状态未知');
  });

  it('输入阶段由运行状态派生 (不再有第二个 mode 变量)', () => {
    const at = (status: any) => inputPhase(reduce(emptyStore(),
      { type: 'selection/status', status }));
    expect(at('idle')).toBe('idle');
    expect(at('waiting')).toBe('waiting');
    expect(at('running')).toBe('running');
    // 已结束/出错/已停止都不接受新输入 (与迁移前的本地 mode 语义一致)
    expect(at('done')).toBe('running');
    expect(at('stopped')).toBe('running');
    expect(at('error')).toBe('running');
  });
});

describe('身份整体切换与逐字段回写', () => {
  it('selection/patch 不清掉查看中的对象, 也不推进加载代号', () => {
    let store = reduce(emptyStore(), {
      type: 'selection/open',
      selection: { projectId: 'p1', runId: 'r1', objectKey: 'claim:c1' },
    });
    const token = store.ui.loadToken;
    store = reduce(store, { type: 'selection/patch', patch: { problemId: 'q1' } });
    expect(store.selection.problemId).toBe('q1');
    expect(store.selection.objectKey).toBe('claim:c1');
    expect(store.ui.loadToken).toBe(token);
  });

  it('selection/open 清掉旧对象并推进加载代号 (迟到响应据此丢弃)', () => {
    let store = reduce(emptyStore(), {
      type: 'selection/open',
      selection: { projectId: 'p1', runId: 'r1', objectKey: 'claim:c1' },
    });
    const token = store.ui.loadToken;
    store = reduce(store, { type: 'selection/open', selection: { projectId: 'p2', runId: 'r2' } });
    expect(store.selection.objectKey).toBe('');
    expect(store.ui.loadToken).toBe(token + 1);
  });

  it('selection/reset 清空身份与运行状态, 保留引擎标识与主题偏好', () => {
    let store = reduce(emptyStore(), {
      type: 'selection/open',
      selection: { projectId: 'p1', problemId: 'q1', sessionId: 's1', threadId: 't1',
                   runId: 'r1', mode: 'theory', runStatus: 'running' },
    });
    store = reduce(store, { type: 'ui/context', topic: '库主题' });
    store = reduce(store, { type: 'selection/reset' });
    expect(store.selection).toMatchObject({
      projectId: '', problemId: '', sessionId: '', threadId: '', runId: '',
      mode: 'theory', runStatus: 'idle', objectKey: '',
    });
    expect(store.ui.contextTopic).toBe('库主题');
  });

  it('切换会话不清空主题偏好 (资料库主题与项目 id 是两个字段)', () => {
    let store = reduce(emptyStore(), { type: 'ui/context', topic: '库主题' });
    store = reduce(store, { type: 'selection/open', selection: { projectId: 'pX' } });
    expect(store.ui.contextTopic).toBe('库主题');
  });
});

describe('草稿身份与工作台查询门禁', () => {
  it('草稿身份不查工作台; 换成别的 id 放行; 绑定运行后放行', () => {
    let store = reduce(emptyStore(), { type: 'ui/draftProject', projectId: 'proj-draft' });
    expect(workbenchQueryTarget(store, 'proj-draft')).toBe('');
    expect(workbenchQueryTarget(store, 'proj-other')).toBe('proj-other');
    expect(workbenchQueryTarget(store, '')).toBe('');
    store = reduce(store, { type: 'ui/boundProject', projectId: 'proj-draft' });
    expect(store.ui.draftProjectId).toBe('');
    expect(workbenchQueryTarget(store, 'proj-draft')).toBe('proj-draft');
  });
});

describe('角色能力已读标记', () => {
  it('空角色列表也算"已读" (不能靠 roles.length 推断)', () => {
    let store = emptyStore();
    expect(store.entities.rolesLoaded).toBe(false);
    store = reduce(store, { type: 'entities/roles', roles: [] });
    expect(store.entities.rolesLoaded).toBe(true);
    store = reduce(store, { type: 'entities/clear' });
    expect(store.entities.rolesLoaded).toBe(false);
  });
});

describe('工作台投影 (§8.1: 工作台不自己持有工作对象)', () => {
  it('服务端投影进 entities, 结论详情按它重渲染', () => {
    let store = emptyStore();
    expect(workbenchFrom(store)).toBeNull();
    const projection = {
      project_id: 'p1',
      claims: [{ id: 'clm-1', statement: '结论一', status: 'supported' }],
    };
    store = reduce(store, { type: 'entities/workbench', data: projection as never });
    expect(workbenchFrom(store)).toBe(projection);
    // 换身份时的整体重置会把它一并清掉 (不残留上一个研究的工作台对象)
    store = reduce(store, { type: 'entities/clear' });
    expect(workbenchFrom(store)).toBeNull();
  });
});

/* ---------------------------------------------------------------------
 * 进程级唯一实例 (§8.1): 页面/控制器/团队视图读写同一份状态
 * ------------------------------------------------------------------- */
describe('唯一 store 实例', () => {
  beforeEach(() => { resetStore(); });
  afterEach(() => { resetStore(); });

  it('dispatch 更新 getState 并只通知一次', () => {
    let notified = 0;
    let seen = '';
    const unsubscribe = subscribe((state) => { notified += 1; seen = state.selection.projectId; });
    dispatch({ type: 'selection/patch', patch: { projectId: 'p1' } });
    expect(getState().selection.projectId).toBe('p1');
    expect(notified).toBe(1);
    expect(seen).toBe('p1');
    // 状态未变化的动作不通知 (游标只前进)
    dispatch({ type: 'transport/cursor', sessionId: 's1', seq: 3 });
    dispatch({ type: 'transport/cursor', sessionId: 's1', seq: 1 });
    expect(notified).toBe(2);
    unsubscribe();
    dispatch({ type: 'selection/patch', patch: { projectId: 'p2' } });
    expect(notified).toBe(2);
  });

  it('switchSelection 只在身份真的变了时整体切换', () => {
    dispatch({ type: 'selection/open', selection: { projectId: 'p1', runId: 'r1' } });
    dispatch({ type: 'selection/object', objectKey: 'claim:c1' });
    const token = getState().ui.loadToken;
    // 同一次刷新把相同身份再交上来: 不清对象、不推进代号
    switchSelection({ projectId: 'p1', runId: 'r1' });
    expect(getState().selection.objectKey).toBe('claim:c1');
    expect(getState().ui.loadToken).toBe(token);
    // 真换了 run: 整体切换
    switchSelection({ projectId: 'p1', runId: 'r2' });
    expect(getState().selection.objectKey).toBe('');
    expect(getState().ui.loadToken).toBe(token + 1);
  });

  it('resetSession 清身份与团队投影, 但不动 ui 的主题偏好', () => {
    dispatch({ type: 'selection/open', selection: { projectId: 'p1', runId: 'r1' } });
    dispatch({ type: 'ui/context', topic: '库主题' });
    dispatch({ type: 'entities/roles', roles: [] });
    dispatch({ type: 'transport/status', status: 'online' });
    resetSession();
    expect(getState().selection.projectId).toBe('');
    expect(getState().entities.rolesLoaded).toBe(false);
    expect(getState().transport.status).toBe('offline');
    expect(getState().ui.contextTopic).toBe('库主题');
  });
});
