/** `window.AIR` 契约的唯一创建点。 */

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
  /** 只读投影: 不是第二份状态, 也没有写入口。 */
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
