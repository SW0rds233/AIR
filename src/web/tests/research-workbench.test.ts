/**
 * 工作台视图契约与标签映射的单元测试 (计划书 §2 F4 / §3 R6)。
 *
 * 这些函数曾内联在 1700 行的遗留页面脚本里, 既没有类型也没有测试; 拆到
 * `views/research-workbench.ts` 后必须在这里验证**安全边界**:
 * - 任何来自服务端的文本都要转义 (工作台片段是拼 HTML 字符串的);
 * - 没有对应结论时不产出详情行 (不得把别的结论的义务/证据挂上去);
 * - 运行身份 (project/problem/run) 如实显示, 不猜。
 */
import { describe, expect, it } from 'vitest';

import {
  claimDetailRow,
  coverageLabel,
  escapeHtml,
  executionLabel,
  feedbackObjectOptions,
  identityLine,
  routeLabel,
  statusLabelRaw,
  statusTag,
  supportKindLabel,
  supportRelationLabel,
  wbEsc,
} from '../src/views/research-workbench';

describe('转义', () => {
  it('转义 HTML 元字符', () => {
    expect(escapeHtml('<img src=x onerror=alert(1)>'))
      .toBe('&lt;img src=x onerror=alert(1)&gt;');
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
    expect(escapeHtml(0)).toBe('0');
  });

  it('wbEsc 与 escapeHtml 行为一致', () => {
    expect(wbEsc('<b>')).toBe(escapeHtml('<b>'));
  });
});

describe('标签映射', () => {
  it('已知状态给出中文标签, 未知状态原样返回', () => {
    expect(statusLabelRaw('supported')).toBe('已成立');
    expect(statusLabelRaw('counterexample_found')).toBe('有反例');
    expect(statusLabelRaw('brand_new_state')).toBe('brand_new_state');
    expect(statusLabelRaw('')).toBe('未知');
  });

  it('statusTag 只给白名单状态加颜色类, 其余用 neutral', () => {
    expect(statusTag('refuted')).toBe('<span class="tag t-refuted">被否定</span>');
    expect(statusTag('<script>')).toBe('<span class="tag t-neutral">&lt;script&gt;</span>');
  });

  it('其余标签映射回退为连字符而不是 undefined', () => {
    expect(supportKindLabel('formal_proof')).toBe('形式化证明');
    expect(supportKindLabel('nope')).toBe('nope');
    expect(coverageLabel('target')).toBe('完整目标');
    expect(coverageLabel(undefined)).toBe('-');
    expect(supportRelationLabel('contradicts')).toBe('反对');
    expect(executionLabel('spec_validated')).toBe('规格已校验（未执行）');
    expect(routeLabel('suspended')).toBe('已挂起');
  });
});

describe('反馈对象选择器', () => {
  it('默认项请求澄清，其余只列出可被后端定位的研究对象', () => {
    const options = feedbackObjectOptions({
      assumptions: { 'asm-1': 'x 为实数' },
      claims: { 'clm-1': 'x^2 >= 0' },
      obligations: { 'obl-1': '证明非负性' },
    });
    expect(options[0].value).toBe('');
    expect(options.map(o => o.value)).toEqual(['', 'asm-1', 'clm-1', 'obl-1']);
    expect(options[1].label).toContain('假设');
    expect(options[2].label).toContain('结论');
    expect(options[3].label).toContain('证明义务');
  });

  it('空输入也要给出默认项 (不能是空列表)', () => {
    expect(feedbackObjectOptions()).toHaveLength(1);
    expect(feedbackObjectOptions({}).map(o => o.value)).toEqual(['']);
  });

  it('长陈述被截断, 避免下拉框被整篇正文撑爆', () => {
    const long = '甲'.repeat(200);
    const options = feedbackObjectOptions({ claims: { 'clm-1': long } });
    expect(options[1].label.length).toBeLessThan(80);
  });
});

describe('结论详情', () => {
  const data = {
    project_id: 'proj-a',
    problem_id: 'pA',
    run_id: 'run-1',
    branch_id: 'rte-1',
    claims: [{ id: 'clm-1', statement: 'x^2 >= 0' }],
    obligations: [
      { id: 'obl-1', claim_id: 'clm-1', statement: 'x^2 >= 0', status: 'closed' },
      { id: 'obl-2', claim_id: 'clm-2', statement: '别的结论的义务', status: 'open' },
    ],
    evidence: [
      { id: 'ev-1', claim_id: 'clm-1', title: '文献 A', support: 'supports',
        locator: 'p.3', support_reason: '条件一致' },
      { id: 'ev-2', claim_id: 'clm-2', title: '文献 B', support: 'supports' },
    ],
    verifications: [
      { id: 'ver-1', claim_id: 'clm-1', tool: 'sympy', validation_status: 'verified',
        certificate: 'x**2 >= 0' },
      { id: 'ver-2', claim_id: 'clm-2', tool: 'z3', validation_status: 'verified' },
    ],
  };

  it('只汇总该结论自己的义务/证据/验证', () => {
    const html = claimDetailRow(data, 'clm-1');
    expect(html).toContain('wb-detail-row');
    expect(html).toContain('obl-1');
    expect(html).toContain('文献 A');
    expect(html).toContain('sympy');
    expect(html).not.toContain('obl-2');
    expect(html).not.toContain('文献 B');
    expect(html).not.toContain('z3');
  });

  it('没有该结论时不产出任何详情行', () => {
    expect(claimDetailRow(data, 'clm-nope')).toBe('');
    expect(claimDetailRow(null, 'clm-1')).toBe('');
  });

  it('服务端文本一律转义 (详情片段是拼 HTML 的)', () => {
    const html = claimDetailRow({
      claims: [{ id: 'clm-1', statement: 'x' }],
      evidence: [{ id: 'ev-1', claim_id: 'clm-1', title: '<img onerror=x>',
                   support_reason: '</div><script>alert(1)</script>' }],
    }, 'clm-1');
    expect(html).not.toContain('<img onerror=x>');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img onerror=x&gt;');
  });

  it('无义务/无证据/无验证时如实说明缺什么', () => {
    const html = claimDetailRow({ claims: [{ id: 'clm-1', statement: 'x' }] }, 'clm-1');
    expect(html).toContain('无义务记录');
    expect(html).toContain('该结论尚无归属证据');
    expect(html).toContain('暂无验证记录');
  });
});

describe('运行身份', () => {
  it('显示项目/问题/运行, 缺失字段不占位', () => {
    expect(identityLine({ project_id: 'p', problem_id: 'q', run_id: 'r' }))
      .toBe('项目 p · 问题 q · 运行 r');
    expect(identityLine({ project_id: 'p' })).toBe('项目 p');
    expect(identityLine(null)).toBe('');
  });
});
