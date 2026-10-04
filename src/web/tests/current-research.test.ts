/**
 * 当前研究状态的用例 (计划书 §2 F1)。
 *
 * 这些用例针对"状态散落在多个全局变量里"导致的真实缺陷:
 * 切换会话只恢复线程、新建会话继承旧问题、上下文主题覆盖研究请求。
 *
 * 统一入口 (本轮迁移) 后的契约变化:
 * - 状态里**不再有"运行模式"选择**, `mode` 只是服务端返回的引擎标识 (显示用);
 * - 因此不再有"按模式挑 topic"的规则 (`topicForRequest` 已随模式选择器一并删除),
 *   但"上下文主题与项目 id 不是同一个字段、互不覆盖"这条判据必须保留。
 */
import { describe, expect, it } from 'vitest';

import {
  applyPatch,
  emptyResearch,
  hasProblem,
  label,
  loadConversation,
  resetForNewSession,
  type CurrentResearch,
} from '../src/current-research';

function runningState(): CurrentResearch {
  return {
    threadId: 't-1', sessionId: 's-1', mode: 'theory',
    projectId: 'projA', problemId: 'p1', contextTopic: '主题X',
    runId: 'run-1', status: 'running',
  };
}

describe('applyPatch', () => {
  it('只改动显式给出的字段', () => {
    const before = runningState();
    const after = applyPatch(before, { status: 'waiting' });
    expect(after.status).toBe('waiting');
    expect(after.projectId).toBe('projA');
    expect(after.problemId).toBe('p1');
    expect(after.threadId).toBe('t-1');
  });

  it('空值被归一化为空串 (不保留 null/undefined)', () => {
    const after = applyPatch(runningState(), { threadId: '', projectId: undefined });
    expect(after.threadId).toBe('');
    expect(after.projectId).toBe('');
  });

  it('不修改传入对象 (纯函数)', () => {
    const before = runningState();
    applyPatch(before, { projectId: 'other' });
    expect(before.projectId).toBe('projA');
  });
});

describe('resetForNewSession', () => {
  it('清空旧项目/问题与运行绑定, 保留引擎标识', () => {
    const after = resetForNewSession(runningState());
    expect(after.projectId).toBe('');
    expect(after.problemId).toBe('');
    expect(after.runId).toBe('');
    expect(after.threadId).toBe('');
    expect(after.sessionId).toBe('');
    expect(after.status).toBe('idle');
    // 引擎标识只用于显示: 新建会话不必清掉它
    expect(after.mode).toBe('theory');
  });

  it('新建会话后不再声称已绑定研究问题', () => {
    expect(hasProblem(resetForNewSession(runningState()))).toBe(false);
  });
});

describe('loadConversation', () => {
  it('整体加载该会话的项目/问题/运行 (而不是只恢复线程)', () => {
    const after = loadConversation(runningState(), {
      mode: 'survey', project_id: '', problem_id: '',
    }, 's-2');
    expect(after.projectId).toBe('');
    expect(after.problemId).toBe('');
    expect(after.sessionId).toBe('s-2');
    expect(after.threadId).toBe('');
    // 引擎标识如实带入用于显示
    expect(after.mode).toBe('survey');
  });

  it('切换会话时项目/问题随之切换, 上下文主题不被覆盖', () => {
    const previous = { ...emptyResearch(), contextTopic: '库主题' };
    const after = loadConversation(previous, {
      mode: 'theory', project_id: 'projB', problem_id: 'p9', run_id: 'r9',
    }, 's-3');
    expect(after.projectId).toBe('projB');
    expect(after.problemId).toBe('p9');
    expect(after.runId).toBe('r9');
    // 资料库主题是另一个字段, 不因切换会话被清空或覆盖
    expect(after.contextTopic).toBe('库主题');
  });

  it('缺少 request 时不抛异常', () => {
    const after = loadConversation(runningState(), null, 's-4');
    expect(after.projectId).toBe('');
    expect(after.mode).toBe('');
  });

  it('未识别的引擎标识如实保留 (不归一化成某个已知取值)', () => {
    const after = loadConversation(emptyResearch(), { mode: 'team_v1' }, 's-5');
    expect(after.mode).toBe('team_v1');
  });
});

describe('label', () => {
  it('显示引擎 · 问题 · 状态', () => {
    expect(label(runningState())).toBe('理论研究 · p1 · 运行中');
  });

  it('没有引擎标识时不编造模式名', () => {
    expect(label(emptyResearch())).toBe('');
  });

  it('未识别的引擎标识按原文显示', () => {
    expect(label({ ...emptyResearch(), mode: 'team_v1' })).toBe('team_v1');
  });

  it('无问题时退化为项目与状态', () => {
    const state = { ...emptyResearch(), mode: 'theory', projectId: 'projA' };
    expect(label(state)).toBe('理论研究 · projA');
  });
});
