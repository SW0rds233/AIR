/**
 * 安全渲染模块的用例 (计划书 §2 F3)。
 *
 * 反向安全测试: 产物与检索内容可能含外部文本, 渲染结果**只能**是白名单标签,
 * 且只生成 http/https/mailto 链接。
 */
import { describe, expect, it } from 'vitest';

import { SAFE_TAGS, render, renderText, safeHref } from '../src/markdown';

function walk(el: Element, visit: (node: Element) => void): void {
  visit(el);
  Array.from(el.children).forEach((child) => walk(child, visit));
}

function tagsOf(el: Element): string[] {
  const out: string[] = [];
  walk(el, (node) => out.push(node.tagName.toUpperCase()));
  return out;
}

describe('safeHref', () => {
  it('允许 http/https/mailto', () => {
    expect(safeHref('https://example.com/a')).toBe('https://example.com/a');
    expect(safeHref('http://example.com')).toBe('http://example.com');
    expect(safeHref('mailto:a@b.c')).toBe('mailto:a@b.c');
  });

  it('拒绝 javascript:/data:/vbscript:', () => {
    expect(safeHref('javascript:alert(1)')).toBe('');
    expect(safeHref('data:text/html,<script>alert(1)</script>')).toBe('');
    expect(safeHref('vbscript:msgbox(1)')).toBe('');
  });

  it('拒绝协议相对 URL (//evil)', () => {
    expect(safeHref('//evil.example.com/x')).toBe('');
  });

  it('允许相对路径', () => {
    expect(safeHref('outputs/x.md')).toBe('outputs/x.md');
  });
});

describe('render', () => {
  it('原始 HTML 不会变成元素, 只作为文本', () => {
    const el = render('<script>alert(1)</script><img src=x onerror=alert(2)>',
                      { className: '' });
    const tags = tagsOf(el);
    expect(tags).not.toContain('SCRIPT');
    expect(tags).not.toContain('IMG');
    expect(el.textContent).toContain('<script>alert(1)</script>');
    expect(tags.every((t) => SAFE_TAGS.map((s) => s.toUpperCase()).includes(t)))
      .toBe(true);
  });

  it('危险链接降级为文本', () => {
    const el = render('[x](javascript:alert(1)) [ok](https://e.com)', { className: '' });
    const anchors = el.querySelectorAll('a');
    expect(anchors.length).toBe(1);
    expect(anchors[0].getAttribute('href')).toBe('https://e.com');
    expect(el.textContent).toContain('（链接协议不受支持）');
  });

  it('结构渲染产出预期标签', () => {
    const el = render([
      '# 标题', '', '段落 **粗体** *斜体* `code`', '',
      '- a', '- b', '', '1. x', '', '```', 'print("<hi>")', '```', '',
      '| A | B |', '|---|---|', '| 1 | 2 |', '', '> 引用 <b>tag</b>',
    ].join('\n'), { className: '' });
    const tags = tagsOf(el);
    ['H1', 'P', 'STRONG', 'EM', 'CODE', 'UL', 'OL', 'LI', 'PRE',
     'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'BLOCKQUOTE']
      .forEach((tag) => expect(tags).toContain(tag));
    // 引用里的 <b> 不成为元素
    expect(tags).not.toContain('B');
    expect(el.textContent).toContain('print("<hi>")');
  });

  it('空输入与 null 不报错', () => {
    expect(render('', { className: '' }).children.length).toBe(0);
    expect(render(null, { className: '' }).children.length).toBe(0);
  });
});

describe('renderText', () => {
  it('只产生 PRE 且保留原始字符', () => {
    const el = renderText('<b>x</b>\n第二行', { className: '' });
    expect(el.tagName).toBe('PRE');
    expect(el.textContent).toBe('<b>x</b>\n第二行');
    expect(el.children.length).toBe(0);
  });
});
