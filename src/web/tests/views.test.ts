/**
 * 交付物与会话历史视图契约的单元测试 (计划书 §2 F3 / §4 `views/*`)。
 *
 * 安全与正确性边界:
 * - 文件清单必须说清"这是谁的交付物"; 未归属文件不得冒充当前问题的产出;
 * - 文件大小/路径/文本一律转义, 不因外部文件名注入 HTML;
 * - 会话历史只有未收尾的才提供"继续"; 状态未知不得伪装成"已完成"。
 */
import { describe, expect, it } from 'vitest';

import {
  artifactClass,
  artifactNameFromPath,
  artifactNote,
  artifactQuery,
  artifactRowHtml,
  basename,
  bindingScope,
  formatSize,
} from '../src/views/artifacts';
import {
  conversationTitle,
  fmtTime,
  historyItemHtml,
  isResumable,
  messageAvatar,
  messageClass,
  statusLabel,
} from '../src/views/conversation';

describe('交付物路径与体积', () => {
  it('basename 兼容 Windows 反斜杠', () => {
    expect(basename('E:\\AIR\\outputs\\run1\\manuscript.md')).toBe('manuscript.md');
    expect(basename('a/b/c.json')).toBe('c.json');
    expect(basename('')).toBe('');
    expect(basename(null)).toBe('');
  });

  it('artifactNameFromPath 取出 outputs/ 之后的相对名', () => {
    expect(artifactNameFromPath('E:\\AIR\\outputs\\run-1\\manuscript.md'))
      .toBe('run-1/manuscript.md');
    expect(artifactNameFromPath('/srv/air/outputs/a/b/paper.tex')).toBe('a/b/paper.tex');
    // 不在 outputs/ 下时退回文件名 (仍然可用于请求)
    expect(artifactNameFromPath('/tmp/notes.md')).toBe('notes.md');
  });

  it('体积格式化: 未知大小显示 - 而不是 0 B', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2048)).toBe('2.0 KB');
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(formatSize(undefined)).toBe('-');
    expect(formatSize(-1)).toBe('-');
  });
});

describe('文件清单绑定说明', () => {
  it('未绑定问题时说明这是 outputs/ 全部文件', () => {
    expect(bindingScope({}, {})).toContain('未绑定研究问题');
    expect(bindingScope({}, {})).toContain('未归属');
  });

  it('绑定后同时显示项目/问题/运行与匹配的交付包数', () => {
    const text = bindingScope({ roots: {a: {}, b: {}} },
                              {projectId: 'p', problemId: 'q', runId: 'r'});
    expect(text).toContain('项目 p');
    expect(text).toContain('问题 q');
    expect(text).toContain('运行 r');
    expect(text).toContain('2 个');
  });

  it('查询串只带真实身份, 不猜 run', () => {
    expect(artifactQuery({projectId: 'p'})).toBe('project_id=p');
    expect(artifactQuery({problemId: 'q', runId: 'r'})).toBe('problem_id=q&run_id=r');
    expect(artifactQuery({})).toBe('');
  });
});

describe('文件行', () => {
  it('未归属文件必须显式标出, 且不显示级别', () => {
    expect(artifactNote({name: 'x', unattributed: true, delivery_level: '论文草稿'}))
      .toBe(' · 未归属');
    expect(artifactNote({name: 'x', delivery_level: '论文草稿'})).toContain('论文草稿');
    expect(artifactNote({name: 'x'})).toBe('');
    expect(artifactClass({name: 'x', unattributed: true})).toContain('unattributed');
    expect(artifactClass({name: 'x'})).toBe('file-item');
  });

  it('目录与文件名照常显示', () => {
    const html = artifactRowHtml({name: 'run-1/manuscript.md', size: 2048});
    expect(html).toContain('run-1/manuscript.md');
    expect(html).toContain('2.0 KB');
  });

  it('文件名里的 HTML 被转义 (不因外部文件名注入)', () => {
    const html = artifactRowHtml({name: '<img src=x onerror=alert(1)>.md', size: 1});
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });
});

describe('会话历史', () => {
  it('只有未收尾的会话才可继续', () => {
    expect(isResumable('running')).toBe(true);
    expect(isResumable('waiting')).toBe(true);
    expect(isResumable('error')).toBe(true);
    expect(isResumable('done')).toBe(false);
  });

  it('状态标签: 已知状态给中文, 未知状态原样返回', () => {
    expect(statusLabel('waiting')).toBe('等待中');
    expect(statusLabel('weird')).toBe('weird');
    expect(statusLabel(undefined)).toBe('未知');
  });

  it('列表条目: 已完成的会话不出现"继续"按钮', () => {
    const done = historyItemHtml({session_id: 's1', topic: 'T', status: 'done',
                                  message_count: 3});
    expect(done).not.toContain('hist-resume');
    const running = historyItemHtml({session_id: 's1', topic: 'T', status: 'running'});
    expect(running).toContain('hist-resume');
    expect(running).toContain('运行中');
  });

  it('理论会话带模式标记; 未归属字段回退到 session_id', () => {
    const html = historyItemHtml({session_id: 's9', status: 'running',
                                  request: {mode: 'theory'}});
    expect(html).toContain('理论');
    expect(html).toContain('s9');
    expect(conversationTitle({session_id: 's9'})).toBe('s9');
  });

  it('主题里的 HTML 被转义', () => {
    const html = historyItemHtml({session_id: 's1', status: 'running',
                                  topic: '<script>alert(1)</script>'});
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('时间格式化对畸形输入不抛错', () => {
    expect(fmtTime('2026-09-24T11:30:05+00:00')).toBe('09-24 11:30');
    expect(fmtTime('2026-09-24 11:30:05')).toBe('09-24 11:30');
    expect(fmtTime('not-a-time')).toBe('not-a-time');
    expect(fmtTime('')).toBe('');
    expect(fmtTime(null)).toBe('');
  });
});

describe('消息角色', () => {
  it('角色映射到样式类与头像', () => {
    expect(messageClass({role: 'user'})).toBe('msg-user');
    expect(messageClass({role: 'node'})).toBe('msg-node');
    expect(messageClass({role: 'done'})).toBe('msg-done');
    expect(messageClass({role: 'stopped'})).toBe('msg-agent');
    expect(messageClass({role: 'unknown'})).toBe('msg-agent');
    expect(messageClass(null)).toBe('msg-agent');
    expect(messageAvatar({role: 'user'})).toBe('我');
    expect(messageAvatar({role: 'node'})).toBe('·');
    expect(messageAvatar({role: 'assistant'})).toBe('AI');
  });
});
