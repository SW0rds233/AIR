/**
 * 页面装配层 (合并计划 §8.1 / §9.5: `app.ts` 只负责装配, 不再承载业务逻辑,
 * 也不再持有任何状态副本)。
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
 * G19: 状态只有一份, 在 `state/research-store.ts`。本文件**不**再定义
 * `currentResearch` 对象, 也**不**再维护 `currentProjectId` / `currentProblemId` /
 * `currentThreadId` / `currentSessionId` / `currentContext` / `mode` 这一组 ID 镜像
 * (它们曾经与 `window.AIR.research` 并行存在, 于是必须靠 `syncResearchGlobals()`
 * 来回对表)。现在:
 *
 * - 读: `getState()` + selector (`selectionOf` / `researchView` / `inputPhase` / ...);
 * - 写: `dispatch(action)` (身份切换用 `selection/open` / `selection/reset`,
 *   字段回写用 `selection/patch`);
 * - 页面只留下 DOM 与装配 (渲染在 `views/*`), 运行状态徽标由 selector 现算。
 *
 * 本文件仍然守住的硬约束 (迁移期不允许退化):
 * 1. 严格 CSP (`script-src 'self'`): **没有内联事件处理器**, 所有动态按钮继续走
 *    `data-action` + 文档级委托 `PAGE_ACTIONS`;
 * 2. 所有 `id` / 选择器不变 (现有 e2e 与后端联调依赖它们);
 * 3. `window.AIRTeam`、`window.AIRLibrary` 与 `window.AIRPaper` 入口保留;
 *    `window.AIR.research` 由 `air-global.ts` 暴露为**只读投影** (不再有可写镜像);
 * 4. 直接的 DOM 访问保留 `any` 边界 (后端 JSON 载荷形状由 `contracts.ts` 描述),
 *    不重新引入 `@ts-nocheck`。
 */
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
  applyTeamEvent,
  getStore as getTeamStore,
  refreshTeamSection,
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
// G19: 唯一状态 (§8.1)。这里只 import **动作与 selector**, 不再 import 任何可写镜像。
import {
  contextTopic as contextTopicOf,
  dispatch,
  getState,
  inputPhase,
  newDraftProjectId,
  subscribe,
  workbenchFrom,
  workbenchQueryTarget,
  type RunStatus,
  type TabName,
} from './state/research-store';
import { researchLabel, researchView } from './current-research';
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

/* ---------------- 唯一状态的只读取用 (G19) ----------------
 * 迁移前这里有一组镜像变量 (`currentProjectId`/`currentProblemId`/`currentThreadId`/
 * `currentSessionId`/`currentContext`/`mode`) 和一个 `currentResearch` 对象, 它们必须与
 * `window.AIR.research` 来回同步。现在这类取值一律走 selector —— 没有第二份可写副本。
 */
function selectionOf() {
  return getState().selection;
}

function currentProjectId(): string {
  return selectionOf().projectId || '';
}

function currentProblemId(): string {
  return selectionOf().problemId || '';
}

function currentThreadId(): string {
  return selectionOf().threadId || '';
}

function currentContext(): string {
  return contextTopicOf(getState());
}

/** 当前查看的对象键 (工作台结论详情)。 */
function openObjectKey(): string {
  return selectionOf().objectKey || '';
}

/** 渲染当前研究上下文徽标 (纯 selector 投影 → 只是 DOM 文本)。 */
function renderResearchBadge() {
  const view = researchView(getState());
  const badge = byId('researchctx');
  if (!badge) return;
  badge.textContent = researchLabel(view);
  badge.className = 'ctx-badge' + (view.status === 'running' ? ' running' : '');
}

const $ = (id: any) => document.getElementById(id);

function setStatus(text: any, cls: any) {
  const el = byId('status');
  if (!el) return;
  el.textContent = text;
  el.className = cls ? ('dot-' + cls) : '';
}

/** 阶段 → 后台运行状态 (与旧 `setMode` 的映射逐字一致)。 */
function phaseStatus(m: any): RunStatus {
  if (m === 'running') return 'running';
  if (m === 'waiting') return 'waiting';
  if (m === 'idle') return 'idle';
  return 'done';
}

function setMode(m: any) {
  // 唯一状态: 阶段写进 store (徽标由订阅者按 selector 重画), 这里只改 DOM
  dispatch({ type: 'selection/status', status: phaseStatus(m) });
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
  renderResearchBadge();
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

/* ---------------- 统一输入的准备 (取代旧的"模式切换") ----------------
 * 统一入口下没有模式可选, 因此这里只做两件**始终成立**的准备:
 * 1. 落实稳定草稿身份 (R2): 附件与检索必须归属到明确的项目/问题;
 * 2. 把资料库列表与已上传附件拉回来 —— 入口始终可见, 打开页面就该看到它们。
 * 工作台仍只在"该项目确实有运行记录"时查询 (草稿身份不查, 避免必然 404)。
 */
function prepareUnifiedIntake() {
  if (inputPhase(getState()) === 'idle') setPlaceholder('reply', defaultPlaceholder());
  // R2: 落实稳定身份 —— 附件必须在有 project_id 之后上传, 否则文件会落到与运行无关的目录
  if (!(val('projid') || '').trim()) {
    const draft = newDraftProjectId();
    setVal('projid', draft);
    dispatch({ type: 'selection/patch', patch: { projectId: draft } });
    // 草稿身份**不是**运行绑定: 必须显式记下来, 否则刷新工作台只能靠"项目 id 恰好
    // 不等于状态里的项目 id"这条间接判据, 判据一旦被后来写入的值填平就会 404
    d.markDraftProject(draft);
  }
  refreshSourceSets();
  void d.refreshAttachments();
  const pid = d.workbenchTargetPid();
  if (pid) {
    dispatch({ type: 'selection/patch', patch: { projectId: pid } });
    refreshWorkbench();
  } else if (byId('workbench')) {
    byId('workbench')!.innerHTML = workbenchMissingHtml(
      (val('projid') || '').trim() || currentProjectId());
  }
}

function copyProjectId() {
  const pid = (val('projid') || '').trim() || currentProjectId();
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

/* ---------------- 科研工作台 (§9.5: 渲染在 views/research-overview.ts) ----------------
 * 工作台**不自己持有工作对象**: 最近一次服务端投影存放在唯一状态的 `entities` 片
 * (`entities/workbench`), 这里只是取数 + 把渲染结果写进 DOM。结论详情导航也因此可以从
 * 状态重渲染, 而不是靠页面局部变量。
 */
function workbenchTargetFor(pid: string): string {
  // 两条判据都必须显式判: "记录里明确说过没有运行记录" 且 "当前就是那个 id"。
  // 只要用户换成别的 id (手填/历史会话), 就允许查一次 —— 否则手动加载工作台被堵死。
  return workbenchQueryTarget(getState(), pid);
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
  const pid = ((byId<HTMLInputElement>('projid') && val('projid')) || currentProjectId() || '').trim();
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
  dispatch({ type: 'selection/patch', patch: { projectId: pid } });
  // F2: 每次读取带加载代号; 旧请求最后返回时不得覆盖新画面。
  // 代号就是唯一状态的 `ui.loadToken` (身份切换/整体重置同样会推进它) ——
  // 不再另设一套 `wbRequestSeq` 序号。
  dispatch({ type: 'ui/loadStarted' });
  const token = getState().ui.loadToken;
  // 工作台状态也走统一客户端; 用 raw 是为了按状态码区分"加载失败"与"内容异常"
  const statePath = researchStateUrl(pid, currentProblemId());
  pageApi().raw(statePath)
    .then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
      if (token !== getState().ui.loadToken) return;  // 已被更新的请求取代
      if (status !== 200) {
        writeWorkbench(workbenchErrorHtml(status, body));
        return;
      }
      if (body.problem_id && body.problem_id !== currentProblemId()) {
        // 服务端已解析出唯一问题: 写回上下文与表单, 保持一致
        dispatch({ type: 'selection/patch', patch: { problemId: body.problem_id } });
        if (byId<HTMLInputElement>('probid')) setVal('probid', body.problem_id);
      }
      // 服务端投影进唯一状态 (只读结论): 工作台不自己持有工作对象
      dispatch({ type: 'entities/workbench', data: body });
      renderWorkbench(body);      // 团队视图: 主控理解与派工在前, 旧工作台 (研究对象与产物) 在后。
      // 状态只有一份 (state/research-store.ts), 这里只负责触发加载与拼装。
      void team().mount(pid, body && body.run_id);
    })
    .catch((e: any) => {
      if (token !== getState().ui.loadToken) return;
      setHtml('workbench', '<div class="empty">读取工作台失败: ' + overviewEsc(String(e)) + '</div>');
    });
}

function writeWorkbench(html: string) {
  const el = byId('workbench');
  if (el) el.innerHTML = html;
}

function renderWorkbench(d: any) {
  const html = renderResearchOverview(d, {
    data: () => workbenchFrom(getState()),
    openClaimId: () => openObjectKey(),
  });
  writeWorkbench(html);
  return html;
}

// 服务端报告多问题时由用户选择 (F0-2: 不擅自取第一个)
function selectProblem(encodedProblemId: any) {
  const pid = decodeURIComponent(encodedProblemId);
  const field = byId<HTMLInputElement>('probid')!;
  if (field) field.value = pid;
  dispatch({ type: 'selection/patch', patch: { problemId: pid } });
  refreshWorkbench();
}

/* F3: 结论详情导航 —— 从结论点开所用模型、原文证据、证明义务与验证记录。
 * 实现已搬到 views/research-overview.ts (纯函数, 有类型与单测), 这里只做开关。 */
function toggleClaimDetail(claimId: any) {
  // 查看中的对象属于唯一状态的 `selection.objectKey` (不再是页面局部变量)
  const next = toggledClaimId(openObjectKey(), claimId);
  dispatch({ type: 'selection/object', objectKey: next });
  const projection = workbenchFrom(getState());
  if (projection) renderWorkbench(projection);
}

function submitWorkbenchFeedback(objectId: any, textOverride: any) {
  const pid = currentProjectId() || ((byId<HTMLInputElement>('projid') && val('projid')) || '').trim();
  const box = byId<HTMLTextAreaElement>('wbfeedback');
  const text = (textOverride !== undefined ? textOverride : (box ? box.value : '')).trim();
  if (!pid) { addMsg('请先关联理论研究项目（启动一次理论研究）', 'msg-error'); return; }
  if (!text) { addMsg('请输入反馈内容', 'msg-error'); return; }
  const picker = byId<HTMLSelectElement>('wbobj');
  const scope = byId<HTMLSelectElement>('wbfeedbackscope')?.value || 'object';
  const target = scope === 'object' && objectId !== undefined && objectId !== null
    ? objectId : (picker ? picker.value : '');
  const selectedTarget = scope === 'object' ? target : '';
  const feedbackId = globalThis.crypto?.randomUUID?.()
    || `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  pageApi().raw(ENDPOINTS.researchFeedback(String(pid)), {
    method: 'POST',
    body: {response: text, object_id: selectedTarget || '',
      problem_id: currentProblemId() || '', scope, feedback_id: feedbackId},
  // 反馈是**变更类**请求: 用 raw 以便如实读出状态码 (4xx 也要照原样报给用户),
  // 同时由客户端统一处理请求头与错误语义。
  }).then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})))
    .then(({status, body}) => {
    if (body.needs_clarification) {
      addMsg('需要澄清: ' + (body.question || body.clarify || '无法确定该意见作用的对象'), 'msg-error');
      // 列出候选对象让用户点选 (F1-5), 而不是只报错
      if ((body.candidates || []).length) d.showFeedbackClarification(body);
    } else if (body.ok) {
      const applied = (body.applied || []).map((a: any) =>
        `${a.owner || '主控'} 已接收需求 ${a.need_id || ''}`).join('; ');
      addMsg(body.duplicate ? '这条调整已提交过，未重复派工' :
        `调整已交主控处理（${body.rounds_used ?? 0} 轮）: ${applied || body.stop_reason || '请查看工作台状态'}`,
      'msg-system');
      if (body.export_error) addMsg('研究调整已保存，但新版交付包导出失败: ' + body.export_error,
        'msg-error');
      if (box) box.value = '';
      refreshWorkbench();
      refreshArtifacts();
    } else {
      addMsg('反馈未施加 (HTTP ' + status + '): ' + JSON.stringify(body), 'msg-error');
    }
  }).catch((e: any) => addMsg('反馈失败: ' + e, 'msg-error'));
}

function forkFromWorkbench() {
  const pid = currentProjectId() || ((byId<HTMLInputElement>('projid') && val('projid')) || '').trim();
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
    const prev = currentContext();
    sel.innerHTML = '<option value="">+ 新主题</option>';
    (d.contexts || []).forEach((c: any) => {
      const opt = document.createElement('option');
      opt.value = c.topic;
      opt.textContent = c.topic + '（' + stageLabelOf(c.stages) + '）';
      sel.appendChild(opt);
    });
    sel.value = prev;
    dispatch({ type: 'ui/context', topic: prev });
  }).catch(() => {});
}

function onContextChange() {
  // 综述上下文只是**资料库/主题偏好**: 它不改变理论研究问题 (F1-3)
  const topic = val('ctx');
  dispatch({ type: 'ui/context', topic });
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
  if (name) dispatch({ type: 'selection/tab', tab: name as TabName });
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
  const binding = {projectId: currentProjectId(),
                   problemId: currentProblemId(),
                   runId: selectionOf().runId};
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
  const identity = {projectId: currentProjectId(), runId: selectionOf().runId};
  // 复用既有清单查询: 与研究问题/运行绑定, 不把别的问题的产物混进来
  const qs = artifactQuery({projectId: identity.projectId,
                            problemId: currentProblemId(),
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
  addMsg: (text, cls) => addMsg(text, cls),
  setMode: (m) => setMode(m),
  setStatus: (text, cls) => setStatus(text, cls),
  clearReply: () => clearReply(),
  replyInput: () => byId<HTMLTextAreaElement>('reply'),
  setPlaceholder: (id, value) => setPlaceholder(id, value),
  refreshSourceSets: () => refreshSourceSets(),
  refreshHistorySelect: () => sessions.refreshHistorySelect(),
  refreshWorkbench: () => refreshWorkbench(),
  refreshArtifacts: () => refreshArtifacts(),
  refreshContexts: () => refreshContexts(),
  switchTab: (name) => switchTab(name),
  connectStream: (tid) => stream.connect(tid),
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
  resetTeam: () => { resetTeamStore(); teamCache = null; },
});

/** 断线补偿返回的服务端状态 → 前端运行状态 (不是连接状态)。 */
function compensateStatus(status: string): RunStatus {
  if (status === 'waiting') return 'waiting';
  if (status === 'running') return 'running';
  if (status === 'done') return 'done';
  return 'idle';
}

/** 事件流 (计划书 §9.5 `events/session-stream.ts`)。 */
const stream: SessionStream = createSessionStream({
  handleEvent: (ev) => handleEvent(ev),
  currentThreadId: () => currentThreadId(),
  getCursor: (threadId) => getState().transport.cursorBySession[threadId] || 0,
  recordCursor: (threadId, seq) => dispatch({ type: 'transport/cursor',
    sessionId: threadId, seq }),
  onGap: () => {
    dispatch({ type: 'transport/needsResync', needs: true });
    refreshWorkbench();
    refreshArtifacts();
  },
  onCompensate: (status) => dispatch({ type: 'selection/status', status: compensateStatus(status) }),
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
      if (ev.name === 'research_team' && ev.team_event) {
        const source = ev.team_event;
        const type = String(source.event || source.node || '');
        const visible = new Set([
          'brief_ready', 'plan_ready', 'supervisor_decision', 'task_started',
          'task_result', 'task_finished', 'commit_failed', 'change_proposed',
          'review_issue', 'run_finished',
        ]);
        if (visible.has(type)) {
          const teamCursor = `team:${String(ev._session_id || currentThreadId())}`;
          const effect = applyTeamEvent({
            seq: (getTeamStore().transport.cursorBySession[teamCursor] || 0) + 1,
            sessionId: teamCursor,
            runId: String(ev.run_id || selectionOf().runId),
            type: type === 'commit_failed' ? 'task_blocked' : type,
            taskId: String(source.task_id || ''),
            agent: String(source.agent || ''),
            payload: type === 'commit_failed'
              ? { ...source, status: 'failed', failure_reason: source.reason }
              : source,
          });
          if (effect.kind === 'apply') {
            refreshTeamSection();
            if (effect.refresh.includes('overview') || effect.refresh.includes('sources'))
              refreshWorkbench();
          } else if (effect.kind === 'resync' || effect.kind === 'incompatible') {
            void team().mount(currentProjectId(), selectionOf().runId);
          }
        }
      } else {
        refreshWorkbench();
      }
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
  // F2: 记录暂停点 ID, 提交响应时回传 (服务端据此幂等去重)。它属于唯一状态的 `ui` 片。
  dispatch({ type: 'ui/interrupt', interruptId: (p && p.interrupt_id) || '' });
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
  // 唯一状态 → 视图: 状态一变就按 selector 重画上下文徽标 (计划书 §8.3 的
  // "事件经 reducer 更新 store 后渲染", 不再是回调各自改全局状态)。
  subscribe(() => renderResearchBadge());
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
  renderResearchBadge();
  // 团队视图: 页面打开即显示团队角色与**真实可用能力** (来自 /api/team/roles)。
  // 迁移期通过 window.AIRTeam 暴露入口, 让浏览器用例不必点击进入某个运行就能验证它。
  exposeTeamApi();
  void team().mount('', '');
}

/* 迁移期入口 (`window.AIRTeam` / `window.AIRLibrary` / `window.AIRPaper`)。
 * 团队视图的读接口在这里暴露, 供浏览器验收与调试使用; 它**只读**, 不启动任何任务。
 * 注意: 它们读的是**唯一状态**, 不再是 `window.AIR.research` 的第二份副本。 */
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
