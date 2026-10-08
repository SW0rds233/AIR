"""Built frontend smoke test in a real browser when Chromium is available."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "web"


def test_built_frontend_loads_without_script_or_research_404(tmp_path, monkeypatch):
    if not shutil.which("node") or not (WEB / "node_modules" / "playwright").is_dir():
        pytest.skip("需要 Node.js 与 Playwright")
    if not (WEB / "dist" / "index.html").is_file():
        pytest.skip("需要先构建 src/web/dist")
    browser_check = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {chromium} from 'playwright'; import {existsSync} from 'node:fs';"
         "process.exit(existsSync(chromium.executablePath()) ? 0 : 1)"],
        cwd=WEB, capture_output=True, text=True, timeout=10, check=False)
    if browser_check.returncode:
        pytest.skip("Playwright 未安装 Chromium 浏览器")

    import uvicorn

    from src import config

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    monkeypatch.setattr(server, "DATA_DIR", config.DATA_DIR)
    monkeypatch.setattr(server, "OUTPUT_DIR", config.OUTPUT_DIR)
    monkeypatch.setattr(server, "CHECKPOINT_DIR", config.DATA_DIR / "checkpoints")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                       log_level="error"))
    worker = threading.Thread(target=app.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 20
    try:
        while not app.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert app.started, "本地 HTTP 服务未启动"
        script = """
import { chromium } from 'playwright';
let browser;
try { browser = await chromium.launch({headless: true}); }
catch (error) { console.error('BROWSER_UNAVAILABLE', error.message); process.exit(3); }
try {
  const page = await browser.newPage();
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => {
    if (response.url().includes('/api/research/') && response.status() >= 400)
      failures.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(process.argv[1], {waitUntil: 'networkidle'});
  if (!(await page.title()).includes('AIR智能体研究系统')) throw new Error('页面标题错误');
  await page.locator('#reply').fill('证明一个数学命题');
  if (!(await page.locator('#btn-send').isVisible())) throw new Error('启动按钮不可见');
  await page.waitForTimeout(300);
  if (failures.length) throw new Error(failures.join('\\n'));
} finally { await browser.close(); }
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, f"http://127.0.0.1:{port}/"],
            cwd=WEB, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False)
        if result.returncode == 3 and "BROWSER_UNAVAILABLE" in result.stderr:
            pytest.skip("Playwright 未安装 Chromium 浏览器")
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        app.should_exit = True
        worker.join(timeout=10)
        server.shutdown_sessions()
