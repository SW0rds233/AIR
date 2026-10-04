/**
 * G19 / 合并计划 §8.1 的守门用例: **唯一状态**与兼容入口的只读性。
 *
 * 为什么需要它: G19 的缺陷正是"同一件事有两份可写状态" ——
 * `window.AIR.research` (current-research.ts 的状态机)、`app.ts::currentResearch`
 * 与一组 ID 镜像、`team-controller.ts` 的 reducer store 并存。删掉它们之后, 如果
 * 没有反向判据, 下一次改动可以毫无阻碍地把镜像加回来 (旧用例当时也在"通过",
 * 因为两边都被同步过)。因此这里断言:
 *
 * 1. 行为面: `window.AIR.research` 是只读投影 —— 没有 setter、对象被冻结、改了也
 *    不会写回唯一状态; `window.AIR` 不再有 `apply/reset/load/empty` 写入口;
 * 2. 源码面 (防回流): `syncSelectionFromLegacy` / `legacyResearch` /
 *    `syncResearchGlobals` / `applyResearch` 以及那些可写 ID 镜像都不存在。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import air from '../src/air-global';
import {
  dispatch,
  getState,
  resetStore,
  type TabName,
} from '../src/state/research-store';
import { researchView } from '../src/current-research';
import { applyTeamEvent, getStore, renderTeamSection } from '../src/team-controller';
import { PROTOCOL_VERSION } from '../src/events/session-events';

/** 与 `research-client.test.ts` 同一取源方式 (构建时读成字符串, 不依赖 node 类型)。 */
const sources = import.meta.glob('../src/**/*.ts', {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>;

function sourceAt(suffix: string): string {
  const key = Object.keys(sources).find((path) => path.endsWith(suffix));
  expect(key, `缺少 ${suffix}`).toBeTruthy();
  return sources[key as string];
}

/** 去掉注释: 解释"为什么删掉旧镜像"的注释里会出现旧名字, 那是文档不是实现。 */
function codeOnly(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

beforeEach(() => { resetStore(); });

afterEach(() => { resetStore(); });

describe('window.AIR.research 是只读投影 (可写镜像已删除)', () => {
  it('读取时从唯一状态现算 (不存在滞后的第二份真相)', () => {
    expect(air.research.projectId).toBe('');
    dispatch({ type: 'selection/patch', patch: { projectId: 'projA', mode: 'theory' } });
    dispatch({ type: 'ui/context', topic: '库主题' });
    const view = air.research;
    expect(view.projectId).toBe('projA');
    expect(view.mode).toBe('theory');
    expect(view.contextTopic).toBe('库主题');
    expect(view).toEqual(researchView(getState()));
  });

  it('没有 setter, 直接赋值不会静默分叉 (可写镜像正是靠赋值分叉的)', () => {
    const descriptor = Object.getOwnPropertyDescriptor(air, 'research');
    expect(descriptor?.set).toBeUndefined();
    expect(descriptor?.get).toBeTypeOf('function');
    // 拿到手里的快照也是冻结的: 改它必须抛错, 而不是"改了但真相没变"
    expect(() => { (air.research as { projectId: string }).projectId = 'hacked'; }).toThrow();
    expect(getState().selection.projectId).toBe('');
  });

  it('window.AIR 不再暴露 apply/reset/load/empty 这些写入口', () => {
    for (const writePath of ['apply', 'reset', 'load', 'empty']) {
      expect(writePath in air, `window.AIR.${writePath} 仍是写入口`).toBe(false);
    }
    // 只读出口仍在 (浏览器用例按全局名读它)
    expect(typeof air.label).toBe('function');
    expect(typeof air.hasProblem).toBe('function');
    expect(air.api).toBeTruthy();
    expect(air.statusLabel.idle).toBe('就绪');
  });

  it('徽标文本是纯 selector: 引擎 · 问题 · 状态 (逐字不变)', () => {
    expect(air.label()).toBe('');
    dispatch({
      type: 'selection/patch',
      patch: { projectId: 'projA', problemId: 'p1', mode: 'theory' },
    });
    dispatch({ type: 'selection/status', status: 'running' });
    expect(air.label()).toBe('理论研究 · p1 · 运行中');
    expect(air.hasProblem()).toBe(true);
  });

  it('window.AIR 挂在全局上 (迁移期入口保留)', () => {
    expect((window as unknown as { AIR?: unknown }).AIR).toBe(air);
  });
});

describe('前端源码里不得再有旧的同步/镜像路径 (防回流)', () => {
  it('兼容同步与旧写入口彻底消失', () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(sources)) {
      const code = codeOnly(source);
      for (const gone of ['syncSelectionFromLegacy', 'legacyResearch', 'syncResearchGlobals',
                          'applyResearch', 'emptyResearch', 'resetForNewSession',
                          'loadConversation']) {
        if (code.includes(gone)) offenders.push(`${path}: ${gone}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('页面/控制器不再自己存一份可写的身份镜像', () => {
    const app = codeOnly(sourceAt('/app.ts'));
    // 迁移前这些是模块级可写变量 (`let currentProjectId = ''` 等)
    for (const mirror of ['let currentProjectId', 'let currentProblemId',
                          'let currentThreadId', 'let currentSessionId',
                          'let currentContext', 'let mode =', 'let openClaimDetail',
                          'let pendingInterruptId', 'let chosenCandidateId',
                          'let runBoundProjectId', 'let draftUnboundProjectId',
                          'const currentResearch']) {
      expect(app, `app.ts 仍有可写镜像: ${mirror}`).not.toContain(mirror);
    }
    // 团队视图也不再持有自己的 store 变量
    const team = codeOnly(sourceAt('/team-controller.ts'));
    expect(team).not.toContain('let store');
    expect(team).toContain('getState()');
    // 唯一状态模块之外不得再有 `window.AIR.research =` 这类整体赋值
    for (const [path, source] of Object.entries(sources)) {
      if (path.endsWith('/air-global.ts')) continue;
      expect(codeOnly(source), path).not.toMatch(/AIR\.research\s*=/);
    }
  });

  it('标签页取值与模板 data-tab 一致 (不再另立一套名字)', () => {
    const tabs: TabName[] = ['preview', 'workbench', 'library', 'paper', 'files'];
    for (const tab of tabs) {
      dispatch({ type: 'selection/tab', tab });
      expect(getState().selection.tab).toBe(tab);
    }
  });

  it('事件层的结果装入唯一 store (团队视图没有第二份 store 变量)', () => {
    dispatch({
      type: 'selection/open',
      selection: { projectId: 'p1', runId: 'r1', sessionId: 's1' },
    });
    expect(getStore()).toBe(getState());
    const effect = applyTeamEvent({
      protocol: PROTOCOL_VERSION, seq: 1, sessionId: 's1', runId: 'r1',
      type: 'task_started', taskId: 't1', agent: 'evidence', payload: {},
    });
    expect(effect.kind).toBe('apply');
    // 事件 → reducer → 唯一状态, 且页面/团队视图读到的就是同一份
    expect(getState().entities.tasksByRun['r1']['t1'].status).toBe('running');
    expect(getState().transport.cursorBySession['s1']).toBe(1);
    expect(renderTeamSection(['team'])).toContain('t1');
  });
});
