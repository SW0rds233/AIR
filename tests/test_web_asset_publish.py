from __future__ import annotations

"""前端产物的**发布契约**: 页面引用的每个资源都必须真的在 `assets/` 里。

为什么单独把守这一条
--------------------
这是"看起来部署成功、打开却是坏页面"的典型成因: `dist/index.html` 引用了
`/assets/index-<hash>.js|css`, 而 `assets/` 里那份文件因为发布顺序问题不存在
(先删旧文件、再拷新文件的老实现就会这样; 中途失败也会留下半份产物)。
浏览器只会报一个 404, 用户看到的是"样式没了/按钮全不响应"。

因此这里断言的是**产物之间的一致性**, 与构建工具无关:
1. `dist/index.html` 引用的每个 `/assets/...` 都能在 `assets/` 找到 (非空文件);
2. `assets/` 里不残留本代页面不再引用的 `.js`/`.css` 哈希产物 (死文件会越积越多);
3. 构建配置里**保留**"先写新、后删旧"的顺序 —— 删旧在前就会重新引入上面那个窗口。
"""

import re
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "web"
DIST_INDEX = WEB_DIR / "dist" / "index.html"
ASSETS_DIR = WEB_DIR / "assets"
VITE_CONFIG = WEB_DIR / "vite.config.ts"

# `src="/assets/index-abc.js"` / `href="/assets/index-abc.css"`
_ASSET_REF = re.compile(r"""["']/assets/([^"'?#]+)["']""")


def _referenced_assets() -> set[str]:
    if not DIST_INDEX.is_file():
        pytest.skip("前端未构建 (缺少 src/web/dist/index.html)")
    return set(_ASSET_REF.findall(DIST_INDEX.read_text(encoding="utf-8-sig")))


def test_every_referenced_asset_exists_and_is_not_empty():
    referenced = _referenced_assets()
    assert referenced, "构建产物的 index.html 没有引用任何 /assets/ 文件"
    missing = [name for name in sorted(referenced)
               if not (ASSETS_DIR / name).is_file()]
    assert not missing, f"页面引用了不存在的产物: {missing}"
    empty = [name for name in sorted(referenced)
             if (ASSETS_DIR / name).stat().st_size == 0]
    assert not empty, f"产物存在但为空: {empty}"


def test_no_stale_hashed_assets_left_behind():
    """`assets/` 只应保留本代页面引用的 js/css (外加非哈希资源如 favicon)。"""
    if not ASSETS_DIR.is_dir():
        pytest.skip("前端未构建 (缺少 src/web/assets)")
    referenced = _referenced_assets()
    stale = []
    for path in sorted(ASSETS_DIR.iterdir()):
        name = path.name
        if not path.is_file() or name.startswith("."):
            continue
        if not re.search(r"\.(js|css)$", name):
            continue
        # 只清理由本工具发布的哈希产物: 形如 index-<hash>.js, 未在页面里引用的就是死文件
        if re.match(r"^index-[A-Za-z0-9_-]+\.(js|css)$", name) and name not in referenced:
            stale.append(name)
    assert not stale, f"assets/ 里残留不再被引用的产物: {stale}"


def test_publish_order_is_write_then_prune():
    """发布顺序: 先写新产物, 再删旧产物 (顺序颠倒会重新引入"半份产物"窗口)。"""
    source = VITE_CONFIG.read_text(encoding="utf-8")
    copy_at = source.find("copyFileSync(")
    prune_at = source.find("unlinkSync(join(target, name))")
    assert copy_at != -1 and prune_at != -1, "发布插件被改写: 找不到拷贝/清理步骤"
    assert copy_at < prune_at, "发布顺序错误: 必须先写入新产物, 再清理旧产物"
    # 同代码的判定: 临时文件先落盘再改名, 读者不会看到半个文件
    assert "renameSync(" in source, "缺少「先写临时文件再原子改名」的保护"


def test_committed_assets_cover_the_built_index():
    """落盘提交的 `assets/` 必须能独立满足 `dist/index.html` (部署即用)。"""
    referenced = _referenced_assets()
    for name in sorted(referenced):
        path = ASSETS_DIR / name
        assert path.is_file() and path.stat().st_size > 0, f"部署缺产物: {name}"
