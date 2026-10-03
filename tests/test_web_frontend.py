from __future__ import annotations

"""前端安全与交付物绑定的用例 (计划书 §2 F3/F4)。

反向安全测试:
- 页面不得再从 CDN 加载脚本, 也不得使用未消毒的 Markdown 渲染;
- 内联脚本必须已迁出 (严格 CSP 下内联脚本不会执行);
- CSP 必须真实下发, 且不允许内联脚本与外部脚本源;
- 构建产物必须存在并由服务端提供;
- 文件清单必须能按研究问题/运行过滤, 未能归属的文件不得冒充当前问题的交付物。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "src" / "web"
NODE = shutil.which("node")


def _has_build() -> bool:
    return (WEB_DIR / "dist" / "index.html").is_file() and (WEB_DIR / "assets").is_dir()


def test_source_template_has_no_remote_script_and_no_marked():
    """源码模板: 不得有 CDN 脚本, 也不得使用未消毒的 marked。"""
    from src import server

    html = server.INDEX_HTML.read_text(encoding="utf-8-sig")
    assert "jsdelivr" not in html, "页面不得从 CDN 加载脚本"
    assert "unpkg" not in html and "cdn." not in html
    assert "marked.parse" not in html and "marked.min.js" not in html
    # 改为 Vite 入口 (TypeScript)
    assert 'type="module"' in html and "/src/main.ts" in html


def test_source_template_has_no_inline_event_handlers():
    """P2 实测缺陷回归: 严格 CSP 会直接拦掉内联事件处理器。

    浏览器实测报 "Executing inline event handler violates the following Content
    Security Policy directive 'script-src 'self''" —— 页面能加载、`window.AIR` 也在,
    但所有按钮与模式切换全部失效。因此模板不得再用 `on*=` 属性, 事件必须在打包
    模块里用 addEventListener 绑定。
    """
    import re

    from src import server

    html = server.INDEX_HTML.read_text(encoding="utf-8-sig")
    found = re.search(r"\son(?:click|change|keydown|input|submit|focus|blur)=", html)
    assert found is None, f"模板仍有内联事件处理器: {html[found.start():found.start() + 60]!r}"
    app = (WEB_DIR / "src" / "app.ts").read_text(encoding="utf-8")
    for binding in ("on('btn-send', 'click', onSend)", "on('runmode', 'change', onModeChange)",
                    "'data-tab'"):
        assert binding in app, binding
    # 动态生成的 HTML 同样不得带内联处理器 (工作台按钮改用 data-action 委托)
    code = "\n".join(line for line in app.splitlines()
                     if not line.strip().startswith(("*", "/*", "//")))
    generated = re.search(r"onclick=\\?[\"']", code)
    assert generated is None, f"app.ts 仍在生成内联处理器: {code[max(0, generated.start() - 40):generated.start() + 40]!r}"
    assert "data-action=" in code and "bindDelegatedActions" in code


def test_page_logic_moved_out_of_inline_script():
    """F4: 页面逻辑必须迁出内联脚本 (否则严格 CSP 下不执行)。"""
    from src import server

    html = server.INDEX_HTML.read_text(encoding="utf-8-sig")
    assert "<script>" not in html, "不得再有内联脚本"
    assert (WEB_DIR / "src" / "app.ts").is_file()
    assert (WEB_DIR / "src" / "main.ts").is_file()
    assert (WEB_DIR / "src" / "research-api.ts").is_file()
    assert (WEB_DIR / "src" / "current-research.ts").is_file()


def test_markdown_module_uses_dom_api_only():
    """渲染模块只能用 DOM API 构造节点 (不拼接 HTML 字符串)。"""
    source = (WEB_DIR / "src" / "markdown.ts").read_text(encoding="utf-8")
    # 注释里可以提到 innerHTML (解释为什么不用它), 但代码里不得出现赋值
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith(("*", "/*", "//")))
    assert "innerHTML" not in code, "安全渲染器不得给 innerHTML 赋值"
    assert "insertAdjacentHTML" not in code
    assert "document.write" not in code
    assert "textContent" in code and "createElement" in code


def test_frontend_project_config_present():
    """F4: 前端必须由 Vite + TypeScript 管理, 产物布局固定。"""
    pkg = json.loads((WEB_DIR / "package.json").read_text(encoding="utf-8"))
    assert "vite" in pkg["devDependencies"] and "typescript" in pkg["devDependencies"]
    assert pkg["scripts"]["build"].startswith("vite build")
    assert (WEB_DIR / "tsconfig.json").is_file()
    assert (WEB_DIR / "vite.config.ts").is_file()
    cfg = (WEB_DIR / "vite.config.ts").read_text(encoding="utf-8")
    assert "outDir" in cfg and "manifest: true" in cfg


def test_index_response_sets_csp():
    from fastapi.testclient import TestClient

    from src import server

    r = TestClient(server.app).get("/")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("x-content-type-options") == "nosniff"
    # 严格 CSP: 不允许内联脚本
    script_src = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in script_src


@pytest.mark.skipif(not _has_build(), reason="需要先构建前端 (npm run build)")
def test_built_page_is_served_with_hashed_assets():
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "/assets/" in r.text, "已构建页必须引用打包产物"
    import re

    names = re.findall(r"/assets/[A-Za-z0-9_.\-]+\.(?:js|css)", r.text)
    assert names, r.text[:400]
    for name in names:
        asset = client.get(name)
        assert asset.status_code == 200, name
        assert "javascript" in asset.headers.get("content-type", "") or \
            "css" in asset.headers.get("content-type", "")


@pytest.mark.skipif(not _has_build(), reason="需要先构建前端 (npm run build)")
def test_built_page_has_no_inline_script():
    from fastapi.testclient import TestClient

    from src import server

    html = TestClient(server.app).get("/").text
    assert "<script>" not in html
    assert "cdn." not in html and "jsdelivr" not in html


def test_asset_endpoint_blocks_escapes(tmp_path):
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    for bad in ("../server.py", "..%2Fserver.py", "/etc/passwd", "nope.js"):
        r = client.get(f"/assets/{bad}")
        assert r.status_code == 404, bad


def test_missing_asset_dir_404s(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from src import server

    monkeypatch.setattr(server, "WEB_ASSETS", tmp_path)
    assert TestClient(server.app).get("/assets/index-x.js").status_code == 404


def test_index_without_build_returns_actionable_error(monkeypatch, tmp_path):
    """P2: 缺构建产物 → 明确的构建提示 (503), 而不是返回未构建的源码模板。"""
    from fastapi.testclient import TestClient

    from src import server

    template = tmp_path / "index.html"
    template.write_text("<!DOCTYPE html><script src='/src/main.ts'></script>",
                        encoding="utf-8")
    monkeypatch.delenv("AIR_WEB_DEV", raising=False)
    monkeypatch.setattr(server, "WEB_BUILT_INDEX", tmp_path / "missing.html")
    monkeypatch.setattr(server, "INDEX_HTML", template)
    r = TestClient(server.app).get("/")
    assert r.status_code == 503, r.status_code
    assert "npm run build" in r.text
    assert "AIR_WEB_DEV" in r.text
    # 绝不能把引用 /src/main.ts 的源码模板当成页面返回 (错误页文字提及不算)
    assert "<script src='/src/main.ts'></script>" not in r.text
    assert "前端尚未构建" in r.text


def test_index_serves_template_only_in_dev_mode(monkeypatch, tmp_path):
    """开发模式 (vite dev) 才回退源码模板。"""
    from fastapi.testclient import TestClient

    from src import server

    template = tmp_path / "index.html"
    template.write_text("<!DOCTYPE html><p>模板</p>", encoding="utf-8")
    monkeypatch.setenv("AIR_WEB_DEV", "1")
    monkeypatch.setattr(server, "WEB_BUILT_INDEX", tmp_path / "missing.html")
    monkeypatch.setattr(server, "INDEX_HTML", template)
    r = TestClient(server.app).get("/")
    assert r.status_code == 200 and "模板" in r.text


@pytest.mark.skipif(NODE is None or not _has_build(),
                    reason="需要 node 与已构建产物")
def test_bundle_security_script_passes():
    result = subprocess.run(
        [NODE, str(REPO_ROOT / "tests" / "js" / "bundle_security.test.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), check=False)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "全部通过" in (result.stdout or "")


# --------------------------------------------------------------------------
# F3: 文件清单绑定 run / 问题
# --------------------------------------------------------------------------
@pytest.fixture()
def outputs_tree(tmp_path, monkeypatch):
    from src import config

    out = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    # 两个问题各自的交付包 + 一个未归属产物
    for project, problem in (("projA", "p1"), ("projA", "p2"), ("projB", "p1")):
        root = out / "research" / project / f"snap-{problem}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(json.dumps({
            "project_id": project, "problem_id": problem, "snapshot_id": f"snap-{problem}",
            "delivery_level": "研究备忘录", "gate_passed": False,
        }, ensure_ascii=False), encoding="utf-8")
        (root / "manuscript.md").write_text(f"# {project}/{problem}\n", encoding="utf-8")
    (out / "loose.txt").write_text("未归属产物", encoding="utf-8")
    from src import server

    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    return out


def test_artifacts_filtered_by_problem(outputs_tree):
    from fastapi.testclient import TestClient

    from src import server

    client = TestClient(server.app)
    all_files = client.get("/api/artifacts").json()
    names = {f["name"] for f in all_files["files"]}
    assert any("snap-p1" in n for n in names)
    assert any(n == "loose.txt" for n in names)
    loose = next(f for f in all_files["files"] if f["name"] == "loose.txt")
    assert loose["unattributed"] is True, "未归属文件必须显式标注"

    scoped = client.get("/api/artifacts?project_id=projA&problem_id=p2").json()
    scoped_names = {f["name"] for f in scoped["files"]}
    assert scoped_names, scoped
    assert all("snap-p2" in n for n in scoped_names), scoped_names
    assert not any("snap-p1" in n for n in scoped_names), "不得混入别的问题的交付物"
    assert all(f.get("problem_id") == "p2" for f in scoped["files"])
    assert all(f.get("project_id") == "projA" for f in scoped["files"])
    # 只报告被返回文件所属的交付包
    assert all("snap-p2" in k for k in scoped["roots"]), scoped["roots"]


def test_artifacts_owner_metadata(outputs_tree):
    from fastapi.testclient import TestClient

    from src import server

    d = TestClient(server.app).get("/api/artifacts?project_id=projB").json()
    assert d["files"]
    entry = next(f for f in d["files"] if f["name"].endswith("manuscript.md"))
    assert entry["project_id"] == "projB"
    assert entry["problem_id"] == "p1"
    assert entry["delivery_level"] == "研究备忘录"
    assert entry["run_id"] == "snap-p1"
    assert entry["package_dir"] == "research/projB/snap-p1"


def test_artifacts_unknown_problem_returns_empty(outputs_tree):
    from fastapi.testclient import TestClient

    from src import server

    d = TestClient(server.app).get("/api/artifacts?project_id=projA&problem_id=nope").json()
    assert d["files"] == []


def test_package_manifest_records_identity(tmp_path, monkeypatch):
    """交付 manifest 必须记录 problem/run/branch 身份 (R6)。

    权威来源是**快照本身**; `base_dir` 只是目录名, 不能反过来决定 manifest 里的
    运行身份 —— 否则目录名一变, 交付物就归属到另一次运行。
    """
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    from src.research.package import export_package
    from src.research.schemas import ResearchSnapshot, ResearchSpec

    spec = ResearchSpec(project_id="pkg", problem_id="pp", problem_statement="x >= 0")
    snapshot = ResearchSnapshot(project_id="pkg", problem_id="pp", run_id="run-abc",
                               branch_id="route-1")
    root = export_package(snapshot, spec, [], "# 稿", base_dir=tmp_path / "pkg-root")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["problem_id"] == "pp"
    assert manifest["project_id"] == "pkg"
    assert manifest["run_id"] == "run-abc"
    assert manifest["branch_id"] == "route-1"
    assert manifest["snapshot_id"] == snapshot.snapshot_id


def test_package_manifest_falls_back_to_spec_and_run_arg(tmp_path, monkeypatch):
    """快照缺身份 (旧数据) 时按 spec / 显式 run_id 兜底, 仍不改变目录名语义。"""
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    from src.research.package import export_package
    from src.research.schemas import ResearchSnapshot, ResearchSpec

    spec = ResearchSpec(project_id="pkg2", problem_id="pp2", problem_statement="x >= 0")
    snapshot = ResearchSnapshot(project_id="pkg2")
    root = export_package(snapshot, spec, [], "# 稿", base_dir=tmp_path / "dir-name",
                          run_id="run-xyz")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["problem_id"] == "pp2"
    assert manifest["run_id"] == "run-xyz"


def test_frontend_binds_artifacts_and_has_detail_nav():
    """页面逻辑必须绑定文件清单与详情导航, 并支持窄屏/键盘 (F4 已按 views/* 拆分)。

    断言覆盖**全部前端源码**而不是只看 app.ts: 计划书 §4 要求把视图按 `views/*`
    整理, 因此"某个 needle 在哪个文件"不是契约; "整个前端源码里存在该绑定"才是。
    """
    sources = {p.name: p.read_text(encoding="utf-8")
               for p in sorted((WEB_DIR / "src").rglob("*.ts"))}
    assert sources, "找不到前端源码"
    joined = "\n".join(sources.values())
    for needle in ("params.set('problem_id'", "filebinding", "function claimDetailRow",
                   "toggleClaimDetail", "claim-link", "wb-detail-row", "aria-expanded",
                   "function onTabKey", "researchStateUrl"):
        assert needle in joined, needle
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8-sig")
    assert 'role="tab"' in html


def test_styles_are_split_out_of_the_template():
    """计划书 §4: 样式拆到 `src/styles/*`, 模板里不再有内联 `<style>`。

    样式内联在模板里时, 窄屏规则、工作台规则混在同一个 250 行的块里,
    既无法按层组织, 也无法让构建产物带哈希缓存。
    """
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8-sig")
    assert "<style>" not in html, "模板不得再保留内联样式块"
    styles = WEB_DIR / "src" / "styles"
    for name in ("base.css", "workbench.css", "layout.css"):
        assert (styles / name).is_file(), f"缺少样式文件 {name}"

    entry = (WEB_DIR / "src" / "main.ts").read_text(encoding="utf-8")
    for name in ("base.css", "workbench.css", "layout.css"):
        assert f"./styles/{name}" in entry, f"main.ts 必须引入 {name}"

    # 窄屏规则与工作台规则必须真的搬进了样式文件 (而不是被删掉)
    layout_rules = (styles / "base.css").read_text(encoding="utf-8")
    workbench_rules = (styles / "workbench.css").read_text(encoding="utf-8")
    assert "@media (max-width: 1000px)" in layout_rules
    assert ".wb-section" in workbench_rules
    assert ".ctx-badge" in workbench_rules


def test_built_page_serves_hashed_css_without_inline_style():
    """构建产物必须带哈希化 CSS, 页面里没有内联样式。"""
    from src import server

    if not _has_build():
        pytest.skip("尚未构建 (先运行 src/web 的 npm run build)")
    built = server._index_page_path().read_text(encoding="utf-8")
    assert "<style>" not in built, "产物不得内联样式"
    assert 'rel="stylesheet"' in built
    css = [n for n in (WEB_DIR / "assets").glob("*.css")]
    assert css, "assets/ 里必须有被提交的 CSS 产物"
    assert any(f"/assets/{p.name}" in built for p in css), built[:300]


def test_built_assets_has_no_stale_bundles():
    """构建产物只允许保留**当前**包: 旧 hash 文件既不会被引用, 也不该留在仓库里。"""
    if not _has_build():
        pytest.skip("尚未构建 (先运行 src/web 的 npm run build)")
    import json

    manifest = json.loads((WEB_DIR / "dist" / ".vite" / "manifest.json")
                          .read_text(encoding="utf-8"))
    referenced = {(WEB_DIR / "dist" / entry["file"]).name
                  for entry in manifest.values() if entry.get("file")}
    # CSS 由 JS 入口隐式关联: 记下每个 chunk 的 css 列表
    for entry in manifest.values():
        referenced |= {Path(name).name for name in (entry.get("css") or [])}
    assert referenced, manifest
    present = {p.name for p in (WEB_DIR / "assets").glob("*.js")}
    present |= {p.name for p in (WEB_DIR / "assets").glob("*.css")}
    assert present == referenced, f"assets/ 与 manifest 不一致: {present} != {referenced}"
