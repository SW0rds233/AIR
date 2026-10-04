/**
 * 资料接入用例 (合并计划 §13.4 / §13.5)。
 *
 * 验收路径里能自动化核对的都在这里: 先预览再确认、拒绝项单独列出、截断显式、
 * 部分失败不报成功、幂等键稳定、不显示绝对路径、失效文件可见。
 */

import { describe, expect, it, vi } from 'vitest';

import {
  importKey,
  parsePaths,
  type LibraryImportReport,
  type LibraryScanReport,
} from '../src/contracts/library';
import { createLibraryController } from '../src/library-controller';
import {
  groupLibraries,
  renderImportReport,
  renderLibraryWorkspace,
  renderScanPreview,
  scanCounts,
} from '../src/views/library';

function scanReport(overrides: Partial<LibraryScanReport> = {}): LibraryScanReport {
  return {
    files: [
      {
        path: 'brc1949.pdf', size: 1024, file_hash: '', kind: 'file', status: 'ok',
        reason: '', mtime: 0, authorized_root: 'D:/papers', symlink: false,
      },
    ],
    denied: [],
    roots: ['D:/papers'],
    truncated: false,
    notes: [],
    counts: { ok: 1, skipped: 0, denied: 0, duplicate: 0, unreadable: 0 },
    total_bytes: 1024,
    ...overrides,
  };
}

function importReport(overrides: Partial<LibraryImportReport> = {}): LibraryImportReport {
  return {
    topic: 'brc', label: 'brc', batch_id: 'b1', origin: 'path_import',
    roots: ['D:/papers'], imported: [], merged: [], skipped: [], denied: [],
    failed: [], duplicates: [], truncated: false, notes: [],
    counts: { imported: 1, merged: 0, duplicates: 0, skipped: 0, denied: 0, failed: 0 },
    ok: true,
    ...overrides,
  };
}

describe('路径解析与幂等键', () => {
  it('逐行切分并去掉引号与空白, 忽略空行', () => {
    expect(parsePaths('D:\\papers\\a.pdf\n\n "D:\\papers\\b\\" ; C:\\x.md'))
      .toEqual(['D:\\papers\\a.pdf', 'D:\\papers\\b\\', 'C:\\x.md']);
  });

  it('同一批路径与库名得到同一个幂等键 (重复点击不二次解析)', () => {
    const first = importKey('brc', ['D:/a.pdf', 'D:/b.pdf']);
    expect(importKey('brc', ['D:/a.pdf', 'D:/b.pdf'])).toBe(first);
    expect(importKey('brc2', ['D:/a.pdf', 'D:/b.pdf'])).not.toBe(first);
  });
});

describe('扫描预览', () => {
  it('未扫描时提示先扫描, 不显示"0 个文件"这种像成功的样子', () => {
    expect(renderScanPreview(null)).toContain('尚未扫描');
  });

  it('拒绝项单独成组并说明放行方式, 不混进跳过', () => {
    const report = scanReport({
      denied: [{
        path: '.env', size: 12, file_hash: '', kind: 'file', status: 'denied',
        reason: '敏感文件', mtime: 0, authorized_root: '', symlink: false,
      }],
      counts: { ok: 1, skipped: 0, denied: 1, duplicate: 0, unreadable: 0 },
    });
    const html = renderScanPreview(report);
    expect(html).toContain('拒绝 1 项');
    expect(html).toContain('DATA_READ_ROOTS');
    expect(html).toContain('.env');
    // 拒绝项不能只被当成"跳过"
    expect(html).not.toContain('跳过（类型不在允许范围）</span> · 敏感文件');
  });

  it('截断必须显式说明, 不得静默丢文件', () => {
    const html = renderScanPreview(scanReport({ truncated: true }));
    expect(html).toContain('截断');
    expect(html).toContain('其余未扫描');
  });

  it('逐状态计数取自后端 counts; 后端缺失时按行统计', () => {
    const fromServer = scanCounts(scanReport({
      counts: { ok: 7, skipped: 2, denied: 0, duplicate: 1, unreadable: 0 },
    }));
    expect(fromServer.ok).toBe(7);
    const derived = scanCounts({
      ...scanReport(),
      counts: undefined as unknown as Record<string, number>,
    });
    expect(derived.ok).toBe(1);
    expect(derived.total).toBe(1);
  });

  it('不把绝对路径渲染出去 (后端只给 basename, 视图也不补全)', () => {
    const html = renderScanPreview(scanReport({
      files: [{
        path: 'brc1949.pdf', size: 1, file_hash: '', kind: 'file', status: 'ok',
        reason: '', mtime: 0, authorized_root: 'D:/papers/secret', symlink: false,
      }],
    }));
    expect(html).toContain('brc1949.pdf');
    expect(html).not.toContain('secret\\brc1949.pdf');
  });
});

describe('导入报告', () => {
  it('完全成功时给出明确结论', () => {
    const html = renderImportReport(importReport());
    expect(html).toContain('导入完成');
    expect(html).not.toContain('未全部成功');
  });

  it('有拒绝/失败/截断时不得报成功, 并逐条列出', () => {
    const html = renderImportReport(importReport({
      ok: false,
      denied: [{
        path: 'id_rsa', size: 1, file_hash: '', kind: 'file', status: 'denied',
        reason: '私钥', mtime: 0, authorized_root: '', symlink: false,
        doc_id: '', title: '', pages: 0, cards: 0, parse_quality: '', created: false,
      }],
      counts: { imported: 0, merged: 0, duplicates: 0, skipped: 0, denied: 1, failed: 0 },
    }));
    expect(html).toContain('未全部成功');
    expect(html).toContain('id_rsa');
  });
});

describe('资料工作区分组', () => {
  const sources = [
    { source_set_id: 'pathlib', documents: 3, cards: 5, origin: 'path_import' },
    { source_set_id: 'legacy', documents: 1, cards: 0 },
  ];

  it('按来源类型分组: 路径导入在前, 缺失 origin 记为系统管理库', () => {
    const groups = groupLibraries(sources);
    expect(groups.map((g) => g.origin)).toEqual(['path_import', 'managed']);
    expect(groups[0].items[0].source_set_id).toBe('pathlib');
  });

  it('失效文件必须显示出来, 不能静默留在库里', () => {
    const html = renderLibraryWorkspace(sources, {
      pathlib: {
        source_set_id: 'pathlib', origin: 'path_import', readable: true,
        roots: ['D:/papers'], documents: 3, cards: 5, files: 2,
        stale_files: ['gone.pdf'],
        hashes: [{ file: 'gone.pdf', file_hash: 'ab', doc_id: 'd1', stale: true }],
      },
    });
    expect(html).toContain('已失效 1');
    expect(html).toContain('gone.pdf');
    expect(html).toContain('重新导入可恢复');
  });

  it('删除按钮说明只解除登记, 不动用户原文件', () => {
    const html = renderLibraryWorkspace(sources);
    expect(html).toContain('data-action="deleteLibrary"');
    expect(html).toContain('不删除你的原文件');
  });

  it('没有资料库时给出可执行下一步', () => {
    expect(renderLibraryWorkspace([])).toContain('从本机路径添加资料');
  });
});

describe('资料接入控制器', () => {
  it('未扫描不得导入 (先预览再确认是硬要求)', async () => {
    const fetchJson = vi.fn();
    const notify = vi.fn();
    const controller = createLibraryController({ fetchJson, notify });
    const result = await controller.commit();
    expect(result).toBeNull();
    expect(fetchJson).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('还没有扫描结果'), 'msg-error');
  });

  it('空路径不发起请求, 直接如实告知', async () => {
    const fetchJson = vi.fn();
    const controller = createLibraryController({ fetchJson, notify: vi.fn() });
    expect(await controller.scan('   \n  ', 'x')).toBeNull();
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it('扫描后 canImport 为真, 导入带上幂等键与库名', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchJson = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith('/scan')) return scanReport();
      return importReport();
    });
    const controller = createLibraryController({ fetchJson, notify: vi.fn() });
    await controller.scan('D:\\papers\\a.pdf', 'brc');
    expect(controller.canImport()).toBe(true);
    await controller.commit();
    const body = JSON.parse(String(calls[1].init?.body));
    expect(calls[1].url).toContain('/api/library/import');
    expect(body.topic).toBe('brc');
    expect(body.idempotency_key).toBe(importKey('brc', ['D:\\papers\\a.pdf']));
  });

  it('缺少库名时拒绝导入并说明原因', async () => {
    const fetchJson = vi.fn(async () => scanReport());
    const notify = vi.fn();
    const controller = createLibraryController({ fetchJson, notify });
    await controller.scan('D:\\a.pdf', '');
    expect(await controller.commit()).toBeNull();
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('库名'), 'msg-error');
  });

  it('部分失败把结论标成需要处理 (hasRejections 为真)', async () => {
    const fetchJson = vi.fn(async (url: string) => (
      url.endsWith('/scan') ? scanReport() : importReport({ ok: false })
    ));
    const notify = vi.fn();
    const controller = createLibraryController({ fetchJson, notify });
    await controller.scan('D:\\a.pdf', 'brc');
    await controller.commit();
    expect(controller.hasRejections()).toBe(true);
    const messages = notify.mock.calls.map((call) => String(call[0]));
    expect(messages.some((m) => m.includes('未全部成功'))).toBe(true);
  });

  it('扫描失败时如实报错, 不产生"可导入"的假状态', async () => {
    const fetchJson = vi.fn(async () => { throw new Error('HTTP 403'); });
    const notify = vi.fn();
    const controller = createLibraryController({ fetchJson, notify });
    expect(await controller.scan('D:\\a.pdf', 'brc')).toBeNull();
    expect(controller.canImport()).toBe(false);
    expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('扫描失败'), 'msg-error');
  });
});
