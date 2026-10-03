/**
 * `window.AIR` 契约的唯一创建点 (计划书 §2 F1/F4)。
 *
 * 为什么单独一个模块:
 * - 之前 `window.AIR` 在 `main.ts` 里创建, 而页面逻辑 `app.ts` 也必须能读到它。
 *   模块的求值顺序由 import 图决定, 把"创建全局契约"和"使用它"拆到两个模块后,
 *   打包器会重排成"先初始化页面, 再赋值 window.AIR" —— 页面拿到的是 undefined,
 *   表现为按钮无反应 / 直接抛错。
 * - 现在 `app.ts` 与 `main.ts` 都 import 本模块, 因此全局契约**必然先建立**,
 *   且只有一份状态对象 (`CurrentResearch`), 不存在两份互相覆盖的副本。
 */

import {
  STATUS_LABEL,
  applyPatch,
  emptyResearch,
  hasProblem,
  label,
  loadConversation,
  resetForNewSession,
  topicForRequest,
  type CurrentResearch,
  type ResearchPatch,
  type RunMode,
  type RunStatus,
} from './current-research';
import {
  buildUrl,
  createResearchApi,
  readJson,
  requestWithRetry,
  type ResearchApi,
} from './research-api';
import { AIRMarkdown } from './markdown';

declare global {
  interface Window {
    AIR: {
      research: CurrentResearch;
      api: ResearchApi;
      markdown: unknown;
      statusLabel: Record<RunStatus, string>;
      apply(patch: ResearchPatch): CurrentResearch;
      reset(): CurrentResearch;
      load(request: Record<string, unknown>, sessionId: string): CurrentResearch;
      hasProblem(): boolean;
      label(): string;
      topicForRequest(request: string, formTopic: string): string;
      buildUrl: typeof buildUrl;
      readJson: typeof readJson;
      requestWithRetry: typeof requestWithRetry;
      empty(mode: RunMode): CurrentResearch;
    };
    AIRMarkdown?: {
      render(text: string, options: { className: string }): Node;
      renderText(text: string, options: { className: string }): Node;
    };
  }
}

const research = emptyResearch();

const air = {
  research,
  api: createResearchApi(),
  markdown: AIRMarkdown,
  statusLabel: STATUS_LABEL,
  apply(patch: ResearchPatch): CurrentResearch {
    Object.assign(air.research, applyPatch(air.research, patch));
    return air.research;
  },
  reset(): CurrentResearch {
    Object.assign(air.research, resetForNewSession(air.research));
    return air.research;
  },
  load(request: Record<string, unknown>, sessionId: string): CurrentResearch {
    Object.assign(air.research, loadConversation(air.research, request, sessionId));
    return air.research;
  },
  hasProblem(): boolean {
    return hasProblem(air.research);
  },
  label(): string {
    return label(air.research);
  },
  topicForRequest(request: string, formTopic: string): string {
    return topicForRequest(air.research, request, formTopic);
  },
  buildUrl,
  readJson,
  requestWithRetry,
  empty: emptyResearch,
};

if (typeof window !== 'undefined') {
  window.AIR = air;
}

export { air };
export default air;
