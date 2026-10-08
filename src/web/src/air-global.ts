/**
 * `window.AIR` 契约的唯一创建点 (计划书 §2 F1/F4 / §8.1)。
 *
 * 为什么单独一个模块:
 * - 之前 `window.AIR` 在 `main.ts` 里创建, 而页面逻辑 `app.ts` 也必须能读到它。
 *   模块的求值顺序由 import 图决定, 把"创建全局契约"和"使用它"拆到两个模块后,
 *   打包器会重排成"先初始化页面, 再赋值 window.AIR" —— 页面拿到的是 undefined,
 *   表现为按钮无反应 / 直接抛错。
 * - 现在 `main.ts` 先 import 本模块, 再 import `app.ts`, 因此全局契约**必然先建立**。
 *
 * 这里**不再持有任何可写状态**。
 *
 * 迁移前 `window.AIR.research` 是一个可写的 `CurrentResearch` 对象, 带
 * `apply()/reset()/load()` 三个写入口, 与 `state/research-store.ts` 并行存在 ——
 * "两份当前研究"正是每次会话切换都要靠兼容同步对表的原因。现在:
 *
 * - 唯一状态在 `state/research-store.ts`, 写入只能经它的 `dispatch(action)`;
 * - `research` 是**只读投影** (getter + 冻结快照), 每次读取都从 store 现算,
 *   因此不存在"镜像落后于真相"的情形; 直接赋值会在严格模式下抛错而不是静默分叉;
 * - `api` / `markdown` / `buildUrl` / `readJson` 是工具出口, 不是状态;
 * - `statusLabel` / `label()` / `hasProblem()` 是纯 selector (状态标签保留为纯 selector)。
 */

import {
  getState,
  STATUS_LABEL,
} from './state/research-store';
import {
  hasProblem as viewHasProblem,
  researchLabel,
  researchView,
  type ResearchView,
} from './current-research';
import {
  buildUrl,
  client as researchClient,
  readJson,
  type ResearchClient,
} from './api/research-client';
import { AIRMarkdown } from './markdown';

declare global {
  interface Window {
    AIR: {
      /** 只读投影: 每次读取都从唯一状态现算 (不可写)。 */
      readonly research: ResearchView;
      api: ResearchClient;
      markdown: unknown;
      statusLabel: Record<string, string>;
      hasProblem(): boolean;
      label(): string;
      buildUrl: typeof buildUrl;
      readJson: typeof readJson;
    };
    AIRMarkdown?: {
      render(text: string, options: { className: string }): Node;
      renderText(text: string, options: { className: string }): Node;
    };
  }
}

const air = {
  /** 只读投影 (§8.1): 不是第二份状态, 也没有写入口。 */
  get research(): ResearchView {
    return researchView(getState());
  },
  api: researchClient(),
  markdown: AIRMarkdown,
  statusLabel: STATUS_LABEL,
  hasProblem(): boolean {
    return viewHasProblem(researchView(getState()));
  },
  label(): string {
    return researchLabel(researchView(getState()));
  },
  buildUrl,
  readJson,
};

if (typeof window !== 'undefined') {
  window.AIR = air;
}

export { air };
export default air;
