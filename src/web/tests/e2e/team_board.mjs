/**
 * 团队工作台的真实浏览器验收 (合并计划 §9.8)。
 *
 * 覆盖的是"团队视图确实来自后端、不是前端假造":
 * 1. 团队能力来自 `/api/team/roles` (8 个角色, 含不可用原因);
 * 2. 提交一条自然语言任务后, 团队状态/任务表出现在 `#team-section` 里;
 * 3. 任务失败/受阻时**原因可见** (不是空壳);
 * 4. 连接状态与运行状态分开显示 (离线不等于已停止);
 * 5. 团队视图与旧工作台共存在同一容器里, 且旧工作台仍然可用;
 * 6. 窄屏下团队状态条纵向堆叠且仍可读。
 *
 * 用法: node tests/e2e/team_board.mjs http://127.0.0.1:PORT
 */
import { chromium } from 'playwright';

const base = (process.argv[2] || '').replace(/\/$/, '');
if (!base) {
  console.log('FAIL: 缺少服务地址参数');
  process.exit(2);
}

const problems = [];
const steps = [];
const requests = [];

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  page.on('console', (msg) => {
    if (msg.type() === 'error') problems.push(`console: ${msg.text()}`);
  });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));
  page.on('response', (res) => {
    const url = res.url();
    requests.push(`${res.status()} ${url}`);
    if (res.status() >= 400) problems.push(`http ${res.status()} ${url}`);
  });

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });
  steps.push('页面加载');

  // 1) 团队能力: 页面打开即从后端读取 (只读; 不启动任何任务)
  await page.waitForFunction(
    () => typeof window.AIRTeam === 'object' && window.AIRTeam !== null,
    null, { timeout: 30000 },
  ).catch(() => problems.push('迁移期入口 window.AIRTeam 不存在'));
  await page.evaluate(() => window.AIRTeam.mount());
  await page.waitForFunction(
    () => (document.querySelector('#team-section')?.textContent || '').includes('独立审阅'),
    null, { timeout: 30000 },
  ).catch(() => problems.push('团队能力未渲染 (未请求 /api/team/roles 或渲染失败)'));
  const sectionText = (await page.textContent('#team-section')) || '';
  for (const label of ['主控', '检索与证据整理', '问题建模', '推理与结论综合',
    '验证方案', '写作', '配图', '独立审阅']) {
    if (!sectionText.includes(label)) problems.push(`团队视图缺少角色: ${label}`);
  }
  const rolesRequested = requests.some((r) => r.includes('/api/team/roles'));
  if (!rolesRequested) problems.push('前端没有请求 /api/team/roles (团队能力是假造的)');
  steps.push('团队角色与真实能力来自后端');

  // 2) 提交一条自然语言任务 (统一入口: 界面上**没有**模式选择器)
  await page.click('details.adv > summary');
  await page.fill('#topic', '判断参数为 2-(211,15,1) 的设计是否存在');
  await page.fill('#projid', 'team-e2e');
  await page.fill('#probid', 'p1');
  await page.click('#btn-send');

  // 3) 团队区块出现任务 (至少一行)
  await page.waitForFunction(
    () => /任务|待命|执行中|完成|失败|等待/.test(
      document.querySelector('#team-section')?.textContent || ''),
    null, { timeout: 90000 },
  ).catch(() => problems.push('团队区块没有出现任务状态'));
  steps.push('团队区块渲染任务状态');

  // 4) 受阻/失败原因可见 (没有资料库时检索必然受阻, 界面必须如实显示原因)
  const reasonSeen = await page.waitForFunction(
    () => /资料范围不可用|检索未执行|原因:|失败|受阻|未知状态/.test(
      document.querySelector('#team-section')?.textContent || ''),
    null, { timeout: 90000 },
  ).then(() => true).catch(() => false);
  if (!reasonSeen) {
    const dump = ((await page.textContent('#team-section')) || '').slice(0, 800);
    problems.push(`团队视图没有显示任何受阻/失败原因; 实际内容: ${dump}`);
  }
  steps.push('阻碍原因在界面上可见');

  // 5) 主控视角与连接状态分开显示
  const afterRun = (await page.textContent('#team-section')) || '';
  if (!/主控视角/.test(afterRun)) problems.push('缺少"主控视角"区块');
  if (!/(已连接|连接中|离线|重连中)/.test(afterRun)) {
    problems.push('没有显示连接状态 (连接状态必须与运行状态分开)');
  }
  // 任务投影接口确实被请求过 —— **显式**请求 (把身份交给团队视图)。
  // 为什么不能只靠"启动一次研究"来触发: 当前会话引擎仍是旧理论图, 它派发的是
  // 引擎动作而不是 `AgentTask`, 因此没有团队任务可投影 (§15 已记为待办:
  // 把团队图接成会话引擎)。这里的判据是"视图能按身份向后端取投影", 不是
  // "普通研究会自动产生团队任务" —— 后者未被实现前不得写成通过。
  //
  // 用启动**前**的空身份请求: 会话仍在跑时用同一个 project 再开一个研究库连接会
  // 撞上 SQLite 写锁 (实测 "database is locked"), 那是测试自己制造的竞争。
  requests.length = 0;
  await page.evaluate(() => window.AIRTeam.mount('', ''));
  await page.waitForTimeout(600);
  const projectionRequested = requests.some((r) => /\/api\/team\/[^/]+\/[^/]+/.test(r)
    && !r.includes('/roles'));
  if (!projectionRequested) problems.push('前端没有请求团队任务投影接口');
  steps.push('主控视角与连接状态可见');

  // 6) 旧工作台仍可用, 且团队区块在它前面
  const order = await page.evaluate(() => {
    const host = document.querySelector('#workbench');
    if (!host) return null;
    const children = Array.from(host.children).map((c) => c.id || c.className);
    return children;
  });
  if (!order || !order.includes('team-section')) {
    problems.push('团队区块不在工作台容器里');
  } else if (order.indexOf('team-section') !== 0) {
    problems.push(`团队区块应在最前, 实际顺序: ${order.join(' > ')}`);
  } else {
    steps.push('团队视图与旧工作台共存, 团队在前');
  }

  // 7) 窄屏: 团队状态条纵向堆叠且仍可读
  await page.setViewportSize({ width: 420, height: 900 });
  const direction = await page.evaluate(() => {
    const bar = document.querySelector('.team-statusbar');
    return bar ? getComputedStyle(bar).flexDirection : '';
  });
  if (direction !== 'column') {
    problems.push(`窄屏下团队状态条未堆叠: ${direction}`);
  }
  const readable = await page.evaluate(() => {
    const bar = document.querySelector('.team-statusbar');
    const host = document.querySelector('#workbench');
    if (!bar || !host) return false;
    return bar.getBoundingClientRect().width <= host.getBoundingClientRect().width + 4;
  });
  if (!readable) problems.push('窄屏下团队状态条溢出主要布局');
  steps.push('窄屏团队状态可读');

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
  console.log('团队工作台浏览器验收未通过');
  process.exit(1);
}
console.log('团队工作台浏览器验收通过');
