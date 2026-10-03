/**
 * P2 浏览器验收 (三): 附件上传入口。
 *
 *     node tests/e2e/web_upload.mjs http://127.0.0.1:PORT /绝对路径/合成.pdf
 *
 * 验证的诚实边界 (计划书 R1/R2/R3/R7 的现场口径):
 * - 入口只在**理论研究模式**下出现, 且位于折叠的「高级选项」里 —— 这是"看起来没有入口"
 *   的主要原因, 因此脚本显式断言: 综述模式下隐藏、切到理论模式后可见;
 * - 上传成功后必须出现在附件列表, 且被并入**启动请求**的 attachment_ids
 *   (只显示在列表里不算接上: 那正是 R1 的原始缺陷);
 * - 服务端解析状态必须如实显示 (解析失败不得看起来"已成功")。
 */
import { chromium } from 'playwright';

const base = (process.argv[2] || '').replace(/\/$/, '');
const pdfPath = process.argv[3] || '';
if (!base || !pdfPath) {
  console.log('FAIL: 需要服务地址与一个可上传的 PDF 路径');
  process.exit(2);
}

const problems = [];
const steps = [];

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));

  const startPayloads = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().endsWith('/api/sessions')) {
      try {
        startPayloads.push(JSON.parse(req.postData() || '{}'));
      } catch {
        startPayloads.push({});
      }
    }
  });

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });
  await page.click('details.adv > summary');

  // 1) 综述模式下不显示附件行 (理论模式的输入链路才有附件语义)
  const surveyVisible = await page.isVisible('#attachrow');
  if (surveyVisible) problems.push('综述模式下不应显示附件行');
  else steps.push('综述模式下附件行隐藏');

  // 2) 切到理论模式: 附件行出现, 且文件类型/用途控件齐全
  await page.selectOption('#runmode', 'theory');
  await page.waitForSelector('#attachrow', { state: 'visible', timeout: 10000 });
  steps.push('切到理论模式后附件行可见');
  // 入口必须在**主界面**(聊天输入区), 不得又退回折叠的「高级选项」里
  const inAdvanced = await page.evaluate(
    () => Boolean(document.querySelector('#attachrow')?.closest('details.adv')));
  if (inAdvanced) problems.push('附件入口又回到了折叠的高级选项里');
  else steps.push('附件入口位于主界面 (不在高级选项内)');
  for (const sel of ['.attach-pick', '#attachkind', '#btn-upload']) {
    if (!(await page.isVisible(sel))) problems.push(`附件入口缺少控件 ${sel}`);
  }

  // 3) 项目身份必须先落实 (R2: 否则附件会落到与运行无关的目录)
  const pid = (await page.inputValue('#projid')).trim();
  if (!pid) problems.push('理论模式下没有先落实项目 ID (R2)');
  else steps.push(`草稿项目身份已落实: ${pid}`);

  // 3b) 显式固定项目/问题 id 后再上传: 上传与启动必须用**同一个**身份,
  //     否则附件会被归属校验拒绝 (启动时 project_id 变了 -> 附件不生效)。
  await page.fill('#projid', pid || 'browser-upload');
  await page.fill('#probid', 'p1');

  // 4) 选"补充问题说明"并上传
  await page.selectOption('#attachkind', 'problem');
  await page.setInputFiles('#attachfiles', pdfPath);
  await page.click('#btn-upload');
  await page.waitForFunction(
    () => /已登记|已入库|失败/.test(document.querySelector('#log')?.textContent || ''),
    null, { timeout: 60000 },
  );
  const log = (await page.textContent('#log')) || '';
  if (/上传失败|失败（/.test(log)) problems.push(`上传失败: ${log.slice(-200)}`);
  else steps.push('上传返回了逐文件结果');

  // 5) 列表里必须出现该附件, 且解析状态如实显示
  await page.waitForFunction(
    () => /\.pdf/.test(document.querySelector('#attachlist')?.textContent || ''),
    null, { timeout: 30000 },
  ).catch(() => problems.push('上传后附件列表没有出现该文件'));
  const list = (await page.textContent('#attachlist')) || '';
  if (/解析失败/.test(list)) problems.push(`附件解析失败却仍可继续: ${list.slice(0, 160)}`);
  steps.push(`附件列表显示: ${list.replace(/\s+/g, ' ').slice(0, 80)}`);

  // 6) 关键: 附件 id 必须被并入**启动请求** (只显示在列表里不算接上)
  await page.fill('#topic', '对所有实数 x: x**2 >= 0');
  await page.fill('#probid', 'p1');
  await page.click('#btn-send');
  // 等**运行真正结束**, 而不是等结论对象出现: 命题在 bootstrap 阶段就已写入,
  // 交付包要到 finalize 才落盘 —— 只等 clm- 会在交付包写出之前就宣布通过。
  // 判据用运行身份 (session id) 查服务端状态, 不用页面状态文字:
  // 状态文字可能来自上一个会话, 会让"运行结束"变成假成立。
  // 身份要**在启动的响应处理完**之后立刻取: 之后任何一次历史列表刷新都可能清空它
  let sessionId = '';
  for (let i = 0; i < 40 && !sessionId; i++) {
    sessionId = await page.evaluate(() => (window.AIR && window.AIR.research)
      ? window.AIR.research.sessionId : '');
    if (!sessionId) await page.waitForTimeout(250);
  }
  if (!sessionId) problems.push('启动后拿不到会话身份, 无法确认运行是否结束');
  const deadline = Date.now() + 120000;
  let status = '';
  while (sessionId && Date.now() < deadline) {
    const res = await page.request.get(`${base}/api/conversations/${encodeURIComponent(sessionId)}`);
    if (res.ok()) {
      status = String(((await res.json()) || {}).status || '');
      if (status === 'done' || status === 'error' || status === 'stopped') break;
    }
    await page.waitForTimeout(500);
  }
  if (status === 'error') problems.push('研究运行报错结束');
  else if (status !== 'done') problems.push(`研究运行未在 120s 内结束 (状态: ${status || '未知'})`);
  else steps.push('研究运行结束');

  await page.waitForFunction(
    () => /clm-/.test(document.querySelector('#workbench')?.textContent || ''),
    null, { timeout: 30000 },
  ).catch(() => problems.push('启动后工作台没有出现结论对象'));
  const payload = startPayloads.find((p) => (p.attachment_ids || []).length) || null;
  if (!payload) {
    problems.push('启动请求没有携带 attachment_ids (附件没有真正进入研究输入)');
  } else {
    steps.push(`启动请求携带 attachment_ids: ${payload.attachment_ids.length} 个`);
  }
  if (payload && payload.project_id !== pid) {
    problems.push(`启动请求的项目 id (${payload.project_id}) 与上传时不一致 (${pid})`);
  }

  // 7) 反向用例: 上传后改动项目 ID 必须在启动前**如实拦下** (R3 归属校验的用户可见面)。
  //    否则附件会被服务端静默拒绝, 用户以为传了而研究其实没读 —— 这正是本用例要防的坑。
  await page.evaluate(() => { const b = document.querySelector('#btn-send'); if (b) b.click(); });
  await page.waitForTimeout(300);
  await page.fill('#projid', 'another-project');
  await page.click('#btn-send');
  await page.waitForTimeout(1500);
  const log2 = (await page.textContent('#log')) || '';
  if (!/与当前项目/.test(log2)) {
    problems.push('改动项目 ID 后启动没有被拦下 (附件会被静默丢弃)');
  } else {
    steps.push('改动项目 ID 后启动前如实拦下附件归属不一致');
  }

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
  console.log('附件上传浏览器验收未通过');
  process.exit(1);
}
console.log('附件上传浏览器验收通过');






