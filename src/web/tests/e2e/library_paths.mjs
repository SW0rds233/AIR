/**
 * 合并计划 §13.4 浏览器验收: 「从本机路径添加资料」的先预览再确认。
 *
 *     node tests/e2e/library_paths.mjs http://127.0.0.1:PORT /授权根目录
 *
 * 需要观察到的结果 (§13.5 验收路径):
 * - 入口在理论研究模式下可见, 且**不在**折叠的高级选项里;
 * - 扫描只预览不导入: 预览清单出现, 且此刻库**还没有**登记这些文件;
 * - 拒绝项单独列出 (敏感文件即使位于授权根内也被拒), 不得混进"跳过";
 * - 确认导入后逐条结果可见; 用户原文件**未被复制**(原目录内容不变);
 * - 资料工作区把该库归入"本机路径导入", 且不显示系统绝对路径;
 * - 解除登记只解除库登记, 不动用户原文件。
 */
import { chromium } from 'playwright';
import { readdirSync, statSync } from 'node:fs';

const base = (process.argv[2] || '').replace(/\/$/, '');
const root = process.argv[3] || '';
if (!base || !root) {
  console.log('FAIL: 需要服务地址与一个已授权根目录');
  process.exit(2);
}

const problems = [];
const steps = [];

function snapshot(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = `${dir}/${name}`;
    const info = statSync(full);
    out.push(`${name}:${info.size}:${info.mtimeMs}`);
  }
  return out.sort().join('|');
}

async function main() {
  const before = snapshot(root);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));

  const scanCalls = [];
  const importCalls = [];
  page.on('request', (req) => {
    if (req.method() !== 'POST') return;
    if (req.url().endsWith('/api/library/scan')) scanCalls.push(req.postData() || '');
    if (req.url().endsWith('/api/library/import')) importCalls.push(req.postData() || '');
  });

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });

  // 1) 统一入口: 资料接入入口**始终可见**, 不需要先选任何模式
  //    (旧用例在这里断言"综述模式隐藏、理论模式可见" —— 那属于已删除的模式选择器)
  if (!(await page.isVisible('#pathrow'))) {
    problems.push('资料接入入口应始终可见 (统一入口下不再按模式隐藏)');
  } else {
    steps.push('资料接入入口始终可见 (无需选择模式)');
  }

  // 2) 入口位于**主界面**, 不得又退回折叠的「高级选项」里
  const inAdvanced = await page.evaluate(
    () => Boolean(document.querySelector('#pathrow')?.closest('details.adv')));
  if (inAdvanced) problems.push('本机路径入口被放进了折叠的高级选项里');
  else steps.push('本机路径入口位于主界面');
  // 附件入口与它同为资料接入, 也必须始终可见
  if (!(await page.isVisible('#attachrow'))) {
    problems.push('附件入口应始终可见 (统一入口下不再按模式隐藏)');
  } else {
    steps.push('附件入口始终可见');
  }
  for (const sel of ['#libpaths', '#liblabel', '#btn-lib-scan', '#btn-lib-import']) {
    if (!(await page.isVisible(sel))) problems.push(`资料入口缺少控件 ${sel}`);
  }
  // 未扫描时"确认导入"必须不可用 (先预览再确认是硬要求)
  if (!(await page.isDisabled('#btn-lib-import'))) {
    problems.push('未扫描前「确认导入」不应可用');
  } else {
    steps.push('未扫描前「确认导入」禁用');
  }

  // 3) 扫描预览: 输入授权根目录 + 一个敏感文件
  await page.fill('#libpaths', `${root}\n${root}/.env`);
  await page.fill('#liblabel', 'browser-paths');
  await page.click('#btn-lib-scan');
  await page.waitForFunction(
    () => /将入库/.test(document.querySelector('#libpreview')?.textContent || ''),
    null, { timeout: 30000 },
  );
  if (!scanCalls.length) problems.push('没有发出扫描请求');
  const preview = (await page.textContent('#libpreview')) || '';
  if (!/拒绝/.test(preview)) problems.push('预览清单里没有出现拒绝项');
  else steps.push('预览清单列出了拒绝项');
  if (!/\.env/.test(preview)) problems.push('被拒绝的敏感文件没有出现在清单里');
  else steps.push('敏感文件 .env 出现在拒绝清单');
  // 预览阶段不得已经入库
  if (importCalls.length) problems.push('只扫描却发出了导入请求');
  else steps.push('扫描阶段没有发起导入');

  // 4) 确认导入
  if (await page.isDisabled('#btn-lib-import')) {
    problems.push('扫描后「确认导入」仍不可用');
  }
  await page.click('#btn-lib-import');
  await page.waitForFunction(
    () => /新增|导入完成|未全部成功/.test(document.querySelector('#libpreview')?.textContent || ''),
    null, { timeout: 60000 },
  );
  if (!importCalls.length) problems.push('没有发出导入请求');
  const report = await page.evaluate(
    () => (window.AIRLibrary && window.AIRLibrary.report) ? window.AIRLibrary.report() : null);
  if (!report) {
    problems.push('导入后拿不到结构化报告 (window.AIRLibrary.report)');
  } else {
    steps.push(`导入报告: 新增 ${report.counts?.imported ?? 0} · 拒绝 ${report.counts?.denied ?? 0}`);
    if ((report.counts?.imported ?? 0) < 1) problems.push('没有文件被入库');
    if ((report.counts?.denied ?? 0) < 1) problems.push('敏感文件没有被拒绝 (安全规则未生效)');
    if (report.truncated) problems.push('本次导入被截断, 与预期不符');
  }
  const preview2 = (await page.textContent('#libpreview')) || '';
  if (!/未全部成功/.test(preview2)) {
    problems.push('存在拒绝项却没有报告"未全部成功"');
  } else {
    steps.push('部分失败没有被报告成成功');
  }

  // 5) 用户原文件不能被复制/改名/修改
  const after = snapshot(root);
  if (after !== before) problems.push('导入过程修改了用户原目录内容');
  else steps.push('用户原目录内容与 mtime 未被改动');

  // 6) 资料工作区: 归入"本机路径导入", 且不显示绝对路径
  await page.click('.tab[data-tab="library"]');
  await page.waitForFunction(
    () => /本机路径导入/.test(document.querySelector('#libworkspace')?.textContent || ''),
    null, { timeout: 30000 },
  ).catch(() => problems.push('资料工作区没有把该库归入"本机路径导入"'));
  const workspace = (await page.textContent('#libworkspace')) || '';
  steps.push(`资料工作区显示: ${workspace.replace(/\s+/g, ' ').slice(0, 80)}`);
  const normalizedRoot = root.replace(/\\/g, '/');
  if (workspace.includes(normalizedRoot) || workspace.includes(root)) {
    problems.push('资料工作区泄露了系统绝对路径');
  } else {
    steps.push('资料工作区未泄露绝对路径');
  }

  // 7) 解除登记: 只解除登记, 原文件仍在
  await page.evaluate(() => {
    const btn = document.querySelector('#libworkspace [data-action="deleteLibrary"]');
    if (btn) btn.click();
  });
  await page.waitForTimeout(1500);
  const afterDelete = snapshot(root);
  if (afterDelete !== before) problems.push('解除登记删除了用户原文件');
  else steps.push('解除登记未删除用户原文件');

  await browser.close();
}

try {
  await main();
} catch (err) {
  problems.push(`异常: ${err.message}`);
}

for (const step of steps) console.log(`PASS ${step}`);
if (problems.length) {
  for (const p of problems) console.log(`FAIL ${p}`);
  console.log('本机路径资料接入浏览器验收未通过');
  process.exit(1);
}
console.log('本机路径资料接入浏览器验收通过');
