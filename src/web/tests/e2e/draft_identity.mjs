/**
 * 回归: 草稿项目身份不得探测工作台 (F2/R2 实测缺陷)。
 *
 * 现场症状 (用户报告):
 *   GET /favicon.svg                      -> 404
 *   GET /api/research/proj-<draft>/state  -> 404 (连续三条)
 *
 * 成因: 切到理论研究模式时页面会生成一个**草稿**项目 id
 * (`proj-` + 时间戳/随机串, 目的是让附件在稳定身份下上传), 它没有任何研究记录。
 * 若此时还带着上一次会话的 `runId` 去调 `/api/research/{草稿}/state`, 后端只能回
 * 404 —— 界面上冒出"读取工作台失败 (404)", 服务端日志被刷满噪音。
 *
 * 判定方式: 用请求拦截记录**是否真的发出过**工作台查询。只断言界面文案不够 ——
 * 那样在"根本没发请求"和"发了但静默吞掉"两种实现下都会假通过。
 *
 *     node tests/e2e/draft_identity.mjs http://127.0.0.1:PORT [真实存在的项目id]
 *
 * 第三个参数可选: 给了就再验证**反向保护** —— 手动输入一个真实存在的项目 id 时
 * 必须照常发起查询 (即"防 404 噪音"不能把手动加载工作台这条路一起堵死)。
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

  const stateCalls = [];
  const badResponses = [];
  page.on('request', (req) => {
    const url = req.url();
    if (url.includes('/api/research/') && url.includes('/state')) stateCalls.push(url);
  });
  page.on('response', (res) => {
    if (res.status() >= 400) badResponses.push(`${res.status()} ${res.url()}`);
  });
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));

  await page.goto(`${base}/`, { waitUntil: 'networkidle' });

  // 1) 统一入口: 页面打开即落实草稿身份 (不再需要"先切到理论模式")
  await page.waitForTimeout(300);

  const draft = ((await page.inputValue('#projid')) || '').trim();
  if (!draft) problems.push('页面打开后没有生成草稿项目 ID');
  else if (!draft.startsWith('proj-')) problems.push(`草稿项目 ID 形态异常: ${draft}`);
  else steps.push(`草稿身份已生成: ${draft}`);

  // 2) 关键断言: 不得向草稿身份发起工作台查询
  if (stateCalls.length) {
    problems.push(`向没有研究记录的项目发起了工作台查询: ${stateCalls.join(', ')}`);
  } else {
    steps.push('未对草稿身份发起 /api/research/*/state');
  }

  // 3) 界面必须说明"为什么是空的", 而不是"读取工作台失败"
  const wb = (await page.textContent('#workbench')) || '';
  if (/读取工作台失败/.test(wb)) problems.push('界面显示"读取工作台失败"而不是空态说明');
  if (!/还没有研究记录/.test(wb)) problems.push(`工作台缺少空态说明: ${wb.slice(0, 120)}`);
  else steps.push('工作台给出"还没有研究记录"的空态说明');

  // 4) 页面里的图标引用必须都能取到 (不要 404)
  const iconHref = await page.getAttribute('link[rel="icon"]', 'href').catch(() => null);
  if (iconHref) {
    const resp = await page.request.get(`${base}${iconHref}`);
    if (resp.status() !== 200) problems.push(`图标 ${iconHref} 返回 ${resp.status()}`);
    else steps.push(`图标可访问: ${iconHref}`);
  } else {
    problems.push('页面没有声明图标 (link rel=icon)');
  }

  // 5) 真正的 4xx (除已知的会话探测) 都算问题
  const unexpected = badResponses.filter((r) => !r.includes('/api/sessions/'));
  if (unexpected.length) problems.push(`出现 4xx/5xx: ${unexpected.join('; ')}`);
  else steps.push('无意外 4xx/5xx');

  // 6) 反向保护: 换成**手动输入的项目 id** 后必须照常查询 (别把这条路堵死)
  if (process.argv[3]) {
    stateCalls.length = 0;
    // 项目/问题字段在「高级选项」里 (折叠的 details): 必须先展开才能填写。
    // 统一入口后页面不再自动展开它, 因此脚本显式打开。
    await page.click('details.adv > summary');
    await page.fill('#projid', process.argv[3]);
    await page.fill('#probid', 'p1');
    await page.evaluate(() => {
      const btn = document.createElement('button');
      btn.setAttribute('data-action', 'refreshWorkbench');
      document.body.appendChild(btn);
      btn.click();
    });
    await page.waitForTimeout(2500);
    if (!stateCalls.length) {
      problems.push('手动填写的项目 id 没有触发工作台查询 (手动加载路径被堵死)');
    } else {
      steps.push('手动填写的项目 id 仍可查询工作台');
    }
    const wb2 = (await page.textContent('#workbench')) || '';
    if (/还没有研究记录/.test(wb2)) {
      problems.push(`真实存在的项目被判为"没有研究记录": ${wb2.slice(0, 80)}`);
    } else if (/读取工作台失败/.test(wb2)) {
      problems.push(`真实存在的项目读取失败: ${wb2.slice(0, 80)}`);
    } else {
      steps.push('真实存在的项目能加载出工作台内容');
    }
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
  console.log('草稿身份回归未通过');
  process.exit(1);
}
console.log('草稿身份回归通过');
