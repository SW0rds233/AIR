import { expect, test } from 'vitest';
import { appendMessage, messageElement } from '../src/views/conversation';
import { conversationEntries } from '../src/views/project-navigation';

test('live and history safely format markdown and collapse long activity', () => {
  const message = { role: 'tool', text: '**检索结果**\n' + '来源详情 '.repeat(80) + '<img src=x onerror=alert(1)>' };
  const row = messageElement(message);
  expect(row.querySelector('details')?.open).toBe(false);
  expect(row.querySelector('strong')?.textContent).toBe('检索结果');
  expect(row.querySelector('img')).toBeNull();
  expect(conversationEntries({ messages: [message] })[0].html).toBe(row.outerHTML);
});

test('new messages do not move a reader away from older content', () => {
  const parent = document.createElement('div');
  const log = document.createElement('div');
  parent.append(log);
  Object.defineProperties(log, { scrollHeight: { value: 1000 }, clientHeight: { value: 200 } });
  log.scrollTop = 120;
  appendMessage(log, { role: 'node', text: '新进展' });
  expect(log.scrollTop).toBe(120);
  const jump = parent.querySelector('button')!;
  expect(jump.hidden).toBe(false);
  jump.click();
  expect(log.scrollTop).toBe(1000);
  expect(jump.hidden).toBe(true);
});
