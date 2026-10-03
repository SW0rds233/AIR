import { describe, expect, it } from 'vitest';

import {
  ACCEPT_ATTR,
  ALLOWED_SUFFIXES,
  MAX_BYTES,
  formatSize,
  kindLabel,
  parseLabel,
  problemAttachmentIds,
  shortHash,
  summarizeResults,
  suffixOf,
  validateFiles,
} from '../src/uploads';

describe('附件上传前端契约', () => {
  it('允许类型与 accept 属性覆盖 pdf/txt/md/docx', () => {
    for (const suffix of ['.pdf', '.txt', '.md', '.docx']) {
      expect(ALLOWED_SUFFIXES).toContain(suffix);
      expect(ACCEPT_ATTR).toContain(suffix);
    }
    expect(suffixOf('a.PDF')).toBe('.pdf');
    expect(suffixOf('noext')).toBe('');
  });

  it('校验: 空选择 / 类型 / 大小 / 文献缺主题', () => {
    expect(validateFiles([], 'problem')).toContain('请选择文件');
    expect(validateFiles([{ name: 'a.exe', size: 10 }], 'problem')).toContain('不支持的文件类型');
    expect(validateFiles([{ name: 'a.txt', size: 0 }], 'problem')).toContain('空文件');
    expect(validateFiles([{ name: 'a.txt', size: MAX_BYTES + 1 }], 'problem')).toContain('文件过大');
    expect(validateFiles([{ name: 'a.pdf', size: 10 }], 'literature', '')).toContain('资料库主题');
    expect(validateFiles([{ name: 'a.pdf', size: 10 }], 'literature', 'RF')).toBe('');
    expect(validateFiles([{ name: 'a.docx', size: 10 }], 'problem')).toBe('');
  });

  it('格式化与标签', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2048)).toBe('2 KB');
    expect(formatSize(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(shortHash('262e4c50caab7a49b76e')).toBe('262e4c50caab');
    expect(kindLabel('literature')).toContain('文献');
    expect(kindLabel('problem')).toContain('问题');
  });

  it('解析状态必须暴露失败, 不能看起来已成功', () => {
    expect(parseLabel({ attachment_id: 'a', kind: 'problem', filename: 'x', size: 1,
                        sha256: '', parse_quality: 'ok', chars: 120 })).toContain('解析正常');
    expect(parseLabel({ attachment_id: 'a', kind: 'problem', filename: 'x', size: 1,
                        sha256: '', parse_quality: 'failed: OSError', chars: 0 }))
      .toContain('解析失败');
    expect(parseLabel({ attachment_id: 'a', kind: 'literature', filename: 'x', size: 1,
                        sha256: '' })).toContain('已入库');
  });

  it('上传结果摘要区分成功/去重/失败', () => {
    const text = summarizeResults({
      failed: 1,
      results: [
        { ok: true, filename: 'a.pdf', document: { doc_id: 'doc-1' } },
        { ok: true, filename: 'b.txt', deduplicated: true },
        { ok: false, filename: 'c.exe', error: '不支持的文件类型' },
      ],
    });
    expect(text).toContain('doc-1');
    expect(text).toContain('跳过重复入库');
    expect(text).toContain('不支持的文件类型');
    expect(text).toContain('1 个失败');
  });

  it('只有"补充问题说明"的附件会并入问题陈述', () => {
    const ids = problemAttachmentIds([
      { attachment_id: 'att-1', kind: 'problem', filename: 'a', size: 1, sha256: '' },
      { attachment_id: 'att-2', kind: 'literature', filename: 'b', size: 1, sha256: '' },
    ]);
    expect(ids).toEqual(['att-1']);
  });
});
