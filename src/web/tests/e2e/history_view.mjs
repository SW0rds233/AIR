/**
 * 回归: 切换到历史会话后必须能查看该会话的研究记录。
 *
 * 现场报告: "切换到历史对话时无法查看已有的历史会话研究记录"。
 * 判定方式 (为什么不只看文案): 用请求拦截记录**是否真的发起**了
 * `/api/research/<pid>/state` 查询, 并核对工作台区域是否渲染出结论行。
 *
 *     node tests/e2e/history_view.mjs http://127.0.0.1:PORT
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
  const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
  const stateCalls = [];
  const badResponses = [];
  page.on('request', (req) => {
    if (req.url().includes('/api/research/') && req.url().includes('/state')) {
      stateCalls.push(req.url());
    }
  });
  page.on('response', (res) => {
    if (res.status() >= 400) badResponses.push(`${res.status()} ${res.url()}`);
  });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto(base + '/', { waitUntil: 'domcontentloaded' });

  // 1) 历史会话下拉框必须有可选项
  const options = await page.evaluate(() =>
    Array.from(document.querySelectorAll('#hist option'))
      .map((o) => ({ value: o.value, label: o.textContent }))
      .filter((o) => o.value));
  steps.push(`历史下拉可选项: ${options.length}`);
  if (!options.length) problems.push('历史会话下拉框没有可选项');

  // 2) 选第一个历史会话
  await page.selectOption('#hist', options[0].value);
  await page.waitForTimeout(1500);

  // 3) 日志区必须显示 [历史会话] 与消息
  const log = await page.evaluate(() => {
    const el = document.getElementById('log');
    return el ? el.innerText.slice(0, 400) : '';
  });
  steps.push(`日志首行: ${(log.split('\n')[0] || '').slice(0, 60)}`);
  if (!log.includes('[历史会话]')) problems.push('日志区没有渲染历史会话标记');

  // 4) 工作台区域必须有内容 (结论/未决/门槛任一项)
  const wb = await page.evaluate(() => {
    const el = document.getElementById('workbench');
    return el ? el.innerText : '';
  });
  steps.push(`工作台文本长度: ${wb.length}`);
  const wbLooksEmpty = /读取工作台失败|尚未关联|还没有研究记录/.test(wb)
    || wb.trim().length < 20;
  if (wbLooksEmpty) problems.push(`工作台未显示研究记录: ${wb.slice(0, 120)}`);

  // 5) 必须真的发起过 state 查询
  steps.push(`state 查询次数: ${stateCalls.length}`);
  if (!stateCalls.length) problems.push('切换历史会话后没有发起 /api/research/*/state 查询');

  // 6) 不应有 4xx/5xx
  if (badResponses.length) problems.push(`出现失败响应: ${badResponses.slice(0, 3).join(', ')}`);

  await browser.close();
  console.log('--- steps ---');
  steps.forEach((s) => console.log('  ' + s));
  if (problems.length) {
    console.log('--- problems ---');
    problems.forEach((p) => console.log('  ' + p));
    console.log('FAIL: 历史会话研究记录不可查看');
    process.exit(1);
  }
  console.log('PASS: 历史会话研究记录可查看');
}

main().catch((err) => {
  console.log('FAIL: ' + err.message);
  process.exit(1);
});
