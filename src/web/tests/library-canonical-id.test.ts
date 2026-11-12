import { expect, test } from 'vitest';
import { createLibraryController, createLibrarySection, LIBRARY_API } from '../src/library-controller';

test('explicit index choice reaches the import API', async () => {
  const bodies: any[] = [];
  const controller = createLibraryController({
    fetchJson: async (url, init) => {
      bodies.push(JSON.parse(String(init?.body || '{}')));
      return url === LIBRARY_API.scan ? { counts: { ok: 1 }, denied: [] }
        : { ok: true, topic: 'library' };
    },
  });
  await controller.scan('C:/papers/a.txt', 'library');
  await controller.commit('session', true);
  // tsconfig 的 lib 是 ES2020: 用下标而不是 Array.prototype.at
  const last = bodies[bodies.length - 1];
  expect(last.embed).toBe(true);
  expect(last.session_id).toBe('session');
});

test('library import selects its canonical id and reports selection only after success', async () => {
  const messages: string[] = [];
  const selected: Array<[string, boolean | undefined]> = [];
  const section = createLibrarySection({
    fetchJson: async (url) => url === LIBRARY_API.scan
      ? { counts: { ok: 1, skipped: 0 }, denied: [], truncated: false }
      : { topic: '探针 库名', source_set_id: '探针_库名', ok: true,
          library_existed: false, bound_session_id: '', binding_error: '' },
    notify: (message) => messages.push(message),
    setPreviewHtml: () => {},
    renderScanPreview: () => '',
    renderImportReport: () => '',
    loadSources: async () => [],
    renderWorkspace: () => {},
    refreshSourceSets: () => {},
    selectSourceSet: async (sourceSetId, announce) => {
      selected.push([sourceSetId, announce]);
      return false;
    },
    currentSessionId: () => '',
    writePaths: () => {},
    setImportDisabled: () => {},
    readPaths: () => 'C:/papers/a.txt',
    readLabel: () => '探针 库名',
    details: () => ({}),
    deleteDetail: () => {},
  });

  await section.scan();
  await section.commit();

  expect(selected).toEqual([['探针_库名', true]]);
  expect(messages.some((message) => message.includes('已为下一次研究选中该库'))).toBe(false);
});
