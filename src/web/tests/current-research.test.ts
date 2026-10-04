/**
 * 当前研究上下文的用例 (原计划书 §2 F1; G19 后改为驱动**唯一状态**)。
 *
 * 这些用例针对"状态散落在多个全局变量里"导致的真实缺陷:
 * 切换会话只恢复线程、新建会话继承旧问题、上下文主题覆盖研究请求。
 *
 * G19 迁移: `current-research.ts` 不再是可写状态机 (`applyPatch` /
 * `resetForNewSession` / `loadConversation` / `emptyResearch` 已删除), 而是
 * `state/research-store.ts` 的**只读投影**。因此:
 *
 * - 原来打在 `applyPatch`/`resetForNewSession`/`loadConversation` 上的行为判据,
 *   现在打在等价的 store 动作上 (`selection/patch` / `selection/status` /
 *   `selection/reset` / `selection/open`) —— 断言逐条保留, 不是删掉;
 * - `label()` 变成 `researchLabel(researchView(state))` (状态标签保留为纯 selector);
 * - 新增: 只读投影必须真的只读 (可写镜像已删除)。
 *
 * 统一入口后的契约不变:
 * - 状态里**不再有"运行模式"选择**, `mode` 只是服务端返回的引擎标识 (显示用);
 * - "上下文主题与项目 id 不是同一个字段、互不覆盖"这条判据必须保留。
 */
import { describe, expect, it } from 'vitest';

import {
  emptyStore,
  getState,
  reduce,
  type ResearchStore,
} from '../src/state/research-store';
import {
  researchLabel,
  researchView,
  hasProblem as viewHasProblem,
  type ResearchView,
} from '../src/current-research';

/** 等价于旧的 `runningState()`: 一个绑定好运行的当前研究上下文。 */
function runningStore(): ResearchStore {
  return reduce(emptyStore(), {
    type: 'selection/open',
    selection: {
      threadId: 't-1', sessionId: 's-1', mode: 'theory',
      projectId: 'projA', problemId: 'p1', runId: 'run-1', runStatus: 'running',
    },
  });
}

function viewOf(store: ResearchStore): ResearchView {
  return researchView(store);
}

describe('selection/patch (原 applyPatch)', () => {
  it('只改动显式给出的字段', () => {
    const before = runningStore();
    const after = reduce(before, { type: 'selection/patch', patch: { runStatus: 'waiting' } });
    expect(after.selection.runStatus).toBe('waiting');
    expect(after.selection.projectId).toBe('projA');
    expect(after.selection.problemId).toBe('p1');
    expect(after.selection.threadId).toBe('t-1');
  });

  it('运行状态也可以只改这一项 (与连接状态分开)', () => {
    const after = reduce(runningStore(), { type: 'selection/status', status: 'waiting' });
    expect(after.selection.runStatus).toBe('waiting');
    expect(after.transport.status).toBe('offline');
  });

  it('空值被归一化为空串 (不保留 null/undefined)', () => {
    const after = reduce(runningStore(), {
      type: 'selection/patch', patch: { threadId: '', projectId: undefined },
    });
    expect(after.selection.threadId).toBe('');
    expect(after.selection.projectId).toBe('');
  });

  it('不修改传入状态 (纯函数)', () => {
    const before = runningStore();
    reduce(before, { type: 'selection/patch', patch: { projectId: 'other' } });
    expect(before.selection.projectId).toBe('projA');
  });

  it('身份字段之外的标签页/对象详情改动不影响身份', () => {
    const after = reduce(runningStore(), { type: 'selection/tab', tab: 'paper' });
    expect(after.selection.tab).toBe('paper');
    expect(after.selection.projectId).toBe('projA');
    expect(after.selection.runId).toBe('run-1');
  });
});

describe('selection/reset (原 resetForNewSession)', () => {
  it('清空旧项目/问题与运行绑定, 保留引擎标识', () => {
    const after = reduce(runningStore(), { type: 'selection/reset' });
    expect(after.selection.projectId).toBe('');
    expect(after.selection.problemId).toBe('');
    expect(after.selection.runId).toBe('');
    expect(after.selection.threadId).toBe('');
    expect(after.selection.sessionId).toBe('');
    expect(after.selection.runStatus).toBe('idle');
    // 引擎标识只用于显示: 新建会话不必清掉它
    expect(after.selection.mode).toBe('theory');
  });

  it('新建会话后不再声称已绑定研究问题', () => {
    expect(viewHasProblem(researchView(reduce(runningStore(), { type: 'selection/reset' }))))
      .toBe(false);
  });
});

describe('selection/open (原 loadConversation)', () => {
  it('整体加载该会话的项目/问题/运行 (而不是只恢复线程)', () => {
    const after = reduce(runningStore(), {
      type: 'selection/open',
      selection: { mode: 'survey', projectId: '', problemId: '', sessionId: 's-2', threadId: '' },
    });
    expect(after.selection.projectId).toBe('');
    expect(after.selection.problemId).toBe('');
    expect(after.selection.sessionId).toBe('s-2');
    expect(after.selection.threadId).toBe('');
    // 引擎标识如实带入用于显示
    expect(after.selection.mode).toBe('survey');
  });

  it('切换会话时项目/问题随之切换, 上下文主题不被覆盖', () => {
    const previous = reduce(emptyStore(), { type: 'ui/context', topic: '库主题' });
    const after = reduce(previous, {
      type: 'selection/open',
      selection: { mode: 'theory', projectId: 'projB', problemId: 'p9', runId: 'r9', sessionId: 's-3' },
    });
    expect(after.selection.projectId).toBe('projB');
    expect(after.selection.problemId).toBe('p9');
    expect(after.selection.runId).toBe('r9');
    // 资料库主题是另一个字段 (ui 片), 不因切换会话被清空或覆盖
    expect(after.ui.contextTopic).toBe('库主题');
  });

  it('缺少 request 时不抛异常 (页面按空记录打开会话)', () => {
    const after = reduce(runningStore(), {
      type: 'selection/open',
      selection: { mode: '', projectId: '', problemId: '', sessionId: 's-4', threadId: '' },
    });
    expect(after.selection.projectId).toBe('');
    expect(after.selection.mode).toBe('');
  });

  it('未识别的引擎标识如实保留 (不归一化成某个已知取值)', () => {
    const after = reduce(emptyStore(), {
      type: 'selection/open', selection: { mode: 'team_v1', sessionId: 's-5' },
    });
    expect(after.selection.mode).toBe('team_v1');
  });
});

describe('researchLabel (原 label)', () => {
  it('显示引擎 · 问题 · 状态', () => {
    const store = reduce(runningStore(), { type: 'ui/context', topic: '主题X' });
    expect(researchLabel(viewOf(store))).toBe('理论研究 · p1 · 运行中');
  });

  it('没有引擎标识时不编造模式名', () => {
    expect(researchLabel(researchView(emptyStore()))).toBe('');
  });

  it('未识别的引擎标识按原文显示', () => {
    expect(researchLabel(researchView(reduce(emptyStore(),
      { type: 'selection/patch', patch: { mode: 'team_v1' } })))).toBe('team_v1');
  });

  it('无问题时退化为项目与状态', () => {
    const store = reduce(emptyStore(), {
      type: 'selection/patch', patch: { mode: 'theory', projectId: 'projA' },
    });
    expect(researchLabel(researchView(store))).toBe('理论研究 · projA');
  });
});

describe('只读投影 (G19: 可写镜像已删除)', () => {
  it('投影是冻结快照: 改不动, 也不会变成第二份真相', () => {
    const view = researchView(runningStore());
    expect(Object.isFrozen(view)).toBe(true);
    expect(() => { (view as { projectId: string }).projectId = 'hacked'; })
      .toThrow();
    expect(view.projectId).toBe('projA');
  });

  it('投影每次都从唯一状态现算 (没有滞后的镜像)', () => {
    const store = runningStore();
    const first = researchView(store);
    const second = researchView(reduce(store, {
      type: 'selection/patch', patch: { projectId: 'projC' },
    }));
    expect(first.projectId).toBe('projA');
    expect(second.projectId).toBe('projC');
  });

  it('唯一状态的初始投影是空的 (没有预置模式)', () => {
    const view = researchView(getState());
    expect(view.projectId).toBe('');
    expect(view.mode).toBe('');
    expect(researchLabel(view)).toBe('');
  });
});
