/**
 * P2 浏览器验收 (二): 绑定资料库 → 启动研究 → 交付物 → 断线重连。
 *
 *     node tests/e2e/web_flow_full.mjs http://127.0.0.1:PORT TOPIC
 *
 * TOPIC 为测试资料库主题名 (由 pytest 侧用合成 PDF 建好)。退出码 0 表示通过。
 */
import { chromium } from 'playwright';

const base = (process.argv[2] || '').replace(/\/$/, '');
const topic = process.argv[3] || '';
const problems = [];
const steps = [];
let blocking = false;   // 故意断线期间忽略由拦截造成的资源错误

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const stateCalls = [];
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    if (blocking && /ERR_FAILED|Failed to load resource/.test(msg.text())) return;
    problems.push(`console: ${msg.text()}`);
  });
  page.on('request', (req) => {
    if (/\/api\/sessions\/[^/]+\/state/.test(req.url())) stateCalls.push(req.url());
  });

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });
  await page.click('details.adv > summary');
  // 统一入口: 不再有模式选择步骤

  // 1) 选资料: 资料库下拉必须列出合成资料集, 选中后需显示其文档数
  await page.waitForFunction(
    (name) => Array.from(document.querySelectorAll('#sourceset option'))
      .some((o) => o.value === name),
    topic, { timeout: 20000 },
  );
  await page.selectOption('#sourceset', topic);
  await page.waitForFunction(
    () => /篇/.test(document.querySelector('#sourceinfo')?.textContent || ''),
    null, { timeout: 15000 },
  );
  steps.push(`绑定资料库 ${topic} 并显示其规模`);

  // 2) 启动一次可离线判定的理论研究
  await page.fill('#topic', '对所有实数 x: x**2 >= 0');
  await page.fill('#projid', 'browser-full');
  await page.fill('#probid', 'p1');
  await page.click('#btn-send');
  await page.waitForFunction(
    () => /clm-/.test(document.querySelector('#workbench')?.textContent || ''),
    null, { timeout: 90000 },
  );
  steps.push('工作台显示结论对象');

  // 3) 交付物: 切到 outputs 标签页, 必须能看到交付包清单
  await page.click('[data-tab="files"]');
  await page.waitForFunction(
    () => /manifest\.json|manuscript/.test(document.querySelector('#files')?.textContent || ''),
    null, { timeout: 30000 },
  );
  steps.push('文件页列出交付包 (manifest/manuscript)');
  const filesText = await page.textContent('#files');
  if (!/browser-full|snap-/.test(filesText || '')) {
    problems.push('文件页未显示本次运行的项目/快照信息');
  }

  // 4) 断线重连: 拦掉 SSE 后启动新会话, 补偿接口必须被调用, 解拦后必须恢复
  await page.route('**/api/sessions/*/events*', (route) => route.abort());
  blocking = true;
  await page.fill('#projid', 'browser-reconnect');
  await page.fill('#probid', 'p2');
  await page.fill('#topic', '对所有实数 x: x**2 + 1 >= 0');
  await page.click('#btn-send');
  const deadline = Date.now() + 40000;
  while (stateCalls.length === 0 && Date.now() < deadline) {
    await page.waitForTimeout(250);
  }
  if (!stateCalls.length) {
    problems.push('SSE 中断后未调用会话状态补偿接口 (断线被静默吞掉)');
  } else {
    const status = await page.textContent('#status');
    steps.push(`断线时调用状态补偿 ${stateCalls.length} 次 (状态: ${(status || '').trim()})`);
  }

  await page.unroute('**/api/sessions/*/events*');
  blocking = false;
  // 恢复的证据: 重连后事件继续到达, 工作台重新出现结论对象
  await page.waitForFunction(
    () => /clm-/.test(document.querySelector('#workbench')?.textContent || ''),
    null, { timeout: 90000 },
  ).catch(() => problems.push('解拦后未恢复: 工作台没有出现结论对象'));
  steps.push('解拦后恢复连接并继续收到事件');

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
  console.log('整链浏览器验收未通过');
  process.exit(1);
}
console.log('整链浏览器验收通过');
