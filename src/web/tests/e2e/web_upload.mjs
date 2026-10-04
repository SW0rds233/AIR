/**
 * P2 浏览器验收 (三): 附件上传入口。
 *
 *     node tests/e2e/web_upload.mjs http://127.0.0.1:PORT /绝对路径/合成.pdf
 *
 * 验证的诚实边界 (计划书 R1/R2/R3/R7 的现场口径):
 * - 入口**始终可见**且位于主界面 (统一入口后不再按"综述/理论模式"隐藏 ——
 *   那套模式选择器已删除), 因此脚本断言: 未选择任何模式时附件行就可见, 且不在
 *   折叠的「高级选项」里;
 * - 上传成功后必须出现在附件列表, 且被并入**启动请求**的 attachment_ids
 *   (只显示在列表里不算接上: 那正是 R1 的原始缺陷);
 * - 启动请求**不得携带 mode** (统一入口契约);
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

  // 1) 统一入口: 打开页面附件行就**可见** (不需要先选模式)
  if (!(await page.isVisible('#attachrow'))) {
    problems.push('附件行应始终可见 (统一入口下不再按模式隐藏)');
  } else {
    steps.push('附件行始终可见 (无需选择模式)');
  }

  // 2) 文件类型/用途控件齐全, 且入口在**主界面**(聊天输入区),
  //    不得又退回折叠的「高级选项」里
  const inAdvanced = await page.evaluate(
    () => Boolean(document.querySelector('#attachrow')?.closest('details.adv')));
  if (inAdvanced) problems.push('附件入口又回到了折叠的高级选项里');
  else steps.push('附件入口位于主界面 (不在高级选项内)');
  for (const sel of ['.attach-pick', '#attachkind', '#btn-upload']) {
    if (!(await page.isVisible(sel))) problems.push(`附件入口缺少控件 ${sel}`);
  }

  // 3) 项目身份必须先落实 (R2: 否则附件会落到与运行无关的目录)
  const pid = (await page.inputValue('#projid')).trim();
  if (!pid) problems.push('页面打开后没有先落实项目 ID (R2)');
  else steps.push(`草稿项目身份已落实: ${pid}`);

  // 3b) 显式固定项目/问题 id 后再上传: 上传与启动必须用**同一个**身份,
  //     否则附件会被归属校验拒绝 (启动时 project_id 变了 -> 附件不生效)。
  //     项目/问题字段在「高级选项」里 (折叠的 details): 统一入口后页面不再自动展开,
  //     因此这里显式打开再填写。
  await page.click('details.adv > summary');
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
  // 统一入口契约: 启动请求**不得**带 mode (由服务端决定引擎)
  const anyMode = startPayloads.find((p) => 'mode' in p);
  if (anyMode) problems.push('启动请求仍携带 mode (统一入口要求前端不再发送该字段)');
  else steps.push('启动请求未携带 mode (统一入口)');

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






