/*!
 * 暂停点 / 澄清卡片视图 (合并计划 §9.5: 交互卡片渲染迁出 `app.ts`)。
 *
 * 这些卡片原先直接在 `app.ts` 里 `createElement` + `onclick`, 于是:
 * "候选路线显示哪些条件" 与 "点选后提交什么 ID" 混在一起, 无法单测。
 * 这里把它们改成**纯函数**: 输入暂停点/澄清载荷, 输出卡片 DOM 或条目标记;
 * 提交与追加消息由调用方通过回调处理 (视图不发请求、不改全局状态)。
 *
 * 硬约束不变: 动态按钮仍然写 `data-action` (CSP 下内联处理器不执行);
 * `buildObjectPicker()` 仍返回真实的 `<select>` 元素, 并继续使用同一份
 * `views/research-workbench.ts::feedbackObjectOptions` 选项构造。
 */

import { feedbackObjectOptions, OBJECT_KIND_LABEL, wbEsc } from './research-workbench';

/** 作用对象选择器 (与迁移前 `app.ts::buildObjectPicker` 行为一致)。 */
export function buildObjectPicker(objects: any, selectId = 'wbobj'): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'adv-row';
  const label = document.createElement('label');
  label.textContent = '作用对象 ';
  const sel = document.createElement('select');
  sel.id = selectId || 'wbobj';
  feedbackObjectOptions(objects || {}).forEach((o: any) => {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.label;
    sel.appendChild(opt);
  });
  label.appendChild(sel);
  wrap.appendChild(label);
  return wrap;
}

/** 综述模式 / 通用暂停点卡片。 */
export function buildInterruptCard(p: any): HTMLElement {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  card.innerHTML = '<div class="it-title">' + wbEsc(p.title || '') + '</div>' +
                   '<div class="it-hint">' + wbEsc(p.hint || '') + '</div>';
  return card;
}

/**
 * 理论研究: 候选路线选择 (点选即可, 也可在输入框回序号)。
 *
 * `currentSelection()` 回显"当前已选的候选 ID"(历史恢复后序号可能错位, 因此按 ID 确认)。
 * 返回的卡片按钮点击时调用 `onRespond(stableId)`; 追加"已选择…"消息由回调负责。
 */
export function buildCandidateCard(p: any, deps: {
  currentSelection: () => string;
  onRespond: (response: string) => void;
  onChosen: (idx: number, candidateId: string, statement: string) => void;
}): HTMLElement {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  const list = document.createElement('div');
  list.className = 'cand-list';
  const candidates = p.candidates || [];
  const recommended = candidates.findIndex((c: any) => c.recommended);
  const selected = String(deps.currentSelection() || '');
  candidates.forEach((c: any, idx: any) => {
    const btn = document.createElement('button');
    btn.className = 'cand';
    const meta = [];
    if (c.category) meta.push('类别 ' + c.category);
    // F1-4: 同时展示模型条件、资料依据、已知结果比较状态与会被改变的问题范围
    if (c.variable_domains && Object.keys(c.variable_domains).length) {
      meta.push('条件 ' + Object.entries(c.variable_domains)
        .map(([k, v]) => k + ' ∈ ' + v).join('、'));
    }
    if (c.study && c.study.design && c.study.design !== 'none') {
      meta.push('设计 ' + c.study.design);
    }
    if (c.known_results) meta.push('已知结果 ' + c.known_results);
    if (c.difference) meta.push('与已有差异 ' + c.difference);
    if (c.verifiability) meta.push('可核验性 ' + c.verifiability);
    if (c.difficulty) meta.push('难点 ' + c.difficulty);
    const candId = c.candidate_id || '';
    if (selected && candId && selected === candId) btn.classList.add('selected');
    btn.innerHTML = '<span class="cand-idx">' + (idx === recommended ? '★' : (idx + 1)) + '</span>' +
      '<span style="flex:1">' + wbEsc(c.statement || '(无陈述)') +
      (candId ? '<div class="cand-meta mono">' + wbEsc(candId) + '</div>' : '') +
      (meta.length ? '<div class="cand-meta">' + wbEsc(meta.join('\n')) + '</div>' : '') + '</span>';
    btn.onclick = () => {
      deps.onChosen(idx, String(candId), String(c.statement || ''));
      card.remove();
      // 有稳定 ID 时按 ID 确认: 历史恢复后序号可能错位
      deps.onRespond(candId || String(idx));
    };
    list.appendChild(btn);
  });
  card.innerHTML = '<div class="it-title">' + wbEsc(p.title || '请选择要研究的主路线') + '</div>' +
                   '<div class="it-hint">' + wbEsc(p.hint || '') +
                   '<br>每条候选显示其条件、资料依据与将改变的问题范围；点选即按稳定候选 ID 确认。</div>';
  card.appendChild(list);
  return card;
}

/** 理论研究: 反馈暂停点 (假设/结论概览 + 作用对象选择器)。 */
export function buildFeedbackPromptCard(p: any): HTMLElement {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  const objs = p.objects || {};
  const fmt = (label: any, items: any) => {
    if (!items || !items.length) return '';
    return '<div class="cand-meta">' + label + ': ' +
           wbEsc(items.map(([id, text]: [any, any]) => id + ' — ' + text).join('\n')) + '</div>';
  };
  card.innerHTML = '<div class="it-title">' + wbEsc(p.title || '可以补充研究意见') + '</div>' +
    '<div class="it-hint">可直接回车跳过；或先选作用对象再写意见（如「不要假设 x ∈ real」）</div>' +
    fmt('假设', objs.assumptions) + fmt('结论', objs.claims);
  card.appendChild(buildObjectPicker(objs));
  return card;
}

/**
 * 服务端请澄清时列出候选对象供点选 (而不是只显示一条错误)。
 * 选择结果再次提交时由 `onApply(objectId, text)` 处理 (调用方读取 `#wbfeedback`)。
 */
export function buildClarificationCard(body: any,
                                      deps: { onApply: (objectId: string, text: string) => void })
  : HTMLElement {
  const card = document.createElement('div');
  card.className = 'interrupt-card';
  card.innerHTML = '<div class="it-title">无法唯一定位作用对象</div>' +
    '<div class="it-hint">' + wbEsc(body.clarify || '请指明该意见针对哪个对象') + '</div>';
  const picker = buildObjectPicker({ assumptions: {}, claims: {}, steps: {} }, 'clarifyobj');
  const sel = picker.querySelector('select') as HTMLSelectElement;
  sel.innerHTML = '';
  (body.candidates || []).forEach((c: any) => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = (OBJECT_KIND_LABEL[c.kind] || c.kind) + ' ' + c.id + ' — ' +
                      String(c.text || '').slice(0, 60);
    sel.appendChild(opt);
  });
  const btn = document.createElement('button');
  btn.className = 'ghost';
  btn.textContent = '对该对象重新施加';
  btn.onclick = () => {
    const box = document.getElementById('wbfeedback') as HTMLTextAreaElement | null;
    const target = sel.value;
    card.remove();
    deps.onApply(target, box ? box.value : '');
  };
  picker.appendChild(btn);
  card.appendChild(picker);
  return card;
}
