/**
 * G19 / 合并计划 §8.1 的真实浏览器验收: **唯一状态**。
 *
 * 现场缺陷: 前端同时存在三份可写状态 —— `window.AIR.research` (current-research.ts
 * 的状态机)、`app.ts::currentResearch` 与一组 ID 镜像、`team-controller.ts` 的 reducer
 * store。于是切换历史会话时, 界面上的项目字段、只读投影与团队视图可以各自指向不同的
 * 研究, 只能靠 `syncSelectionFromLegacy()` 事后对表。
 *
 * 判据 (为什么不是"看文案") —— 切换历史会话后, 在真实页面里取三种身份:
 * 1. 表单字段 `#projid`;
 * 2. `window.AIR.research` (迁移期只读投影);
 * 3. `window.AIRTeam.store().selection` (团队视图读的状态);
 * 三者必须**完全相同**, 且切换会话时真的按该身份查询了工作台; 切回「+ 历史会话」
 * 后三者必须一起清空。
 *
 * 用法: node tests/e2e/single_store.mjs http://127.0.0.1:PORT
 * (会话由本脚本用真实接口建立, 因此不依赖运行环境里预先存在的数据)
 */
import { chromium } from 'playwright';

const base = (process.argv[2] || '').replace(/\/$/, '');
if (!base) {
  console.log('FAIL: 缺少服务地址参数');
  process.exit(2);
}

const problems = [];
const steps = [];

async function waitForConversation(sessionId) {
  for (let i = 0; i < 120; i += 1) {
    const res = await fetch(`${base}/api/conversations`);
    const body = await res.json();
    const found = (body.conversations || []).find((c) => c.session_id === sessionId);
    if (found) return found;
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

async function main() {
  // 1) 用真实接口建一个会话 (离线引擎), 让"历史会话"真的存在
  const res = await fetch(`${base}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      request: '判断 x+1 是否可逆', topic: '唯一状态验收',
      project_id: 'hist-probe', problem_id: 'p1',
      max_actions: 2, max_tool_calls: 2, skip_retrieval: true,
      source_policy: 'user_kb', time_range: '2019-2026',
    }),
  });
  if (!res.ok) {
    console.log(`FAIL: 建立会话失败 HTTP ${res.status} ${await res.text()}`);
    process.exit(1);
  }
  const started = await res.json();
  steps.push(`会话已建立: ${started.session_id}`);
  const record = await waitForConversation(started.session_id);
  if (!record) {
    console.log('FAIL: 会话没有出现在 /api/conversations');
    process.exit(1);
  }

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const stateCalls = [];
  const bad = [];
  page.on('request', (req) => {
    if (req.url().includes('/api/research/') && req.url().includes('/state')) {
      stateCalls.push(req.url());
    }
  });
  page.on('response', (r) => { if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`); });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });

  // 2) 切到刚建的会话: 三种身份必须一起走
  await page.selectOption('#hist', started.session_id);
  await page.waitForTimeout(2500);
  const after = await page.evaluate(() => ({
    projid: (document.getElementById('projid') || {}).value || '',
    log: (document.getElementById('log') || {}).innerText || '',
    airProject: (window.AIR && window.AIR.research) ? window.AIR.research.projectId : '<无 AIR>',
    teamProject: (window.AIRTeam && window.AIRTeam.store)
      ? window.AIRTeam.store().selection.projectId : '<无 store>',
  }));
  steps.push(`切换后 projid=${after.projid} AIR.research=${after.airProject} `
    + `AIRTeam.store=${after.teamProject}`);

  if (after.projid !== 'hist-probe') {
    problems.push(`项目字段没有随历史会话恢复: ${after.projid}`);
  }
  if (after.airProject !== 'hist-probe') {
    problems.push(`window.AIR.research 没反映切换结果: ${after.airProject}`);
  }
  if (after.teamProject !== after.airProject) {
    problems.push('团队视图与只读投影不是同一份 selection '
      + `(${after.teamProject} vs ${after.airProject}) —— 又出现了第二份状态`);
  }
  if (!after.log.includes('[历史会话]')) problems.push('日志区没有 [历史会话] 标记');
  if (!stateCalls.length) problems.push('切换历史会话后没有查询 /api/research/*/state');
  steps.push('三种身份一致, 且按该身份查询了工作台');

  // 3) 切回「+ 历史会话」: 身份必须整体清空 (三处一起)
  await page.selectOption('#hist', '');
  await page.waitForTimeout(600);
  const reset = await page.evaluate(() => ({
    projid: (document.getElementById('projid') || {}).value || '',
    airProject: (window.AIR && window.AIR.research) ? window.AIR.research.projectId : '<无 AIR>',
    teamProject: (window.AIRTeam && window.AIRTeam.store)
      ? window.AIRTeam.store().selection.projectId : '<无 store>',
  }));
  steps.push(`清空后 projid=${reset.projid} AIR.research=${reset.airProject} `
    + `AIRTeam.store=${reset.teamProject}`);
  if (reset.projid !== '') problems.push(`新会话没有清空项目字段: ${reset.projid}`);
  if (reset.airProject !== '') problems.push(`新会话后只读投影仍带旧项目: ${reset.airProject}`);
  if (reset.teamProject !== '') problems.push(`新会话后团队视图仍带旧项目: ${reset.teamProject}`);

  if (bad.length) problems.push(`出现失败响应: ${bad.slice(0, 3).join(', ')}`);

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
  console.log('唯一状态浏览器验收未通过');
  process.exit(1);
}
console.log('唯一状态浏览器验收通过');
