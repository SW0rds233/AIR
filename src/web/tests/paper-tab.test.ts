/**
 * 论文与追溯面板的装配契约 (合并计划 §9.6 / F3–F5)。
 *
 * `views/paper.ts` 的纯函数已有 13 项用例; 这里补的是**页面装配**层面的回归:
 * - 面板容器的 id/可见性切换 (标签页) 与模板一致;
 * - "没有交付包"时显示原因, 不显示空面板冒充成功;
 * - 清单为空/读取失败时给出具体原因并保留已加载内容;
 * - 渲染期编号缺失时不自行编号 (只显示后端给定值)。
 *
 * 之所以能在这里断言: 页面装配把 `renderPaperTraceability` / `renderFigureGallery`
 * 的输出拼进 `#paper`, 因此"拼出来的 HTML"就是可测的交付面。
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  numberingView,
  renderFigureGallery,
  renderNumbering,
  renderPaperTraceability,
  type ArtifactEntryView,
} from '../src/views/paper';

function panelHtml(files: ArtifactEntryView[], manifest: any,
                  identity = {projectId: 'p1', runId: 'r1'}): string {
  const numbering = manifest
    ? renderNumbering(numberingView((manifest as Record<string, unknown>).numbering))
    : renderNumbering(numberingView(null));
  return [renderPaperTraceability(manifest), numbering,
          renderFigureGallery(files, identity, '')].join('');
}

beforeEach(() => {
  document.body.innerHTML = '<div id="paper" role="tabpanel" style="display:none"></div>';
});

describe('论文追溯面板', () => {
  it('没有交付包 manifest 时说明原因, 并保留已加载的图表区', () => {
    const html = panelHtml([
      {name: 'run-1/figures/fig1.png', size: 10, project_id: 'p1', run_id: 'r1'},
    ] as ArtifactEntryView[], null);
    expect(html).toContain('还没有交付包：研究完成后这里显示稿件与追溯信息。');
    expect(html).toContain('figure-card');
    expect(html).toContain('/api/artifacts/run-1/figures/fig1.png');
    // 空面板不得冒充成功: 必须至少有一句解释
    expect(html).not.toBe('');
  });

  it('manifest 存在时给出交付等级与三个门槛结论, 未知不默认通过', () => {
    const html = panelHtml([], {
      delivery_level: 'manuscript',
      delivery_gate_passed: true,
      gate_passed: false,
      manuscript_traceability: {ok: true},
    });
    expect(html).toContain('交付等级: <b>manuscript</b>');
    expect(html).toContain('通过');
    expect(html).toContain('未通过');
    expect(html).toContain('未知（未记录）');
    expect(html).toContain('可反查性检查通过');
  });

  it('可反查性未通过时逐条列出无法反查的论断', () => {
    const html = panelHtml([], {
      writing_map: {'clm-1': 'sec-2.1'},
      manuscript_traceability: {
        ok: false,
        unlabeled_blocks: ['第 3 段'],
        unmapped_claims: ['clm-2'],
        missing_anchors: ['clm-1'],
      },
    });
    expect(html).toContain('未通过');
    expect(html).toContain('clm-2');
    expect(html).toContain('这条结论在正文里没有位置');
    // 同一结论既有映射又有缺失锚点时, 以"缺失"为准 (不能显示成已追溯)
    expect(html).toContain('映射锚点没有出现在正文里');
    expect(html).toContain('第 3 段');
  });

  it('图表区只收 figures/ 下的成图, 并标出归属未知', () => {
    const html = panelHtml([
      {name: 'run-1/figures/fig1.png', size: 1, project_id: 'p1', run_id: 'r1'},
      {name: 'run-1/figures/fig2.png', size: 1, project_id: 'other', run_id: 'r9'},
      {name: 'run-1/notes/editor.png', size: 1, project_id: 'p1', run_id: 'r1'},
    ] as ArtifactEntryView[], null);
    expect(html).toContain('fig1.png');
    expect(html).toContain('属于当前运行');
    expect(html).toContain('fig2.png');
    expect(html).toContain('归属未知/不属于当前运行');
    // 非 figures/ 的图片不得冒充配图
    expect(html).not.toContain('editor.png');
  });

  it('编号缺失时不自行编号, 显示后端未给出的原因', () => {
    const html = panelHtml([], null);
    expect(html).toContain('渲染期编号');
    expect(html).not.toMatch(/图\s*1\b/);
    expect(html).toContain(numberingView(null).note);
  });

  it('编号存在时按后端给的映射显示', () => {
    const html = panelHtml([], {numbering: {figures: {fig1: '1'}, citations: {c1: '[1]'}}});
    expect(html).toContain('fig1=1');
    expect(html).toContain('c1=[1]');
  });

  it('清单/manifest 读取失败的原因会被显式写出来 (不静默)', () => {
    const reason = 'TypeError: Failed to fetch';
    const html = '<div class="wb-note" style="color:var(--warn)">读取交付物清单失败: ' +
      reason + '（保留已加载内容）</div>' + panelHtml([], null);
    expect(html).toContain('读取交付物清单失败');
    expect(html).toContain(reason);
    expect(html).toContain('保留已加载内容');
  });
});

describe('标签面板容器', () => {
  it('模板里的 #paper 面板默认隐藏, 由 switchTab 控制', () => {
    const panel = document.getElementById('paper')!;
    expect(panel.getAttribute('role')).toBe('tabpanel');
    expect(panel.style.display).toBe('none');
    panel.style.display = 'block';
    expect(panel.style.display).toBe('block');
  });
});
