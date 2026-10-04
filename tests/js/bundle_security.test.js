// F3/F4 产物级校验: 直接加载 Vite 构建出的 bundle, 验证
// 1. `AIRMarkdown` 仍然只生成白名单标签与安全协议链接;
// 2. **页面逻辑真的在包里并且能初始化** —— 曾经 app.ts 从未被打包,
//    产物加载"成功"但所有按钮都没有反应, 只有真的驱动页面才发现。
//
// 为什么要在"产物"上再验一次: 源码测试通过不等于打包后行为不变 (压缩、摇树、
// 目标降级、入口漏 import 都可能改变结果)。这里加载 `src/web/assets/index-*.js`,
// 用最小 DOM 桩驱动渲染与初始化。
//
// 运行: node tests/js/bundle_security.test.js
'use strict';

const fs = require('fs');
const path = require('path');

const WEB = path.join(__dirname, '..', '..', 'src', 'web');
const ASSETS = path.join(WEB, 'assets');

const ALLOWED_TAGS = new Set(['DIV', 'P', 'BR', 'STRONG', 'EM', 'CODE', 'PRE', 'UL',
  'OL', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'HR', 'TABLE',
  'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'A']);

function makeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    nodeName: String(tag).toUpperCase(),
    childNodes: [],
    attributes: {},
    dataset: {},
    className: '',
    id: '',
    style: {},
    disabled: false,
    placeholder: '',
    value: '',
    textContent: '',
    scrollTop: 0,
    scrollHeight: 0,
    classList: {
      add() {}, remove() {}, toggle() {}, contains() { return false; },
    },
    appendChild(child) { this.childNodes.push(child); return child; },
    removeChild(child) {
      this.childNodes = this.childNodes.filter((c) => c !== child);
      return child;
    },
    /**
     * 团队视图要插在工作台容器**最前** (合并计划 §9.2: 先给团队状态, 再给旧工作台)。
     * 桩必须支持它 —— 否则页面初始化会在这里抛错, 而"插入位置"是真实需求, 不能用
     * `appendChild` 代替。
     */
    insertBefore(child, reference) {
      const index = reference ? this.childNodes.indexOf(reference) : -1;
      if (index < 0) { this.childNodes.push(child); return child; }
      this.childNodes.splice(index, 0, child);
      return child;
    },
    get firstChild() { return this.childNodes[0] || null; },
    remove() {},
    focus() {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html || ''; },
    set(v) { this._html = String(v); this.childNodes = []; },
  });
  return el;
}

/** 具名元素桩: 让页面初始化真的跑起来 (缺元素就是页面真实故障)。 */
const ELEMENT_IDS = [
  'status', 'reply', 'btn-send', 'btn-stop', 'btn-del', 'log', 'preview', 'workbench',
  'files', 'filebinding', 'runhint', 'researchctx', 'projid', 'probid', 'runmode',
  'topic', 'keywords', 'subtopics', 'maxrev', 'skipret', 'maxactions', 'maxtools',
  'sourceset', 'sourceinfo', 'theoryrow', 'sourcerow', 'modebadge', 'outtitle',
  'ctx', 'hist', 'history-overlay', 'history-list', 'history-detail', 'history-title',
  'btn-history-back', 'wbobj', 'wbfeedback', 'wbsnapid', 'clarifyobj', 'archives',
];

function installDom() {
  const registry = new Map();
  ELEMENT_IDS.forEach((id) => {
    const el = makeElement('div');
    el.id = id;
    registry.set(id, el);
  });
  const document = {
    readyState: 'complete',
    createElement: (tag) => makeElement(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: String(text) }),
    querySelector: (sel) => (sel && sel.startsWith('#') ? registry.get(sel.slice(1)) || null : null),
    querySelectorAll: () => [],
    getElementById: (id) => registry.get(id) || null,
    addEventListener: () => {},
    body: { appendChild: () => {} },
  };
  const window = {
    document,
    location: { origin: 'http://127.0.0.1:8000', href: 'http://127.0.0.1:8000/' },
    addEventListener: () => {},
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    EventSource: function EventSource() {
      return { close() {}, addEventListener() {}, set onmessage(_v) {}, set onerror(_v) {} };
    },
    setTimeout,
    clearTimeout,
    navigator: { clipboard: { writeText: async () => {} } },
  };
  window.window = window;
  global.window = window;
  global.document = document;
  global.self = window;
  // Node 的 global.navigator 只有 getter —— 用 defineProperty 覆盖而不是赋值
  Object.defineProperty(global, 'navigator', {
    value: window.navigator, configurable: true, writable: true,
  });
  // Vite 的 modulepreload polyfill 需要 MutationObserver; 这里给最小实现
  global.MutationObserver = class MutationObserver {
    observe() {}
    disconnect() {}
    takeRecords() { return []; }
  };
  global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  return window;
}

function walk(el, visit) {
  if (!el) return;
  visit(el);
  (el.childNodes || []).forEach((child) => {
    if (child && (child.tagName || child.nodeType === 3)) walk(child, visit);
  });
}

let failures = 0;
function check(name, condition, detail) {
  if (condition) {
    console.log('PASS', name);
  } else {
    failures += 1;
    console.log('FAIL', name, detail === undefined ? '' : detail);
  }
}

function bundleFile() {
  if (!fs.existsSync(ASSETS)) return null;
  const files = fs.readdirSync(ASSETS).filter((n) => n.endsWith('.js'));
  return files.length ? path.join(ASSETS, files[0]) : null;
}

const bundle = bundleFile();
if (!bundle) {
  console.log('FAIL 未找到构建产物: 请先运行 `npm run build` (src/web)');
  process.exit(1);
}

const win = installDom();
// bundle 是 ESM (type="module"); 用动态 import 加载, 并把 window/document 注入全局
(async () => {
  await import('file://' + bundle.replace(/\\/g, '/'));
  const api = win.AIRMarkdown;
  check('bundle 暴露 AIRMarkdown', Boolean(api && api.render));
  check('bundle 暴露 AIR', Boolean(win.AIR && win.AIR.api && win.AIR.research));
  // 统一入口: 页面打开即落实**草稿身份** (R2, 供附件/检索归属), 因此初始 projectId
  // 要么为空、要么是草稿 id (`proj-...`); 它**不是**运行绑定, 工作台不会拿它查状态。
  const bootProject = win.AIR ? String(win.AIR.research.projectId || '') : '';
  check('研究状态初始不绑定真实项目',
        win.AIR && (bootProject === '' || bootProject.startsWith('proj-')));
  // 统一入口: 前端不再预置任何"运行模式" —— 引擎标识只由服务端返回值填充,
  // 初始为空串表示"还不知道", 不假装默认是某种模式。
  check('状态对象不预置模式 (等后端返回)',
        win.AIR && win.AIR.research.mode === '');

  // 页面逻辑必须真的在包里, 且**委托动作**能从全局解析到 (模板已无内联处理器:
  // 严格 CSP 会拦掉内联事件处理器, 因此改由 data-action + 文档级委托分发)
  const HANDLERS = ['selectProblem', 'toggleClaimDetail', 'submitWorkbenchFeedback',
    'forkFromWorkbench', 'refreshWorkbench', 'resumeSameProblem', 'startNewProblem',
    'startWithResume', 'loadArtifact', 'resumeSession', 'resumeConversation',
    'viewConversation'];
  const missing = HANDLERS.filter((name) => typeof win[name] !== 'function');
  check('产物包含页面逻辑并暴露全部委托动作', missing.length === 0, missing.join(','));
  check('产物暴露工作台绑定函数', typeof win.refreshWorkbench === 'function');

  const el = api.render('<script>alert(1)</script>[x](javascript:alert(1))', { className: '' });
  const tags = [];
  const hrefs = [];
  walk(el, (node) => {
    if (node.tagName) tags.push(node.tagName.toUpperCase());
    if (node.tagName === 'A') hrefs.push(node.getAttribute('href'));
  });
  check('产物不生成 SCRIPT 元素', !tags.includes('SCRIPT'), tags.join(','));
  check('产物标签均在白名单', tags.every((t) => ALLOWED_TAGS.has(t)), tags.join(','));
  check('产物不生成危险链接', hrefs.every((h) => /^(https?:|mailto:)/.test(h)),
        hrefs.join('|'));

  check('safeHref 拒绝 javascript:', api.safeHref('javascript:alert(1)') === '');
  check('safeHref 允许 https', api.safeHref('https://e.com') === 'https://e.com');

  if (failures) {
    console.log(`\n${failures} 项失败`);
    process.exit(1);
  }
  console.log('\n全部通过');
})();
