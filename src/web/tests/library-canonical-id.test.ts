import { expect, test } from 'vitest';
import { createLibrarySection, LIBRARY_API } from '../src/library-controller';

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
