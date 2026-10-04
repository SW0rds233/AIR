/**
 * 页面装配层 (合并计划 §9.5: `app.ts` 只负责装配, 不再承载业务逻辑)。
 *
 * 迁出历史: 本文件原先是 2228 行的单体页面逻辑 (启动、附件、SSE、历史、工作台、
 * 产物与 DOM 绑定全在一起)。现在按 §9.5 的表拆成:
 *
 * - `session-controller.ts` —— 会话操作单一入口 (新建/查看/继续/停止/删除);
 * - `features/intake/controller.ts` + `views/research-input.ts` —— 统一输入、附件绑定、
 *   稳定草稿身份 (R2/R3);
 * - `events/session-stream.ts` —— SSE 连接与带上限退避的重连 (`events/session-events.ts`
 *   的协议层之上);
 * - `views/research-overview.ts` / `views/project-navigation.ts` / `views/interrupt-cards.ts`
 *   —— 工作台总览、历史导航与暂停点卡片的纯渲染函数;
 * - `library-controller.ts::createLibrarySection` —— 本机路径资料接入;
 * - `team-controller.ts::createTeamMount` —— 团队区块挂载。
 *
 * 本文件仍然守住的硬约束 (迁移期不允许退化):
 * 1. 严格 CSP (`script-src 'self'`): **没有内联事件处理器**, 所有动态按钮继续走
 *    `data-action` + 文档级委托 `PAGE_ACTIONS`;
 * 2. 所有 `id` / 选择器不变 (现有 e2e 与后端联调依赖它们);
 * 3. `window.AIRTeam`、`window.AIRLibrary` 与 `window.AIR` 兼容转发保留;
 * 4. **唯一状态**是 `window.AIR.research` (经 `currentResearch` 单向投影), 这里不新建
 *    第二份可写状态, 也不做双向同步;
 * 5. 直接的 DOM 访问保留 `any` 边界 (后端 JSON 载荷形状由 `contracts.ts` 描述),
 *    不重新引入 `@ts-nocheck`。
 */
import { air as AIR } from './air-global';
import type { ResearchPatch, RunStatus } from './current-research';
import {
  byId, scrollToBottom, setDisabled, setHtml, setPlaceholder, setProp,
  setText, setVal, val,
} from './dom';
import {
  createLibrarySection,
  type LibraryController,
  type LibrarySection,
} from './library-controller';
import {
  renderImportReport,
  renderLibraryWorkspace,
  renderScanPreview,
} from './views/library';
import {
  createTeamMount,
  getStore as getTeamStore,
  resetTeamStore,
  type TeamMount,
} from './team-controller';
import { createIntakeController, type IntakeController } from './features/intake/controller';
import { createSessionController, type SessionController } from './session-controller';
import { createSessionStream, type SessionStream } from './events/session-stream';
import {
  artifactClass as artifactRowClass,
  artifactNote,
  artifactQuery,
  artifactRowHtml,
  bindingScope,
} from './views/artifacts';
import { escapeHtml as overviewEsc } from './views/research-workbench';
import { pickManifestEntry, readManifestBody } from './contracts/publication';
import { buildInterruptCard } from './views/interrupt-cards';
import { stageLabel as stageLabelOf } from './views/project-navigation';
// §9.6: 论文/公式/图表追溯 (纯函数视图; 取数与 DOM 装配在这里)
import {
  numberingView,
  renderFigureGallery,
  renderNumbering,
  renderPaperTraceability,
  type ArtifactEntryView,
  type DeliveryManifestView,
} from './views/paper';
import {
  renderResearchOverview,
  toggledClaimId,
  workbenchErrorHtml,
  workbenchMissingHtml,
  workbenchNoProjectHtml,
  workbenchUnboundHtml,
} from './views/research-overview';
import type { WorkbenchData } from './views/research-workbench';
// §9.5 取数入口: 页面装配层也不再自己拼 URL / 判错 / 重试 —— 全部走 api/ 层。
// `pageApi()` 每次调用时解析单例, 这样它永远是最新的客户端 (允许多个入口各自替换)。
import {
  ENDPOINTS,
  client as researchClient,
  type ResearchClient,
} from './api/research-client';

function pageApi(): ResearchClient {
  return researchClient();
}
import type { LibraryDetail } from './contracts/library';

let currentThreadId: string | null = null;
let currentSessionId: string | null = null;

let mode = 'idle';            // 会话状态: idle / running / waiting
let currentContext = '';      // 选中的资料库主题 (与项目 id 不是同一个字段)
let currentProjectId = '';    // 研究项目 ID (反馈/派生/工作台)
let currentProblemId = '';
let pendingInterruptId = '';  // 当前等待回答的暂停点 ID (F2 幂等键)
let chosenCandidateId = '';   // 用户点选的候选路线 ID (仅用于卡片回显)

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
  mode: '',
  projectId: '',
  problemId: '',
  // 选中的资料库主题; 与项目 id 分开保存, 互不覆盖
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
    this.mode = src.mode || '';
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
      mode: this.mode,
      projectId: this.projectId, problemId: this.problemId,
      contextTopic: this.contextTopic, runId: this.runId,
      status: this.status as RunStatus,
    });
    return this;
  },
  // 是否已绑定一个研究问题 (工作台/反馈/派生都需要)
  hasProblem() { return AIR_STATE ? AIR_STATE.hasProblem() : Boolean(this.projectId); },
  label() {
    if (AIR_STATE) return AIR_STATE.label();
    const bits: string[] = [];
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
  // 本地直接改字段的地方 (setStatus 等) 也要回写到权威状态,
  // 否则同一份"当前研究"又会出现两个不同步的副本 (F1)。
  if (currentResearch.canonical()) {
    currentResearch.pull();
  } else if (AIR_STATE) {
    AIR_STATE.apply({
      threadId: currentResearch.threadId, sessionId: currentResearch.sessionId,
      mode: currentResearch.mode, projectId: currentResearch.projectId,
      problemId: currentResearch.problemId,
      contextTopic: currentResearch.contextTopic, runId: currentResearch.runId,
      status: currentResearch.status as RunStatus,
    });
  }
  currentThreadId = currentResearch.threadId || null;
  currentSessionId = currentResearch.sessionId || null;
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

// 初始投影 (必须在 $ 与 STATUS_LABEL 之后: label() 依赖它们)
currentResearch.pull();

function setStatus(text: any, cls: any) {
  const el = byId('status');
  if (!el) return;
  el.textContent = text;
  el.className = cls ? ('dot-' + cls) : '';
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
  return '用一句话描述要研究的问题或方向（回车发送；项目、资料与附件入口始终可用）';
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

/* ---------------- 运行模式 ---------------- */

/** 生成新的草稿项目 id (R2: 让附件在稳定身份下上传)。 */
function newDraftProjectId(): string {
  return 'proj-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

/* ---------------- 统一输入的准备 (取代旧的"模式切换") ----------------
 * 统一入口下没有模式可选, 因此这里只做两件**始终成立**的准备:
 * 1. 落实稳定草稿身份 (R2): 附件与检索必须归属到明确的项目/问题;
 * 2. 把资料库列表与已上传附件拉回来 —— 入口始终可见, 打开页面就该看到它们。
 * 工作台仍只在"该项目确实有运行记录"时查询 (草稿身份不查, 避免必然 404)。
 */
function prepareUnifiedIntake() {
  if (mode === 'idle') setPlaceholder('reply', defaultPlaceholder());
  // R2: 落实稳定身份 —— 附件必须在有 project_id 之后上传, 否则文件会落到与运行无关的目录
  if (!(val('projid') || '').trim()) {
    const draft = newDraftProjectId();
    setVal('projid', draft);
    currentResearch.apply({ projectId: draft });
    // 草稿身份**不是**运行绑定: 必须显式记下来, 否则刷新工作台只能靠"项目 id 恰好
    // 不等于状态里的项目 id"这条间接判据, 判据一旦被后来写入的值填平就会 404
    d.markDraftProject(draft);
  }
  refreshSourceSets();
  void d.refreshAttachments();
  const pid = d.workbenchTargetPid();
  if (pid) {
    currentResearch.apply({projectId: pid});
    refreshWorkbench();
  } else if (byId('workbench')) {
    byId('workbench')!.innerHTML = workbenchMissingHtml(
      (val('projid') || '').trim() || currentProjectId);
  }
}

function copyProjectId() {
  const pid = (val('projid') || '').trim() || currentProjectId;
  if (!pid) { addMsg('当前没有项目 ID', 'msg-error'); return; }
  if (navigator.clipboard) navigator.clipboard.writeText(pid);
  addMsg('项目 ID: ' + pid, 'msg-system');
}

/* ---------------- 资料源绑定 (计划书 §3 R0) ---------------- */
function refreshSourceSets() {
  const sel = byId<HTMLSelectElement>('sourceset')!;
  if (!sel) return;
  pageApi().json(ENDPOINTS.sources).then((d: any) => {
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

function onSourceSetChange() {
  const sel = byId<HTMLSelectElement>('sourceset')!;
  const info = byId('sourceinfo')!;
  const id = sel ? sel.value : '';
  if (!info) return;
  if (!id) { info.textContent = '未绑定：研究将按主题名匹配同名资料库'; return; }
  pageApi().json(ENDPOINTS.source(String(id))).then((d: any) => {
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

/* ---------------- 资料库工作区 (合并计划 §13.4 的列表与详情) ---------------- */
/** 已展开详情的资料库 (按需查询, 不预先拉取全部库的文件清单)。 */
const libraryDetails: Record<string, LibraryDetail> = {};
let librarySection: LibrarySection | null = null;

function library(): LibrarySection {
  if (librarySection) return librarySection;
  librarySection = createLibrarySection({
    notify: (text, cls) => addMsg('[资料库] ' + text, cls || 'msg-system'),
    setPreviewHtml: (html) => setHtml('libpreview', html),
    renderScanPreview: (report) => renderScanPreview(report),
    renderImportReport: (report) => renderImportReport(report),
    loadSources: async () => {
      const body = await pageApi().json<Record<string, any>>(ENDPOINTS.sources);
      return (body && body.sources) || [];
    },
    renderWorkspace: (sources) => setHtml('libworkspace',
      renderLibraryWorkspace(sources, libraryDetails)),
    refreshSourceSets: () => refreshSourceSets(),
    setImportDisabled: (disabled) => setDisabled('btn-lib-import', disabled),
    readPaths: () => val('libpaths', ''),
    readLabel: () => val('liblabel', ''),
    details: () => libraryDetails,
    deleteDetail: (id) => { delete libraryDetails[id]; },
  });
  return librarySection;
}

/** 迁移期入口用: 当前资料接入 controller (只读 + 显式动作)。 */
function getLibraryController(): LibraryController {
  return library().controller();
}

/* ---------------- 科研工作台 (§9.5: 渲染在 views/research-overview.ts) ---------------- */
let wbRequestSeq = 0;
let wbRequestKey = '';
let workbenchData: WorkbenchData | null = null;   // 最近一次工作台状态 (供详情导航查询)
let openClaimDetail = '';        // 当前展开详情的结论 id

/** 已确认建立过运行的项目 id (`''` = 尚未确认)。 */
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

function workbenchTargetFor(pid: string): string {
  if (!pid) return '';
  // 两条判据都必须显式判: "记录里明确说过没有运行记录" 且 "当前就是那个 id"。
  // 只要用户换成别的 id (手填/历史会话), 就允许查一次 —— 否则手动加载工作台被堵死。
  if (!runBoundProjectId && pid === draftUnboundProjectId) return '';
  return pid;
}

// F0-1: 用 URL/searchParams 构造地址。早期实现手拼 '?problem_id=...' + '&_=...',
// 在没有 problem_id 时会拼出 '.../state&_=123' (缺少 '?'), 请求打不到路由。
function researchStateUrl(projectId: any, problemId: any) {
  // 只产出**相对路径** (含查询串): 绝对地址由 api/ 层按 base 解析, 组件不关心 origin。
  const url = new URL(ENDPOINTS.researchState(String(projectId)), 'http://local');
  if (problemId) url.searchParams.set('problem_id', problemId);
  url.searchParams.set('_', String(Date.now()));
  return url.pathname + url.search;
}

function refreshWorkbench() {
  const pid = ((byId<HTMLInputElement>('projid') && val('projid')) || currentProjectId || '').trim();
  if (!pid) {
    writeWorkbench(workbenchNoProjectHtml());
    return;
  }
  // 没有研究记录的项目: 不发注定 404 的请求, 直接说明为什么空
  const bound = workbenchTargetFor(pid);
  if (!bound) {
    writeWorkbench(workbenchUnboundHtml(pid));
    return;
  }
  currentResearch.apply({projectId: pid});
  // F2: 每次读取绑定 (项目, 问题) 与序号; 旧请求最后返回时不得覆盖新画面
  const key = pid + '|' + (currentProblemId || '');
  const seq = ++wbRequestSeq;
  wbRequestKey = key;
  // 工作台状态也走统一客户端; 用 raw 是为了按状态码区分"加载失败"与"内容异常"
  const statePath = researchStateUrl(pid, currentProblemId);
  pageApi().raw(statePath)
    .then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
      if (seq !== wbRequestSeq || key !== wbRequestKey) return;  // 已被更新的请求取代
      if (status !== 200) {
        writeWorkbench(workbenchErrorHtml(status, body));
        return;
      }
      if (body.problem_id && body.problem_id !== currentProblemId) {
        // 服务端已解析出唯一问题: 写回上下文与表单, 保持一致
        currentResearch.apply({problemId: body.problem_id});
        if (byId<HTMLInputElement>('probid')) setVal('probid', body.problem_id);
      }
      workbenchData = body;
      renderWorkbench(body);
      // 团队视图: 主控理解与派工在前, 旧工作台 (研究对象与产物) 在后。
      // 状态只有一份 (team-controller 的 ResearchStore), 这里只负责触发加载与拼装。
      void team().mount(pid, body && body.run_id);
    })
    .catch((e: any) => {
      if (seq !== wbRequestSeq) return;
      setHtml('workbench', '<div class="empty">读取工作台失败: ' + overviewEsc(String(e)) + '</div>');
    });
}

function writeWorkbench(html: string) {
  const el = byId('workbench');
  if (el) el.innerHTML = html;
}

function renderWorkbench(d: any) {
  const html = renderResearchOverview(d, {
    data: () => workbenchData,
    openClaimId: () => openClaimDetail,
  });
  writeWorkbench(html);
  return html;
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
 * 实现已搬到 views/research-overview.ts (纯函数, 有类型与单测), 这里只做开关。 */
function toggleClaimDetail(claimId: any) {
  openClaimDetail = toggledClaimId(openClaimDetail, claimId);
  if (workbenchData) renderWorkbench(workbenchData);
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
  pageApi().raw(ENDPOINTS.researchFeedback(String(pid)), {
    method: 'POST',
    body: {response: text, object_id: target || '', problem_id: currentProblemId || ''},
  // 反馈是**变更类**请求: 用 raw 以便如实读出状态码 (4xx 也要照原样报给用户),
  // 同时由客户端统一处理请求头与错误语义。
  }).then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
    if (body.needs_clarification) {
      addMsg('需要澄清: ' + (body.clarify || '无法确定该意见作用的对象'), 'msg-error');
      // 列出候选对象让用户点选 (F1-5), 而不是只报错
      if ((body.candidates || []).length) d.showFeedbackClarification(body);
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
  pageApi().raw(ENDPOINTS.fork, {
    method: 'POST',
    body: {project_id: pid, snapshot_id: snap},
  }).then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d}))).then(({status, body}) => {
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

/* ---------------- 上下文 ---------------- */
// 综述上下文的阶段标签在 views/project-navigation.ts (纯函数, 已单测), 这里只调用。
function refreshContexts() {
  pageApi().json(ENDPOINTS.contexts, {params: {_: Date.now()}}).then((d: any) => {
    const sel = byId<HTMLSelectElement>('ctx')!;
    if (!sel) return;
    const prev = currentContext || '';
    sel.innerHTML = '<option value="">+ 新主题</option>';
    (d.contexts || []).forEach((c: any) => {
      const opt = document.createElement('option');
      opt.value = c.topic;
      opt.textContent = c.topic + '（' + stageLabelOf(c.stages) + '）';
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
  pageApi().json(ENDPOINTS.contextNotes(String(topic)))
    .then((d: any) => {
      if (d.notes) {
        renderMarkdown(byId('preview'), '# ' + overviewEsc(d.topic || topic) + '\n\n' + d.notes);
      } else {
        renderMarkdown(byId('preview'),
          '# ' + overviewEsc(d.topic || topic) + '\n\n该上下文暂无综述笔记。');
      }
      switchTab('preview');
    }).catch(() => {
      addMsg('加载上下文笔记失败', 'msg-error');
    });
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
  setProp('library', 'display', name === 'library' ? 'block' : 'none');
  setProp('paper', 'display', name === 'paper' ? 'block' : 'none');
  setProp('files', 'display', name === 'files' ? 'block' : 'none');
  if (name === 'files') refreshArtifacts();
  if (name === 'workbench') refreshWorkbench();
  if (name === 'paper') void refreshPaperTrace();
  if (name === 'library') void library().refreshWorkspace();
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
  pageApi().json(ENDPOINTS.artifacts + (qs ? '?' + qs + '&' : '?') + '_=' + Date.now())
    .then((data: any) => {
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
      item.innerHTML = artifactRowHtml(f) + artifactNote(f);
      item.onclick = () => loadArtifact(f.name);
      el.appendChild(item);
    });
  }).catch(() => {});
}

function loadArtifact(name: any) {
  pageApi().json(ENDPOINTS.artifact(String(name))).then((data: any) => {
    if (data.binary) { window.open(data.url, '_blank'); }
    else if (data.error) { addMsg('读取失败: ' + data.error, 'msg-error'); }
    else { renderMarkdown(byId('preview'), data.content || ''); switchTab('preview'); }
  }).catch(() => {});
}

/* ---------------- 论文与追溯 (§9.6, F3–F5) ----------------
 * 交付物清单 + `manifest.json` → 稿件追溯 / 图表 / 渲染期编号。
 * 三条硬要求:
 * 1. **不伪造**: manifest 缺失就显示"还没有交付包", 编号缺失就显示"只在最终渲染时分配";
 * 2. **失败保留已加载内容**并说明具体原因, 不把空面板当成成功;
 * 3. 只是**查看**, 不触发任何研究动作。
 */
async function refreshPaperTrace() {
  const target = byId('paper');
  if (!target) return;
  const identity = {projectId: currentResearch.projectId, runId: currentResearch.runId};
  // 复用既有清单查询: 与研究问题/运行绑定, 不把别的问题的产物混进来
  const qs = artifactQuery({projectId: identity.projectId,
                            problemId: currentResearch.problemId,
                            runId: identity.runId});
  let files: ArtifactEntryView[] = [];
  let listReason = '';
  try {
    const body = await pageApi().json<Record<string, any>>(
      ENDPOINTS.artifacts + (qs ? '?' + qs + '&' : '?') + '_=' + Date.now());
    files = (body && body.files) || [];
  } catch (error) {
    listReason = String(error);
  }

  const manifestEntry = pickManifestEntry(files);
  let manifest: DeliveryManifestView | null = null;
  let manifestReason = '';
  if (manifestEntry) {
    try {
      const body = await pageApi().json<Record<string, unknown>>(
        ENDPOINTS.artifact(String(manifestEntry.name)));
      // 取值与"为什么读不到"的判定在 contracts/publication.ts (有单测), 这里只负责渲染
      const parsed = readManifestBody(body);
      manifest = parsed.manifest as DeliveryManifestView | null;
      manifestReason = parsed.reason;
    } catch (error) {
      manifestReason = String(error);
    }
  } else if (listReason) {
    manifestReason = listReason;
  }

  const numbering = manifest
    ? renderNumbering(numberingView((manifest as Record<string, unknown>).numbering))
    : renderNumbering(numberingView(null));
  const parts = [renderPaperTraceability(manifest), numbering,
                 renderFigureGallery(files, identity, '')];
  if (listReason) {
    parts.unshift('<div class="wb-note" style="color:var(--warn)">读取交付物清单失败: ' +
      overviewEsc(listReason) + '（保留已加载内容）</div>');
  } else if (manifestReason) {
    parts.unshift('<div class="wb-note" style="color:var(--warn)">读取交付包 manifest 失败: ' +
      overviewEsc(manifestReason) + '（保留已加载内容）</div>');
  } else if (files.length && !manifestEntry) {
    parts.unshift('<div class="wb-note">该运行没有交付包 manifest（可能是研究备忘录或纯综述交付）。</div>');
  }
  target.innerHTML = parts.join('');
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

/* ---------------- 控制器装配 ---------------- */

/** 统一输入与附件 (计划书 §9.5 `features/intake/`)。 */
const d: IntakeController = createIntakeController({
  threadId: () => currentThreadId || '',
  setThreadId: (id) => { currentResearch.apply({threadId: id}); },
  applyResearch: (patch) => { currentResearch.apply(patch as ResearchPatch); },
  problemId: () => currentProblemId,
  projectId: () => currentProjectId,
  projectField: () => ((byId<HTMLInputElement>('projid') && val('projid')) || '').trim(),
  boundProjectId: () => runBoundProjectId,
  workbenchTargetFor: (pid) => workbenchTargetFor(pid),
  bindRun: (projectId) => bindRunToProject(projectId),
  markDraftProject: (projectId) => markDraftProject(projectId),
  ensureDraftProjectId: () => newDraftProjectId(),
  researchContext: () => currentContext || '',
  addMsg: (text, cls) => addMsg(text, cls),
  setMode: (m) => setMode(m),
  mode: () => mode,
  setStatus: (text, cls) => setStatus(text, cls),
  clearReply: () => clearReply(),
  replyInput: () => byId<HTMLTextAreaElement>('reply'),
  setPlaceholder: (id, value) => setPlaceholder(id, value),
  pendingInterruptId: () => pendingInterruptId,
  currentCandidateId: () => chosenCandidateId,
  onCandidateChosen: (candidateId) => { chosenCandidateId = candidateId; },
  refreshSourceSets: () => refreshSourceSets(),
  refreshHistorySelect: () => sessions.refreshHistorySelect(),
  refreshWorkbench: () => refreshWorkbench(),
  refreshArtifacts: () => refreshArtifacts(),
  refreshContexts: () => refreshContexts(),
  switchTab: (name) => switchTab(name),
  connectStream: (tid) => stream.connect(tid),
  setPendingInterruptId: (id) => { pendingInterruptId = id; },
  submitFeedback: (objectId, text) => submitWorkbenchFeedback(objectId, text),
});

/** 会话生命周期 (计划书 §9.5 `session-controller.ts`)。 */
const sessions: SessionController = createSessionController({
  connectStream: (tid) => stream.connect(tid),
  closeStream: () => stream.close(),
  addMsg: (text, cls) => addMsg(text, cls),
  setMode: (m) => setMode(m),
  setStatus: (text, cls) => setStatus(text, cls),
  switchTab: (name) => switchTab(name),
  refreshWorkbench: () => refreshWorkbench(),
  refreshArtifacts: () => refreshArtifacts(),
  refreshContexts: () => refreshContexts(),
  refreshHistorySelect: () => sessions.refreshHistorySelect(),
  loadArtifact: (name) => loadArtifact(name),
  applyResearch: (patch) => { currentResearch.apply(patch as ResearchPatch); },
  threadId: () => currentThreadId || '',
  sessionId: () => currentSessionId || '',
  workbenchSeqBump: () => { wbRequestSeq += 1; },
  resetTeam: () => { resetTeamStore(); teamCache = null; },
});

/** 事件流 (计划书 §9.5 `events/session-stream.ts`)。 */
const stream: SessionStream = createSessionStream({
  handleEvent: (ev) => handleEvent(ev),
  currentThreadId: () => currentThreadId || '',
  onCompensate: (status) => {
    currentResearch.apply({status: status === 'waiting' ? 'waiting'
                                   : (status === 'running' ? 'running'
                                   : (status === 'done' ? 'done' : 'idle'))});
  },
  onOfflineStatus: () => {
    setStatus('连接中断，等待你的输入', 'running');
    setMode('waiting');
  },
  onFinished: () => setStatus('已完成', 'done'),
  maxReconnectAttempts: 6,
});

/** 团队区块挂载 (合并计划 §9.2)。 */
let teamCache: TeamMount | null = null;
function team(): TeamMount {
  if (teamCache) return teamCache;
  teamCache = createTeamMount({
    legacyResearch: () => (AIR.research || null) as any,
    problemId: () => currentProblemId,
    notify: (text, cls) => addMsg(text, cls || 'msg-error'),
  });
  return teamCache;
}

/* ---------------- 事件流 ---------------- */
function handleEvent(ev: any) {
  switch (ev.type) {
    case 'connected': break;
    case 'node':
      addMsg(ev.text, 'msg-node');
      if (ev.research) updateRunHint(ev.research);
      refreshWorkbench();
      break;
    case 'log': addMsg(ev.text, 'msg-node'); break;
    case 'interrupt': onInterrupt(ev.payload); break;
    case 'done': sessions.onDone(ev.state); break;
    case 'stopped': sessions.onStopped(); break;
    case 'error':
      addMsg('出错: ' + (ev.message || ''), 'msg-error');
      setStatus('出错', 'error');
      sessions.finishRun();
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

  if (type === 'theory_candidates') { d.showCandidateChoice(p); return; }
  if (type === 'theory_feedback') { d.showFeedbackPrompt(p); return; }

  // 综述模式 / 通用暂停点
  byId('log')!.appendChild(buildInterruptCard(p));
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
        d.onSend();
      }
    });
  }
  // P2: 模板不再使用内联 on* 属性 —— CSP (`script-src 'self'`) 会直接拦掉内联事件
  // 处理器 (浏览器报 "Executing inline event handler violates CSP"), 页面加载正常
  // 但按钮全部失效。事件必须在打包后的模块里显式绑定。
  on('hist', 'change', () => sessions.onHistoryChange());
  on('ctx', 'change', onContextChange);
  on('btn-del', 'click', () => sessions.deleteSession());
  on('btn-copy-projid', 'click', copyProjectId);
  on('sourceset', 'change', onSourceSetChange);
  on('btn-refresh-sources', 'click', refreshSourceSets);
  d.bindUploads();
  library().bind();
  on('btn-send', 'click', () => d.onSend());
  on('btn-stop', 'click', () => sessions.stopSession());
  on('btn-history-back', 'click', () => sessions.historyBack());
  on('btn-history-close', 'click', () => sessions.closeHistory());
  document.querySelectorAll<HTMLElement>('[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => switchTab(btn.getAttribute('data-tab')));
    btn.addEventListener('keydown', onTabKey);
  });
  // 工作台/历史片段用 data-action 生成按钮: 统一走文档级委托 (CSP 下内联处理器不执行)
  bindDelegatedActions();
}

function boot() {
  bindStaticHandlers();
  setMode('idle');
  refreshContexts();
  refreshArtifacts();
  void refreshPaperTrace();
  sessions.refreshHistorySelect();
  // 顺序很关键: `newSession()` 会清空项目字段并重置团队视图, 因此"落实草稿身份 +
  // 拉取资料/附件"必须在它**之后** —— 否则刚落实的草稿项目 id 会被清掉。
  sessions.newSession();
  prepareUnifiedIntake();
  // 团队视图: 页面打开即显示团队角色与**真实可用能力** (来自 /api/team/roles)。
  // 迁移期通过 window.AIRTeam 暴露入口, 让浏览器用例不必点击进入某个运行就能验证它。
  exposeTeamApi();
  void team().mount('', '');
}

/* 迁移期入口 (计划书 §9.3: `window.AIR` 在迁移期提供兼容转发, 最终退出业务写路径)。
 * 团队视图的读接口在这里暴露, 供浏览器验收与调试使用; 它**只读**, 不启动任何任务。 */
function exposeTeamApi() {
  if (typeof window === 'undefined') return;
  (window as any).AIRTeam = {
    mount: (projectId?: string, runId?: string) =>
      team().mount(String(projectId || ''), String(runId || '')),
    store: () => getTeamStore(),
  };
  // §13.4: 资料接入入口 (只读 + 显式动作; 浏览器验收与调试用)。
  (window as any).AIRLibrary = {
    scan: (paths?: string, label?: string) =>
      getLibraryController().scan(String(paths || ''), String(label || '')),
    commit: () => getLibraryController().commit(),
    report: () => getLibraryController().importReport(),
    remove: (sourceSetId?: string) => library().remove(String(sourceSetId || '')),
    refresh: () => library().refreshWorkspace(),
  };
  // §9.6: 论文与追溯面板的只读刷新入口 (浏览器验收与调试用; 不触发任何研究动作)。
  (window as any).AIRPaper = {
    refresh: () => refreshPaperTrace(),
  };
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
  resumeSameProblem: (id) => d.resumeSameProblem({problem_id: id}),
  startNewProblem: () => d.startNewProblem(''),
  startWithResume: () => d.startWithResume(''),
  loadArtifact: (id) => loadArtifact(id || ''),
  deleteAttachment: (id) => { void d.deleteAttachment(id || ''); },
  deleteLibrary: (id) => { void library().remove(id || ''); },
  resumeSession: (id) => sessions.resumeSession(id || ''),
  resumeConversation: (id) => sessions.resumeConversation(id || ''),
  viewConversation: (id) => sessions.viewConversation(id || ''),
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




