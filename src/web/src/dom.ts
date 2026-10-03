/**
 * 类型化 DOM 访问 (P2 逐段类型化第一步)。
 *
 * 页面逻辑原先直接 `document.getElementById(id).value` —— 在严格类型检查下会报
 * "对象可能为 null"。这里把取元素与取值集中成一层薄封装: 元素不存在时返回空值或
 * 静默跳过, 而不是抛错中断整页逻辑 (后者正是"页面加载了但完全不能点"的成因)。
 */

export function byId<T extends HTMLElement = HTMLElement>(id: string): T | null {
  const el = typeof document === 'undefined' ? null : document.getElementById(id);
  return (el as T | null) ?? null;
}

export function val(id: string, fallback = ''): string {
  const el = byId<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(id);
  return el && typeof el.value === 'string' ? el.value : fallback;
}

export function num(id: string, fallback: number): number {
  const parsed = Number.parseInt(val(id), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function text(id: string, fallback = ''): string {
  const el = byId(id);
  return el ? el.textContent ?? fallback : fallback;
}

export function setText(id: string, value: string): void {
  const el = byId(id);
  if (el) el.textContent = value;
}

export function setVal(id: string, value: string): void {
  const el = byId<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(id);
  if (el) el.value = value;
}

export function setHtml(id: string, html: string): void {
  const el = byId(id);
  if (el) el.innerHTML = html;
}

export function checked(id: string): boolean {
  const el = byId<HTMLInputElement>(id);
  return Boolean(el && el.checked);
}

export function setChecked(id: string, on: boolean): void {
  const el = byId<HTMLInputElement>(id);
  if (el) el.checked = on;
}

export function isDisabled(id: string): boolean {
  const el = byId<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>(id);
  return Boolean(el && el.disabled);
}

export function setDisabled(id: string, off: boolean): void {
  const el = byId<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>(id);
  if (el) el.disabled = off;
}

export function setDisplay(id: string, visible: boolean): void {
  const el = byId(id);
  if (el) el.style.display = visible ? '' : 'none';
}

export function setProp(id: string, prop: string, value: string): void {
  const el = byId(id);
  if (el) (el.style as unknown as Record<string, string>)[prop] = value;
}

export function setPlaceholder(id: string, value: string): void {
  const el = byId<HTMLInputElement | HTMLTextAreaElement>(id);
  if (el) el.placeholder = value;
}

export function scrollToBottom(id: string): void {
  const el = byId(id);
  if (el) el.scrollTop = el.scrollHeight;
}

export function setOptions(id: string, options: Array<{ value: string; label: string }>,
                           selected = ''): void {
  const el = byId<HTMLSelectElement>(id);
  if (!el) return;
  el.innerHTML = '';
  options.forEach((item) => {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.label;
    el.appendChild(option);
  });
  el.value = selected;
}

export function on(id: string, event: string, handler: (ev: Event) => void): void {
  const el = byId(id);
  if (el) el.addEventListener(event, handler);
}
