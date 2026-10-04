/**
 * 团队工作台视图的用例 (合并计划 §9.2 / §9.6)。
 *
 * 视图只读状态、只输出字符串: 因此可以直接断言"角色多任务不重复头像""状态不只靠颜色"
 * "失败原因可见""能力不可用原因可见"这些**可验收**的要求。
 */
import { describe, expect, it } from 'vitest';

import { emptyStore, reduce, type ResearchStore } from '../src/state/research-store';
import {
  esc,
  renderIssuePanel,
  renderRoleCapabilities,
  renderRoleStatusBar,
  renderTaskDetail,
  renderTaskTable,
  renderTeamBoard,
} from '../src/views/team-board';

function storeWithTeam(): ResearchStore {
  let store = reduce(emptyStore(), {
    type: 'selection/open',
    selection: { projectId: 'p1', problemId: 'q1', runId: 'r1', sessionId: 's1' },
  });
  store = reduce(store, {
    type: 'entities/roles',
    roles: [
      { agent: 'evidence', label: '检索与证据整理', objective: '覆盖文献/案例/数据',
        deliverables: ['EvidenceBundle'], available: ['sources', 'tools:search'],
        unavailable: [{ capability: 'tools:vector', reason: '未配置 embedding' }],
        mayRequestDowngrade: false },
      { agent: 'review', label: '独立审阅', objective: '独立检查',
        deliverables: ['ReviewReport'], available: ['sources'], unavailable: [],
        mayRequestDowngrade: true },
    ],
  });
  store = reduce(store, {
    type: 'entities/tasks', runId: 'r1', tasks: [
      { taskId: 't1', agent: 'evidence', objective: '检索可定位来源', subquestion: 'sq1',
        expectedGain: '得到出处', status: 'running', outcome: '', summary: '',
        failureReason: '', dependsOn: [], attempt: 1, needsHuman: false, planVersion: 1 },
      { taskId: 't2', agent: 'evidence', objective: '补检索', subquestion: 'sq2',
        expectedGain: '补齐', status: 'failed', outcome: 'failed', summary: '',
        failureReason: '工具不可用', dependsOn: ['t1'], attempt: 2, needsHuman: false,
        planVersion: 2 },
    ],
  });
  store = reduce(store, {
    type: 'entities/decision', runId: 'r1',
    decision: { round: 1, decision: 'dispatch', reason: '依赖已满足, 派 2 个子任务',
                note: '', taskIds: ['t1', 't2'] },
  });
  store = reduce(store, {
    type: 'entities/issue',
    issue: { issueId: 'i1', severity: 'blocking', category: 'science',
             summary: '结论无依据', blocking: true },
  });
  return store;
}

describe('renderRoleStatusBar', () => {
  it('同一角色多个任务只显示一行, 并给出任务数', () => {
    const html = renderRoleStatusBar([
      { agent: 'evidence', label: '检索与证据整理', total: 2,
        byStatus: { running: 1, failed: 1 }, running: 1, failed: 1, waiting: 0,
        text: '执行中 ×1' },
    ]);
    expect(html.match(/class="role-status[ "]/g)?.length).toBe(1);
    expect(html).toContain('team-statusbar');
    expect(html).toContain('任务 2');
    expect(html).toContain('执行中 ×1');
    // 失败的角色被标记出来 (不与正常行同色依赖)
    expect(html).toContain('blocked');
  });

  it('没有任务时给明确说明而不是空白', () => {
    expect(renderRoleStatusBar([])).toContain('还没有任务记录');
  });
});

describe('renderTaskTable', () => {
  it('失败原因与子问题直接可见', () => {
    const html = renderTaskTable([
      { taskId: 't2', agent: 'evidence', objective: '补检索', subquestion: 'sq2',
        expectedGain: '', status: 'failed', outcome: 'failed', summary: '',
        failureReason: '工具不可用', dependsOn: ['t1'], attempt: 2, needsHuman: false,
        planVersion: 2 },
    ]);
    expect(html).toContain('工具不可用');
    expect(html).toContain('sq2');
    expect(html).toContain('第 2 次');
    expect(html).toContain('1 个前置');
  });

  it('状态同时给文字与图标 (不只靠颜色)', () => {
    const html = renderTaskTable([
      { taskId: 't1', agent: 'evidence', objective: 'x', subquestion: '',
        expectedGain: '', status: 'waiting', outcome: '', summary: '',
        failureReason: '', dependsOn: [], attempt: 1, needsHuman: false, planVersion: 1 },
    ]);
    expect(html).toContain('等依赖/等用户');
    expect(html).toContain('⏸');
  });

  it('未知状态显式显示为未知, 不假装成功', () => {
    const html = renderTaskTable([
      { taskId: 't9', agent: 'writing', objective: 'x', subquestion: '',
        expectedGain: '', status: 'mystery', outcome: '', summary: '',
        failureReason: '', dependsOn: [], attempt: 1, needsHuman: false, planVersion: 1 },
    ]);
    expect(html).toContain('未知状态');
  });

  it('空列表给出说明', () => {
    expect(renderTaskTable([])).toContain('没有符合条件的任务');
  });
});

describe('renderTaskDetail', () => {
  it('详情包含派工契约的全部字段', () => {
    const html = renderTaskDetail({
      taskId: 't1', agent: 'evidence', objective: '检索', subquestion: 'sq1',
      expectedGain: '得到出处', status: 'running', outcome: '', summary: '',
      failureReason: '', dependsOn: ['t0'], attempt: 1, needsHuman: false,
      planVersion: 3,
    });
    for (const label of ['要回答的子问题', '预期新信息', '计划版本', '依赖', '失败原因']) {
      expect(html).toContain(label);
    }
  });
});

describe('renderRoleCapabilities', () => {
  it('不可用能力给出原因 (工具不可用时不能静默)', () => {
    const html = renderRoleCapabilities([
      { agent: 'evidence', label: '检索与证据整理', objective: '',
        deliverables: [], available: ['sources'],
        unavailable: [{ capability: 'tools:vector', reason: '未配置 embedding' }],
        mayRequestDowngrade: false },
    ]);
    expect(html).toContain('tools:vector');
    expect(html).toContain('未配置 embedding');
  });

  it('审阅角色标出"可请求降级"', () => {
    const html = renderRoleCapabilities([
      { agent: 'review', label: '独立审阅', objective: '', deliverables: [],
        available: [], unavailable: [], mayRequestDowngrade: true },
    ]);
    expect(html).toContain('可请求降级');
  });

  it('尚未读取能力时给说明', () => {
    expect(renderRoleCapabilities([])).toContain('尚未读取团队能力');
  });
});

describe('renderIssuePanel', () => {
  it('阻断问题排在前面', () => {
    let store = storeWithTeam();
    store = reduce(store, { type: 'entities/issue', issue: {
      issueId: 'i2', severity: 'minor', category: 'readability',
      summary: '句子太长', blocking: false } });
    const html = renderIssuePanel(store);
    expect(html.indexOf('结论无依据')).toBeLessThan(html.indexOf('句子太长'));
    expect(html).toContain('阻断交付');
  });

  it('没有问题账本时给说明', () => {
    expect(renderIssuePanel(emptyStore())).toContain('还没有审阅问题');
  });
});

describe('renderTeamBoard', () => {
  it('装配主控摘要 + 团队状态 + 任务 + 能力 + 审阅', () => {
    const html = renderTeamBoard(storeWithTeam());
    expect(html).toContain('主控视角');
    expect(html).toContain('团队状态');
    expect(html).toContain('第 1 轮决策');
    expect(html).toContain('独立审阅');
    expect(html).toContain('审阅与返工');
  });

  it('需要重新同步时明确提示 (不把缺口当完成)', () => {
    let store = storeWithTeam();
    store = reduce(store, { type: 'transport/needsResync', needs: true });
    expect(renderTeamBoard(store)).toContain('需要重新加载投影');
  });

  it('连接错误与运行状态分开显示', () => {
    let store = storeWithTeam();
    store = reduce(store, { type: 'transport/status', status: 'reconnecting', error: '连接被重置' });
    const html = renderTeamBoard(store);
    expect(html).toContain('连接被重置');
    expect(html).toContain('重连中');
  });
});

describe('esc', () => {
  it('转义 HTML, 不让来源文本变成脚本', () => {
    expect(esc('<script>alert(1)</script>')).toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(esc('a"b\'c&d')).toBe('a&quot;b&#39;c&amp;d');
  });

  it('任务目标里的标记被转义后不执行', () => {
    const html = renderTaskTable([
      { taskId: 't1', agent: 'evidence', objective: '<img src=x onerror=alert(1)>',
        subquestion: '', expectedGain: '', status: 'running', outcome: '', summary: '',
        failureReason: '', dependsOn: [], attempt: 1, needsHuman: false, planVersion: 1 },
    ]);
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });
});
