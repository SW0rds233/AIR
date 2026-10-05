/**
 * 统一输入与附件接入控制器 (合并计划 §9.5 `features/intake/controller.ts` / §8.1)。
 *
 * 这里集中"用户到底研究什么"的所有入口:
 *
 * - `start()` —— **唯一**启动入口 (统一入口: 用户不再选择"综述/理论"模式);
 * - `sendResponse()` / `respondWith()` —— 暂停点答复 (幂等键 `interrupt_id` 由状态持有);
 * - 附件 (`uploads.ts` 的协作面): 上传、列出、移除、归属自检、稳定草稿身份 (R2/R3);
 * - 续研与问题冲突 (同一 problem_id 上已有另一个请求) 的显式选择。
 *
 * 统一入口的契约 (本轮迁移):
 *
 * - 启动载荷**不再包含 `mode`**; 服务端按统一研究的默认引擎启动;
 * - 项目/问题/预算/资料授权/附件等字段**始终**随请求发出 (不再"只有某种模式才带");
 * - 项目与问题在启动前落实稳定身份 (草稿 id), 附件据此校验归属 (R2/R3)。
 *
 * 仍然:
 *
 * - 只使用 `data-action` (没有内联事件处理器), 因此 CSP 约束不变;
 * - 所有 `id` / 表单字段名与请求载荷字段不变;
 * - 唯一状态是 `state/research-store.ts`: 本模块**不存**项目/问题/线程/暂停点/
 *   候选路线的副本 —— 读走 selector, 写走 `dispatch(action)` (G19: 不再经
 *   `applyResearch(patch)` 这类兼容写入口)。
 */

import { byId, checked, scrollToBottom, setDisabled, setHtml, setVal, val } from '../../dom';
import {
  problemAttachmentIds, summarizeResults, validateFiles,
  type AttachmentView, type UploadKind,
} from '../../uploads';
import {
  attachmentBindingHtml, attachmentListHtml,
} from '../../views/research-input';
import { buildCandidateCard, buildClarificationCard, buildFeedbackPromptCard }
  from '../../views/interrupt-cards';
import { wbEsc } from '../../views/research-workbench';
import {
  contextTopic,
  dispatch,
  getState,
  inputPhase,
  newDraftProjectId,
  workbenchQueryTarget,
} from '../../state/research-store';
// §9.5: 取数入口统一在 api/ 层 (URL/错误/取消/重试/幂等语义只在那里实现)
import {
  ENDPOINTS,
  client as defaultApi,
  endpointMethod,
  type ResearchClient,
} from '../../api/research-client';

export interface IntakeDeps {
  // 页面出口 (DOM)
  addMsg(text: string, cls: string): void;
  setMode(mode: string): void;
  setStatus(text: string, cls: string): void;
  clearReply(): void;
  replyInput(): HTMLTextAreaElement | null;
  setPlaceholder(id: string, value: string): void;

  // 其它模块
  refreshSourceSets(): void;
  refreshHistorySelect(): void;
  refreshWorkbench(): void;
  refreshArtifacts(): void;
  refreshContexts(): void;
  switchTab(name: string): void;
  connectStream(threadId: string): void;
  /** 提交明确反馈 (视图侧的澄清卡片回调)。 */
  submitFeedback(objectId: string, text: string): void;

  /**
   * 生成稳定草稿身份 (R2)。默认取自唯一状态模块 (`newDraftProjectId`),
   * 用例可注入固定值 —— 它只是纯 id 生成, 不持有状态。
   */
  ensureDraftProjectId?(): string;

  /** HTTP 出口 (§9.5): URL/错误/取消/重试都在 `api/` 层, 控制器不自己拼。 */
  api?: ResearchClient;
}

export interface IntakeController {
  // 输入 / 会话动作
  onSend(): void;
  start(requestText: string): void;
  startWithResume(requestText: string): void;
  startNewProblem(requestText: string): void;
  resumeSameProblem(detail: any): void;
  sendResponse(): void;
  respondWith(response: string): void;

  // 暂停点视图 (DOM 出口仍在页面, 渲染在 views/interrupt-cards.ts)
  showCandidateChoice(p: any): void;
  showFeedbackPrompt(p: any): void;
  showFeedbackClarification(body: any): void;
  showProblemConflict(detail: any): void;

  // 附件与草稿身份 (R2/R3)
  pendingAttachmentIds(): string[];
  checkAttachmentBinding(currentPid: string): boolean;
  markDraftProject(projectId: string): void;
  workbenchTargetPid(): string;
  uploadAttachments(): Promise<void>;
  refreshAttachments(): Promise<void>;
  renderAttachments(): void;
  deleteAttachment(attachmentId: string): Promise<void>;
  bindUploads(): void;
}

function splitList(s: any) {
  return (s || '').split(/[,，、;；]+/).map((x: any) => x.trim()).filter(Boolean);
}

/** 唯一状态里的只读取值 (本模块不另存副本)。 */
function currentProjectId(): string {
  return getState().selection.projectId || '';
}

function currentProblemId(): string {
  return getState().selection.problemId || '';
}

function currentThreadId(): string {
  return getState().selection.threadId || '';
}

/** 启动/续研同一个端点: 只差载荷里的 `resume` 字段 (§9.1 "续研与启动语义分开")。
 *
 * 方法取自端点方法表 (`endpointMethod`), 不在调用点另写一份 —— 契约表已经声明过
 * 这个端点用 POST, 两处各写一遍就是等着漂移。
 */
function startSession(api: ResearchClient, payload: Record<string, unknown>) {
  return api.raw(ENDPOINTS.sessions, {
    method: endpointMethod('sessions') as 'POST', body: payload,
  }).then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})));
}

export function createIntakeController(deps: IntakeDeps): IntakeController {
  const api = deps.api ?? defaultApi();
  let attachments: AttachmentView[] = [];
  let pendingAttachments: string[] = [];   // 仅"补充问题说明"用途的附件 id
  /** 已上传的问题附件属于哪个项目 (附件按项目归属校验, 启动时项目 id 变了就不再生效)。 */
  let attachmentProjectId = '';

  function uploadMessage(text: string, cls = 'msg-system') {
    deps.addMsg('[附件] ' + text, cls);
  }

  function attachmentTopic(): string {
    // 文献入库的主题: 优先显式资料库, 否则用当前主题名 (与按主题名匹配的规则一致)
    return (val('sourceset') || val('topic') || contextTopic(getState()) || '').trim();
  }

  function buildAttachmentUrl(kind: UploadKind, projectId: string, topic: string): string {
    const params = new URLSearchParams({ kind });
    if (projectId) params.set('project_id', projectId);
    if (topic) params.set('topic', topic);
    const query = params.toString();
    return ENDPOINTS.uploads + (query ? '?' + query : '');
  }

  function renderAttachments() {
    setHtml('attachlist', attachmentListHtml(attachments));
    const problemIds = problemAttachmentIds(attachments);
    pendingAttachments = problemIds;
    // 记住这些附件归属的项目: 启动前据此自检"项目 ID 有没有被改过"
    if (problemIds.length) {
      attachmentProjectId = (currentProjectId() || val('projid') || '').trim();
    } else {
      attachmentProjectId = '';
    }
    setHtml('filebinding', attachmentBindingHtml(attachments));
  }

  async function refreshAttachments() {
    // R1: 问题附件看项目、文献附件看主题, 必须分别查询再合并 ——
    // 两个字段一起发给后端会被当成交集, 两个输入都有值时列表恒为空。
    const projectId = (currentProjectId() || val('projid') || '').trim();
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
        const body = await api.json<Record<string, any>>(
          url.replace(/^https?:\/\/[^/]+/, ''));
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
    form.append('project_id', currentProjectId() || val('projid'));
    form.append('problem_id', currentProblemId() || val('probid'));
    form.append('topic', topic);
    list.forEach((file) => form.append('files', file as unknown as File, file.name));
    setDisabled('btn-upload', true);
    try {
      // 上传是**变更类**且带表单: 不自动重试 (重试可能把同一批文件传两遍),
      // multipart 的边界由浏览器设置, 因此用 raw 读状态码、由客户端处理其余语义。
      const resp = await api.raw(ENDPOINTS.uploads, { method: 'POST', form });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        uploadMessage(`上传失败 (HTTP ${resp.status}): ${body.detail || ''}`, 'msg-error');
        return;
      }
      uploadMessage(summarizeResults(body));
      if (kind === 'literature') deps.refreshSourceSets();
      const input = byId<HTMLInputElement>('attachfiles');
      if (input) input.value = '';
      await refreshAttachments();
    } catch (err) {
      uploadMessage('上传请求失败: ' + String(err), 'msg-error');
    } finally {
      setDisabled('btn-upload', false);
    }
  }

  async function deleteAttachment(attachmentId: string) {
    try {
      const resp = await api.raw(ENDPOINTS.upload(attachmentId), { method: 'DELETE' });
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
    const on = (id: string, event: string, fn: () => void) => {
      const el = byId(id);
      if (el) el.addEventListener(event, fn);
    };
    on('btn-upload', 'click', () => { void uploadAttachments(); });
    on('attachfiles', 'change', () => { void uploadAttachments(); });
    void refreshAttachments();
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

  function markDraftProject(projectId: string) {
    // 草稿身份属于唯一状态 (`ui` 片, §8.1 的"选择/草稿")
    dispatch({ type: 'ui/draftProject', projectId: String(projectId || '') });
  }

  function workbenchTargetPid(): string {
    const pid = (val('projid') || getState().ui.boundProjectId || '').trim();
    if (!pid) return '';
    return workbenchQueryTarget(getState(), pid);
  }

  // ------------------------------------------------------------------
  // 启动 / 续研
  // ------------------------------------------------------------------
  function start(requestText: any) {
    const request = (requestText || '').trim();
    // 统一入口: 不再按模式挑 topic。表单里的精确主题优先, 否则用资料库/上下文主题,
    // 最后才退回输入本身 —— 任何一条都不依赖"用户选了哪种模式"。
    const topic = val('topic').trim() || contextTopic(getState()) || request;
    if (!request && !topic) { alert('请描述研究主题，或用一句话说明你想研究的内容'); return; }

    deps.setMode('running');
    deps.setStatus('解析研究意图…', 'running');
    deps.addMsg(request || topic, 'msg-user');
    deps.clearReply();

    // R2: 启动前落实稳定身份 —— 附件与检索都必须归属到某个明确的项目/问题,
    // 否则文件会落到与运行无关的默认目录。
    const pid = (val('projid') || '').trim()
      || (deps.ensureDraftProjectId ? deps.ensureDraftProjectId() : newDraftProjectId());
    setVal('projid', pid);
    const probid = (val('probid') || '').trim() || 'problem';
    setVal('probid', probid);
    dispatch({ type: 'selection/patch', patch: { projectId: pid, problemId: probid } });
    // 附件按项目归属校验: 项目 ID 与上传时不同就如实拦下, 不静默丢附件 (R3)
    if (!checkAttachmentBinding(pid)) return;

    // 统一载荷: 不再发送 `mode` (服务端决定引擎); 其余字段始终带上。
    const payload: Record<string, any> = {
      request: request,
      topic: topic,
      keywords: splitList(val('keywords')),
      subtopics: splitList(val('subtopics')),
      max_revisions: parseInt(val('maxrev')) || 3,
      skip_retrieval: checked('skipret'),
      project_id: pid,
      problem_id: probid,
      max_actions: parseInt(val('maxactions')) || 40,
      max_tool_calls: parseInt(val('maxtools')) || 60,
      // R0: 资料源绑定随请求发出; 服务端在确认研究前校验可用性
      source_set_id: (byId<HTMLSelectElement>('sourceset') && val('sourceset')) || '',
      // 附件: "补充问题说明"的附件文本由服务端并入问题陈述 (带 sha256 溯源)
      attachment_ids: pendingAttachments,
      // R5: 资料授权策略必须显式随请求发出 (后端校验, 非法策略拒绝而非静默回退)
      source_policy: val('sourcepolicy', 'both') || 'both',
    };

    startSession(api, payload).then(({status, body: data}) => {
      if (data.thread_id) {
        // 启动成功 = 新 run: 身份**整体切换** (清掉上一个 run 的对象详情, 并推进
        // 加载代号让在途的旧回包作废)
        dispatch({
          type: 'selection/open',
          selection: {
            threadId: String(data.thread_id),
            sessionId: data.session_id || '',
            // 服务端返回的引擎标识只用于显示 (不再作为界面分支依据)
            mode: data.mode || '',
            runId: data.run_id || '',
            projectId: data.project_id || pid,
            problemId: data.problem_id || probid,
          },
        });
        // 启动成功 = 该项目确实有运行记录: 从这里起工作台才允许查询它
        dispatch({ type: 'ui/boundProject', projectId: data.project_id || '' });
        if (data.project_id) setVal('projid', data.project_id);
        if (data.problem_id && byId<HTMLInputElement>('probid')) setVal('probid', data.problem_id);
        deps.connectStream(data.thread_id);
        deps.refreshHistorySelect();
        deps.switchTab('workbench');
        deps.refreshWorkbench();
      } else if (status === 409 && data.detail && data.detail.options) {
        // F0-4 / F1: 同一问题已在研究另一个请求 → 让用户明确选择, 不静默改题
        showProblemConflict(data.detail);
        deps.setMode('idle');
      } else {
        deps.addMsg('启动失败 (HTTP ' + status + '): ' +
                    JSON.stringify(data.detail || data), 'msg-error');
        deps.setMode('idle');
        deps.refreshContexts();
        deps.refreshArtifacts();
      }
    }).catch((e: any) => {
      deps.addMsg('启动失败: ' + e, 'msg-error');
      deps.setMode('idle');
    });
  }

  // 同一 problem_id 上已有不同研究请求: 列出"继续原问题 / 另建新问题"两个明确选项
  function showProblemConflict(detail: any) {
    const card = document.createElement('div');
    card.className = 'interrupt-card';
    card.innerHTML = '<div class="it-title">该问题 ID 已在研究另一个请求</div>' +
      '<div class="it-hint">问题 ' + wbEsc(detail.problem_id || '') + ' 已在研究：' +
      wbEsc(detail.existing_request || '') + '<br>本次输入：' +
      wbEsc(detail.requested || '') + '</div>';
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
    const pid = detail.problem_id || currentProblemId();
    if (byId<HTMLInputElement>('probid')) setVal('probid', pid);
    dispatch({ type: 'selection/patch', patch: { problemId: pid } });
    deps.addMsg('继续原问题 ' + pid + '（不重新生成规格）', 'msg-system');
    startWithResume(val('reply') || detail.requested || '');
  }

  function startNewProblem(requestText: any) {
    const next = 'p' + Math.random().toString(16).slice(2, 7);
    if (byId<HTMLInputElement>('probid')) setVal('probid', next);
    dispatch({ type: 'selection/patch', patch: { problemId: next } });
    deps.addMsg('作为新问题 ' + next + ' 开始研究', 'msg-system');
    start(requestText);
  }

  // 显式续研: 与 start 分开语义 (计划书 §9.1), 复用已落盘规格
  function startWithResume(requestText: any) {
    const request = (requestText || '').trim();
    if (!request) { alert('请输入研究请求'); return; }
    deps.setMode('running');
    deps.setStatus('恢复同一问题…', 'running');
    const pid = (val('projid') || '').trim() || getState().ui.boundProjectId;
    const probid = (val('probid') || '').trim() || 'problem';
    const payload: Record<string, any> = {
      request: request,
      topic: val('topic').trim() || request,
      keywords: splitList(val('keywords')),
      subtopics: splitList(val('subtopics')),
      project_id: pid,
      problem_id: probid,
      max_actions: parseInt(val('maxactions')) || 40,
      max_tool_calls: parseInt(val('maxtools')) || 60,
      resume: true,
    };
    startSession(api, payload).then(({status, body}) => {
        if (!body.thread_id) {
          deps.addMsg('续研失败 (HTTP ' + status + '): ' +
                      JSON.stringify(body.detail || body), 'msg-error');
          deps.setMode('idle');
          return;
        }
        dispatch({
          type: 'selection/open',
          selection: {
            threadId: String(body.thread_id),
            sessionId: body.session_id || '',
            mode: body.mode || '',
            projectId: body.project_id || pid,
            problemId: body.problem_id || probid,
          },
        });
        deps.connectStream(body.thread_id);
        deps.refreshHistorySelect();
        deps.switchTab('workbench');
        deps.refreshWorkbench();
      }).catch((e: any) => { deps.addMsg('续研失败: ' + e, 'msg-error'); deps.setMode('idle'); });
  }

  // ------------------------------------------------------------------
  // 暂停点答复
  // ------------------------------------------------------------------
  function respondWith(response: any) {
    const tid = currentThreadId();
    if (!tid) return;
    const input = deps.replyInput();
    const previous = input ? input.value : '';
    const interruptId = getState().ui.pendingInterruptId;
    // F2: 先确认服务端接受, 再改变界面状态; 失败时保留输入以便重试
    deps.setStatus('发送中…', 'running');
    // respond 带 interrupt_id (幂等键): 只有它能安全重试 (同一回答不会被施加两遍)
    api.raw(ENDPOINTS.sessionRespond(tid), {
      method: 'POST',
      body: {response: response, interrupt_id: interruptId},
      idempotencyKey: interruptId,
    }).then((r: Response) => r.json().then((d: any) => ({status: r.status, body: d})))
      .then(({status, body}) => {
        if (status >= 200 && status < 300 && body && body.ok !== false) {
          deps.clearReply();
          dispatch({ type: 'ui/interrupt', interruptId: '' });
          deps.setMode('running');
          deps.setStatus('运行中…', 'running');
          return;
        }
        if (input && !input.value) input.value = previous;
        const why = (body && (body.detail || body.reason)) || JSON.stringify(body);
        deps.addMsg('发送失败 (HTTP ' + status + '): ' + why + '（输入已保留，可重试）', 'msg-error');
        deps.setStatus('发送失败', 'error');
        deps.setMode('waiting');
      })
      .catch((e: any) => {
        if (input && !input.value) input.value = previous;
        deps.addMsg('发送失败: ' + e + '（输入已保留，可重试）', 'msg-error');
        deps.setStatus('发送失败', 'error');
        deps.setMode('waiting');
      });
  }

  function sendResponse() {
    if (!currentThreadId() || inputPhase(getState()) !== 'waiting') return;
    const response = val('reply');
    deps.addMsg(response || '(跳过)', 'msg-user');
    respondWith(response);
  }

  function onSend() {
    if (inputPhase(getState()) === 'waiting') { sendResponse(); }
    else if (inputPhase(getState()) === 'idle') { start(val('reply')); }
  }

  // ------------------------------------------------------------------
  // 暂停点视图 (DOM 出口)
  // ------------------------------------------------------------------
  function showCandidateChoice(p: any) {
    const card = buildCandidateCard(p, {
      currentSelection: () => getState().ui.candidateId,
      onRespond: (response: string) => respondWith(response),
      onChosen: (idx: number, candId: string, statement: string) => {
        dispatch({ type: 'ui/candidate', candidateId: candId });
        deps.addMsg('已选择候选路线 ' + (idx + 1) + (candId ? ' (' + candId + ')' : '') + ': ' +
          statement, 'msg-user');
      },
    });
    byId('log')!.appendChild(card);
    scrollToBottom('log');
    setDisabled('reply', false);
    deps.setPlaceholder('reply', '也可以输入候选 ID，或序号（0 开始）后回车');
    setDisabled('btn-send', false);
    deps.clearReply();
  }

  function showFeedbackPrompt(p: any) {
    byId('log')!.appendChild(buildFeedbackPromptCard(p));
    scrollToBottom('log');
    setDisabled('reply', false);
    deps.setPlaceholder('reply', '输入研究意见（回车发送，空回车跳过）');
    setDisabled('btn-send', false);
    deps.clearReply();
  }

  function showFeedbackClarification(body: any) {
    byId('log')!.appendChild(buildClarificationCard(body, {
      onApply: (objectId: string, text: string) => deps.submitFeedback(objectId, text),
    }));
    scrollToBottom('log');
  }

  return {
    onSend,
    start,
    startWithResume,
    startNewProblem,
    resumeSameProblem,
    sendResponse,
    respondWith,
    showCandidateChoice,
    showFeedbackPrompt,
    showFeedbackClarification,
    showProblemConflict,
    pendingAttachmentIds: () => pendingAttachments,
    checkAttachmentBinding,
    markDraftProject,
    workbenchTargetPid,
    uploadAttachments,
    refreshAttachments,
    renderAttachments,
    deleteAttachment,
    bindUploads,
  };
}
