/**
 * 页面逻辑 (计划书 §2 F4): 从 index.html 的内联 <script> 迁出的页面逻辑。
 *
 * 迁出原因: 严格 CSP (`script-src 'self'`) 既不允许内联脚本, **也不允许内联事件处理器**
 * (浏览器直接报 CSP 违规, 页面能加载但按钮全部失效), 因此模板只保留结构, 事件在
 * `bindStaticHandlers()` 与 `bindDelegatedActions()` 里显式绑定。
 *
 * 类型状态 (P2): 本文件已移除 `@ts-nocheck`, 通过 `tsc --strict`。未类型化的部分只剩
 * **后端 JSON 载荷的字段访问**, 这些位置显式写成 `any`, 载荷形状由 `contracts.ts` 描述。
 */
// 注意: 本文件是**入口 main.ts 的依赖**, 不能反过来 import main.ts (循环依赖会让
// `window.AIR` 在页面逻辑执行时还不存在)。全局契约由 air-global.ts 建立, 这里只引用。
import { air as AIR } from './air-global';
import type { ResearchPatch, RunMode, RunStatus } from './current-research';
// P2 逐段类型化: `byId('id')!.value` 这类直接取值改为类型化封装 (见 dom.ts)
import {
  byId, checked, scrollToBottom, setDisabled, setHtml, setPlaceholder, setProp,
  setText, setVal, val,
} from './dom';
// P2: 附件上传 (补充问题说明 / 人工补充文献)
import {
  formatSize, kindLabel, parseLabel, problemAttachmentIds, shortHash,
  summarizeResults, validateFiles, type AttachmentView, type UploadKind,
} from './uploads';

// F4: 工作台只读契约与标签映射搬到 views/research-workbench.ts (纯函数, 可单测/可类型检查)
import {
  OBJECT_KIND_LABEL,
  claimDetailRow as wbClaimDetailRow,
  coverageLabel,
  executionLabel,
  escapeHtml,
  feedbackObjectOptions,
  routeLabel,
  statusLabelRaw,
  statusTag,
  supportKindLabel,
  supportRelationLabel,
  wbEsc,
} from './views/research-workbench';
import type { WorkbenchData } from './views/research-workbench';
// F4: 交付物与会话历史视图的纯函数搬到 views/* (有类型, 可单测)
import {
  artifactClass as artifactRowClass,
  artifactNameFromPath,
  artifactQuery,
  artifactRowHtml,
  basename,
  bindingScope,
} from './views/artifacts';
import {
  fmtTime,
  historyItemHtml,
  messageAvatar,
  messageClass,
  statusLabel,
} from './views/conversation';

let currentThreadId: string | null = null;
let currentSessionId: string | null = null;
let es: EventSource | null = null;

let mode = 'idle';            // 会话状态: idle / running / waiting
let runMode = 'survey';       // 运行模式: survey / theory
let currentContext = '';      // 综述模式选中的资料库主题 (与理论问题不是同一个字段)
let currentProjectId = '';    // 理论研究项目 ID (反馈/派生/工作台)
let currentProblemId = '';
let pendingInterruptId = '';  // 当前等待回答的暂停点 ID (F2 幂等键)

/* ---------------- 当前研究状态 (计划书 §2 F1) ----------------
 * 一次上下文切换必须让**所有**视图跟着走: 标题、问题、预算、反馈目标、
 * 文件与 SSE 事件都读取这里, 而不是各自持有副本。早期实现把 ID 散落在
 * 多个全局变量里, 切换历史会话时只恢复了线程, 模式和项目字段会沿用上一个会话。
 *
 * F1/F4: 状态的**唯一权威**是 main.ts 暴露的 `window.AIR.research`
 * (纯函数状态机 current-research.ts + Vitest 用例)。此前 app.ts 自己又定义了
 * 一份同名字段的对象, 两份状态各自更新 —— 这正是计划书 F1 要消除的"页面状态与
 * 用户研究意图不一致"的成因。这里只保留薄适配层, 不再持有第二份状态。
 */
const AIR_STATE = (typeof window !== 'undefined' && window.AIR) ? window.AIR : AIR;

const currentResearch = {
  threadId: '',
  sessionId: '',
  mode: 'survey',
  projectId: '',
  problemId: '',
  // 综述模式下选中的资料库主题; 与理论问题分开保存, 互不覆盖
  contextTopic: '',
  runId: '',
  status: 'idle',              // idle / running / waiting / done
  reset() {
    if (AIR_STATE) AIR_STATE.reset();
    this.pull();
    syncResearchGlobals();
    return this;
  },
  apply(patch: ResearchPatch) {
    if (AIR_STATE) AIR_STATE.apply(patch || {});
    this.pull();
    syncResearchGlobals();
    return this;
  },
  /** 从权威状态同步本文件的本地投影 (两个文件不得各自维护一份状态)。 */
  pull() {
    if (!AIR_STATE) return this;
    const src = AIR_STATE.research;
    this.threadId = src.threadId || '';
    this.sessionId = src.sessionId || '';
    this.mode = src.mode || 'survey';
    this.projectId = src.projectId || '';
    this.problemId = src.problemId || '';
    this.contextTopic = src.contextTopic || '';
    this.runId = src.runId || '';
    this.status = src.status || 'idle';
    return this;
  },
  /** 是否存在权威状态 (没有时退化为纯本地对象, 仍保持可用的降级行为)。 */
  canonical() { return Boolean(AIR_STATE); },
  /** 把本地直接修改的字段回写到权威状态 (调用方随后调用 syncResearchGlobals)。 */
  push() {
    if (!AIR_STATE) return this;
    AIR_STATE.apply({
      threadId: this.threadId, sessionId: this.sessionId,
      mode: this.mode as RunMode,
      projectId: this.projectId, problemId: this.problemId,
      contextTopic: this.contextTopic, runId: this.runId,
      status: this.status as RunStatus,
    });
    return this;
  },
  // 是否已绑定一个理论研究问题 (工作台/反馈/派生都需要)
  hasProblem() { return AIR_STATE ? AIR_STATE.hasProblem() : Boolean(this.projectId); },
  label() {
    if (AIR_STATE) return AIR_STATE.label();
    const bits = [this.mode === 'theory' ? '理论研究' : '综述'];
    if (this.problemId) bits.push(this.problemId);
    else if (this.projectId) bits.push(this.projectId);
    if (this.status && this.status !== 'idle') bits.push(statusLabelOf(this.status) || this.status);
    return bits.join(' · ');
  },
};

function labelOf(map: Record<string, string>, key: string): string {
  return map && map[key] !== undefined ? map[key] : key;
}

function statusLabelOf(status: string): string {
  return labelOf(STATUS_LABEL as Record<string, string>, status);
}

const STATUS_LABEL = {idle: '就绪', running: '运行中', waiting: '等待输入', done: '已完成'};

// 让既有渲染路径读到同一份状态 (旧变量保留为投影, 避免各处重复维护)
function syncResearchGlobals() {
  // 本地直接改字段的地方 (setStatus/onModeChange/...) 也要回写到权威状态,
  // 否则同一份"当前研究"又会出现两个不同步的副本 (F1)。
  if (currentResearch.canonical()) {
    currentResearch.pull();
  } else if (AIR_STATE) {
    AIR_STATE.apply({
      threadId: currentResearch.threadId, sessionId: currentResearch.sessionId,
      mode: currentResearch.mode as RunMode, projectId: currentResearch.projectId,
      problemId: currentResearch.problemId,
      contextTopic: currentResearch.contextTopic, runId: currentResearch.runId,
      status: currentResearch.status as RunStatus,
    });
  }
  currentThreadId = currentResearch.threadId || null;
  currentSessionId = currentResearch.sessionId || null;
  runMode = currentResearch.mode || 'survey';
  currentProjectId = currentResearch.projectId || '';
  currentProblemId = currentResearch.problemId || '';
  currentContext = currentResearch.contextTopic || '';
  const badge = byId('researchctx')!;
  if (badge) {
    badge.textContent = currentResearch.label();
    badge.className = 'ctx-badge' + (currentResearch.status === 'running' ? ' running' : '');
  }
}

const $ = (id: any) => document.getElementById(id);
const statusEl = byId('status')!;

// 初始投影 (必须在 $ 与 STATUS_LABEL 之后: label() 依赖它们)
currentResearch.pull();

function setStatus(text: any, cls: any) {
  statusEl.textContent = text;
  statusEl.className = cls ? ('dot-' + cls) : '';
}

function setMode(m: any) {
  mode = m;
  currentResearch.status = m === 'running' ? 'running'
    : (m === 'waiting' ? 'waiting' : (mode === 'idle' ? 'idle' : 'done'));
  currentResearch.push();
  const input = byId<HTMLTextAreaElement>('reply')!;
  const btn = byId<HTMLButtonElement>('btn-send')!;
  const stop = byId<HTMLButtonElement>('btn-stop')!;
  if (m === 'idle') {
    input.disabled = false;
    input.placeholder = defaultPlaceholder();
    btn.textContent = '启动';
    btn.disabled = false;
    stop.disabled = true;
  } else if (m === 'running') {
    input.disabled = true;
    input.placeholder = '运行中…';
    btn.textContent = '发送';
    btn.disabled = true;
    stop.disabled = false;
  } else { // waiting
    input.disabled = false;
    btn.textContent = '发送';
    btn.disabled = false;
    stop.disabled = false;
  }
  syncResearchGlobals();
}

function defaultPlaceholder() {
  return runMode === 'theory'
    ? '用一句话描述要研究的问题或方向（如：分析信道变化如何影响射频指纹可分性）'
    : '用一句话描述研究（建议含英文检索形式/缩写），回车发送';
}

function addMsg(text: any, cls: any) {
  const row = document.createElement('div');
  row.className = 'msg ' + cls;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = cls === 'msg-user' ? '我' : (cls === 'msg-node' ? '·' : 'AI');
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.appendChild(avatar);
  row.appendChild(bubble);
  byId('log')!.appendChild(row);
  scrollToBottom('log');
}

function splitList(s: any) {
  return (s || '').split(/[,，、;；]+/).map((x: any) => x.trim()).filter(Boolean);
}

/* ---------------- 运行模式 ---------------- */
/* ---------------- 资料源绑定 (计划书 §3 R0) ---------------- */
function refreshSourceSets() {
  const sel = byId<HTMLSelectElement>('sourceset')!;
  if (!sel) return;
  fetch('/api/sources').then((r: any) => r.json()).then((d: any) => {
    const prev = sel.value;
    sel.innerHTML = '<option value="">（不绑定，按主题名匹配）</option>';
    (d.sources || []).forEach((s: any) => {
      const opt = document.createElement('option');
      opt.value = s.source_set_id;
      opt.textContent = s.source_set_id + '（' + (s.documents || 0) + ' 篇 / ' +
        (s.cards || 0) + ' 卡' + (s.indexed ? ' · 已建索引' : '') + '）';
      sel.appendChild(opt);
    });
    sel.value = prev;
    onSourceSetChange();
  }).catch(() => {});
}

/* ---------------- 附件上传 (补充问题说明 / 补充文献) ---------------- */
let attachments: AttachmentView[] = [];
let pendingAttachments: string[] = [];   // 仅"补充问题说明"用途的附件 id

/** 已上传的问题附件属于哪个项目 (附件按项目归属校验, 启动时项目 id 变了就不再生效)。 */
let attachmentProjectId = '';

/** 生成新的草稿项目 id (R2: 让附件在稳定身份下上传)。 */
function newDraftProjectId(): string {
  return 'proj-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

/**
 * 启动前的附件归属自检 (R3 的用户可见面)。
 *
 * 问题附件按 `project_id` 校验归属: 上传之后再改项目 ID, 启动时这些附件会被服务端
 * 拒绝 —— 而**界面不会报错**, 于是"我明明传了附件, 研究却没读"变成一个静默的坑。
 * 这里在启动前主动检查并如实告知, 而不是让用户以为附件生效了。
 */
function checkAttachmentBinding(currentPid: string): boolean {
  if (!pendingAttachments.length) return true;
  if (attachmentProjectId && attachmentProjectId === currentPid) return true;
  uploadMessage(
    `已上传的 ${pendingAttachments.length} 个问题附件属于项目 ${attachmentProjectId || '(空)'}, ` +
    `与当前项目 ${currentPid || '(空)'} 不同 —— 启动时会被归属校验拒绝。` +
    '请重新选择文件上传一次 (或把项目 ID 改回上传时用的那个)。', 'msg-error');
  return false;
}

function attachmentTopic(): string {
  // 文献入库的主题: 优先显式资料库, 否则用当前主题名 (与按主题名匹配的规则一致)
  return (val('sourceset') || val('topic') || currentContext || '').trim();
}

function uploadMessage(text: string, cls = 'msg-system') {
  addMsg('[附件] ' + text, cls);
}

async function uploadAttachments() {
  const files = (byId<HTMLInputElement>('attachfiles')?.files || null);
  const list = Array.from(files || []);
  const kind = (val('attachkind', 'problem') || 'problem') as UploadKind;
  const topic = attachmentTopic();
  const error = validateFiles(list, kind, topic);
  if (error) {
    uploadMessage(error, 'msg-error');
    return;
  }
  const form = new FormData();
  form.append('kind', kind);
  form.append('project_id', currentResearch.projectId || val('projid'));
  form.append('problem_id', currentResearch.problemId || val('probid'));
  form.append('topic', topic);
  list.forEach((file) => form.append('files', file, file.name));
  setDisabled('btn-upload', true);
  try {
    const resp = await fetch('/api/uploads', { method: 'POST', body: form });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      uploadMessage(`上传失败 (HTTP ${resp.status}): ${body.detail || ''}`, 'msg-error');
      return;
    }
    uploadMessage(summarizeResults(body));
    if (kind === 'literature') refreshSourceSets();
    const input = byId<HTMLInputElement>('attachfiles');
    if (input) input.value = '';
    await refreshAttachments();
  } catch (err) {
    uploadMessage('上传请求失败: ' + String(err), 'msg-error');
  } finally {
    setDisabled('btn-upload', false);
  }
}

async function refreshAttachments() {
  // R1: 问题附件看项目、文献附件看主题, 必须分别查询再合并 ——
  // 两个字段一起发给后端会被当成交集, 两个输入都有值时列表恒为空。
  const projectId = (currentResearch.projectId || val('projid') || '').trim();
  const topic = attachmentTopic();
  const queries: string[] = [];
  if (projectId) queries.push(buildAttachmentUrl('problem', projectId, ''));
  if (topic) queries.push(buildAttachmentUrl('literature', '', topic));
  if (!queries.length) {
    attachments = [];
    renderAttachments();
    return;
  }
  const merged: AttachmentView[] = [];
  const seen = new Set<string>();
  for (const url of queries) {
    try {
      const resp = await fetch(url);
      const body = await resp.json();
      for (const item of (body && body.attachments) || []) {
        if (item && item.attachment_id && !seen.has(item.attachment_id)) {
          seen.add(item.attachment_id);
          merged.push(item as AttachmentView);
        }
      }
    } catch (err) {
      uploadMessage('附件列表读取失败: ' + String(err), 'msg-error');
    }
  }
  attachments = merged;
  renderAttachments();
}

function buildAttachmentUrl(kind: UploadKind, projectId: string, topic: string): string {
  const params = new URLSearchParams({ kind });
  if (projectId) params.set('project_id', projectId);
  if (topic) params.set('topic', topic);
  return '/api/uploads?' + params.toString();
}

function renderAttachments() {
  const rows = attachments.map((item) => (
    '<div class="attach-item">' +
    '<span class="mono">' + escapeHtml(item.filename) + '</span> · ' +
    escapeHtml(kindLabel(item.kind)) + ' · ' + escapeHtml(formatSize(item.size)) +
    ' · <span class="mono">' + escapeHtml(shortHash(item.sha256)) + '</span> · ' +
    escapeHtml(parseLabel(item)) +
    ' <button class="ghost" data-action="deleteAttachment" data-id="' +
    escapeHtml(item.attachment_id) + '">移除</button></div>'
  ));
  setHtml('attachlist', rows.join('') || '<span class="wb-note">还没有附件</span>');
  const problemIds = problemAttachmentIds(attachments);
  pendingAttachments = problemIds;
  // 记住这些附件归属的项目: 启动前据此自检"项目 ID 有没有被改过"
  if (problemIds.length) attachmentProjectId = (currentResearch.projectId || val('projid') || '').trim();
  else attachmentProjectId = '';
  setHtml('filebinding', attachments.length
    ? '本次研究绑定的附件: ' + escapeHtml(attachments.map(
        (i) => `${i.filename}（${kindLabel(i.kind)}）`).join('、')) +
      (problemIds.length ? '；' + problemIds.length + ' 份问题说明将并入问题陈述' : '')
    : '');
}

async function deleteAttachment(attachmentId: string) {
  try {
    const resp = await fetch('/api/uploads/' + encodeURIComponent(attachmentId),
                              { method: 'DELETE' });
    if (!resp.ok) {
      uploadMessage(`移除失败 (HTTP ${resp.status})`, 'msg-error');
      return;
    }
    attachments = attachments.filter((i) => i.attachment_id !== attachmentId);
    renderAttachments();
    uploadMessage('已移除附件登记（已入库的文献仍留在资料库中）');
  } catch (err) {
    uploadMessage('移除请求失败: ' + String(err), 'msg-error');
  }
}

function bindUploads() {
  on('btn-upload', 'click', () => { void uploadAttachments(); });
  on('attachfiles', 'change', () => { void uploadAttachments(); });
  void refreshAttachments();
}

function onSourceSetChange() {
  const sel = byId<HTMLSelectElement>('sourceset')!;
  const info = byId('sourceinfo')!;
  const id = sel ? sel.value : '';
  if (!info) return;
  if (!id) { info.textContent = '未绑定：研究将按主题名匹配同名资料库'; return; }
  fetch('/api/sources/' + encodeURIComponent(id)).then((r: any) => r.json()).then((d: any) => {
    const s = d.source_set || {};
    const bits = [(s.documents || 0) + ' 篇', (s.cards || 0) + ' 卡',
                  s.indexed ? '已建索引' : '未建索引'];
    if (s.languages && s.languages.length) bits.push('语言 ' + s.languages.join('/'));
    if (s.year_range) bits.push('年份 ' + s.year_range);
    info.textContent = (d.bindable ? '可用于研究：' : '不可用于研究：') + bits.join(' · ') +
      ((d.warnings && d.warnings.length) ? ' ⚠ ' + d.warnings.join('；') : '') +
      (d.bindable ? '' : '（' + (d.reason || '') + '）');
  }).catch(() => { info.textContent = ''; });
}

function onModeChange() {
  // F1-3: 综述资料库主题与理论研究问题是**两个字段**, 切换模式不得互相覆盖
  runMode = byId<HTMLSelectElement>('runmode') ? val('runmode') : 'survey';
  currentResearch.mode = runMode;
  currentResearch.push();
  const theory = runMode === 'theory';
  if (byId('theoryrow')) setProp('theoryrow', 'display', theory ? '' : 'none');
  if (byId('sourcerow')) setProp('sourcerow', 'display', theory ? '' : 'none');
  if (byId('policyrow')) setProp('policyrow', 'display', theory ? '' : 'none');
  if (byId('attachrow')) setProp('attachrow', 'display', theory ? '' : 'none');
  // R2: 进入理论研究前先落实稳定身份 —— 附件必须在有 project_id 之后再上传,
  // 否则文件会落到与运行无关的默认目录
  if (theory && !(val('projid') || '').trim()) {
    const draft = newDraftProjectId();
    setVal('projid', draft);
    currentResearch.apply({ projectId: draft });
    // 草稿身份**不是**运行绑定: 必须显式记下来, 否则刷新工作台只能靠"项目 id 恰好
    // 不等于状态里的项目 id"这条间接判据, 判据一旦被后来写入的值填平就会 404
    markDraftProject(draft);
  }
  if (theory) refreshSourceSets();
  if (byId('modebadge')) {
    setText('modebadge', theory
      ? '理论研究 · 对象级反馈 · 交付门槛'
      : '综述论文 · 多智能体 · 对话式协作');
  }
  if (byId('outtitle')) setText('outtitle', theory ? '研究产出' : '输出');
  if (mode === 'idle') setPlaceholder('reply', defaultPlaceholder());
  // 切到理论模式时尝试自动加载工作台
  if (theory) {
    const pid = workbenchTargetPid();
    // R2: 草稿身份下还没有研究记录, 不要拿它去探 /state (否则是必然 404 的噪音);
    // 只有"当前项目确实建立过运行"时才刷新工作台。
    if (pid) {
      currentResearch.apply({projectId: pid});
      refreshWorkbench();
    } else {
      const shown = (val('projid') || '').trim() || currentProjectId;
      if (shown) currentResearch.apply({projectId: shown});
      // 附件入口已在主界面: 切到理论模式时把已上传列表拉回来 (草稿身份也拉),
      // 让用户先看到"已经传了什么", 而不是一片空白
      void refreshAttachments();
      if (shown) {
        byId('workbench')!.innerHTML = '<div class="empty">项目 ' + wbEsc(shown) +
          ' 还没有研究记录。启动一次理论研究（或从「会话」下拉框选一个历史会话）' +
          '后即可加载工作台。</div>';
      }
    }
  } else {
    switchTab('preview');
    syncResearchGlobals();
  }
}

function copyProjectId() {
  const pid = (val('projid') || '').trim() || currentProjectId;
  if (!pid) { addMsg('当前没有项目 ID', 'msg-error'); return; }
  if (navigator.clipboard) navigator.clipboard.writeText(pid);
  addMsg('项目 ID: ' + pid, 'msg-system');
}

/* ---------------- 启动 / 事件流 ---------------- */
function onSend() {
  if (mode === 'waiting') { sendResponse(); }
  else if (mode === 'idle') { start(val('reply')); }
}

function start(requestText: any) {
  const request = (requestText || '').trim();
  // F1-3: 综述资料库主题只在综述模式下作为 topic; 理论模式用输入本身作为研究请求,
  // 不把上一个综述会话选中的资料库主题悄悄并入新请求。
  const theory = runMode === 'theory';
  const topic = theory
    ? (val('topic').trim() || request)
    : (currentContext || val('topic').trim());
  if (!request && !topic) { alert('请描述研究主题，或用一句话说明你想研究的内容'); return; }

  runMode = byId<HTMLSelectElement>('runmode') ? val('runmode') : 'survey';
  currentResearch.mode = runMode;
  currentResearch.push();
  setMode('running');
  setStatus(theory ? '解析研究问题…' : '解析研究意图…', 'running');
  addMsg(request || topic, 'msg-user');
  clearReply();

  const payload: Record<string, any> = {
    request: request,
    topic: topic,
    keywords: splitList(val('keywords')),
    subtopics: splitList(val('subtopics')),
    time_range: '2019-2026',
    max_revisions: parseInt(val('maxrev')) || 3,
    skip_retrieval: checked('skipret'),
    mode: runMode,
  };
  if (runMode === 'theory') {
    const pid = (val('projid') || '').trim() || newDraftProjectId();
    setVal('projid', pid);
    const probid = (val('probid') || '').trim() || 'problem';
    currentResearch.apply({projectId: pid, problemId: probid});
    // 附件按项目归属校验: 项目 ID 与上传时不同就如实拦下, 不静默丢附件 (R3)
    // 附件按项目归属校验: 项目 ID 与上传时不同就如实拦下, 不静默丢附件 (R3)
    if (!checkAttachmentBinding(pid)) return;
    payload.project_id = pid;
    payload.problem_id = probid;
    payload.max_actions = parseInt(val('maxactions')) || 40;
    payload.max_tool_calls = parseInt(val('maxtools')) || 60;
    // R0: 资料源绑定随请求发出; 服务端在确认研究前校验可用性
    payload.source_set_id = (byId<HTMLSelectElement>('sourceset') && val('sourceset')) || '';
    // 附件: "补充问题说明"的附件文本由服务端并入问题陈述 (带 sha256 溯源)
    payload.attachment_ids = pendingAttachments;
    // R5: 资料授权策略必须显式随请求发出 (后端校验, 非法策略拒绝而非静默回退)
    payload.source_policy = val('sourcepolicy', 'user_kb') || 'user_kb';
  }

  fetch('/api/sessions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  }).then((r: any) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body: data}) => {
    if (data.thread_id) {
      currentResearch.apply({
        threadId: data.thread_id,
        sessionId: data.session_id || '',
        mode: data.mode || runMode,
        runId: data.run_id || '',
        projectId: data.project_id || '',
        problemId: data.problem_id || '',
      });
      // 启动成功 = 该项目确实有运行记录: 从这里起工作台才允许查询它
      bindRunToProject(data.project_id || '');
      if (data.project_id) setVal('projid', data.project_id);
      if (data.problem_id && byId<HTMLInputElement>('probid')) setVal('probid', data.problem_id);
      connectSSE(currentThreadId);
      refreshHistorySelect();
      if (runMode === 'theory') { switchTab('workbench'); refreshWorkbench(); }
    } else if (status === 409 && data.detail && data.detail.options) {
      // F0-4 / F1: 同一问题已在研究另一个请求 → 让用户明确选择, 不静默改题
      renderProblemConflict(data.detail);
      setMode('idle');
    } else {
      addMsg('启动失败 (HTTP ' + status + '): ' + JSON.stringify(data.detail || data), 'msg-error');
      setMode('idle');
      refreshContexts();
      refreshArtifacts();
    }
  }).catch((e: any) => {
    addMsg('启动失败: ' + e, 'msg-error');
    setMode('idle');
  });
}

// 同一 problem_id 上已有不同研究请求: 列出"继续原问题 / 另建新问题"两个明确选项
function renderProblemConflict(detail: any) {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  card.innerHTML = '<div class="it-title">该问题 ID 已在研究另一个请求</div>' +
    '<div class="it-hint">问题 ' + escapeHtml(detail.problem_id || '') + ' 已在研究：' +
    escapeHtml(detail.existing_request || '') + '<br>本次输入：' +
    escapeHtml(detail.requested || '') + '</div>';
  const row = document.createElement('div');
  row.className = 'adv-row';
  const resumeBtn = document.createElement('button');
  resumeBtn.className = 'ghost';
  resumeBtn.textContent = '继续原问题（resume）';
  resumeBtn.onclick = () => { card.remove(); resumeSameProblem(detail); };
  const newBtn = document.createElement('button');
  newBtn.className = 'ghost';
  newBtn.textContent = '另建新问题（换 problem_id）';
  newBtn.onclick = () => { card.remove(); startNewProblem(detail.requested); };
  row.appendChild(resumeBtn);
  row.appendChild(newBtn);
  card.appendChild(row);
  byId('log')!.appendChild(card);
  scrollToBottom('log');
}

function resumeSameProblem(detail: any) {
  const pid = detail.problem_id || currentProblemId;
  if (byId<HTMLInputElement>('probid')) setVal('probid', pid);
  currentResearch.apply({problemId: pid});
  addMsg('继续原问题 ' + pid + '（不重新生成规格）', 'msg-system');
  startWithResume(val('reply') || detail.requested || '');
}

function startNewProblem(requestText: any) {
  const next = 'p' + Math.random().toString(16).slice(2, 7);
  if (byId<HTMLInputElement>('probid')) setVal('probid', next);
  currentResearch.apply({problemId: next});
  addMsg('作为新问题 ' + next + ' 开始研究', 'msg-system');
  start(requestText);
}

// 显式续研: 与 start 分开语义 (计划书 §9.1), 复用已落盘规格
function startWithResume(requestText: any) {
  const request = (requestText || '').trim();
  if (!request) { alert('请输入研究请求'); return; }
  runMode = 'theory';
  currentResearch.mode = 'theory';
  currentResearch.push();
  setMode('running');
  setStatus('恢复同一问题…', 'running');
  const pid = (val('projid') || '').trim() || currentProjectId;
  const payload: Record<string, any> = {
    request: request,
    topic: val('topic').trim() || request,
    keywords: splitList(val('keywords')),
    subtopics: splitList(val('subtopics')),
    time_range: '2019-2026',
    mode: 'theory',
    project_id: pid,
    problem_id: (val('probid') || '').trim() || 'problem',
    max_actions: parseInt(val('maxactions')) || 40,
    max_tool_calls: parseInt(val('maxtools')) || 60,
    resume: true,
  };
  fetch('/api/sessions', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).then((r: any) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
      if (!body.thread_id) {
        addMsg('续研失败 (HTTP ' + status + '): ' + JSON.stringify(body.detail || body), 'msg-error');
        setMode('idle');
        return;
      }
      currentResearch.apply({threadId: body.thread_id, sessionId: body.session_id || '',
                             mode: 'theory', projectId: body.project_id || pid,
                             problemId: body.problem_id || payload.problem_id});
      connectSSE(body.thread_id);
      refreshHistorySelect();
      switchTab('workbench');
      refreshWorkbench();
    }).catch((e: any) => { addMsg('续研失败: ' + e, 'msg-error'); setMode('idle'); });
}

let lastEventId = 0;          // F2: 已收到的事件序号 (断线重连据此补齐)

function connectSSE(tid: any) {
  if (es) es.close();
  const url = '/api/sessions/' + tid + '/events' +
    (lastEventId ? ('?last_event_id=' + lastEventId) : '');
  es = new EventSource(url);
  es.onmessage = (e: any) => {
    if (e.lastEventId) lastEventId = parseInt(e.lastEventId) || lastEventId;
    try { handleEvent(JSON.parse(e.data)); } catch (err) {}
  };
  // F2: 断线不能静默 —— 先查会话状态做补偿, 再重连并回放缺失事件
  es.onerror = () => {
    if (es) { es.close(); es = null; }
    fetch('/api/sessions/' + tid + '/state').then((r: any) => r.json()).then((st: any) => {
      if (st && st.status) {
        currentResearch.apply({status: st.status === 'waiting' ? 'waiting'
                                       : (st.status === 'running' ? 'running'
                                       : (st.status === 'done' ? 'done' : 'idle'))});
        if (st.status === 'waiting') {
          setStatus('连接中断，等待你的输入', 'running');
          setMode('waiting');
        } else if (st.status === 'done') {
          setStatus('已完成', 'done');
        }
      }
    }).catch(() => {}).finally(() => {
      setTimeout(() => { if (currentThreadId === tid) connectSSE(tid); }, 1500);
    });
  };
}

function handleEvent(ev: any) {
  switch (ev.type) {
    case 'connected': break;
    case 'node':
      addMsg(ev.text, 'msg-node');
      if (ev.research) updateRunHint(ev.research);
      if (runMode === 'theory') refreshWorkbench();
      break;
    case 'log': addMsg(ev.text, 'msg-node'); break;
    case 'interrupt': onInterrupt(ev.payload); break;
    case 'done': onDone(ev.state); break;
    case 'stopped': onStopped(); break;
    case 'error':
      addMsg('出错: ' + (ev.message || ''), 'msg-error');
      setStatus('出错', 'error');
      finishRun();
      break;
  }
}

function updateRunHint(r: any) {
  const bits = [];
  if (r.action) bits.push('动作 ' + r.action);
  if (r.claims_supported) bits.push('已成立 ' + r.claims_supported);
  if (r.claims_refuted) bits.push('被否定 ' + r.claims_refuted);
  if (r.obligations_open) bits.push('未关闭义务 ' + r.obligations_open);
  if (r.obligations_blocked) bits.push('受阻义务 ' + r.obligations_blocked);
  if (typeof r.actions_used === 'number') {
    bits.push('动作 ' + r.actions_used + '/' + r.max_actions);
  }
  setText('runhint', bits.join(' · '));
}

/* ---------------- 暂停点交互 ---------------- */
function onInterrupt(p: any) {
  setMode('waiting');
  setStatus('等待你的输入', 'running');
  // F2: 记录暂停点 ID, 提交响应时回传 (服务端据此幂等去重)
  pendingInterruptId = (p && p.interrupt_id) || '';
  const type = (p && p.type) || '';

  if (type === 'theory_candidates') { renderCandidateChoice(p); return; }
  if (type === 'theory_feedback') { renderFeedbackPrompt(p); return; }

  // 综述模式 / 通用暂停点
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  card.innerHTML = '<div class="it-title">' + escapeHtml(p.title || '') + '</div>' +
                   '<div class="it-hint">' + escapeHtml(p.hint || '') + '</div>';
  byId('log')!.appendChild(card);
  scrollToBottom('log');

  renderMarkdown(byId('preview'), p.content || '');
  switchTab('preview');

  setDisabled('reply', false);
  setPlaceholder('reply', (p.hint || '') + '（回车发送）');
  setDisabled('btn-send', false);
  setDisabled('btn-stop', false);
  clearReply();
  byId<HTMLTextAreaElement>('reply')!.focus();
}

// 理论研究: 候选路线选择 (点选即可, 也可在输入框回序号)
function renderCandidateChoice(p: any) {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  const list = document.createElement('div');
  list.className = 'cand-list';
  const candidates = p.candidates || [];
  const recommended = candidates.findIndex((c: any) => c.recommended);
  candidates.forEach((c: any, idx: any) => {
    const btn = document.createElement('button');
    btn.className = 'cand';
    const meta = [];
    if (c.category) meta.push('类别 ' + c.category);
    // F1-4: 同时展示模型条件、资料依据、已知结果比较状态与会被改变的问题范围
    if (c.variable_domains && Object.keys(c.variable_domains).length) {
      meta.push('条件 ' + Object.entries(c.variable_domains)
        .map(([k, v]) => k + ' ∈ ' + v).join('、'));
    }
    if (c.study && c.study.design && c.study.design !== 'none') {
      meta.push('设计 ' + c.study.design);
    }
    if (c.known_results) meta.push('已知结果 ' + c.known_results);
    if (c.difference) meta.push('与已有差异 ' + c.difference);
    if (c.verifiability) meta.push('可核验性 ' + c.verifiability);
    if (c.difficulty) meta.push('难点 ' + c.difficulty);
    const candId = c.candidate_id || '';
    btn.innerHTML = '<span class="cand-idx">' + (idx === recommended ? '★' : (idx + 1)) + '</span>' +
      '<span style="flex:1">' + escapeHtml(c.statement || '(无陈述)') +
      (candId ? '<div class="cand-meta mono">' + escapeHtml(candId) + '</div>' : '') +
      (meta.length ? '<div class="cand-meta">' + escapeHtml(meta.join('\n')) + '</div>' : '') + '</span>';
    btn.onclick = () => {
      addMsg('已选择候选路线 ' + (idx + 1) + (candId ? ' (' + candId + ')' : '') + ': ' +
             (c.statement || ''), 'msg-user');
      card.remove();
      // 有稳定 ID 时按 ID 确认: 历史恢复后序号可能错位
      respondWith(candId || String(idx));
    };
    list.appendChild(btn);
  });
  card.innerHTML = '<div class="it-title">' + escapeHtml(p.title || '请选择要研究的主路线') + '</div>' +
                   '<div class="it-hint">' + escapeHtml(p.hint || '') +
                   '<br>每条候选显示其条件、资料依据与将改变的问题范围；点选即按稳定候选 ID 确认。</div>';
  card.appendChild(list);
  byId('log')!.appendChild(card);
  scrollToBottom('log');
  setDisabled('reply', false);
  setPlaceholder('reply', '也可以输入候选 ID，或序号（0 开始）后回车');
  setDisabled('btn-send', false);
  clearReply();
}

// 理论研究: 反馈暂停点
function renderFeedbackPrompt(p: any) {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  const objs = p.objects || {};
  const fmt = (label: any, items: any) => {
    if (!items || !items.length) return '';
    return '<div class="cand-meta">' + label + ': ' +
           escapeHtml(items.map(([id, text]: [any, any]) => id + ' — ' + text).join('\n')) + '</div>';
  };
  card.innerHTML = '<div class="it-title">' + escapeHtml(p.title || '可以补充研究意见') + '</div>' +
    '<div class="it-hint">可直接回车跳过；或先选作用对象再写意见（如「不要假设 x ∈ real」）</div>' +
    fmt('假设', objs.assumptions) + fmt('结论', objs.claims);
  card.appendChild(buildObjectPicker(objs));
  byId('log')!.appendChild(card);
  scrollToBottom('log');
  setDisabled('reply', false);
  setPlaceholder('reply', '输入研究意见（回车发送，空回车跳过）');
  setDisabled('btn-send', false);
  clearReply();
}

/* F1-5: 反馈的对象选择器 —— 先选命题/假设/推导步骤, 再写意见。
 * 选择结果通过 object_id 提交, 服务端仍负责语义解析与校验; 无法唯一定位时
 * 服务端回传候选对象, 由 clarificationCard() 让用户点选完成澄清。
 * 选项构造已搬到 views/research-workbench.ts (`feedbackObjectOptions`)。 */

function buildObjectPicker(objects: any, selectId = 'wbobj') {
  const wrap = document.createElement('div');
  wrap.className = 'adv-row';
  const label = document.createElement('label');
  label.textContent = '作用对象 ';
  const sel = document.createElement('select');
  sel.id = selectId || 'wbobj';
  feedbackObjectOptions(objects || {}).forEach((o: any) => {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.label;
    sel.appendChild(opt);
  });
  label.appendChild(sel);
  wrap.appendChild(label);
  return wrap;
}

// 服务端请澄清时列出候选对象供点选 (而不是只显示一条错误)
function renderFeedbackClarification(body: any) {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  card.innerHTML = '<div class="it-title">无法唯一定位作用对象</div>' +
    '<div class="it-hint">' + escapeHtml(body.clarify || '请指明该意见针对哪个对象') + '</div>';
  const picker = buildObjectPicker(
    {assumptions: {}, claims: {}, steps: {}}, 'clarifyobj');
  const sel = picker.querySelector('select') as HTMLSelectElement;
  sel.innerHTML = '';
  (body.candidates || []).forEach((c: any) => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = (OBJECT_KIND_LABEL[c.kind] || c.kind) + ' ' + c.id + ' — ' +
                      String(c.text || '').slice(0, 60);
    sel.appendChild(opt);
  });
  const btn = document.createElement('button');
  btn.className = 'ghost';
  btn.textContent = '对该对象重新施加';
  btn.onclick = () => {
    const box = byId<HTMLTextAreaElement>('wbfeedback');
    const target = sel.value;
    card.remove();
    submitWorkbenchFeedback(target, box ? box.value : '');
  };
  picker.appendChild(btn);
  card.appendChild(picker);
  byId('log')!.appendChild(card);
  scrollToBottom('log');
}

function respondWith(response: any) {
  if (!currentThreadId) return;
  const input = byId<HTMLTextAreaElement>('reply')!;
  const previous = input ? input.value : '';
  // F2: 先确认服务端接受, 再改变界面状态; 失败时保留输入以便重试
  setStatus('发送中…', 'running');
  fetch('/api/sessions/' + currentThreadId + '/respond', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({response: response, interrupt_id: pendingInterruptId || ''})
  }).then((r: any) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
      if (status >= 200 && status < 300 && body && body.ok !== false) {
        clearReply();
        pendingInterruptId = '';
        setMode('running');
        setStatus('运行中…', 'running');
        return;
      }
      if (input && !input.value) input.value = previous;
      const why = (body && (body.detail || body.reason)) || JSON.stringify(body);
      addMsg('发送失败 (HTTP ' + status + '): ' + why + '（输入已保留，可重试）', 'msg-error');
      setStatus('发送失败', 'error');
      setMode('waiting');
    })
    .catch((e: any) => {
      if (input && !input.value) input.value = previous;
      addMsg('发送失败: ' + e + '（输入已保留，可重试）', 'msg-error');
      setStatus('发送失败', 'error');
      setMode('waiting');
    });
}

function sendResponse() {
  if (!currentThreadId || mode !== 'waiting') return;
  const response = val('reply');
  addMsg(response || '(跳过)', 'msg-user');
  respondWith(response);
}

/* ---------------- 完成 / 停止 ---------------- */
function onDone(state: any) {
  setStatus('完成', 'done');
  if (state && state.error) {
    addMsg('错误: ' + state.error, 'msg-error');
  } else if (state) {
    const theory = runMode === 'theory' || state.delivery_level || state.package_dir;
    if (theory) {
      const lines = ['研究完成'];
      if (state.delivery_level) lines.push('交付级别: ' + state.delivery_level);
      if (state.gate_passed !== null && state.gate_passed !== undefined) {
        lines.push('交付门槛: ' + (state.gate_passed ? '通过' : '未通过'));
      }
      if (state.snapshot_id) lines.push('快照: ' + state.snapshot_id);
      if (state.package_dir) lines.push('交付包: ' + state.package_dir);
      addMsg(lines.join('\n'), 'msg-done');
      if (state.project_id) currentResearch.apply({projectId: state.project_id});
      switchTab('workbench');
      refreshWorkbench();
    } else {
      const parts = ['完成'];
      if (state.literature_notes_path) parts.push('文献综述总结: ' + basename(state.literature_notes_path));
      if (state.figure_count) parts.push('已生成图表: ' + state.figure_count + ' 张');
      if (state.review_score && state.review_score !== 'N/A') {
        parts.push('审稿评分: ' + state.review_score + '/50，修改轮次: ' + state.revision_count);
      }
      if (state.draft_path) parts.push('初稿: ' + basename(state.draft_path));
      if (state.paper_tex_path) parts.push('LaTeX: ' + basename(state.paper_tex_path));
      addMsg(parts.join('\n'), 'msg-done');
      if (state.literature_notes_path) { loadArtifact(artifactNameFromPath(state.literature_notes_path)); }
      else if (state.draft_path) { loadArtifact(artifactNameFromPath(state.draft_path)); }
    }
  } else {
    addMsg('完成', 'msg-done');
  }
  finishRun();
  refreshArtifacts();
}

function onStopped() {
  setStatus('已停止', 'done');
  addMsg('已停止（会话状态已保存到检查点）。', 'msg-agent');
  finishRun();
}

function finishRun() {
  setMode('idle');
  // 结束的只是**本次运行**的线程; 项目与问题仍是当前研究上下文, 工作台继续指向它
  currentResearch.apply({threadId: ''});
  if (es) { es.close(); es = null; }
  refreshContexts();
  refreshHistorySelect();
  if (runMode === 'theory') refreshWorkbench();
}

function stopSession() {
  if (!currentThreadId) return;
  fetch('/api/sessions/' + currentThreadId + '/stop', {method: 'POST'}).catch(() => {});
  addMsg('已请求停止（等待当前节点完成）…', 'msg-agent');
}


/** 已确认建立过运行的项目 id (`''` = 尚未确认)。
 *
 * 为什么要跟"草稿身份"分开记: 切换到理论模式时, 表单里会被填上一个自动生成的
 * **草稿**项目 id (R2: 让附件在稳定身份下上传), 它没有研究记录。若拿它去调
 * `/api/research/{草稿}/state`, 后端只能给 404 —— 界面冒出"读取工作台失败 (404)",
 * 服务端日志被刷满噪音 (现场报告)。
 * 但判据不能是"当前项目 id 与记录中的不同" —— 用户手填一个**真实存在**的项目时
 * 也会不同, 那样就把"手动加载工作台"这条路堵死了。因此显式记住"哪个 id 是草稿":
 * 只有它自己不查; 换成别的 id (手填/历史会话) 一律放行查询。
 */
let runBoundProjectId = '';
let draftUnboundProjectId = '';

function bindRunToProject(projectId: any) {
  runBoundProjectId = String(projectId || '');
  draftUnboundProjectId = '';
}

function markDraftProject(projectId: any) {
  runBoundProjectId = '';
  draftUnboundProjectId = String(projectId || '');
}

function workbenchTargetPid(): string {
  const pid = ((byId<HTMLInputElement>('projid') && val('projid')) || currentProjectId || '').trim();
  if (!pid) return '';
  // 两条判据都必须显式判: "记录里明确说过没有运行记录" 且 "当前就是那个 id"。
  // 只要用户换成别的 id (手填/历史会话), 就允许查一次 —— 否则手动加载工作台被堵死。
  if (!runBoundProjectId && pid === draftUnboundProjectId) return '';
  return pid;
}

/* ---------------- 科研工作台 ---------------- */
// F0-1: 用 URL/searchParams 构造地址。早期实现手拼 '?problem_id=...' + '&_=...',
// 在没有 problem_id 时会拼出 '.../state&_=123' (缺少 '?'), 请求打不到路由。
function researchStateUrl(projectId: any, problemId: any) {
  const url = new URL('/api/research/' + encodeURIComponent(projectId) + '/state',
                      window.location.origin);
  if (problemId) url.searchParams.set('problem_id', problemId);
  url.searchParams.set('_', String(Date.now()));
  return url;
}

let wbRequestSeq = 0;
let wbRequestKey = '';
let workbenchData: WorkbenchData | null = null;   // 最近一次工作台状态 (供详情导航查询)
let openClaimDetail = '';        // 当前展开详情的结论 id

function refreshWorkbench() {
  const pid = ((byId<HTMLInputElement>('projid') && val('projid')) || currentProjectId || '').trim();
  if (!pid) {
    if (runMode === 'theory') {
      byId('workbench')!.innerHTML = '<div class="empty">尚未关联理论研究项目。' +
        '启动一次理论研究，或在高级选项里填写项目 ID 后点「加载工作台」。</div>';
    }
    return;
  }
  // 没有研究记录的项目: 不发注定 404 的请求, 直接说明为什么空
  const bound = workbenchTargetPid();
  if (!bound) {
    if (runMode === 'theory') {
      byId('workbench')!.innerHTML = '<div class="empty">项目 ' + wbEsc(pid) +
        ' 还没有研究记录。启动一次理论研究（或从「会话」下拉框选一个历史会话）后即可加载工作台。' +
        '若这是你之前研究过的项目，请确认项目 ID 没有改动。</div>';
    }
    return;
  }
  currentResearch.apply({projectId: pid});
  // F2: 每次读取绑定 (项目, 问题) 与序号; 旧请求最后返回时不得覆盖新画面
  const key = pid + '|' + (currentProblemId || '');
  const seq = ++wbRequestSeq;
  wbRequestKey = key;
  fetch(researchStateUrl(pid, currentProblemId))
    .then((r: any) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
      if (seq !== wbRequestSeq || key !== wbRequestKey) return;  // 已被更新的请求取代
      if (status !== 200) {
        let hint = '';
        if (status === 409 && body && body.detail && body.detail.problems) {
          hint = '<div class="wb-note">该项目含多个研究问题，请选择其一：' +
            body.detail.problems.map((p: any) => '<button class="ghost" data-action="selectProblem" ' +
              'data-id="' + wbEsc(encodeURIComponent(p.problem_id)) + '">' +
              wbEsc(p.problem_id) + '</button>')
              .join(' ') + '</div>';
        } else if (status === 404) {
          hint = '<div class="wb-note">该项目下没有这个研究问题。</div>';
        }
        byId('workbench')!.innerHTML = '<div class="empty">读取工作台失败 (' + status + '): ' +
          wbEsc((body && body.detail) ? (body.detail.message || JSON.stringify(body.detail))
                                      : JSON.stringify(body)) + '</div>' + hint;
        return;
      }
      if (body.problem_id && body.problem_id !== currentProblemId) {
        // 服务端已解析出唯一问题: 写回上下文与表单, 保持一致
        currentResearch.apply({problemId: body.problem_id});
        if (byId<HTMLInputElement>('probid')) setVal('probid', body.problem_id);
      }
      workbenchData = body;
      renderWorkbench(body);
    })
    .catch((e: any) => {
      if (seq !== wbRequestSeq) return;
      setHtml('workbench', '<div class="empty">读取工作台失败: ' + escapeHtml(String(e)) + '</div>');
    });
}

// 服务端报告多问题时由用户选择 (F0-2: 不擅自取第一个)
function selectProblem(encodedProblemId: any) {
  const pid = decodeURIComponent(encodedProblemId);
  const field = byId<HTMLInputElement>('probid')!;
  if (field) field.value = pid;
  currentResearch.apply({problemId: pid});
  refreshWorkbench();
}

/* F3: 结论详情导航 —— 从结论点开所用模型、原文证据、证明义务与验证记录。
 * 早期界面只有长表格, 无法从一条结论追溯到"它依据什么"。
 * 实现已搬到 views/research-workbench.ts (纯函数, 有类型与单测), 这里只做绑定。 */
function claimDetailRow(claimId: any) {
  return wbClaimDetailRow(workbenchData, claimId);
}

function toggleClaimDetail(claimId: any) {
  openClaimDetail = (openClaimDetail === claimId) ? '' : claimId;
  if (workbenchData) renderWorkbench(workbenchData);
}

function renderCapabilityCard(d: any) {
  const sel = d.model_selection || {};
  const perClaim = sel.claims || {};
  const models = sel.models || [];
  const ids = Object.keys(perClaim);
  if (!ids.length && !models.length) return '';

  const stateLabel = {
    selected: '已选模型',
    missing_selection: '缺模型选择',
    not_required: '无需领域模型',
    no_model: '尚无模型',
  };
  const rows = ids.map((cid: any) => {
    const s = perClaim[cid];
    const ref = s.model_ref ? (s.model_ref.id + ' v' + s.model_ref.version) : '—';
    const warn = s.capability_action !== 'proceed' || s.selected_state === 'missing_selection';    const cls = warn ? ' style="color:var(--warn)"' : '';
    return '<tr><td class="mono">' + wbEsc(cid) + '</td><td class="mono">' + wbEsc(s.claim_type) +
      '</td><td' + cls + '>' + wbEsc(s.declared_scheme) + '</td>' +
      '<td class="mono">' + wbEsc(ref) + '</td>' +
      '<td' + cls + '>' + wbEsc(labelOf(stateLabel, s.selected_state)) + '</td>' +
      '<td' + cls + '>' + wbEsc(s.capability) + '</td></tr>';
  }).join('');

  const modelRows = models.map((m: any) => '<tr><td class="mono">' + wbEsc(m.id) + ' v' + wbEsc(m.version) +
    '</td><td>' + wbEsc(m.name) + '</td><td>' + (m.selected ? '已选' : '候选') + '</td>' +
    '<td>' + wbEsc(String(m.sources)) + '</td><td>' + wbEsc(m.verified_scope || '—') + '</td></tr>').join('');

  // R2: 候选机制比较 + 可区分检验 + 术语量纲
  const modeling = d.modeling || {};
  const mechRows = (modeling.mechanisms || []).map((m: any) =>
    '<tr><td class="mono">' + wbEsc(m.id) + '</td><td>' + wbEsc(m.name) + '</td>' +
    '<td>' + wbEsc(m.relation || '—') + '</td>' +
    '<td>' + wbEsc((m.predictions || []).join('；') || '—') + '</td>' +
    '<td>' + wbEsc((m.source_refs || []).length ? (m.source_refs || []).length + ' 条来源' : '无来源') + '</td>' +
    '<td' + ((m.missing || []).length ? ' style="color:var(--warn)"' : '') + '>' +
    wbEsc((m.missing || []).join('、') || '—') + '</td></tr>').join('');
  const testRows = (modeling.distinguishing || []).map((t: any) =>
    '<tr><td class="mono">' + wbEsc(t.kind) + '</td><td>' + wbEsc(t.statement) + '</td>' +
    '<td>若 ' + wbEsc((t.discriminates || [])[0] || '?') + ': ' + wbEsc(t.expected_if_a || '待定') +
    '<br>若 ' + wbEsc((t.discriminates || [])[1] || '?') + ': ' + wbEsc(t.expected_if_b || '待定') +
    '</td></tr>').join('');
  const termRows = (modeling.terms || []).map((t: any) =>
    '<tr><td class="mono">' + wbEsc(t.name) + '</td><td>' + wbEsc(t.meaning || '—') + '</td>' +
    '<td' + (t.unit ? '' : ' style="color:var(--warn)"') + '>' + wbEsc(t.unit || '单位缺失') +
    '</td><td>' + wbEsc(t.domain || '—') + '</td></tr>').join('');

  return '<div class="wb-section"><h3>问题类型与模型' +
    '<span class="wb-count">能力声明 ' + ids.length + ' 条 / 模型 ' + models.length + ' 个</span></h3>' +
    (rows ? '<table class="wb"><tr><th>结论</th><th>类型</th><th>所需模型</th><th>当前模型</th>' +
      '<th>选择状态</th><th>能力声明</th></tr>' + rows + '</table>' : '') +
    (modelRows ? '<table class="wb" style="margin-top:6px"><tr><th>模型</th><th>名称</th>' +
      '<th>选中</th><th>来源数</th><th>已验证范围</th></tr>' + modelRows + '</table>' : '') +
    (mechRows ? '<h3 style="margin-top:10px">候选机制比较<span class="wb-count">' +
      (modeling.selected ? '选中 ' + wbEsc(modeling.selected) + '：' + wbEsc(modeling.why_selected || '') : '') +
      '</span></h3><table class="wb"><tr><th>机制</th><th>名称</th><th>变量关系</th>' +
      '<th>可观测预测</th><th>来源</th><th>缺失项</th></tr>' + mechRows + '</table>' : '') +
    (testRows ? '<h3 style="margin-top:10px">可区分检验</h3><table class="wb">' +
      '<tr><th>类型</th><th>检验</th><th>预期分离</th></tr>' + testRows + '</table>' : '') +
    (termRows ? '<h3 style="margin-top:10px">术语与量纲</h3><table class="wb">' +
      '<tr><th>术语</th><th>含义</th><th>单位</th><th>取值域</th></tr>' + termRows + '</table>' : '') +
    '<div class="wb-note">能力声明只描述该类型问题需要什么 (数据/设计/后端); ' +
    '「缺模型选择」表示该结论尚未绑定具体模型版本；缺失项表示建模信息不足。</div>' +
    '</div>';
}

function renderWorkbench(d: any) {
  const el = byId('workbench')!;
  const spec = d.spec || {};
  const budget = d.budget || {};
  const objects = d.objects || {};
  const claims = d.claims || [];
  const obligations = d.obligations || [];
  const verifications = d.verifications || [];
  const evidence = d.evidence || [];
  const experiments = d.experiments || [];
  const routes = d.routes || [];
  const novelty = d.novelty || [];
  const events = d.events || [];

  const problem = spec.problem_statement || spec.original_request || spec.direction || '(未记录研究问题)';
  const parts = [];

  // 状态总览
  parts.push(
    '<div class="wb-section"><h3>研究问题<span class="wb-count">项目 ' + wbEsc(d.project_id) + '</span></h3>' +
    '<div class="wb-note" style="font-size:12px;color:var(--text)">' + wbEsc(problem) + '</div>' +
    (Object.keys(spec.variable_domains || {}).length
      ? '<div class="wb-note">变量域: ' + wbEsc(Object.entries(spec.variable_domains)
          .map(([k, v]) => k + ' ∈ ' + v).join('、')) + '</div>' : '') +
    (spec.unknown_fields && spec.unknown_fields.length
      ? '<div class="wb-note">未确定字段 (需澄清): ' + wbEsc(spec.unknown_fields.join(', ')) + '</div>' : '') +
    '</div>'
  );

  // 问题类型能力声明与领域模型 (计划书 §5.2 / §7.2)
  parts.push(renderCapabilityCard(d));

  // 进度统计
  const stats = [
    ['已成立结论', objects.claims_supported, 'supported'],
    ['被否定结论', objects.claims_refuted, 'refuted'],
    ['未决/受阻', (objects.claims_open || 0) + (objects.claims_blocked || 0), 'blocked'],
    ['未关闭义务', objects.obligations_open, ''],
    ['受阻义务', objects.obligations_blocked, 'blocked'],
    ['证据条目', objects.evidence, ''],
    ['验证记录', objects.verifications, ''],
    ['动作/上限', (budget.actions_used || 0) + '/' + (budget.max_actions || 0), ''],
  ];
  parts.push(
    '<div class="wb-section"><h3>进度' +
    (budget.done ? '<span class="wb-count">研究循环已结束</span>' :
                   '<span class="wb-count">研究循环进行中</span>') +
    '</h3><div class="wb-grid">' +
    stats.map(([k, v, cls]) =>
      '<div class="stat"><div class="k">' + k + '</div><div class="v ' + cls + '">' +
      (v === undefined || v === null ? 0 : v) + '</div></div>').join('') +
    '</div></div>'
  );

  // 当前动作与缺口
  const decisions = d.decisions || [];
  if (decisions.length || (d.gaps || []).length) {
    const last = decisions.length ? decisions[decisions.length - 1] : null;
    parts.push('<div class="wb-section"><h3>当前在做什么</h3>');
    if (last) {
      parts.push('<div class="wb-note" style="color:var(--text)">最近动作: <b>' +
        wbEsc(last.action) + '</b>' + (last.target_gap ? '（针对缺口 ' + wbEsc(last.target_gap) + '）' : '') +
        '<br>' + wbEsc(last.reason || '') + '</div>');
    }
    if ((d.gaps || []).length) {
      parts.push('<table class="wb"><tr><th>缺口</th><th>说明</th><th>可消除动作</th></tr>' +
        d.gaps.map((g: any) => '<tr><td class="mono">' + wbEsc(g.gap_type) + '</td><td>' +
          wbEsc(g.statement) + '</td><td class="mono">' +
          wbEsc((g.resolving_actions || []).join(', ')) + '</td></tr>').join('') + '</table>');
    }
    parts.push('</div>');
  }

  // 研究过程事件
  if (events.length) {
    parts.push('<div class="wb-section"><h3>研究过程<span class="wb-count">最近 ' +
      events.length + ' 条</span></h3><div class="wb-events">' +
      events.slice().reverse().map((e: any) =>
        '<div class="wb-event"><span class="seq">#' + wbEsc(e.seq) + '</span><span>' +
        wbEsc(e.detail) + '</span></div>').join('') + '</div></div>');
  }

  // 结论状态表
  parts.push('<div class="wb-section"><h3>结论状态表<span class="wb-count">' +
    claims.length + ' 条</span></h3>');
  if (!claims.length) {
    parts.push('<div class="empty">尚无结论</div>');
  } else {
    parts.push('<table class="wb"><tr><th>结论</th><th>状态</th><th>支持方式</th>' +
      '<th>覆盖</th><th>适用条件 / 未覆盖因素</th><th>ID</th></tr>' +
      claims.map((c: any) => {
        const cond = (c.conditions || []).join('、');
        const unc = (c.not_covered || []).length
          ? '<div class="wb-note" style="color:var(--warn)">未覆盖: ' + wbEsc(c.not_covered.join('；')) + '</div>'
          : '';
        const est = c.effect_estimate && Object.keys(c.effect_estimate).length && c.effect_estimate.estimate !== undefined
          ? '<div class="wb-note">估计 ' + wbEsc(String(c.effect_estimate.estimate)) +
            ' (95%CI [' + wbEsc(String(c.effect_estimate.ci_low)) + ', ' +
            wbEsc(String(c.effect_estimate.ci_high)) + '])</div>'
          : '';
        return '<tr><td>' +
          '<button class="claim-link" aria-expanded="' +
            (openClaimDetail === c.id ? 'true' : 'false') + '" ' +
            'data-action="toggleClaimDetail" data-id="' + wbEsc(c.id) + '" ' +
            'title="展开该结论的证据/义务/验证详情">' + wbEsc(c.statement) + '</button>' +
          est + '</td><td>' + statusTag(c.status) + '</td>' +
          '<td>' + wbEsc(supportKindLabel(c.support_kind)) + '</td>' +
          '<td>' + wbEsc(coverageLabel(c.coverage)) + '</td>' +
          '<td>' + wbEsc(cond || '-') + unc + '</td>' +
          '<td class="mono">' + wbEsc(c.id) + '</td></tr>' +
          (openClaimDetail === c.id ? claimDetailRow(c.id) : '');
      }).join('') + '</table>');
  }
  parts.push('</div>');

  // 未决义务
  parts.push('<div class="wb-section"><h3>证明义务<span class="wb-count">' +
    obligations.length + ' 条</span></h3>');
  if (!obligations.length) {
    parts.push('<div class="empty">尚无义务</div>');
  } else {
    const claimText: Record<string, string> = {};
    claims.forEach((c: any) => { claimText[c.id] = c.statement; });
    parts.push('<table class="wb"><tr><th>义务</th><th>类型</th><th>状态</th>' +
      '<th>验证结果</th><th>说明</th></tr>' +
      obligations.map((o: any) => {
        const ce = o.counterexample && Object.keys(o.counterexample).length
          ? '<div class="wb-note" style="color:var(--danger)">反例: ' +
            wbEsc(JSON.stringify(o.counterexample)) + '</div>' : '';
        return '<tr><td>' + wbEsc(o.statement) +
          (claimText[o.claim_id] ? '<div class="wb-note">结论: ' +
            wbEsc(claimText[o.claim_id].slice(0, 60)) + '</div>' : '') + ce + '</td>' +
          '<td class="mono">' + wbEsc(o.kind) + '</td>' +
          '<td>' + statusTag(o.status) + (o.required ? '' : '<div class="wb-note">非必需</div>') + '</td>' +
          '<td>' + wbEsc(statusLabelRaw(o.validation_status)) + '</td>' +
          '<td>' + wbEsc(o.detail || '-') + '</td></tr>';
      }).join('') + '</table>');
  }
  parts.push('</div>');

  // 证据定位
  if (evidence.length) {
    parts.push('<div class="wb-section"><h3>证据与定位<span class="wb-count">' +
      evidence.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>来源</th><th>定位</th><th>关系</th><th>判定依据</th>' +
      '<th>可信度</th></tr>' +
      evidence.map((e: any) =>
        '<tr><td>' + wbEsc(e.title || e.source_id) +
          (e.excerpt ? '<div class="wb-note">' + wbEsc(e.excerpt.slice(0, 160)) + '</div>' : '') + '</td>' +
        '<td class="mono">' + wbEsc(e.locator || (e.page ? 'p' + e.page : '无定位')) + '</td>' +
        '<td>' + wbEsc(supportRelationLabel(e.support)) +
          (e.reviewer ? '<div class="wb-note">判定者: ' + wbEsc(e.reviewer) + '</div>' : '') + '</td>' +
        '<td>' + wbEsc(e.support_reason || '-') + '</td>' +
        '<td>' + wbEsc(e.credibility) +
          (e.existence_verified ? '<div class="wb-note">存在性已核</div>' : '') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 验证记录
  if (verifications.length) {
    parts.push('<div class="wb-section"><h3>验证记录<span class="wb-count">' +
      verifications.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>工具</th><th>结论</th><th>状态</th><th>覆盖</th>' +
      '<th>证书 / 反例</th></tr>' +
      verifications.map((v: any) => {
        const ce = v.counterexample && Object.keys(v.counterexample).length
          ? '<div class="wb-note" style="color:var(--danger)">反例 ' + wbEsc(JSON.stringify(v.counterexample)) + '</div>'
          : '';
        return '<tr><td>' + wbEsc(v.tool) + '</td>' +
          '<td class="mono">' + wbEsc(v.claim_id) + '</td>' +
          '<td>' + wbEsc(statusLabelRaw(v.validation_status)) +
            (v.stale ? ' <span class="tag t-blocked">已过期</span>' : '') + '</td>' +
          '<td>' + wbEsc(coverageLabel(v.scope)) + '</td>' +
          '<td>' + wbEsc(v.certificate || '-') + ce + '</td></tr>';
      }).join('') + '</table></div>');
  }

  // 实验/仿真建议
  parts.push('<div class="wb-section"><h3>实验/仿真建议<span class="wb-count">' +
    experiments.length + ' 条</span></h3>');
  if (!experiments.length) {
    parts.push('<div class="empty">尚未生成实验规格（未执行的建议才会出现在这里）</div>');
  } else {
    parts.push('<table class="wb"><tr><th>规格</th><th>关联结论</th><th>目的</th>' +
      '<th>执行状态</th><th>判据 / 指标</th></tr>' +
      experiments.map((s: any) =>
        '<tr><td>' + wbEsc(s.title || s.id) + '</td>' +
        '<td class="mono">' + wbEsc(s.claim_id) + '</td>' +
        '<td>' + wbEsc(s.purpose || '-') + '</td>' +
        '<td>' + wbEsc(executionLabel(s.execution_status)) + '</td>' +
        '<td>' + wbEsc(s.decision_rule || '-') +
          ((s.metrics || []).length ? '<div class="wb-note">指标: ' +
            wbEsc(s.metrics.join('、')) + '</div>' : '') +
          ((s.limitations || []).length ? '<div class="wb-note">局限: ' +
            wbEsc(s.limitations.join('；')) + '</div>' : '') +
        '</td></tr>').join('') + '</table>');
  }
  parts.push('</div>');

  // 研究路线与失败记忆
  if (routes.length) {
    parts.push('<div class="wb-section"><h3>研究路线<span class="wb-count">' +
      routes.length + ' 条</span></h3>' +
      '<table class="wb"><tr><th>策略</th><th>状态</th><th>目标</th>' +
      '<th>失败原因</th><th>恢复条件</th></tr>' +
      routes.map((r: any) =>
        '<tr><td class="mono">' + wbEsc(r.strategy) + '</td>' +
        '<td>' + wbEsc(routeLabel(r.status)) + '</td>' +
        '<td>' + wbEsc((r.goal || '').slice(0, 80)) + '</td>' +
        '<td>' + wbEsc(r.failure_reason || '-') + '</td>' +
        '<td>' + wbEsc(r.recovery_condition || '-') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 新颖性
  if (novelty.length) {
    parts.push('<div class="wb-section"><h3>新颖性审查</h3>' +
      '<table class="wb"><tr><th>结论</th><th>状态</th><th>结论说明</th><th>检索边界</th></tr>' +
      novelty.map((n: any) =>
        '<tr><td class="mono">' + wbEsc(n.claim_id) + '</td>' +
        '<td>' + wbEsc(statusLabelRaw(n.status)) + '</td>' +
        '<td>' + wbEsc(n.conclusion || '-') + '</td>' +
        '<td>' + wbEsc((n.covered_sources || []).join('、') || '-') +
          ((n.inaccessible || []).length ? '<div class="wb-note" style="color:var(--warn)">' +
            wbEsc(n.inaccessible.join('；')) + '</div>' : '') + '</td></tr>').join('') +
      '</table></div>');
  }

  // 服务端给出的"还缺什么 / 日志契约异常 / 预算停止"说明。
  // 这些是**显式声明**而不是让前端从空数组猜: 计数为 0 可能是"确实没有",
  // 也可能是"尚未做", 二者在界面上的含义完全不同。
  const notes = d.coverage_notes || [];
  if (notes.length) {
    parts.push('<div class="wb-section"><h3>覆盖说明<span class="wb-count">' +
      notes.length + ' 条</span></h3>' +
      notes.map((n: any) => '<div class="wb-note">· ' + wbEsc(n) + '</div>').join('') +
      '</div>');
  }

  // 日志契约异常必须显眼 (统一日志键: 少字段不再是静默的 0)
  const anomalies = d.log_anomalies || [];
  if (anomalies.length) {
    parts.push('<div class="wb-section"><h3>日志契约异常<span class="wb-count">' +
      anomalies.length + ' 条</span></h3><div class="wb-note" style="color:var(--warn)">' +
      '这些事件缺少必需字段或类型未登记, 可能导致界面显示为 0。' +
      anomalies.map((a: any) => '<div>· ' + wbEsc(a.kind || '') + ': ' +
        wbEsc(a.missing || a.reason || '') + '</div>').join('') +
      '</div></div>');
  }

  // 对象级操作
  parts.push(
    '<div class="wb-section"><h3>对象级操作</h3>' +
    '<div class="adv-row">' +
    '<label>作用对象 <select id="wbobj">' +
    feedbackObjectOptions({
      assumptions: Object.fromEntries((d.assumptions || []).map((a: any) => [a.id, a.statement || ''])),
      claims: Object.fromEntries(claims.map((c: any) => [c.id, c.statement])),
      steps: Object.fromEntries((d.steps || []).map((s: any) => [s.id, s.text || ''])),
    }).map((o: any) => '<option value="' + wbEsc(o.value) + '">' + wbEsc(o.label) + '</option>').join('') +
    '</select></label>' +
    '</div>' +
    '<div class="adv-row">' +
    '<input type="text" id="wbfeedback" placeholder="反馈：如「不要假设 x ∈ real」/「这个结论请给反例」" style="flex:2">' +
    '<button class="ghost" data-action="submitWorkbenchFeedback">施加反馈</button>' +
    '</div>' +
    '<div class="adv-row">' +
    '<input type="text" id="wbsnapid" placeholder="快照 ID（留空取最新）">' +
    '<button class="ghost" data-action="forkFromWorkbench">从快照派生新问题</button>' +
    '<button class="ghost" data-action="refreshWorkbench">刷新工作台</button>' +
    '</div>' +
    '<div class="wb-note">反馈会落到具体假设/结论上；无法唯一确定对象时系统会请求澄清，不会猜。</div>' +
    '</div>'
  );

  el.innerHTML = parts.join('');
  return el.innerHTML;
}

function submitWorkbenchFeedback(objectId: any, textOverride: any) {
  const pid = currentProjectId || ((byId<HTMLInputElement>('projid') && val('projid')) || '').trim();
  const box = byId<HTMLTextAreaElement>('wbfeedback');
  const text = (textOverride !== undefined ? textOverride : (box ? box.value : '')).trim();
  if (!pid) { addMsg('请先关联理论研究项目（启动一次理论研究）', 'msg-error'); return; }
  if (!text) { addMsg('请输入反馈内容', 'msg-error'); return; }
  const picker = byId<HTMLSelectElement>('wbobj');
  const target = objectId !== undefined && objectId !== null
    ? objectId : (picker ? picker.value : '');
  fetch('/api/research/' + encodeURIComponent(pid) + '/feedback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({response: text, object_id: target || '',
                          problem_id: currentProblemId || ''})
  }).then((r: any) => r.json().then((d: any) => ({status: r.status, body: d}))).then(({status, body}) => {
    if (body.needs_clarification) {
      addMsg('需要澄清: ' + (body.clarify || '无法确定该意见作用的对象'), 'msg-error');
      // 列出候选对象让用户点选 (F1-5), 而不是只报错
      if ((body.candidates || []).length) renderFeedbackClarification(body);
    } else if (body.ok) {
      const applied = (body.applied || []).map((a: any) =>
        a.action + (a.object_id ? ' → ' + a.object_id : '') +
        (a.affected_claims && a.affected_claims.length
          ? '（失效: ' + a.affected_claims.join(', ') + '）' : '')
      ).join('; ');
      addMsg('已按对象施加: ' + (applied || '无具体变更'), 'msg-system');
      if (box) box.value = '';
      refreshWorkbench();
    } else {
      addMsg('反馈未施加 (HTTP ' + status + '): ' + JSON.stringify(body), 'msg-error');
    }
  }).catch((e: any) => addMsg('反馈失败: ' + e, 'msg-error'));
}

function forkFromWorkbench() {
  const pid = currentProjectId || ((byId<HTMLInputElement>('projid') && val('projid')) || '').trim();
  if (!pid) { addMsg('请先关联理论研究项目', 'msg-error'); return; }
  const snapBox = byId<HTMLInputElement>('wbsnapid');
  const snap = snapBox ? snapBox.value.trim() : '';
  fetch('/api/research/fork', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({project_id: pid, snapshot_id: snap})
  }).then((r: any) => r.json().then((d: any) => ({status: r.status, body: d}))).then(({status, body}) => {
    if (body.ok) {
      addMsg('已派生新问题；需重算结论: ' + (body.imported_claims || []).join(', ') +
             '（未复用任何旧验证记录）', 'msg-system');
      refreshWorkbench();
    } else {
      addMsg('派生失败 (HTTP ' + status + '): ' +
             (body.detail || body.reason || JSON.stringify(body)), 'msg-error');
    }
  }).catch((e: any) => addMsg('派生失败: ' + e, 'msg-error'));
}

/* ---------------- 上下文 / 历史 ---------------- */
function stageLabel(stages: any) {
  if (!stages || stages.length === 0) return '无';
  if (stages.includes('write')) return '已撰写';
  if (stages.includes('research')) return '已检索';
  return stages.join('+');
}

function refreshContexts() {
  fetch('/api/contexts?_=' + Date.now()).then((r: any) => r.json()).then((d: any) => {
    const sel = byId<HTMLSelectElement>('ctx')!;
    const prev = currentContext || '';
    sel.innerHTML = '<option value="">+ 新主题</option>';
    (d.contexts || []).forEach((c: any) => {
      const opt = document.createElement('option');
      opt.value = c.topic;
      opt.textContent = c.topic + '（' + stageLabel(c.stages) + '）';
      sel.appendChild(opt);
    });
    sel.value = prev;
    currentResearch.apply({contextTopic: prev});
  }).catch(() => {});
}

function onContextChange() {
  // 综述上下文只是**资料库/主题偏好**: 它不改变理论研究问题 (F1-3)
  const topic = val('ctx');
  currentResearch.apply({contextTopic: topic});
  if (topic) {
    addMsg('已切换到上下文: ' + topic, 'msg-agent');
    loadContextNotes(topic);
  } else {
    addMsg('已切换到新主题', 'msg-agent');
  }
}

function loadContextNotes(topic: any) {
  fetch('/api/contexts/' + encodeURIComponent(topic) + '/notes')
    .then((r: any) => r.json()).then((d: any) => {
      if (d.notes) {
        renderMarkdown(byId('preview'), '# ' + escapeHtml(d.topic || topic) + '\n\n' + d.notes);
      } else {
        renderMarkdown(byId('preview'), '# ' + escapeHtml(d.topic || topic) + '\n\n该上下文暂无综述笔记。');
      }
      switchTab('preview');
    }).catch(() => {
      addMsg('加载上下文笔记失败', 'msg-error');
    });
}

function refreshHistorySelect() {
  fetch('/api/conversations?_=' + Date.now(), {cache: 'no-store'}).then((r: any) => r.json()).then((d: any) => {
    const sel = byId<HTMLSelectElement>('hist')!;
    const prev = currentSessionId || '';
    sel.innerHTML = '<option value="">+ 历史会话</option>';
    (d.conversations || []).forEach((c: any) => {
      const opt = document.createElement('option');
      opt.value = c.session_id;
      opt.textContent = (c.topic || c.session_id) + ' · ' + fmtTime(c.updated_at || c.created_at) +
                        ' · ' + statusLabel(c.status);
      sel.appendChild(opt);
    });
    sel.value = prev;
  }).catch(() => {});
}

function onHistoryChange() {
  const sid = val('hist');
  if (!sid) { newSession(); return; }
  switchToConversation(sid);
}

function newSession() {
  // F1-2: 新会话必须清空旧项目/问题与工作台绑定, 否则新研究会沿用上一个问题
  currentResearch.apply({threadId: '', sessionId: '', projectId: '', problemId: '', runId: ''});
  bindRunToProject('');  if (es) { es.close(); es = null; }
  setHtml('log', '');
  if (byId<HTMLInputElement>('projid')) setVal('projid', '');
  if (byId<HTMLInputElement>('probid')) setVal('probid', 'problem');
  if (byId('wbfeedback')) setVal('wbfeedback', '');
  if (byId('wbsnapid')) setVal('wbsnapid', '');
  if (byId('runhint')) setText('runhint', '');
  wbRequestSeq += 1;                 // 作废在途的工作台请求
  if (byId('workbench')) {
    byId('workbench')!.innerHTML = '<div class="empty">新会话：尚未关联理论研究项目。' +
      '启动一次理论研究，或在高级选项里填写项目 ID 后点「加载工作台」。</div>';
  }
  setMode('idle');
  setStatus('就绪', '');
  addMsg('欢迎使用 AIR 智能体研究系统。', 'msg-agent');
  addMsg(runMode === 'theory'
    ? '当前为「理论研究」模式：输入一个明确问题（如「对所有实数 x: x**2 >= 0」）或研究方向，系统会先生成候选路线再由你确认。'
    : '在下方输入研究主题或用一句话描述研究内容，回车启动；或从顶部「会话」下拉框切换历史会话继续工作。', 'msg-agent');
}

function switchToConversation(sid: any) {
  // F1-1: 切换历史会话时**整体**加载该会话的上下文 (模式/项目/问题),
  // 不能只恢复线程 —— 否则从理论会话切到综述会话时, 模式和项目字段会沿用上一个会话。
  fetch('/api/conversations/' + encodeURIComponent(sid)).then((r: any) => r.json()).then((rec: any) => {
    const req = rec.request || {};
    const sessionMode = (req.mode === 'theory') ? 'theory' : 'survey';
    currentResearch.apply({
      sessionId: sid,
      mode: sessionMode,
      projectId: req.project_id || '',
      problemId: req.problem_id || '',
      runId: req.run_id || rec.run_id || '',
    });
    // 历史会话带的是**真实**项目 id (不是草稿): 允许工作台查询它。
    // 必须在 `onModeChange()` **之前**把字段写上, 原因有两条:
    //   1) `onModeChange()` 内部按 `#projid` 是否为空决定要不要生成**草稿** id
    //      (R2: 附件需要稳定身份); 字段为空它会生成草稿, 而草稿没有研究记录;
    //   2) `workbenchTargetPid()` 要求"已绑定运行"或"当前 id 不是草稿"才放行。
    bindRunToProject(req.project_id || '');
    if (byId<HTMLInputElement>('projid')) setVal('projid', req.project_id || '');
    if (byId<HTMLInputElement>('probid')) setVal('probid', req.problem_id || 'problem');
    if (byId<HTMLSelectElement>('runmode')) setVal('runmode', sessionMode);
    onModeChange();
    // `onModeChange()` 内部还会写 `#projid`/`currentResearch.projectId`, 因此这里再断言
    // 一次: 历史会话的身份必须胜过该函数里的模式相关副作用。
    if (sessionMode === 'theory') {
      if (byId<HTMLInputElement>('projid')) setVal('projid', req.project_id || '');
      currentResearch.apply({projectId: req.project_id || ''});
    }
    renderHistoryToLog(rec);
    if (sessionMode === 'theory' && req.project_id) {
      switchTab('workbench');
      refreshWorkbench();
    } else if (sessionMode === 'theory') {
      switchTab('workbench');
    } else {
      switchTab('preview');
    }
    if ((rec.status || '') !== 'done') {
      resumeSession(sid);
    } else {
      currentResearch.apply({threadId: ''});
      setMode('idle');
      setStatus('已完成', 'done');
    }
  }).catch(() => addMsg('加载历史会话失败', 'msg-error'));
}

function renderHistoryToLog(rec: any) {
  const log = byId('log')!;
  log.innerHTML = '';
  const msgs = rec.messages || [];
  const resumable = (rec.status || '') !== 'done';
  const skipLast = resumable && msgs.length && msgs[msgs.length - 1].role === 'interrupt';
  const toRender = skipLast ? msgs.slice(0, -1) : msgs;
  addMsg('[历史会话] ' + (rec.topic || rec.session_id || ''), 'msg-agent');
  toRender.forEach((m: any) => {
    if (m.role === 'interrupt') {
      const card = document.createElement('div');
      card.className = 'interrupt-card';
      card.innerHTML = '<div class="it-title">' + escapeHtml(m.title || '') + '</div>' +
                       '<div class="it-hint">' + escapeHtml(m.hint || '') + '</div>';
      log.appendChild(card);
      return;
    }
    const row = document.createElement('div');
    row.className = 'msg ' + messageClass(m);
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = messageAvatar(m);
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = m.text || '';
    row.appendChild(avatar);
    row.appendChild(bubble);
    log.appendChild(row);
  });
  log.scrollTop = log.scrollHeight;
}

function resumeSession(sid: any) {
  fetch('/api/sessions/' + encodeURIComponent(sid) + '/resume', {method: 'POST'})
    .then((r: any) => r.json()).then((d: any) => {
      if (d.thread_id) {
        currentResearch.apply({threadId: d.thread_id});
        setMode('running');
        setStatus('恢复会话…', 'running');
        connectSSE(d.thread_id);
      } else {
        addMsg('恢复失败: ' + JSON.stringify(d), 'msg-error');
      }
    }).catch((e: any) => addMsg('恢复失败: ' + e, 'msg-error'));
}

function deleteSession() {
  const sid = currentSessionId || val('hist');
  if (!sid) { alert('请先在「历史会话」下拉框选择要删除的会话'); return; }
  const label = byId<HTMLSelectElement>('hist')!.selectedOptions[0] ? byId<HTMLSelectElement>('hist')!.selectedOptions[0].textContent : sid;
  if (!confirm('确认删除该会话？\n\n' + label + '\n\n将删除以下内容（不可恢复）：\n' +
    '· 对话记录（data/conversations/）\n' +
    '· 检查点（data/checkpoints/）\n' +
    '· 产出文件夹（outputs/ 对应子目录）\n' +
    '· 检索缓存（data/pipeline_cache/ 对应主题）\n' +
    '· 向量库（data/chroma/，全局 RAG 检索数据）\n\n' +
    '确定删除吗？')) return;
  fetch('/api/sessions/' + encodeURIComponent(sid), {method: 'DELETE'})
    .then((r: any) => r.json()).then((d: any) => {
      const removed = (d.removed || []).join('、');
      addMsg(removed ? '已删除会话: ' + removed : '会话已删除', 'msg-agent');
      currentResearch.apply({threadId: '', sessionId: ''});
      if (es) { es.close(); es = null; }
      setHtml('log', '');
      setMode('idle');
      setStatus('就绪', '');
      const sel = byId<HTMLSelectElement>('hist')!;
      const opt = sel.querySelector('option[value="' + sid + '"]');
      if (opt) opt.remove();
      sel.value = '';
      refreshHistorySelect();
      refreshArtifacts();
      refreshContexts();
    }).catch((e: any) => addMsg('删除失败: ' + e, 'msg-error'));
}

/* ---------------- 标签页 / 产物 ---------------- */
function switchTab(name: any) {
  document.querySelectorAll<HTMLElement>('.tab').forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle('active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  setProp('preview', 'display', name === 'preview' ? 'block' : 'none');
  setProp('workbench', 'display', name === 'workbench' ? 'block' : 'none');
  setProp('files', 'display', name === 'files' ? 'block' : 'none');
  if (name === 'files') refreshArtifacts();
  if (name === 'workbench') refreshWorkbench();
}

// F3: 标签页可键盘操作 (←/→ 在标签间移动并切换, Home/End 到首尾)
function onTabKey(ev: KeyboardEvent) {
  const tabs = Array.from(document.querySelectorAll<HTMLElement>('.tab'));
  const idx = tabs.findIndex((t: any) => t.classList.contains('active'));
  let next = -1;
  if (ev.key === 'ArrowRight') next = (idx + 1) % tabs.length;
  else if (ev.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
  else if (ev.key === 'Home') next = 0;
  else if (ev.key === 'End') next = tabs.length - 1;
  if (next < 0) return;
  ev.preventDefault();
  tabs[next].focus();
  switchTab(tabs[next].dataset.tab);
}

function renderMarkdown(el: any, text: any) {
  // F3: 只走本地安全渲染器 (DOM API), 不再使用 marked 的 innerHTML 输出
  const md = window.AIRMarkdown;
  if (md && md.render) {
    el.innerHTML = '';
    el.className = 'markdown';
    el.appendChild(md.render(text || '', {className: ''}));
  } else {
    el.innerHTML = '';
    el.className = '';
    el.appendChild(md!.renderText(text || '', {className: ''}));
  }
}

function refreshArtifacts() {
  // F3: 文件清单绑定当前 run/研究问题; 不再把全部 outputs/ 混在一起展示
  const binding = {projectId: currentResearch.projectId,
                   problemId: currentResearch.problemId,
                   runId: currentResearch.runId};
  const qs = artifactQuery(binding);
  fetch('/api/artifacts' + (qs ? '?' + qs : '') + (qs ? '&' : '?') + '_=' + Date.now())
    .then((r: any) => r.json()).then((data: any) => {
    const el = byId('files')!;
    el.innerHTML = '';
    const files = data.files || [];
    const bindingEl = byId('filebinding')!;
    if (bindingEl) bindingEl.textContent = bindingScope(data, binding);
    if (!files.length) {
      el.innerHTML = '<div class="empty">当前研究问题暂无输出文件' +
        (qs ? '' : '（outputs/ 也为空）') + '</div>';
      return;
    }
    files.forEach((f: any) => {
      const item = document.createElement('div');
      item.className = artifactRowClass(f);
      item.innerHTML = artifactRowHtml(f);
      item.onclick = () => loadArtifact(f.name);
      el.appendChild(item);
    });
  }).catch(() => {});
}

function loadArtifact(name: any) {
  fetch('/api/artifacts/' + encodeURI(name)).then((r: any) => r.json()).then((data: any) => {
    if (data.binary) { window.open(data.url, '_blank'); }
    else if (data.error) { addMsg('读取失败: ' + data.error, 'msg-error'); }
    else { renderMarkdown(byId('preview'), data.content || ''); switchTab('preview'); }
  }).catch(() => {});
}

function autoGrowReply() {
  const el = byId<HTMLTextAreaElement>('reply')!;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

function clearReply() {
  setVal('reply', '');
  autoGrowReply();
}


function closeHistory() {
  setProp('history-overlay', 'display', 'none');
}

function historyBack() {
  showHistoryList();
}

function showHistoryList() {
  setText('history-title', '历史会话');
  setProp('btn-history-back', 'display', 'none');
  setProp('history-list', 'display', 'block');
  setProp('history-detail', 'display', 'none');
  setHtml('history-detail', '');
  fetch('/api/conversations?_=' + Date.now()).then((r: any) => r.json()).then((d: any) => {
    renderHistoryList(d.conversations || []);
  }).catch(() => {
    setHtml('history-list', '<div class="empty">加载失败</div>');
  });
}

function renderHistoryList(items: any) {
  const el = byId('history-list')!;
  el.innerHTML = '';
  if (!items.length) { el.innerHTML = '<div class="empty">暂无历史会话</div>'; return; }
  items.forEach((c: any) => {
    const item = document.createElement('div');
    item.className = 'history-item';
    item.innerHTML = historyItemHtml(c);
    item.onclick = () => viewConversation(c.session_id);
    el.appendChild(item);
    const btn = item.querySelector<HTMLElement>('.hist-resume');
    if (btn) {
      btn.onclick = (e: Event) => { e.stopPropagation(); resumeConversation(c.session_id); };
    }
  });
}

function resumeConversation(sid: any) {
  fetch('/api/sessions/' + encodeURIComponent(sid) + '/resume', {method: 'POST'})
    .then((r: any) => r.json()).then((d: any) => {
      if (d.thread_id) {
        closeHistory();
        currentResearch.apply({threadId: d.thread_id});
        setHtml('log', '');
        setMode('running');
        setStatus('恢复会话…', 'running');
        connectSSE(d.thread_id);
      } else {
        alert('恢复失败: ' + JSON.stringify(d));
      }
    }).catch((e: any) => alert('恢复失败: ' + e));
}

function viewConversation(sid: any) {
  fetch('/api/conversations/' + encodeURIComponent(sid)).then((r: any) => r.json()).then((rec: any) => {
    renderHistory(rec);
  }).catch(() => {
    setHtml('history-list', '<div class="empty">加载失败</div>');
  });
}

function renderHistory(rec: any) {
  setText('history-title', rec.topic || '会话回看');
  setProp('btn-history-back', 'display', 'inline-block');
  setProp('history-list', 'display', 'none');
  const detail = byId('history-detail')!;
  detail.style.display = 'block';
  detail.innerHTML = '';
  (rec.messages || []).forEach((m: any) => {
    if (m.role === 'interrupt') {
      const card = document.createElement('div');
      card.className = 'interrupt-card';
      card.innerHTML = '<div class="it-title">' + escapeHtml(m.title || '') + '</div>' +
                       '<div class="it-hint">' + escapeHtml(m.hint || '') + '</div>';
      detail.appendChild(card);
      return;
    }
    const row = document.createElement('div');
    row.className = 'msg ' + messageClass(m);
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = messageAvatar(m);
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = m.text || '';
    row.appendChild(avatar);
    row.appendChild(bubble);
    detail.appendChild(row);
  });
  detail.scrollTop = detail.scrollHeight;
}

/* ---------------- 初始化 ----------------
 * 必须等 DOM 就绪: 顶层直接 `byId<HTMLTextAreaElement>('reply')!.addEventListener(...)` 会在脚本被提前
 * 执行 (或元素缺失) 时抛错并**中断整个页面逻辑**, 表现为"页面加载了但完全不能点"。
 */
function on(id: any, event: any, fn: any) {
  const el = $(id);
  if (el) el.addEventListener(event, fn);
}

function bindStaticHandlers() {
  const reply = byId<HTMLTextAreaElement>('reply')!;
  if (reply) reply.addEventListener('input', autoGrowReply);
  if (reply) {
    reply.addEventListener('keydown', (e: any) => {
      const send = byId<HTMLButtonElement>('btn-send')!;
      if (e.key === 'Enter' && !e.shiftKey && !(send && send.disabled)) {
        e.preventDefault();
        onSend();
      }
    });
  }
  // P2: 模板不再使用内联 on* 属性 —— CSP (`script-src 'self'`) 会直接拦掉内联事件
  // 处理器 (浏览器报 "Executing inline event handler violates CSP"), 页面加载正常
  // 但按钮全部失效。事件必须在打包后的模块里显式绑定。
  on('hist', 'change', onHistoryChange);
  on('ctx', 'change', onContextChange);
  on('btn-del', 'click', deleteSession);
  on('runmode', 'change', onModeChange);
  on('btn-copy-projid', 'click', copyProjectId);
  on('sourceset', 'change', onSourceSetChange);
  on('btn-refresh-sources', 'click', refreshSourceSets);
  bindUploads();
  on('btn-send', 'click', onSend);
  on('btn-stop', 'click', stopSession);
  on('btn-history-back', 'click', historyBack);
  on('btn-history-close', 'click', closeHistory);
  document.querySelectorAll<HTMLElement>('[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => switchTab(btn.getAttribute('data-tab')));
    btn.addEventListener('keydown', onTabKey);
  });
  // 工作台/历史片段用 data-action 生成按钮: 统一走文档级委托 (CSP 下内联处理器不执行)
  bindDelegatedActions();
}

function boot() {
  bindStaticHandlers();
  onModeChange();
  setMode('idle');
  refreshContexts();
  refreshArtifacts();
  refreshHistorySelect();
  newSession();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}

/* 页面动作表 (P2): 动态生成的按钮不再写 `onclick="..."`。
 *
 * 严格 CSP 下**内联事件处理器根本不执行** (浏览器报 CSP 违规), 页面能加载但按钮
 * 全部失效。改为在生成的 HTML 上写 `data-action="名字" data-id="..."`, 由下面这个
 * 表统一分发: 新增动作只改这里一处, 模板与视图都不再出现内联处理器。
 */
const PAGE_ACTIONS: Record<string, (id?: string, target?: HTMLElement) => void> = {
  selectProblem: (id) => selectProblem(id || ''),
  toggleClaimDetail: (id) => toggleClaimDetail(id || ''),
  submitWorkbenchFeedback: () => submitWorkbenchFeedback(undefined, undefined),
  forkFromWorkbench: () => forkFromWorkbench(),
  refreshWorkbench: () => refreshWorkbench(),
  resumeSameProblem: (id) => resumeSameProblem(id || ''),
  startNewProblem: () => startNewProblem(undefined),
  startWithResume: () => startWithResume(undefined),
  loadArtifact: (id) => loadArtifact(id || ''),
  deleteAttachment: (id) => { void deleteAttachment(id || ''); },
  resumeSession: (id) => resumeSession(id || ''),
  resumeConversation: (id) => resumeConversation(id || ''),
  viewConversation: (id) => viewConversation(id || ''),
};

function bindDelegatedActions() {
  if (typeof document === 'undefined') return;
  document.addEventListener('click', (ev) => {
    const node = (ev.target as HTMLElement | null)?.closest?.('[data-action]');
    if (!node) return;
    const action = node.getAttribute('data-action') || '';
    const handler = PAGE_ACTIONS[action];
    if (!handler) return;
    ev.preventDefault();
    handler(node.getAttribute('data-id') || '', node as HTMLElement);
  });
}

if (typeof window !== 'undefined') {
  // 兼容: 旧的外部脚本/书签仍按全局函数名调用
  Object.entries(PAGE_ACTIONS).forEach(([name, fn]) => {
    (window as unknown as Record<string, unknown>)[name] = fn;
  });
}



