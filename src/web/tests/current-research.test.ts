/**
 * 当前研究状态的用例 (计划书 §2 F1)。
 *
 * 这些用例针对"状态散落在多个全局变量里"导致的真实缺陷:
 * 切换会话只恢复线程、新建会话继承旧问题、综述主题覆盖理论请求。
 */
import { describe, expect, it } from 'vitest';

import {
  applyPatch,
  emptyResearch,
  hasProblem,
  label,
  loadConversation,
  resetForNewSession,
  topicForRequest,
  type CurrentResearch,
} from '../src/current-research';

function theoryState(): CurrentResearch {
  return {
    threadId: 't-1', sessionId: 's-1', mode: 'theory',
    projectId: 'projA', problemId: 'p1', contextTopic: '主题X',
    runId: 'run-1', status: 'running',
  };
}

describe('applyPatch', () => {
  it('只改动显式给出的字段', () => {
    const before = theoryState();
    const after = applyPatch(before, { status: 'waiting' });
    expect(after.status).toBe('waiting');
    expect(after.projectId).toBe('projA');
    expect(after.problemId).toBe('p1');
    expect(after.threadId).toBe('t-1');
  });

  it('空值被归一化为空串 (不保留 null/undefined)', () => {
    const after = applyPatch(theoryState(), { threadId: '', projectId: undefined });
    expect(after.threadId).toBe('');
    expect(after.projectId).toBe('');
  });

  it('不修改传入对象 (纯函数)', () => {
    const before = theoryState();
    applyPatch(before, { projectId: 'other' });
    expect(before.projectId).toBe('projA');
  });
});

describe('resetForNewSession', () => {
  it('清空旧项目/问题与运行绑定, 保留模式', () => {
    const after = resetForNewSession(theoryState());
    expect(after.projectId).toBe('');
    expect(after.problemId).toBe('');
    expect(after.runId).toBe('');
    expect(after.threadId).toBe('');
    expect(after.sessionId).toBe('');
    expect(after.status).toBe('idle');
    expect(after.mode).toBe('theory');
  });

  it('新建会话后不再声称已绑定研究问题', () => {
    expect(hasProblem(resetForNewSession(theoryState()))).toBe(false);
  });
});

describe('loadConversation', () => {
  it('整体加载该会话的模式/项目/问题 (而不是只恢复线程)', () => {
    const after = loadConversation(theoryState(), {
      mode: 'survey', project_id: '', problem_id: '',
    }, 's-2');
    expect(after.mode).toBe('survey');
    expect(after.projectId).toBe('');
    expect(after.problemId).toBe('');
    expect(after.sessionId).toBe('s-2');
    expect(after.threadId).toBe('');
  });

  it('从综述切到理论会话时项目/问题随之切换', () => {
    const survey = { ...emptyResearch('survey'), contextTopic: '综述主题' };
    const after = loadConversation(survey, {
      mode: 'theory', project_id: 'projB', problem_id: 'p9', run_id: 'r9',
    }, 's-3');
    expect(after.mode).toBe('theory');
    expect(after.projectId).toBe('projB');
    expect(after.problemId).toBe('p9');
    expect(after.runId).toBe('r9');
    // 综述资料库主题不被覆盖 (它是另一个字段)
    expect(after.contextTopic).toBe('综述主题');
  });

  it('缺少 request 时不抛异常', () => {
    const after = loadConversation(theoryState(), null, 's-4');
    expect(after.mode).toBe('survey');
    expect(after.projectId).toBe('');
  });
});

describe('topicForRequest', () => {
  it('综述模式使用资料库主题', () => {
    const state = { ...emptyResearch('survey'), contextTopic: '库主题' };
    expect(topicForRequest(state, '我的请求', '表单主题')).toBe('库主题');
  });

  it('理论模式不得被资料库主题覆盖', () => {
    const state = { ...emptyResearch('theory'), contextTopic: '库主题' };
    expect(topicForRequest(state, '我的请求', '')).toBe('我的请求');
    expect(topicForRequest(state, '我的请求', '表单主题')).toBe('表单主题');
  });

  it('理论模式无输入时退回表单主题', () => {
    const state = emptyResearch('theory');
    expect(topicForRequest(state, '', '表单主题')).toBe('表单主题');
  });
});

describe('label', () => {
  it('显示模式 · 问题 · 状态', () => {
    expect(label(theoryState())).toBe('理论研究 · p1 · 运行中');
  });

  it('无问题时退化为项目与就绪', () => {
    expect(label(emptyResearch('survey'))).toBe('综述');
  });
});
