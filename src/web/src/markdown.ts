/*!
 * AIR 2026-09-23 前端安全渲染模块 (计划书 §2 F3)。
 *
 * 为什么不用 marked:
 * 1. 页面原来从 jsdelivr CDN 加载 `marked@12`, 这既是"本地应用却依赖外部脚本",
 *    也让离线环境直接失去渲染能力;
 * 2. [Marked 官方明确说明其输出不做 HTML 消毒](https://marked.js.org/), 而
 *    产物与检索内容可能含外部文本 —— 直接 `innerHTML = marked.parse(x)` 等于
 *    把外部文本当 HTML 执行。
 *
 * 本模块的渲染器**只使用 DOM API 构造节点** (`createElement` / `textContent`),
 * 全程不拼接 HTML 字符串、不给 `innerHTML` 赋值, 因此不存在注入面; 链接还会做
 * 协议白名单 (`http/https/mailto`), 其它协议一律降级为纯文本。
 *
 * 覆盖范围: 标题、段落、无序/有序列表、围栏代码块、行内代码、加粗/斜体、
 * 表格、水平线、引用块。纯文本文件走 `renderText()`。
 */

export interface RenderOptions {
  tag?: string;
  className?: string;
}

export const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:'];

export const SAFE_TAGS = ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'thead',
  'tbody', 'tr', 'th', 'td', 'a', 'div', 'span'];

/** 只允许 http/https/mailto; 其它协议返回空串。 */
export function safeHref(raw: unknown): string {
  const text = String(raw == null ? '' : raw).trim();
  if (!text) return '';
  // 显式解析协议: 只允许白名单。
  // 注意不能用 `new URL(text, base)` 之后只看 protocol —— 那会把
  // `javascript:alert(1)` 当作相对路径解析成 `https://base/javascript:alert(1)`,
  // 于是危险协议被"洗白"成合法链接。
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(text);
  if (!scheme) {
    // 无协议 → 相对路径; 但以 `//` 开头是协议相对 URL, 需要拒绝
    return text.startsWith('//') ? '' : text;
  }
  const proto = scheme[1].toLowerCase() + ':';
  if (!SAFE_PROTOCOLS.includes(proto)) return '';
  try {
    const url = new URL(text);
    return SAFE_PROTOCOLS.includes(url.protocol) ? text : '';
  } catch (err) {
    return '';
  }
}

// 把一段文本按行内语法拆成 DOM 片段 (加粗/斜体/行内代码/链接)
function inlineInto(parent: HTMLElement, text: unknown): void {
  const source = String(text == null ? '' : text);
  const pattern = /(`[^`]*`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > cursor) {
      parent.appendChild(document.createTextNode(source.slice(cursor, match.index)));
    }
    const token = match[0];
    if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      parent.appendChild(code);
    } else if (token.startsWith('**')) {
      const strong = document.createElement('strong');
      strong.textContent = token.slice(2, -2);
      parent.appendChild(strong);
    } else if (token.startsWith('*')) {
      const em = document.createElement('em');
      em.textContent = token.slice(1, -1);
      parent.appendChild(em);
    } else {
      const label = token.slice(1, token.indexOf(']'));
      const href = safeHref(token.slice(token.indexOf('](') + 2, -1));
      if (href) {
        const link = document.createElement('a');
        link.textContent = label;
        link.setAttribute('href', href);
        link.setAttribute('rel', 'noopener noreferrer');
        link.setAttribute('target', '_blank');
        parent.appendChild(link);
      } else {
        // 不认识的协议 → 只显示文字, 不生成链接
        parent.appendChild(document.createTextNode(label + '（链接协议不受支持）'));
      }
    }
    cursor = match.index + token.length;
  }
  if (cursor < source.length) {
    parent.appendChild(document.createTextNode(source.slice(cursor)));
  }
}

function tableRow(cells: string[], cellTag: 'th' | 'td'): HTMLTableRowElement {
  const tr = document.createElement('tr');
  cells.forEach((cell) => {
    const td = document.createElement(cellTag);
    inlineInto(td, cell.trim());
    tr.appendChild(td);
  });
  return tr;
}

/** 把 Markdown 子集渲染成 DOM 节点 (不使用 innerHTML)。 */
export function render(text: unknown, options: RenderOptions = {}): HTMLElement {
  const host = document.createElement(options.tag || 'div');
  host.className = options.className === undefined ? 'markdown' : options.className;
  const lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
  let i = 0;
  let list: HTMLElement | null = null;
  let listTag = '';

  function closeList() { list = null; listTag = ''; }

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块
    if (/^\s*```/.test(line)) {
      closeList();
      i += 1;
      const body: string[] = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;   // 跳过结束围栏
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = body.join('\n');
      pre.appendChild(code);
      host.appendChild(pre);
      continue;
    }

    // 标题
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length, 6);
      const h = document.createElement(`h${level}`);
      inlineInto(h, heading[2].trim());
      host.appendChild(h);
      i += 1;
      continue;
    }

    // 水平线
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      closeList();
      host.appendChild(document.createElement('hr'));
      i += 1;
      continue;
    }

    // 表格 (需要分隔行)
    if (line.indexOf('|') >= 0 && i + 1 < lines.length
        && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      closeList();
      const table = document.createElement('table');
      table.className = 'md-table';
      const headerCells = line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
      const thead = document.createElement('thead');
      thead.appendChild(tableRow(headerCells, 'th'));
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      i += 2;
      while (i < lines.length && lines[i].indexOf('|') >= 0 && lines[i].trim()) {
        const cells = lines[i].replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
        tbody.appendChild(tableRow(cells, 'td'));
        i += 1;
      }
      table.appendChild(tbody);
      host.appendChild(table);
      continue;
    }

    // 引用块
    if (/^\s*>/.test(line)) {
      closeList();
      const blockquote = document.createElement('blockquote');
      const collected: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        collected.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      inlineInto(blockquote, collected.join(' '));
      host.appendChild(blockquote);
      continue;
    }

    // 列表
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || ordered) {
      const wanted = bullet ? 'ul' : 'ol';
      if (!list || listTag !== wanted) {
        closeList();
        list = document.createElement(wanted);
        listTag = wanted;
        host.appendChild(list);
      }
      const li = document.createElement('li');
      inlineInto(li, (bullet || ordered)![1]);
      list!.appendChild(li);
      i += 1;
      continue;
    }

    // 空行
    if (!line.trim()) {
      closeList();
      i += 1;
      continue;
    }

    // 段落 (连续非空行合并)
    closeList();
    const chunk = [line.trim()];
    i += 1;
    while (i < lines.length && lines[i].trim()
           && !/^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```)/.test(lines[i])
           && lines[i].indexOf('|') < 0) {
      chunk.push(lines[i].trim());
      i += 1;
    }
    const p = document.createElement('p');
    inlineInto(p, chunk.join(' '));
    host.appendChild(p);
  }
  return host;
}

/** 纯文本: 保留换行, 不使用任何 HTML 解析。 */
export function renderText(text: unknown, options: RenderOptions = {}): HTMLElement {
  const pre = document.createElement('pre');
  pre.className = options.className === undefined ? 'plain' : options.className;
  pre.textContent = String(text == null ? '' : text);
  return pre;
}

export const AIRMarkdown = {
  SAFE_PROTOCOLS,
  SAFE_TAGS,
  render,
  renderText,
  safeHref,
};

if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).AIRMarkdown = AIRMarkdown;
}

export default AIRMarkdown;
