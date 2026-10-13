/** 资料接入控制器: 「从本机路径添加资料」的先预览再确认流程。 */

import {
  emptyPathRequest,
  importKey,
  parsePaths,
  type LibraryDeleteResult,
  type LibraryDetail,
  type LibraryImportReport,
  type LibraryScanReport,
} from './contracts/library';
import { ENDPOINTS, client as researchClient } from './api/research-client';

export const LIBRARY_API = {
  scan: ENDPOINTS.libraryScan,
  import: ENDPOINTS.libraryImport,
  pickFolder: ENDPOINTS.libraryPickFolder,
  detail: (sourceSetId: string) => ENDPOINTS.library(sourceSetId),
  remove: (sourceSetId: string) => ENDPOINTS.library(sourceSetId),
} as const;

export interface LibraryControllerDeps {
  fetchJson?: (url: string, init?: RequestInit) => Promise<any>;
  /** 用户可见的提示 (走对话区的消息流, 与附件上传同一出口)。 */
  notify?: (text: string, cls?: string) => void;
  /** 扫描成功后重绘预览清单。 */
  renderPreview?: (report: LibraryScanReport) => void;
  /** 导入完成后重绘报告与资料工作区。 */
  renderResult?: (report: LibraryImportReport) => void;
  /** 库列表变化后刷新下拉框与工作区。 */
  refresh?: () => void;
}

export interface LibraryController {
  scan(rawPaths: string, label: string, folderGrants?: string[]): Promise<LibraryScanReport | null>;
  commit(sessionId?: string): Promise<LibraryImportReport | null>;
  invalidate(): void;
  remove(sourceSetId: string): Promise<LibraryDeleteResult | null>;
  detail(sourceSetId: string): Promise<LibraryDetail | null>;
  pendingPaths(): string[];
  scanReport(): LibraryScanReport | null;
  importReport(): LibraryImportReport | null;
  /** 待导入请求是否已就绪 (没有扫描结果时为 false)。 */
  canImport(): boolean;
  /** 是否有未处理的拒绝/失败项 (供界面提示)。 */
  hasRejections(): boolean;
}

async function defaultFetchJson(url: string, init?: RequestInit): Promise<any> {
  // 统一走 HTTP 客户端: URL/错误/取消/重试语义只在一处。
  // 导入是**变更类**请求, 因此带上幂等键 —— 那也正是"允许自动重试"的前提。
  const { client } = await import('./api/research-client');
  const method = (init?.method ?? 'GET').toUpperCase() as 'GET' | 'POST' | 'DELETE';
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  const key = method === 'GET'
    ? undefined
    : String((body && (body.idempotency_key || body.topic)) || url);
  return client().json(url, { method, body, idempotencyKey: key });
}

export function createLibraryController(deps: LibraryControllerDeps = {}): LibraryController {
  const fetchJson = deps.fetchJson ?? defaultFetchJson;
  let scanned: LibraryScanReport | null = null;
  let imported: LibraryImportReport | null = null;
  let request = emptyPathRequest();
  let busy = false;

  function notify(text: string, cls = 'msg-system') {
    deps.notify?.(text, cls);
  }

  return {
    invalidate() {
      scanned = null;
      imported = null;
    },
    async scan(rawPaths: string, label: string, folderGrants: string[] = []) {
      if (busy) return null;
      scanned = null;
      imported = null;
      const paths = parsePaths(rawPaths);
      if (!paths.length) {
        notify('请先填入至少一个本机文件或文件夹路径（每行一条）。', 'msg-error');
        return null;
      }
      request = { ...emptyPathRequest(), paths, label: String(label || '').trim(),
                  folder_grants: [...folderGrants] };
      busy = true;
      try {
        const body = await fetchJson(LIBRARY_API.scan, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
        });
        scanned = body as LibraryScanReport;
        const counts = scanned?.counts ?? {};
        notify(
          `扫描完成：将入库 ${counts.ok ?? 0} · 跳过 ${counts.skipped ?? 0} · ` +
          `拒绝 ${(scanned?.denied ?? []).length} · 截断 ` +
          `${scanned?.truncated ? '是' : '否'}。请先核对清单再确认导入。`);
        deps.renderPreview?.(scanned as LibraryScanReport);
        return scanned;
      } catch (error) {
        notify('扫描失败: ' + String(error), 'msg-error');
        return null;
      } finally {
        busy = false;
      }
    },

    async commit(sessionId = '') {
      if (busy) return null;
      if (!scanned) {
        notify('还没有扫描结果：请先「扫描预览」确认将要入库的文件。', 'msg-error');
        return null;
      }
      const label = String(request.label || '').trim();
      if (!label) {
        notify('请先填写库名（资料库的名字，用于检索时绑定）。', 'msg-error');
        return null;
      }
      busy = true;
      try {
        const body = await fetchJson(LIBRARY_API.import, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...request,
            topic: label,
            embed: false,
            idempotency_key: importKey(label, request.paths),
            session_id: sessionId,
          }),
        });
        imported = body as LibraryImportReport;
        const ok = Boolean(imported?.ok);
        // 命中幂等缓存时**不得**报"导入完成": 服务端这次没有重新导入任何文件
        const cached = Boolean(imported?.cached);
        const merged = Boolean(imported?.library_existed);
        const binding = imported?.binding_error
          ? ` 当前对话未绑定：${imported.binding_error}`
          : imported?.bound_session_id ? ' 已绑定当前对话。' : '';
        notify(
          cached
            ? `资料库「${label}」命中幂等缓存（目录内容未变，本次未重新导入）。` +
              '若刚新增了文件，请重新「扫描预览」后再导入。' + binding
            : ok ? `资料库「${label}」${merged ? '已并入新文献' : '已建立'}。` + binding
                 : `资料库「${label}」导入未全部成功：有文件被拒绝、失败或被截断，` +
                   '请查看清单后处理（已入库部分保留）。' + binding,
          !ok || imported?.binding_error ? 'msg-error' : 'msg-system');
        deps.renderResult?.(imported as LibraryImportReport);
        deps.refresh?.();
        return imported;
      } catch (error) {
        notify('导入失败: ' + String(error), 'msg-error');
        return null;
      } finally {
        busy = false;
      }
    },

    async detail(sourceSetId: string) {
      try {
        return await fetchJson(LIBRARY_API.detail(sourceSetId)) as LibraryDetail;
      } catch (error) {
        notify('读取资料库信息失败: ' + String(error), 'msg-error');
        return null;
      }
    },

    async remove(sourceSetId: string) {
      try {
        const body = await fetchJson(LIBRARY_API.remove(sourceSetId), { method: 'DELETE' });
        notify(body?.note || '已解除库登记（用户原文件未被修改）。');
        deps.refresh?.();
        return body as LibraryDeleteResult;
      } catch (error) {
        notify('解除登记失败: ' + String(error), 'msg-error');
        return null;
      }
    },

    pendingPaths: () => [...request.paths],
    scanReport: () => scanned,
    importReport: () => imported,
    canImport: () => Boolean(scanned && !busy),
    hasRejections: () => Boolean(
      imported ? !imported.ok
        : (scanned?.denied?.length || scanned?.truncated)),
  };
}

// 页面接入
export interface LibrarySectionDeps {
  fetchJson?: LibraryControllerDeps['fetchJson'];
  /** 用户可见提示。 */
  notify(text: string, cls?: string): void;
  /** 预览清单/导入报告的渲染出口 (纯视图函数在 `views/library.ts`, 由页面注入)。 */
  setPreviewHtml(html: string): void;
  /** 扫描报告 → HTML (由页面注入 `renderScanPreview`, 保持 controller 不依赖视图模块)。 */
  renderScanPreview(report: LibraryScanReport): string;
  /** 导入报告 → HTML (由页面注入 `renderImportReport`)。 */
  renderImportReport(report: LibraryImportReport): string;
  /** 资料库列表摘要 (下拉框与资料工作区共用)。 */
  loadSources(): Promise<any[]>;
  renderWorkspace(sources: any[]): void;
  /** 库列表变化后刷新资料源下拉框。 */
  refreshSourceSets(): void;
  selectSourceSet(sourceSetId: string, announce?: boolean): Promise<boolean>;
  currentSessionId(): string;
  writePaths(text: string): void;
  pickFolder?(): Promise<{ path: string; cancelled: boolean; grant_token: string }>;
  setImportDisabled(disabled: boolean): void;
  readPaths(): string;
  readLabel(): string;
  /** 已展开详情的库 (按需查询, 不预先拉取全部清单)。 */
  details(): Record<string, LibraryDetail>;
  deleteDetail(sourceSetId: string): void;
}

export interface LibrarySection {
  controller(): LibraryController;
  scan(): Promise<void>;
  commit(): Promise<void>;
  chooseFolder(): Promise<void>;
  refreshWorkspace(): Promise<void>;
  remove(sourceSetId: string): Promise<void>;
  bind(): void;
}

/**
 * 「从本机路径添加资料」的页面接入。
 *
 * 两条行为是硬要求, 与 `createLibraryController` 一起保证: **未扫描不得导入**;
 * **部分失败不报成功**。这里只做编排与 DOM 出口, 不在调用方重复判断。
 */
export function createLibrarySection(deps: LibrarySectionDeps): LibrarySection {
  let controller: LibraryController | null = null;
  const folderGrants = new Map<string, string>();

  function getController(): LibraryController {
    if (controller) return controller;
    controller = createLibraryController({
      fetchJson: deps.fetchJson,
      notify: (text, cls) => deps.notify(text, cls),
      renderPreview: (report) => deps.setPreviewHtml(deps.renderScanPreview(report)),
      renderResult: (report) => deps.setPreviewHtml(deps.renderImportReport(report)),
      refresh: () => {
        void refreshWorkspace();
      },
    });
    return controller;
  }

  async function scan(): Promise<void> {
    const paths = deps.readPaths();
    const grants = parsePaths(paths).map((path) => folderGrants.get(path)).filter(
      (token): token is string => Boolean(token));
    const report = await getController().scan(paths, deps.readLabel(), grants);
    if (report) deps.setImportDisabled(!getController().canImport());
  }

  async function commit(): Promise<void> {
    const sessionId = deps.currentSessionId();
    const result = await getController().commit(sessionId);
    if (result && !result.binding_error && deps.currentSessionId() === sessionId) {
      await deps.selectSourceSet(result.source_set_id || result.topic, true);
    }
    deps.setImportDisabled(!getController().canImport());
  }

  async function chooseFolder(): Promise<void> {
    try {
      const result = await (deps.pickFolder
        ? deps.pickFolder()
        : researchClient().json<{ path: string; cancelled: boolean; grant_token: string }>(
            LIBRARY_API.pickFolder, { method: 'POST' }));
      if (!result.path || result.cancelled) return;
      if (result.grant_token) folderGrants.set(result.path, result.grant_token);
      const existing = deps.readPaths().trim();
      const paths = parsePaths(existing);
      if (!paths.includes(result.path)) {
        deps.writePaths(existing ? `${existing}\n${result.path}` : result.path);
      }
      getController().invalidate();
      deps.setImportDisabled(true);
      deps.notify('已添加所选文件夹，请扫描预览后确认导入。');
    } catch (error) {
      deps.notify('文件夹选择失败：' + String(error) + '。也可手动填写路径。', 'msg-error');
    }
  }

  async function refreshWorkspace(): Promise<void> {
    try {
      const sources = await deps.loadSources();
      deps.renderWorkspace(sources);
    } catch (error) {
      // 读不到就保留已有内容并说明原因, 不显示成"没有资料库"
      deps.notify('读取资料工作区失败: ' + String(error), 'msg-error');
    }
  }

  async function remove(sourceSetId: string): Promise<void> {
    const id = String(sourceSetId || '');
    if (!id) return;
    const outcome = await getController().remove(id);
    if (outcome && outcome.ok) deps.deleteDetail(id);
    deps.refreshSourceSets();
    await refreshWorkspace();
  }

  function bind(): void {
    const on = (id: string, event: string, handler: () => void) => {
      const el = typeof document === 'undefined' ? null : document.getElementById(id);
      if (el) el.addEventListener(event, handler);
    };
    on('btn-lib-scan', 'click', () => { void scan(); });
    on('btn-lib-import', 'click', () => { void commit(); });
    on('btn-lib-choose-folder', 'click', () => { void chooseFolder(); });
    const invalidate = () => {
      getController().invalidate();
      deps.setImportDisabled(true);
    };
    on('libpaths', 'input', invalidate);
    on('liblabel', 'input', invalidate);
  }

  return { controller: getController, scan, commit, chooseFolder,
           refreshWorkspace, remove, bind };
}
