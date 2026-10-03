/**
 * P2 真实浏览器验收 (计划书 §5 发布判据 5)。
 *
 * 覆盖: 构建产物能真正跑起来 (无 JS 报错/无 4xx 资源) → 切到理论研究模式 → 提交问题 →
 * 工作台出现结论对象 → 窄屏布局仍可操作。用法:
 *
 *     node tests/browser/web_flow.mjs http://127.0.0.1:PORT
 *
 * 退出码 0 表示通过; 失败时把原因打到 stdout 供 pytest 展示。
 */
import { chromium } from 'playwright';

const base = (process.argv[2] || '').replace(/\/$/, '');
if (!base) {
  console.log('FAIL: 缺少服务地址参数');
  process.exit(2);
}

const problems = [];
const steps = [];

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('console', (msg) => {
    if (msg.type() === 'error') problems.push(`console: ${msg.text()}`);
  });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));
  page.on('response', (res) => {
    const status = res.status();
    const url = res.url();
    if (status >= 400 && !url.includes('/api/sessions/')) {
      problems.push(`http ${status} ${url}`);
    }
  });

  // 1) 页面能加载, 且脚本真的在跑 (window.AIR 由打包产物建立)
  await page.goto(`${base}/`, { waitUntil: 'networkidle' });
  const hasAir = await page.evaluate(() => typeof window.AIR === 'object' && window.AIR !== null);
  if (!hasAir) problems.push('打包脚本未执行: window.AIR 不存在');
  steps.push('页面加载 + window.AIR 存在');

  // 2) 理论研究模式 + 提交一个可离线判定的问题 (模式选择在"高级选项"里, 先展开)
  await page.click('details.adv > summary');
  await page.selectOption('#runmode', 'theory');
  await page.fill('#topic', '对所有实数 x: x**2 >= 0');
  await page.fill('#projid', 'browser-e2e');
  await page.fill('#probid', 'p1');
  steps.push('切到理论模式并填写项目/问题');

  await page.click('#btn-send');
  // 3) 工作台出现命题对象 (结论 id)
  await page.waitForFunction(
    () => /clm-/.test(document.querySelector('#workbench')?.textContent || ''),
    null, { timeout: 90000 },
  );
  steps.push('工作台显示结论对象');
  const wbText = await page.textContent('#workbench');
  if (!/研究问题|结论/.test(wbText || '')) problems.push('工作台缺少研究问题/结论栏目');

  // 4) 对象级反馈控件可用
  if (await page.locator('#wbfeedback').count() === 0) {
    problems.push('缺少对象级反馈控件 #wbfeedback');
  } else {
    steps.push('对象级反馈控件存在');
  }

  // 4b) 工作台动作真的能点 (P2 实测缺陷: 内联 onclick 被 CSP 拦掉后按钮是死的)
  const claimToggle = page.locator('[data-action="toggleClaimDetail"]').first();
  if (await claimToggle.count() === 0) {
    problems.push('工作台没有可点的结论详情按钮 (data-action=toggleClaimDetail)');
  } else {
    const before = await page.locator('#workbench').textContent();
    await claimToggle.click();
    await page.waitForFunction(
      (previous) => (document.querySelector('#workbench')?.textContent || '') !== previous,
      before, { timeout: 15000 },
    ).catch(() => problems.push('点击结论详情后工作台没有任何变化 (事件未生效)'));
    if ((await page.locator('#workbench').textContent()) === before) {
      problems.push('点击结论详情无效: 工作台内容未变化');
    } else {
      steps.push('工作台动作点击生效 (data-action 委托)');
    }
  }

  // 5) 窄屏: 布局改为纵向且控件仍可点
  await page.setViewportSize({ width: 420, height: 900 });
  const flexDir = await page.evaluate(() => getComputedStyle(document.querySelector('main')).flexDirection);
  if (flexDir !== 'column') problems.push(`窄屏未切换为纵向布局: ${flexDir}`);
  const stillClickable = await page.locator('#btn-send').isVisible();
  if (!stillClickable) problems.push('窄屏下启动按钮不可见');
  steps.push('窄屏布局与可操作性');

  // 6) 键盘可达: Tab 能落到标签页按钮, Enter 能切换标签
  await page.locator('[data-tab="preview"]').focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement?.getAttribute('data-tab'));
  if (focused !== 'workbench') problems.push(`键盘焦点未按预期移动: ${focused}`);
  steps.push('键盘焦点与标签切换');

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
  console.log('浏览器验收未通过');
  process.exit(1);
}
console.log('浏览器验收通过');
