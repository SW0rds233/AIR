/**
 * 工作台总览与历史导航视图的单元测试 (合并计划 §9.5 拆分后的回归保障)。
 *
 * 关心的不是"HTML 长什么样", 而是迁出后仍然守住的几条硬约束:
 * - 动态按钮仍然走 `data-action` (严格 CSP 下内联处理器不执行);
 * - 外部文本一律转义 (结论陈述/来源标题/会话主题都不能注入 HTML);
 * - 计数为 0 与"尚未做"的表述不混为一谈 (未知不假报);
 * - 历史回看只跳过**未收尾**会话的最后一条 interrupt。
 */
import { describe, expect, it } from 'vitest';

import {
  renderCapabilityCard,
  renderResearchOverview,
  toggledClaimId,
  workbenchErrorHtml,
  workbenchMissingHtml,
  workbenchNewSessionHtml,
  workbenchNoProjectHtml,
  workbenchUnboundHtml,
} from '../src/views/research-overview';
import {
  conversationEntries,
  historyDetailTitle,
  historyListItemHtml,
  historyListHtml,
  historyOption,
  stageLabel,
} from '../src/views/project-navigation';

const noClaim = { data: () => null, openClaimId: () => '' };

describe('工作台总览 (§9.5 renderWorkbench 迁出)', () => {
  it('渲染结论状态表与详情开关按钮, 且不出现内联处理器', () => {
    const html = renderResearchOverview({
      project_id: 'p1',
      spec: { problem_statement: '研究问题' },
      claims: [{ id: 'clm-1', statement: '结论一', status: 'supported', support_kind: 'proof' }],
    }, noClaim);
    expect(html).toContain('结论状态表');
    expect(html).toContain('data-action="toggleClaimDetail"');
    expect(html).toContain('data-id="clm-1"');
    expect(html).not.toContain('onclick=');
    expect(html).toContain('id="wbobj"');
    expect(html).toContain('data-action="submitWorkbenchFeedback"');
    expect(html).toContain('data-action="refreshWorkbench"');
  });

  it('展开的结论带入详情行, 未展开则不带', () => {
    const data = {
      project_id: 'p1',
      claims: [{ id: 'clm-2', statement: '结论二', status: 'supported' }],
    };
    const closed = renderResearchOverview(data, noClaim);
    const open = renderResearchOverview(data, { data: () => data as any, openClaimId: () => 'clm-2' });
    expect(closed).not.toContain('aria-expanded="true"');
    expect(open).toContain('aria-expanded="true"');
    expect(open.length).toBeGreaterThan(closed.length);
  });

  it('结论陈述里的 HTML 被转义', () => {
    const html = renderResearchOverview({
      project_id: 'p1',
      claims: [{ id: 'c', statement: '<img src=x onerror=alert(1)>', status: 'supported' }],
    }, noClaim);
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });

  it('无结论/无义务时说明"尚无", 不显示成空白表', () => {
    const html = renderResearchOverview({ project_id: 'p1' }, noClaim);
    expect(html).toContain('尚无结论');
    expect(html).toContain('尚无义务');
    expect(html).toContain('尚未生成实验规格');
  });

  it('覆盖说明与日志契约异常必须显式出现', () => {
    const html = renderResearchOverview({
      project_id: 'p1',
      coverage_notes: ['缺少数据来源'],
      log_anomalies: [{ kind: 'node', missing: 'detail' }],
    }, noClaim);
    expect(html).toContain('覆盖说明');
    expect(html).toContain('缺少数据来源');
    expect(html).toContain('日志契约异常');
    expect(html).toContain('detail');
  });
});

describe('能力声明卡 (§5.2/§7.2)', () => {
  it('缺模型选择与缺单位标成警示, 并给出候选机制与检验', () => {
    const html = renderCapabilityCard({
      model_selection: {
        claims: { 'c1': { claim_type: 'theorem', declared_scheme: 'analysis',
                          selected_state: 'missing_selection', capability: 'no_model',
                          capability_action: 'request_model' } },
        models: [{ id: 'm1', version: '2', name: '模型', selected: true, sources: 3 }],
      },
      modeling: {
        mechanisms: [{ id: 'mech-1', name: '机制一', missing: ['变量关系'] }],
        distinguishing: [{ kind: 'experiment', statement: '检验一', discriminates: ['mech-1', 'mech-2'] }],
        terms: [{ name: 'x', unit: '' }],
      },
    });
    expect(html).toContain('问题类型与模型');
    expect(html).toContain('缺模型选择');
    expect(html).toContain('候选机制比较');
    expect(html).toContain('可区分检验');
    expect(html).toContain('单位缺失');
    expect(html).toContain('变量关系');
  });

  it('既无能力声明也无模型时不渲染该区块', () => {
    expect(renderCapabilityCard({})).toBe('');
  });
});

describe('工作台空态与错误态', () => {
  it('空态文案区分"未关联"与"未建立运行"', () => {
    expect(workbenchNoProjectHtml()).toContain('尚未关联理论研究项目');
    expect(workbenchMissingHtml('p1')).toContain('p1');
    expect(workbenchUnboundHtml('p2')).toContain('确认项目 ID');
    expect(workbenchNewSessionHtml()).toContain('新会话');
  });

  it('409 给出可选问题按钮 (data-action), 404 明确说明没有该问题', () => {
    const conflict = workbenchErrorHtml(409, {
      detail: { problems: [{ problem_id: 'q1' }] },
    });
    expect(conflict).toContain('data-action="selectProblem"');
    expect(conflict).toContain('data-id="q1"');
    expect(workbenchErrorHtml(404, { detail: { message: 'x' } }))
      .toContain('该项目下没有这个研究问题');
    expect(workbenchErrorHtml(500, { detail: { message: 'boom' } })).toContain('boom');
  });
});

describe('结论详情开关', () => {
  it('再次点击同一条结论即收起', () => {
    expect(toggledClaimId('', 'c1')).toBe('c1');
    expect(toggledClaimId('c1', 'c1')).toBe('');
    expect(toggledClaimId('c1', 'c2')).toBe('c2');
  });
});

describe('历史导航 (§9.5 renderHistory* 迁出)', () => {
  it('未收尾会话跳过最后一条 interrupt, 已完成的照常渲染', () => {
    const rec = {
      session_id: 's1', topic: 'T', status: 'waiting',
      messages: [
        { role: 'user', text: '问题' },
        { role: 'interrupt', title: '等待确认', hint: 'h' },
      ],
    };
    const resumable = conversationEntries(rec, true);
    expect(resumable).toHaveLength(1);
    expect(resumable[0].html).toContain('问题');
    const done = conversationEntries({ ...rec, status: 'done' }, true);
    expect(done).toHaveLength(2);
    expect(done[1].html).toContain('等待确认');
  });

  it('回看抽屉不跳过 interrupt', () => {
    const entries = conversationEntries({
      status: 'waiting',
      messages: [{ role: 'interrupt', title: 't', hint: 'h' }],
    }, false);
    expect(entries).toHaveLength(1);
  });

  it('消息文本里的 HTML 被转义', () => {
    const entries = conversationEntries({
      status: 'running',
      messages: [{ role: 'assistant', text: '<script>alert(1)</script>' }],
    }, false);
    expect(entries[0].html).not.toContain('<script>');
    expect(entries[0].html).toContain('&lt;script&gt;');
  });

  it('历史条目带 data-sid, 主题里的 HTML 被转义', () => {
    const html = historyListItemHtml({
      session_id: 's1', topic: '<b>x</b>', status: 'running',
    });
    expect(html).toContain('data-sid="s1"');
    expect(html).not.toContain('<b>x</b>');
    expect(historyListHtml([])).toContain('暂无历史会话');
  });

  it('下拉框条目: 主题/时间/状态齐备, 未知状态不伪装成已完成', () => {
    const option = historyOption({
      session_id: 's9', topic: '主题', status: 'weird',
      updated_at: '2026-09-24T11:30:05+00:00',
    });
    expect(option.value).toBe('s9');
    expect(option.label).toContain('主题');
    expect(option.label).toContain('09-24 11:30');
    expect(option.label).toContain('weird');
    expect(historyOption({ session_id: 's0' }).label).toContain('s0');
    expect(historyDetailTitle({})).toBe('会话回看');
  });

  it('上下文阶段标签: 未开始不显示成已完成', () => {
    expect(stageLabel([])).toBe('无');
    expect(stageLabel(undefined)).toBe('无');
    expect(stageLabel(['research'])).toBe('已检索');
    expect(stageLabel(['research', 'write'])).toBe('已撰写');
    expect(stageLabel(['a', 'b'])).toBe('a+b');
  });
});
