/** 当前研究上下文的只读投影。 */

import {
  STATUS_LABEL,
  type ResearchStore,
  type RunStatus,
} from './state/research-store';

export type { RunStatus };

/** `window.AIR.research` 的形状: 只是 `ResearchStore` 的只读投影, 不是可写状态。 */
export interface ResearchView {
  threadId: string;
  sessionId: string;
  /**
   * 服务端返回的引擎标识 (`theory` / `survey` / ...), **只用于显示**。
   *
   * 刻意声明为 `string` 而不是字面量联合: 前端不再理解它的取值含义,
   * 也就不会有人再写 `mode === 'theory'` 这种分支。
   */
  mode: string;
  projectId: string;
  problemId: string;
  /** 资料库/主题偏好 (与项目 id 不是同一个字段, 互不覆盖) */
  contextTopic: string;
  runId: string;
  status: RunStatus;
}

/** 从唯一状态派生只读投影 (每次调用产生新的冻结快照)。 */
export function researchView(state: ResearchStore): ResearchView {
  return Object.freeze({
    threadId: state.selection.threadId || '',
    sessionId: state.selection.sessionId || '',
    mode: state.selection.mode || '',
    projectId: state.selection.projectId || '',
    problemId: state.selection.problemId || '',
    contextTopic: state.ui.contextTopic || '',
    runId: state.selection.runId || '',
    status: state.selection.runStatus || 'idle',
  });
}

export function hasProblem(view: ResearchView): boolean {
  return Boolean(view.projectId);
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
export function researchLabel(view: ResearchView): string {
  const bits: string[] = [];
  const engine = ENGINE_LABEL[view.mode] || view.mode;
  if (engine) bits.push(engine);
  if (view.problemId) bits.push(view.problemId);
  else if (view.projectId) bits.push(view.projectId);
  if (view.status !== 'idle') bits.push(STATUS_LABEL[view.status] || view.status);
  return bits.join(' · ');
}
