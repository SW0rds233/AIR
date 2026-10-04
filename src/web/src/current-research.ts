/**
 * 当前研究状态 (计划书 §2 F1)。
 *
 * 一次上下文切换必须让**所有**视图跟着走: 项目、问题、运行、状态。
 * 早期实现把这些字段散落在多个全局变量里, 切换历史会话时只恢复线程,
 * 于是项目字段会沿用上一个会话。
 *
 * 本模块是纯数据 + 纯函数: 不访问 `document`/`window`/网络, 因此可以直接
 * 在测试里驱动 (见 `tests/current-research.test.ts`)。
 *
 * 统一入口 (本轮迁移): 用户**不再选择"综述/理论"模式**。
 * `mode` 只剩一个用途 —— 保存服务端返回的引擎标识**用于显示**;
 * 前端不得再据它分支任何界面行为, 因此这里不再声明 `RunMode` 字面量联合,
 * 也删掉了 `topicForRequest` (它的全部意义就是按模式挑 topic)。
 */

import type { RunStatus } from './contracts/session';

export type { RunStatus };

export interface CurrentResearch {
  threadId: string;
  sessionId: string;
  /**
   * 服务端返回的引擎标识 (`theory` / `survey` / ...), **只用于显示**。
   *
   * 刻意声明为 `string` 而不是字面量联合: 前端不再理解它的取值含义,
   * 也就不会有人再写 `mode === 'theory'` 这种分支 (那正是本轮要删掉的旧实现)。
   */
  mode: string;
  projectId: string;
  problemId: string;
  /** 资料库/主题偏好 (与项目 id 不是同一个字段, 互不覆盖) */
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
  stopped: '已停止',
  error: '出错',
};

export function emptyResearch(): CurrentResearch {
  return {
    threadId: '',
    sessionId: '',
    mode: '',
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
  if ('mode' in patch) next.mode = patch.mode || '';
  if ('projectId' in patch) next.projectId = patch.projectId || '';
  if ('problemId' in patch) next.problemId = patch.problemId || '';
  if ('contextTopic' in patch) next.contextTopic = patch.contextTopic || '';
  if ('runId' in patch) next.runId = patch.runId || '';
  if ('status' in patch) next.status = (patch.status as RunStatus) || 'idle';
  return next;
}

/** 新会话必须清空旧项目/问题与运行绑定。 */
export function resetForNewSession(state: CurrentResearch): CurrentResearch {
  return applyPatch(state, {
    threadId: '', sessionId: '', projectId: '', problemId: '', runId: '',
    status: 'idle',
  });
}

/**
 * 切换历史会话时的整体加载: 项目/问题/运行一次到位。
 *
 * `mode` 只从**会话记录**里如实带入 (用于显示); 不在这里做任何取值归一化 ——
 * 归一化就意味着前端还在理解模式语义。
 */
export function loadConversation(state: CurrentResearch,
                                 request: Record<string, unknown> | null | undefined,
                                 sessionId: string): CurrentResearch {
  const req = request || {};
  return applyPatch(state, {
    sessionId,
    mode: String(req.mode || ''),
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

/** 引擎标识的中文显示名 (纯展示, 不参与任何判断)。 */
const ENGINE_LABEL: Record<string, string> = {
  theory: '理论研究',
  survey: '综述',
};

/**
 * 上下文徽标文本: 引擎 · 问题 · 状态。
 *
 * 只是**显示**: 未识别的引擎标识如实显示原文, 不猜测、不据此切换行为。
 */
export function label(state: CurrentResearch): string {
  const bits: string[] = [];
  const engine = ENGINE_LABEL[state.mode] || state.mode;
  if (engine) bits.push(engine);
  if (state.problemId) bits.push(state.problemId);
  else if (state.projectId) bits.push(state.projectId);
  if (state.status !== 'idle') bits.push(STATUS_LABEL[state.status] || state.status);
  return bits.join(' · ');
}

/** 显式续研与启动分开语义 (计划书 §9.1): 只有续研才带 resume=true。 */
export function startPayloadOptions(): { resume: boolean } {
  return { resume: false };
}

