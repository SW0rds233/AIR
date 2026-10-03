from __future__ import annotations

"""P2 真实浏览器验收 (计划书 §5 发布判据 5)。

用真实 Chromium 打开**构建产物**页面, 走一遍"切模式 → 提问 → 工作台出结论 →
对象级反馈控件 → 窄屏 → 键盘", 并检查没有 JS 报错与 4xx 资源。
需要 node + playwright + 已构建的 dist/; 任一缺失则跳过 (并说明原因)。
"""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "src" / "web"
SCRIPT = WEB_DIR / "tests" / "e2e" / "web_flow.mjs"
FULL_SCRIPT = WEB_DIR / "tests" / "e2e" / "web_flow_full.mjs"
DRAFT_SCRIPT = WEB_DIR / "tests" / "e2e" / "draft_identity.mjs"
UPLOAD_SCRIPT = WEB_DIR / "tests" / "e2e" / "web_upload.mjs"


def _node() -> str | None:
    for name in ("node", "node.exe"):
        try:
            out = subprocess.run([name, "--version"], capture_output=True, text=True,
                                 check=False)
        except FileNotFoundError:
            continue
        if out.returncode == 0:
            return name
    return None


def _playwright_ready() -> bool:
    """node_modules 里有 playwright 且浏览器已下载。"""
    pkg = WEB_DIR / "node_modules" / "playwright"
    if not pkg.exists():
        return False
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return cache.exists() and any(cache.glob("chromium*"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.skipif(_node() is None, reason="需要 node 运行浏览器脚本")
@pytest.mark.skipif(not _playwright_ready(), reason="需要 playwright 与已下载的浏览器")
@pytest.mark.skipif(not (WEB_DIR / "dist" / "index.html").is_file(),
                    reason="需要先构建前端 (cd src/web && npm run build)")
def test_web_flow_in_real_browser(tmp_path, monkeypatch):
    import uvicorn

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("AIR_WEB_DEV", raising=False)

    from src import server

    port = _free_port()
    uv_config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    uv_server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not uv_server.started and time.time() < deadline:
        time.sleep(0.1)
    assert uv_server.started, "uvicorn 未能在 30s 内启动"

    try:
        result = subprocess.run(
            [_node(), str(SCRIPT), f"http://127.0.0.1:{port}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(WEB_DIR), check=False, timeout=300)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, output
        assert "浏览器验收通过" in output, output
    finally:
        uv_server.should_exit = True
        thread.join(timeout=15)
        server.shutdown_sessions()


@pytest.mark.skipif(_node() is None, reason="需要 node 运行浏览器脚本")
@pytest.mark.skipif(not _playwright_ready(), reason="需要 playwright 与已下载的浏览器")
@pytest.mark.skipif(not (WEB_DIR / "dist" / "index.html").is_file(),
                    reason="需要先构建前端 (cd src/web && npm run build)")
def test_full_chain_in_real_browser(tmp_path, monkeypatch):
    """选资料 → 启动研究 → 交付物可见 → 断线重连 (计划书 P2 整链验收)。"""
    import uvicorn

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("AIR_WEB_DEV", raising=False)

    # 合成一份可检索的资料集 (含一页正常正文), 供界面绑定
    from src.kb.ingest import ensure_topic, ingest_manual

    topic = "BROWSERKB"
    topic_dir = ensure_topic(topic)
    import tests.test_visibility_trust as vis

    vis._make_pdf(topic_dir / "manual", [
        ("正常正文: 可分性随信道变化下降", 72, 90, 11, 0x000000),
    ], name="browser-kb.pdf")
    ingest_manual(topic, embed=False)

    from src import server

    port = _free_port()
    uv_server = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                              log_level="warning"))
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not uv_server.started and time.time() < deadline:
        time.sleep(0.1)
    assert uv_server.started

    try:
        result = subprocess.run(
            [_node(), str(FULL_SCRIPT), f"http://127.0.0.1:{port}", topic],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(WEB_DIR), check=False, timeout=420)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, output
        assert "整链浏览器验收通过" in output, output
    finally:
        uv_server.should_exit = True
        thread.join(timeout=15)
        server.shutdown_sessions()


@pytest.mark.skipif(_node() is None, reason="需要 node 运行浏览器脚本")
@pytest.mark.skipif(not _playwright_ready(), reason="需要 playwright 与已下载的浏览器")
@pytest.mark.skipif(not (WEB_DIR / "dist" / "index.html").is_file(),
                    reason="需要先构建前端 (cd src/web && npm run build)")
def test_draft_identity_does_not_probe_workbench(tmp_path, monkeypatch):
    """草稿项目身份不得探测工作台, 但手动填写的真实项目仍要能查 (现场 404 回归)。

    切到理论模式会生成草稿项目 id (R2: 让附件在稳定身份下上传), 它没有研究记录。
    这里用请求拦截确认**没有真的发出** `/api/research/*/state`, 而不是只看界面文案
    —— 后者在"发了但静默吞掉"的实现下会假通过; 同时验证反向保护: 换成**真实存在**
    的项目 id 时必须照常查询 (防 404 噪音不能把手动加载工作台一起堵死)。
    """
    import uvicorn

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("AIR_WEB_DEV", raising=False)

    # 先造一个**真实存在**的项目 (离线跑一小段研究循环即可)
    from src.graph import theory_pipeline

    theory_pipeline.run_theory_pipeline(
        request="对所有实数 x: x**2 >= 0", topic="regression", project_id="draftreg",
        problem_id="p1", max_actions=6, max_tool_calls=6)

    from src import server

    port = _free_port()
    uv_server = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                              log_level="warning"))
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not uv_server.started and time.time() < deadline:
        time.sleep(0.1)
    assert uv_server.started

    try:
        result = subprocess.run(
            [_node(), str(DRAFT_SCRIPT), f"http://127.0.0.1:{port}", "draftreg"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(WEB_DIR), check=False, timeout=180)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, output
        assert "草稿身份回归通过" in output, output
        assert "手动填写的项目 id 仍可查询工作台" in output, output
    finally:
        uv_server.should_exit = True
        thread.join(timeout=15)
        server.shutdown_sessions()


@pytest.mark.skipif(_node() is None, reason="需要 node 运行浏览器脚本")
@pytest.mark.skipif(not _playwright_ready(), reason="需要 playwright 与已下载的浏览器")
@pytest.mark.skipif(not (WEB_DIR / "dist" / "index.html").is_file(),
                    reason="需要先构建前端 (cd src/web && npm run build)")
def test_attachment_upload_entry_in_real_browser(tmp_path, monkeypatch):
    """附件上传入口在真实浏览器里可用, 且附件真的进入研究输入。

    "看起来没有入口" 的根因是入口在**理论模式的折叠高级选项**里, 因此这里显式断言
    综述模式隐藏/理论模式可见、上传后列表出现、以及启动请求携带 `attachment_ids`
    (只显示在列表里不算接上 —— 那正是 R1 的原始缺陷)。
    """
    import uvicorn

    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("AIR_WEB_DEV", raising=False)

    # 合成一份可解析的 PDF 作为上传材料
    import tests.test_visibility_trust as vis

    pdf_dir = tmp_path / "upload_src"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    vis._make_pdf(pdf_dir, [("附件正文: 研究范围限定在低信噪比区间", 72, 90, 11, 0x000000)],
                  name="problem-note.pdf")
    pdf = pdf_dir / "problem-note.pdf"
    assert pdf.is_file()

    from src import server

    port = _free_port()
    uv_server = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                              log_level="warning"))
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not uv_server.started and time.time() < deadline:
        time.sleep(0.1)
    assert uv_server.started

    try:
        result = subprocess.run(
            [_node(), str(UPLOAD_SCRIPT), f"http://127.0.0.1:{port}", str(pdf)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(WEB_DIR), check=False, timeout=240)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, output
        assert "附件上传浏览器验收通过" in output, output
        assert "研究运行结束" in output, output
        # 附件必须真的并入研究输入: 交付包的 input_snapshot 记录附件身份,
        # 请求正文里带附件文本 (两者缺一都不算"接上了")。
        # 导出在 finalize 之后落盘, 界面已显示"完成"时通常已就绪; 留一点重试余量。
        manifests: list[Path] = []
        for _ in range(40):
            manifests = sorted((config.OUTPUT_DIR / "research").rglob("manifest.json"))
            if manifests:
                break
            time.sleep(0.5)
        assert manifests, "本次运行没有导出交付包"
        manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
        snapshot = manifest.get("input_snapshot") or {}
        assert snapshot.get("attachment_ids"), "输入快照里没有记录附件 id"
        # 附件身份必须**逐份可校验**: 只记 id 而没有 sha256 时, 续跑校验对它无能为力
        # (输入快照会声称"一致", 实际无从比对)。这正是本用例要挡住的假可复现。
        records = snapshot.get("attachments") or []
        assert records, "输入快照只记了附件 id, 没有记录 sha256 (无法校验是否变过)"
        assert records[0].get("sha256"), "附件记录缺少 sha256"
        assert "problem-note.pdf" == records[0].get("filename"), records[0]
        # 顶层请求保持"用户说了什么", 附件是**候选要求**分开存 (R4), 不得混入 request
        assert "problem-note.pdf" not in (snapshot.get("request") or ""), \
            "附件文本不得混入顶层 request (R4: 候选要求与用户陈述分开)"
    finally:
        uv_server.should_exit = True
        thread.join(timeout=15)
        server.shutdown_sessions()


def test_favicon_routes_are_served():
    """/favicon.svg 与 /favicon.ico 都必须可访问 (现场每次打开页面刷 404 日志)。"""
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    for path in ("/favicon.svg", "/favicon.ico"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert resp.headers["content-type"].startswith("image/svg+xml")


def test_browser_script_has_no_executed_assertions():
    """静态契约: 浏览器脚本必须真的检查关键界面对象 (防止退化成空跑)。"""
    source = SCRIPT.read_text(encoding="utf-8")
    for needle in ("window.AIR", "#workbench", "#runmode", "#btn-send", "#wbfeedback",
                   "setViewportSize", "pageerror"):
        assert needle in source, needle
    full = FULL_SCRIPT.read_text(encoding="utf-8")
    for needle in ("#sourceset", "#sourceinfo", '[data-tab="files"]', "manifest",
                   "page.route", "stateCalls"):
        assert needle in full, needle
    draft = DRAFT_SCRIPT.read_text(encoding="utf-8")
    # 关键: 断言的是"没有发出请求", 因此脚本必须自己拦截请求并统计
    for needle in ("page.on('request'", "/api/research/", "stateCalls",
                   "link[rel=\"icon\"]"):
        assert needle in draft, needle
    upload = UPLOAD_SCRIPT.read_text(encoding="utf-8")
    for needle in ("setInputFiles", "#attachrow", "#attachkind", "#btn-upload",
                   "attachment_ids", "POST"):
        assert needle in upload, needle
    assert json  # 保持导入有用性检查
