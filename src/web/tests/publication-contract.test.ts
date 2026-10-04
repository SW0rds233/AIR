/**
 * 交付清单读取的判据 (从 `app.ts` 提出来的那部分, §9.6)。
 *
 * 这段逻辑原来内联在页面装配里, 有三种返回形态 (文本 / 二进制 / 错误) 和一条
 * "缺字段"判定, 却没有任何用例 —— 而它决定追溯面板显示"可以看"还是"读不到"。
 * 提出来的判据如下, 用例逐条固定:
 * 1. **不伪造**: 拿到什么就说什么, 缺 manifest 就报缺失, 不拿空对象当成功;
 * 2. **失败可解释**: 每个失败分支都返回一句**能显示给用户的原因**;
 * 3. **按名字挑**: manifest 必须按 basename 精确匹配, 不能按下标猜。
 */

import { describe, expect, it } from 'vitest';

import {
  pickManifestEntry,
  readManifestBody,
} from '../src/contracts/publication';

function manifestJson(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    project_id: 'p', problem_id: 'q', run_id: 'r', snapshot_id: 's',
    delivery_level: '完整论文', writing_map: {},
    manuscript_traceability: { ok: true, mapped: [], unmapped_claims: [], missing_anchors: [], note: '' },
    ...overrides,
  });
}

describe('挑出 manifest 条目', () => {
  it('按 basename 匹配 (子目录里的 manifest.json 也算)', () => {
    const entry = pickManifestEntry([
      { name: 'out/a.md' },
      { name: 'run-1/package/manifest.json' },
    ]);
    expect(entry?.name).toBe('run-1/package/manifest.json');
  });

  it('没有 manifest 时返回 null (而不是第一个文件)', () => {
    expect(pickManifestEntry([{ name: 'a.md' }, { name: 'b.tex' }])).toBeNull();
    expect(pickManifestEntry([])).toBeNull();
    expect(pickManifestEntry(null)).toBeNull();
    expect(pickManifestEntry(undefined)).toBeNull();
  });

  it('只看 basename: 名字里恰好含 manifest.json 的别的文件不算', () => {
    // 例如 `manifest.json.bak` 或 `x-manifest.jsonl`
    expect(pickManifestEntry([{ name: 'manifest.json.bak' }])).toBeNull();
    expect(pickManifestEntry([{ name: 'amani fest.json' }])).toBeNull();
  });
});

describe('读取 manifest 响应', () => {
  it('文本内容形态: 解析 content 字符串', () => {
    const result = readManifestBody({ content: manifestJson() });
    expect(result.reason).toBe('');
    expect(result.manifest?.project_id).toBe('p');
  });

  it('直接返回对象形态: 原样采用', () => {
    const result = readManifestBody({
      project_id: 'p2', problem_id: 'q2', run_id: 'r2', snapshot_id: 's2',
      delivery_level: '完整论文', writing_map: {},
      manuscript_traceability: { ok: false, mapped: [], unmapped_claims: [], missing_anchors: [], note: '' },
    });
    expect(result.reason).toBe('');
    expect(result.manifest?.project_id).toBe('p2');
  });

  it('二进制形态: 明确说"不是可读文本", 不当成清单', () => {
    const result = readManifestBody({ binary: true, url: '/api/artifacts/download?x' });
    expect(result.manifest).toBeNull();
    expect(result.reason).toContain('二进制');
  });

  it('错误形态: 后端 error 原样带出', () => {
    const result = readManifestBody({ error: '文件不存在' });
    expect(result.manifest).toBeNull();
    expect(result.reason).toBe('文件不存在');
  });

  it('内容不是合法 JSON: 说出"不是合法 JSON"而不是抛异常', () => {
    const result = readManifestBody({ content: '{ 坏掉的 json' });
    expect(result.manifest).toBeNull();
    expect(result.reason).toContain('不是合法 JSON');
  });

  it('响应不是对象: 也有可显示的原因', () => {
    for (const bad of [null, undefined, 'text', 42]) {
      const result = readManifestBody(bad);
      expect(result.manifest).toBeNull();
      expect(result.reason).toBeTruthy();
    }
  });

  it('二进制与 content 同时出现时以 content 为准 (字段比标志更具体)', () => {
    const result = readManifestBody({ content: manifestJson(), binary: true });
    expect(result.manifest?.project_id).toBe('p');
  });
});
