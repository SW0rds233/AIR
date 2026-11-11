import { expect, test } from 'vitest';
import { render } from '../src/markdown';

test('manuscript math is separated and trace markers stay hidden', () => {
  const node = render('正文\n$$ x=1 $$\n<!-- block:blk-1 -->\n\n变量 $q$。\n$$\ny=2\n$$');
  expect([...node.querySelectorAll('.math-display')].map(el => el.textContent)).toEqual(['x=1', 'y=2']);
  expect(node.querySelector('.math-inline')?.textContent).toBe('q');
  expect((node.querySelector('#blk-1') as HTMLElement).hidden).toBe(true);
  expect(node.textContent).not.toContain('<!--');
});

test('formula fallback cannot execute HTML and fenced code stays literal', () => {
  const node = render('$$ <img src=x onerror=alert(1)> $$\n```\n$$x=1$$\n<!-- block:x -->\n```');
  expect(node.querySelector('img')).toBeNull();
  expect(node.querySelectorAll('.math-display')).toHaveLength(1);
  expect(node.querySelectorAll('pre')[1].textContent).toContain('<!-- block:x -->');
});
