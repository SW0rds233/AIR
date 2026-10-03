/**
 * 当前研究状态 (计划书 §2 F1)。
 *
 * 一次上下文切换必须让**所有**视图跟着走: 模式、项目、问题、运行、状态。
 * 早期实现把这些字段散落在多个全局变量里, 切换历史会话时只恢复线程,
 * 于是理论/综述的模式与项目字段会沿用上一个会话。
 *
 * 本模块是纯数据 + 纯函数: 不访问 `document`/`window`/网络, 因此可以直接
 * 在测试里驱动 (见 `tests/js/current-research.test.ts`)。
 */

export type RunMode = 'survey' | 'theory';
export type RunStatus = 'idle' | 'running' | 'waiting' | 'done';

export interface CurrentResearch {
  threadId: string;
  sessionId: string;
  mode: RunMode;
  projectId: string;
  problemId: string;
  /** 综述模式下选中的资料库主题; 与理论研究问题**不是**同一个字段 */
  contextTopic: string;
  runId: string;
  status: RunStatus;
}

export type ResearchPatch = Partial<CurrentResearch>;

export const STATUS_LABEL: Record<RunStatus, string> = {
  idle: '就绪',
  running: '运行中',
  waiting: '等待输入',
  done: '已完成',
};

export function emptyResearch(mode: RunMode = 'survey'): CurrentResearch {
  return {
    threadId: '',
    sessionId: '',
    mode,
    projectId: '',
    problemId: '',
    contextTopic: '',
    runId: '',
    status: 'idle',
  };
}

/** 合并一次状态变更 (只有显式给出的字段会被改动)。 */
export function applyPatch(state: CurrentResearch, patch: ResearchPatch): CurrentResearch {
  const next: CurrentResearch = { ...state };
  if ('threadId' in patch) next.threadId = patch.threadId || '';
  if ('sessionId' in patch) next.sessionId = patch.sessionId || '';
  if ('mode' in patch) next.mode = (patch.mode as RunMode) || 'survey';
  if ('projectId' in patch) next.projectId = patch.projectId || '';
  if ('problemId' in patch) next.problemId = patch.problemId || '';
  if ('contextTopic' in patch) next.contextTopic = patch.contextTopic || '';
  if ('runId' in patch) next.runId = patch.runId || '';
  if ('status' in patch) next.status = (patch.status as RunStatus) || 'idle';
  return next;
}

/** 新会话必须清空旧项目/问题与运行绑定 (但保留用户显式选择的模式)。 */
export function resetForNewSession(state: CurrentResearch): CurrentResearch {
  return applyPatch(state, {
    threadId: '', sessionId: '', projectId: '', problemId: '', runId: '',
    status: 'idle',
  });
}

/** 切换历史会话时的整体加载: 模式/项目/问题/运行一次到位。 */
export function loadConversation(state: CurrentResearch,
                                 request: Record<string, unknown> | null | undefined,
                                 sessionId: string): CurrentResearch {
  const req = request || {};
  const mode: RunMode = req.mode === 'theory' ? 'theory' : 'survey';
  return applyPatch(state, {
    sessionId,
    mode,
    projectId: String(req.project_id || ''),
    problemId: String(req.problem_id || ''),
    runId: String(req.run_id || ''),
    threadId: '',
    status: 'idle',
  });
}

export function hasProblem(state: CurrentResearch): boolean {
  return Boolean(state.projectId);
}

/** 上下文徽标文本: 模式 · 问题 · 状态。 */
export function label(state: CurrentResearch): string {
  const bits = [state.mode === 'theory' ? '理论研究' : '综述'];
  if (state.problemId) bits.push(state.problemId);
  else if (state.projectId) bits.push(state.projectId);
  if (state.status !== 'idle') bits.push(STATUS_LABEL[state.status] || state.status);
  return bits.join(' · ');
}

/**
 * 启动请求的主题选择 (F1-3)。
 *
 * 综述模式用**资料库主题** (currentContext) 作为 topic; 理论模式必须用输入本身,
 * 不能把上一个综述会话选中的主题悄悄并入新请求。
 */
export function topicForRequest(state: CurrentResearch, requestText: string,
                                formTopic: string): string {
  const request = (requestText || '').trim();
  const form = (formTopic || '').trim();
  if (state.mode === 'theory') return form || request;
  return state.contextTopic || form;
}

/** 显式续研与启动分开语义 (计划书 §9.1): 只有续研才带 resume=true。 */
export function startPayloadOptions(): { resume: boolean } {
  return { resume: false };
}
