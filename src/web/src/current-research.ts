/**
 * 当前研究上下文的**只读投影** (G19 / 合并计划 §8.1)。
 *
 * 这个模块曾经是一套**可写状态机** (`CurrentResearch` + `applyPatch` /
 * `resetForNewSession` / `loadConversation`), 并由 `air-global.ts` 保存成
 * `window.AIR.research`。那份状态与 `state/research-store.ts` 的选择器和
 * `app.ts` 的 ID 镜像并行存在 —— 于是切换会话时必须靠
 * `syncSelectionFromLegacy()` 之类的兼容同步来"对表", 任何一条路径漏同步就会串号。
 *
 * 现在可写状态只有 `state/research-store.ts` 一份:
 *
 * - 这里**只有纯 selector**, 没有任何 `apply`/`reset`/`load` 之类的写入口;
 * - `window.AIR.research` 是 `researchView()` 的只读快照 (对象被冻结, 见
 *   `air-global.ts`), 供迁移期的浏览器用例读取, 不再是第二份真相;
 * - 用户可见的状态文案 (`STATUS_LABEL`) 由 store 提供, 这里只做纯函数拼装。
 *
 * 统一入口 (本轮迁移): 用户**不再选择"综述/理论"模式**。`mode` 只剩一个用途 ——
 * 保存服务端返回的引擎标识**用于显示**; 前端不得再据它分支任何界面行为。
 */

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
