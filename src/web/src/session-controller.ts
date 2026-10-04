/**
 * 会话操作单一入口 (合并计划 §9.5 `session-controller.ts`)。
 *
 * 归属这里的操作都是"会话级生命周期": 新建 / 查看历史 / 继续 / 停止 / 结束 / 删除。
 * 迁移前它们散在 `app.ts` 各处, 于是同一件事有几套写法:
 *
 * - "查看"会顺手改模式或项目字段 (切到另一个会话后模式泄漏到下一个会话);
 * - "继续"与"新建"各自处理在途请求与连接, 旧请求的最后回包可能污染新会话;
 * - 切换会话时团队状态没有整体重置, 界面显示上一个 run 的任务。
 *
 * 现在统一为: **旧请求先作废 + 状态整体切换 + 明确的查看/继续/停止语义**。
 * 统一入口 (本轮迁移) 之后这里不再有"运行模式"要恢复或回放 —— 模式选择器已删除。仍然:
 *
 * - 唯一状态是 `window.AIR.research` (经 `deps.applyResearch`), 这里不存第二份;
 * - 行为与 `id` / 选择器零变化 (历史下拉框、回看抽屉、日志容器都保持原样);
 * - 渲染判定在 `views/project-navigation.ts` (纯函数、可单测), 这里只做装配;
 * - 不新增内联事件处理器 (CSP 不变)。
 */

import { byId, setProp, setText, val } from './dom';
import {
  conversationEntries,
  historyDetailTitle,
  historyListHtml,
  historyOption,
  type HistoryLogEntry,
} from './views/project-navigation';
import { artifactNameFromPath, basename } from './views/artifacts';
import {
  ENDPOINTS,
  client as defaultApi,
  type ResearchClient,
} from './api/research-client';

export interface SessionDeps {
  // 连接 (SSE) 启停: 重连策略归 events/ 层
  connectStream(threadId: string): void;
  closeStream(): void;

  // 页面出口
  addMsg(text: string, cls: string): void;
  setMode(mode: string): void;
  setStatus(text: string, cls: string): void;
  switchTab(name: string): void;
  refreshWorkbench(): void;
  refreshArtifacts(): void;
  refreshContexts(): void;
  refreshHistorySelect(): void;
  loadArtifact(name: string): void;

  // 唯一研究状态 (只经这里读写)
  applyResearch(patch: Record<string, any>): void;
  threadId(): string;
  sessionId(): string;
  /** 作废在途的工作台请求 (新会话/新切换都不得被旧回包覆盖)。 */
  workbenchSeqBump(): void;

  // 团队状态整体重置 (会话级)
  resetTeam(): void;

  /** HTTP 出口 (§9.5): URL/错误/取消/重试都在 `api/` 层, 控制器不自己拼。 */
  api?: ResearchClient;
}

export interface SessionController {
  newSession(): void;
  onHistoryChange(): void;
  refreshHistorySelect(): void;
  switchToConversation(sid: string): void;
  resumeSession(sid: string): void;
  resumeConversation(sid: string): void;
  stopSession(): void;
  finishRun(): void;
  onDone(state: any): void;
  onStopped(): void;
  deleteSession(): void;
  closeHistory(): void;
  historyBack(): void;
  showHistoryList(): void;
  viewConversation(sid: string): void;
}

/** 只允许安全字符的会话 id 才能进属性选择器; 其它按"找不到"处理。 */
function selectorValue(value: string): string {
  return /^[A-Za-z0-9_.:-]+$/.test(value) ? value : '';
}

export function createSessionController(deps: SessionDeps): SessionController {
  const api = deps.api ?? defaultApi();

  // ------------------------------------------------------------------
  // 新建 / 切换
  // ------------------------------------------------------------------
  function newSession() {
    // F1-2: 新会话必须清空旧项目/问题与工作台绑定, 否则新研究会沿用上一个问题
    deps.applyResearch({threadId: '', sessionId: '', projectId: '', problemId: '', runId: ''});
    deps.workbenchSeqBump();           // 作废在途的工作台请求
    deps.closeStream();
    // 团队状态同样是**会话级**: 新会话必须整体重置, 否则会显示上一个 run 的任务
    deps.resetTeam();
    const staleTeam = byId('team-section');
    if (staleTeam) staleTeam.remove();
    const log = byId('log');
    if (log) log.innerHTML = '';
    const projid = byId<HTMLInputElement>('projid');
    if (projid) projid.value = '';
    const probid = byId<HTMLInputElement>('probid');
    if (probid) probid.value = 'problem';
    const feedback = byId<HTMLInputElement>('wbfeedback');
    if (feedback) feedback.value = '';
    const snapid = byId<HTMLInputElement>('wbsnapid');
    if (snapid) snapid.value = '';
    setText('runhint', '');
    const workbench = byId('workbench');
    if (workbench) {
      workbench.innerHTML = '<div class="empty">新会话：尚未关联理论研究项目。' +
        '启动一次理论研究，或在高级选项里填写项目 ID 后点「加载工作台」。</div>';
    }
    deps.setMode('idle');
    deps.setStatus('就绪', '');
    deps.addMsg('欢迎使用 AIR 智能体研究系统。', 'msg-agent');
    deps.addMsg('在下方输入研究主题或用一句话描述研究内容，回车启动。' +
      '系统自行判断研究类型与交付形态；项目、资料与附件入口始终可用。' +
      '顶部「会话」下拉框可切换历史会话继续工作。', 'msg-agent');
  }

  function onHistoryChange() {
    const sid = val('hist');
    if (!sid) { newSession(); return; }
    switchToConversation(sid);
  }

  function switchToConversation(sid: any) {
    // F1-1: 切换历史会话时**整体**加载该会话的上下文 (项目/问题/引擎标识),
    // 不能只恢复线程 —— 否则从上一个会话切过来时项目与问题字段会沿用旧值。
    // 统一入口: 不再有"模式"需要恢复或回放 (模式选择器已删除)。
    api.json(ENDPOINTS.conversation(String(sid))).then((rec: any) => {
      const req = rec.request || {};
      deps.applyResearch({
        sessionId: sid,
        // 服务端记录的引擎标识只用于显示
        mode: String(req.mode || ''),
        projectId: req.project_id || '',
        problemId: req.problem_id || '',
        runId: req.run_id || rec.run_id || '',
      });
      // 历史会话带的是**真实**项目 id (不是草稿): 允许工作台查询它。
      // 字段必须在工作台查询之前写上, 否则 `workbenchTargetPid()` 会按草稿/空身份拒绝查询。
      fieldValue('projid', req.project_id || '');
      fieldValue('probid', req.problem_id || 'problem');
      renderHistoryToLog(rec);
      deps.switchTab('workbench');
      if (req.project_id) deps.refreshWorkbench();
      if ((rec.status || '') !== 'done') {
        resumeSession(sid);
      } else {
        deps.applyResearch({threadId: ''});
        deps.setMode('idle');
        deps.setStatus('已完成', 'done');
      }
    }).catch(() => deps.addMsg('加载历史会话失败', 'msg-error'));
  }

  function fieldValue(id: string, value: string) {
    const el = byId<HTMLInputElement | HTMLSelectElement>(id);
    if (el) el.value = value;
  }

  /** 历史会话回放到 `#log` (入口行 + 消息/暂停点卡片)。 */
  function renderHistoryToLog(rec: any) {
    const log = byId('log');
    if (!log) return;
    log.innerHTML = '';
    deps.addMsg('[历史会话] ' + (rec.topic || rec.session_id || ''), 'msg-agent');
    const html = conversationEntries(rec, true).map((entry: HistoryLogEntry) => entry.html).join('');
    if (html) log.insertAdjacentHTML('beforeend', html);
    log.scrollTop = log.scrollHeight;
  }

  function resumeSession(sid: any) {
    api.json(ENDPOINTS.sessionResume(String(sid)), {method: 'POST'})
      .then((d: any) => {
        if (d.thread_id) {
          deps.applyResearch({threadId: d.thread_id});
          deps.setMode('running');
          deps.setStatus('恢复会话…', 'running');
          deps.connectStream(d.thread_id);
        } else {
          deps.addMsg('恢复失败: ' + JSON.stringify(d), 'msg-error');
        }
      }).catch((e: any) => deps.addMsg('恢复失败: ' + e, 'msg-error'));
  }

  function resumeConversation(sid: any) {
    api.json(ENDPOINTS.sessionResume(String(sid)), {method: 'POST'})
      .then((d: any) => {
        if (d.thread_id) {
          closeHistory();
          deps.applyResearch({threadId: d.thread_id});
          const log = byId('log');
          if (log) log.innerHTML = '';
          deps.setMode('running');
          deps.setStatus('恢复会话…', 'running');
          deps.connectStream(d.thread_id);
        } else {
          alert('恢复失败: ' + JSON.stringify(d));
        }
      }).catch((e: any) => alert('恢复失败: ' + e));
  }

  // ------------------------------------------------------------------
  // 历史下拉框 / 回看抽屉
  // ------------------------------------------------------------------
  function refreshHistorySelect() {
    api.json(ENDPOINTS.conversations, {params: {_: Date.now()}})
      .then((d: any) => {
        const sel = byId<HTMLSelectElement>('hist');
        if (!sel) return;
        const prev = deps.sessionId();
        sel.innerHTML = '<option value="">+ 历史会话</option>';
        (d.conversations || []).forEach((c: any) => {
          const view = historyOption(c);
          const opt = document.createElement('option');
          opt.value = view.value;
          opt.textContent = view.label;
          sel.appendChild(opt);
        });
        sel.value = prev;
      }).catch(() => {});
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
    const detail = byId('history-detail');
    if (detail) detail.innerHTML = '';
    api.json(ENDPOINTS.conversations, {params: {_: Date.now()}}).then((d: any) => {
      renderHistoryList(d.conversations || []);
    }).catch(() => {
      const el = byId('history-list');
      if (el) el.innerHTML = '<div class="empty">加载失败</div>';
    });
  }

  function renderHistoryList(items: any[]) {
    const el = byId('history-list');
    if (!el) return;
    el.innerHTML = historyListHtml(items);
    if (!items.length) return;
    items.forEach((c: any) => {
      const sid = String(c.session_id || '');
      const item = el.querySelector<HTMLElement>(
        '.history-item[data-sid="' + selectorValue(sid) + '"]');
      if (!item) return;
      item.onclick = () => viewConversation(sid);
      const btn = item.querySelector<HTMLElement>('.hist-resume');
      if (btn) {
        btn.onclick = (event: Event) => { event.stopPropagation(); resumeConversation(sid); };
      }
    });
  }

  function viewConversation(sid: any) {
    api.json(ENDPOINTS.conversation(String(sid))).then((rec: any) => {
      renderHistory(rec);
    }).catch(() => {
      const el = byId('history-list');
      if (el) el.innerHTML = '<div class="empty">加载失败</div>';
    });
  }

  function renderHistory(rec: any) {
    setText('history-title', historyDetailTitle(rec));
    setProp('btn-history-back', 'display', 'inline-block');
    setProp('history-list', 'display', 'none');
    const detail = byId('history-detail');
    if (!detail) return;
    detail.style.display = 'block';
    detail.innerHTML = '';
    const html = conversationEntries(rec, false).map((entry: HistoryLogEntry) => entry.html).join('');
    if (html) detail.insertAdjacentHTML('beforeend', html);
    detail.scrollTop = detail.scrollHeight;
  }

  // ------------------------------------------------------------------
  // 结束 / 停止 / 删除
  // ------------------------------------------------------------------
  function onDone(state: any) {
    deps.setStatus('完成', 'done');
    if (state && state.error) {
      deps.addMsg('错误: ' + state.error, 'msg-error');
    } else if (state) {
      // 判据是"有没有交付元数据", 不是"用户选了哪种模式" (模式选择器已删除):
      // 交付等级/交付包存在就报交付结论, 否则退回运行摘要。
      const hasDelivery = Boolean(state.delivery_level || state.package_dir
        || state.snapshot_id || state.gate_passed !== undefined);
      if (hasDelivery) {
        const lines = ['研究完成'];
        if (state.delivery_level) lines.push('交付级别: ' + state.delivery_level);
        if (state.gate_passed !== null && state.gate_passed !== undefined) {
          lines.push('交付门槛: ' + (state.gate_passed ? '通过' : '未通过'));
        }
        if (state.snapshot_id) lines.push('快照: ' + state.snapshot_id);
        if (state.package_dir) lines.push('交付包: ' + state.package_dir);
        deps.addMsg(lines.join('\n'), 'msg-done');
        if (state.project_id) deps.applyResearch({projectId: state.project_id});
        deps.switchTab('workbench');
        deps.refreshWorkbench();
      } else {
        const parts = ['完成'];
        if (state.literature_notes_path) parts.push('文献综述总结: ' + basename(state.literature_notes_path));
        if (state.figure_count) parts.push('已生成图表: ' + state.figure_count + ' 张');
        if (state.review_score && state.review_score !== 'N/A') {
          parts.push('审稿评分: ' + state.review_score + '/50，修改轮次: ' + state.revision_count);
        }
        if (state.draft_path) parts.push('初稿: ' + basename(state.draft_path));
        if (state.paper_tex_path) parts.push('LaTeX: ' + basename(state.paper_tex_path));
        deps.addMsg(parts.join('\n'), 'msg-done');
        if (state.literature_notes_path) { deps.loadArtifact(artifactNameFromPath(state.literature_notes_path)); }
        else if (state.draft_path) { deps.loadArtifact(artifactNameFromPath(state.draft_path)); }
      }
    } else {
      deps.addMsg('完成', 'msg-done');
    }
    finishRun();
    deps.refreshArtifacts();
  }

  function onStopped() {
    deps.setStatus('已停止', 'done');
    deps.addMsg('已停止（会话状态已保存到检查点）。', 'msg-agent');
    finishRun();
  }

  function finishRun() {
    deps.setMode('idle');
    // 结束的只是**本次运行**的线程; 项目与问题仍是当前研究上下文, 工作台继续指向它
    deps.applyResearch({threadId: ''});
    deps.closeStream();
    deps.refreshContexts();
    deps.refreshHistorySelect();
    deps.refreshWorkbench();
  }

  function stopSession() {
    const tid = deps.threadId();
    if (!tid) return;
    api.json(ENDPOINTS.sessionStop(tid), {method: 'POST'}).catch(() => {});
    deps.addMsg('已请求停止（等待当前节点完成）…', 'msg-agent');
  }

  function deleteSession() {
    const sel = byId<HTMLSelectElement>('hist');
    const sid = deps.sessionId() || val('hist');
    if (!sid) { alert('请先在「历史会话」下拉框选择要删除的会话'); return; }
    const label = sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : sid;
    if (!confirm('确认删除该会话？\n\n' + label + '\n\n将删除以下内容（不可恢复）：\n' +
      '· 对话记录（data/conversations/）\n' +
      '· 检查点（data/checkpoints/）\n' +
      '· 产出文件夹（outputs/ 对应子目录）\n' +
      '· 检索缓存（data/pipeline_cache/ 对应主题）\n' +
      '· 向量库（data/chroma/，全局 RAG 检索数据）\n\n' +
      '确定删除吗？')) return;
    api.raw(ENDPOINTS.sessionDelete(String(sid)), {method: 'DELETE'})
      .then((r: any) => r.json()).then((d: any) => {
        const removed = (d.removed || []).join('、');
        deps.addMsg(removed ? '已删除会话: ' + removed : '会话已删除', 'msg-agent');
        deps.applyResearch({threadId: '', sessionId: ''});
        deps.closeStream();
        const log = byId('log');
        if (log) log.innerHTML = '';
        deps.setMode('idle');
        deps.setStatus('就绪', '');
        if (sel) {
          const opt = sel.querySelector('option[value="' + selectorValue(String(sid)) + '"]');
          if (opt) opt.remove();
          sel.value = '';
        }
        refreshHistorySelect();
        deps.refreshArtifacts();
        deps.refreshContexts();
      }).catch((error: any) => deps.addMsg('删除失败: ' + error, 'msg-error'));
  }

  return {
    newSession,
    onHistoryChange,
    refreshHistorySelect,
    switchToConversation,
    resumeSession,
    resumeConversation,
    stopSession,
    finishRun,
    onDone,
    onStopped,
    deleteSession,
    closeHistory,
    historyBack,
    showHistoryList,
    viewConversation,
  };
}

