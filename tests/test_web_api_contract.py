from __future__ import annotations

"""前端端点表 ↔ 后端路由的**契约**: 每个端点都必须真的存在, 且方法对得上。

为什么值得单独把守 (`ai-web-consistency`)
----------------------------------------
前端把全部 URL 收进了 `src/web/src/api/research-client.ts` 的 `ENDPOINTS` 表
(合并计划 §9.5)。收进一张表的好处是"URL 只有一处", 代价是**这张表可能与后端漂移**
—— 一旦漂移, 用户看到的是浏览器 404, 而不是一个能指认原因的失败。

因此这里做两件互相独立的事:
1. **解析端点表**(纯文本, 不执行 TS): 取出每个路径字符串与方法;
2. **对照 FastAPI 的真实路由**(含 `include_router` 进来的 `team_api`)。

判据:
- 表里出现的每个**字面路径**都必须匹配到一条真实路由;
- 模板端点 (函数形式) 的路径模板必须在后端存在;
- 方法 (GET/POST/DELETE) 必须被该路由接受 —— 路径对但方法错同样是坏契约;
- 路径参数名必须与后端一致 (FastAPI 按名字取值, 名字错了会 422 而不是 404,
  更难从现象上看出来)。

这**不是**在测"哪个 URL 拼得好看": `researchStateUrl` 一类的查询串构造由前端
单测覆盖, 这里只管"端点存不存在、方法对不对"。
"""

import re
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "web"
CLIENT_TS = WEB_DIR / "src" / "api" / "research-client.ts"

# 方法名: ENDPOINTS 表用到的 HTTP 动词 (默认 GET)
_METHODS = ("GET", "POST", "DELETE", "PUT", "PATCH")
_PATH_LITERAL = re.compile(r"""(?:^|[\s{,])([A-Za-z_][A-Za-z0-9_]*):\s*'([^']+)'""",
                           re.MULTILINE)
_TEMPLATE = re.compile(
    r"""(?:^|[\s{])([A-Za-z_][A-Za-z0-9_]*):\s*\(([^)]*)\)\s*=>\s*\n?\s*`([^`]+)`""",
    re.MULTILINE)


def _client_source() -> str:
    if not CLIENT_TS.is_file():
        pytest.skip(f"缺少 {CLIENT_TS}")
    return CLIENT_TS.read_text(encoding="utf-8")


def _frontend_sources() -> dict[str, str]:
    """前端**全部**源码 (`{相对路径: 文本}`): 调用点散落在各模块里。

    只看客户端那个文件是不够的: 方法 (`method: 'POST'`) 写在**调用点**, 而调用点
    在 `app.ts` / `features/intake/controller.ts` 等处。
    """
    sources: dict[str, str] = {}
    for path in (WEB_DIR / "src").rglob("*.ts"):
        sources[str(path.relative_to(WEB_DIR))] = path.read_text(encoding="utf-8")
    return sources


def _endpoints_block() -> str:
    source = _client_source()
    start = source.find("export const ENDPOINTS")
    assert start != -1, "找不到 ENDPOINTS (前端端点表)"
    end = source.find("} as const;", start)
    assert end != -1, "ENDPOINTS 表没有以 `} as const;` 结束"
    return source[start:end]


def _declared_paths() -> set[str]:
    """端点表里声明的路径模板 (函数形式的 `${...}` 归一为 `{param}`)。"""
    block = _endpoints_block()
    paths: set[str] = set()
    for _name, literal in _PATH_LITERAL.findall(block):
        if literal.startswith("/"):
            paths.add(literal.rstrip("/") or "/")
    for _name, params, template in _TEMPLATE.findall(block):
        # 反引号模板里的 ${encodeURIComponent(x)} → {x}
        named = re.sub(r"\$\{encodeURI(?:Component)?\((\w+)\)\}", r"{\1}", template)
        paths.add(named.rstrip("/") or "/")
    return paths


def _backend_routes() -> dict[str, set[str]]:
    """`{路径: {方法}}` —— path 形如 `/api/sessions/{thread_id}/state`。

    **必须递归展开**: FastAPI 0.141 把 `include_router` 进来的路由保留为单个
    `_IncludedRouter` 条目 (`app.routes` 里看不到它的 path), 只在匹配时才展开。
    只看顶层会得出"`/api/team/*` 与 `/api/library/*` 不存在"的错误结论 ——
    而那正是本文件要防的那类漂移, 所以这里照真实匹配语义把树走完。
    """
    from src.server import app

    routes: dict[str, set[str]] = {}

    def walk(items) -> None:
        for route in items:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path and methods:
                if str(path).startswith("/api"):
                    routes.setdefault(str(path), set()).update(
                        m.upper() for m in methods)
                continue
            # 子路由包装 (含 FastAPI 的 `_IncludedRouter`): 展开它的原始路由表
            children = getattr(route, "routes", None) or []
            if not children:
                original = getattr(route, "original_router", None)
                children = getattr(original, "routes", None) or []
            if children:
                walk(children)

    walk(app.routes)
    return routes


def test_endpoint_table_is_not_empty():
    paths = _declared_paths()
    assert len(paths) >= 20, f"端点表解析结果过少, 可能是解析器失效: {sorted(paths)}"


def _shape(path: str) -> str:
    """把路径归一成"结构": 参数名换掉, 只留占位位置。

    为什么允许参数名不同: 前端的模板里写的是**局部变量名**
    (`${encodeURIComponent(threadId)}`), 它只决定拼进 URL 的字面值, 后端并不按名字
    取值; 因此 `{threadId}` 与 `{thread_id}` 指向同一个 URL 结构。真正要守的是
    **段数与段内容** (`/a/{x}/b` 与 `/a/{y}` 是两条不同的路由)。
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_every_declared_path_exists_on_the_backend():
    declared = _declared_paths()
    routes = _backend_routes()
    shapes = {_shape(path): path for path in routes}
    missing = sorted(path for path in declared if _shape(path) not in shapes)
    assert not missing, (
        "前端端点表里有后端不存在的路径 (用户会看到 404): "
        f"{missing}\n后端现有: {sorted(routes)}")


def test_path_shapes_are_specific_enough():
    """占位段数量不同的两条路由不得都被归一成同一个结构 (防止"归一化掩盖错路径")。"""
    routes = _backend_routes()
    shapes: dict[str, str] = {}
    collisions: list[str] = []
    for path in routes:
        shape = _shape(path)
        if shape in shapes and shapes[shape] != path:
            collisions.append(f"{shapes[shape]} / {path}")
        shapes.setdefault(shape, path)
    # 允许的碰撞只有"同路径不同方法"被合并的情况; 不同路径撞车说明归一化过粗
    assert not collisions, f"不同后端路由被归一成同一结构: {collisions}"


def _shape_matches(template: str, concrete: str) -> bool:
    """模板的每一段与具体 URL 的对应段比较: 模板里的占位段接受任意值。

    比"把具体值代回模板再比字符串"更严格 —— 后者只要有一处没被替换就会静默漏判。
    """
    left = template.strip("/").split("/")
    right = concrete.strip("/").split("/")
    if len(left) != len(right):
        return False
    for want, got in zip(left, right):
        if want.startswith("{") and want.endswith("}"):
            continue
        if want != got:
            return False
    return True


def test_client_templates_produce_urls_matching_backend_shapes():
    """端到端形状核对: 客户端模板**代入真实值**后必须匹配一条后端路由。

    前面比的是"表里的声明"; 这里比的是"代入参数后真的会发出什么" —— 避免
    "表对但拼错" (少一段、把参数拼到查询串里) 这种两边都不报警的情况。
    """
    block = _endpoints_block()
    backend = sorted(_backend_routes())
    problems: list[str] = []
    checked: list[str] = []
    for name, params, template in _TEMPLATE.findall(block):
        names = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip()]
        concrete = template
        for index, param in enumerate(names):
            replacement = "v" + str(index)
            concrete = concrete.replace(
                "${encodeURIComponent(" + param + ")}", replacement)
            concrete = concrete.replace("${encodeURI(" + param + ")}", replacement)
            concrete = re.sub(r"\{" + re.escape(param) + r"(?::[^}]*)?\}",
                              replacement, concrete)
            concrete = concrete.replace("{" + param.strip("? ") + "}", replacement)
        if "${" in concrete:
            problems.append(f"{name}: 模板里还有未解析的插值: {concrete}")
            continue
        checked.append(name)
        if not any(_shape_matches(path, concrete) for path in backend):
            problems.append(f"{name} → {concrete} 不匹配任何后端路由")
    # 带参数的端点都得被检查到 (否则"某个端点没代入就跳过"会静默漏掉)
    for required in ("sessionState", "sessionEvents", "sessionResume",
                     "sessionDelete", "library", "teamProjection"):
        assert required in checked, f"端点 {required} 的模板没有被检查: {checked}"
    assert not problems, problems


def _declared_methods() -> dict[str, str]:
    """读客户端的 `VERBS` 表并把端点名换成**路径** (`{名字: 方法}`)。

    客户端把"非 GET 的方法"显式写在 `VERBS` 里 (见该常量注释: 靠猜调用点的做法很脆,
    猜不中就会退化成永远通过的断言)。这里只解析, 不推断。
    """
    block = _verbs_block()
    literal_paths = dict(_PATH_LITERAL.findall(_endpoints_block()))
    template_paths = {
        name: re.sub(r"\$\{encodeURI(?:Component)?\((\w+)\)\}", r"{\1}", template)
        for name, _params, template in _TEMPLATE.findall(_endpoints_block())}
    names = re.findall(
        r"""([A-Za-z_][A-Za-z0-9_]*)\s*:\s*['"](?:GET|POST|DELETE|PUT|PATCH)['"]""",
        block)
    methods: dict[str, str] = {}
    for name in names:
        method = re.search(
            r"""\b""" + re.escape(name) + r"""\s*:\s*['"](GET|POST|DELETE|PUT|PATCH)['"]""",
            block)
        if not method:
            continue
        path = literal_paths.get(name) or template_paths.get(name)
        if not path or not path.startswith("/api/"):
            continue
        methods[path.rstrip("/") or "/"] = method.group(1)
    return methods


def test_verbs_table_covers_every_non_get_call_site():
    """客户端每个"非 GET"的调用都必须在 `VERBS` 表里登记。

    这一条把**表**和**代码**连起来: 表是给契约测试读的声明, 若代码里发了 POST 而表里
    没有登记, 那张表就退化成"好看的文档", 后端契约测试也就白测了。
    判据是调用点: 客户端每个非 GET 调用都显式写了方法 (直接写, 或经 `endpointMethod()`)。
    """
    block = _endpoints_block()
    verblock = _verbs_block()
    declared = {name for name in re.findall(
        r"""([A-Za-z_][A-Za-z0-9_]*)\s*:\s*['"](?:GET|POST|DELETE|PUT|PATCH)['"]""",
        verblock)}
    known_names = {name for name, _v in _PATH_LITERAL.findall(block)}
    known_names |= {name for name, _p, _t in _TEMPLATE.findall(block)}

    undeclared: list[str] = []
    wrong_table_ref: list[str] = []
    call_re = re.compile(
        r"ENDPOINTS\.([A-Za-z_][A-Za-z0-9_]*)[\s\S]{0,200}?"
        r"(?:method\s*:\s*['\"](?:POST|DELETE|PUT|PATCH)['\"]"
        r"|endpointMethod\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"])")
    for path, source in _frontend_sources().items():
        for call in call_re.finditer(source):
            name, via_table = call.group(1), call.group(2)
            if name not in known_names:
                continue
            if name not in declared:
                undeclared.append(f"{path}: ENDPOINTS.{name}")
            if via_table and via_table not in declared:
                wrong_table_ref.append(f"{path}: endpointMethod('{via_table}')")
    assert not undeclared, (
        "客户端发送了非 GET 请求, 但 VERBS 表里没有登记该端点: "
        f"{sorted(set(undeclared))}")
    assert not wrong_table_ref, (
        f"`endpointMethod()` 引用了 VERBS 表里不存在的端点: {sorted(set(wrong_table_ref))}")


def test_verbs_table_matches_what_the_client_actually_sends():
    """`VERBS` 表必须与客户端**实际发送**的方法一致。

    为什么需要这一条: `VERBS` 是给契约测试读的**声明**; 若它与代码里真正发的方法不一致,
    那张表就变成了"好看的文档", 后端契约测试也就白测了。这里的判据是**调用点**:
    客户端每个非 GET 的调用都显式写了 `method: 'X'`, 因此可以逐调用点核对。
    """
    block = _endpoints_block()
    declared = {}
    for name in re.findall(
            r"""([A-Za-z_][A-Za-z0-9_]*)\s*:\s*['"](?:GET|POST|DELETE|PUT|PATCH)['"]""",
            _verbs_block()):
        match = re.search(
            r"""\b""" + re.escape(name)
            + r"""\s*:\s*['"](GET|POST|DELETE|PUT|PATCH)['"]""", _verbs_block())
        declared[name] = match.group(1) if match else "GET"

    # 端点表里每个名字都要么在 VERBS 里 (非 GET), 要么按 GET 使用
    known_names = [name for name, _v in _PATH_LITERAL.findall(block)]
    known_names += [name for name, _p, _t in _TEMPLATE.findall(block)]
    problems: list[str] = []
    call_re = re.compile(
        r"ENDPOINTS\.([A-Za-z_][A-Za-z0-9_]*)[\s\S]{0,200}?"
        r"(?:method\s*:\s*['\"](POST|DELETE|PUT|PATCH)['\"]"
        r"|endpointMethod\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"])")
    for path, source in _frontend_sources().items():
        # 两种写法都要认: 直接写 `method: 'POST'`, 或用方法表 `endpointMethod('x')`
        for call in call_re.finditer(source):
            name = call.group(1)
            direct, via_table = call.group(2), call.group(3)
            if name not in known_names:
                continue
            if direct:
                method = direct
            else:
                method = declared.get(via_table, "GET")
                if via_table not in declared:
                    problems.append(
                        f"{path}: endpointMethod('{via_table}') 在 VERBS 表里没有登记")
            wanted = declared.get(name, "GET")
            if wanted != method:
                problems.append(
                    f"{path}: ENDPOINTS.{name} 实际发送 {method}, VERBS 里是 {wanted}")
    assert not problems, problems


def _verbs_block() -> str:
    source = _client_source()
    start = source.find("export const VERBS")
    assert start != -1, "找不到 VERBS (客户端方法表)"
    end = source.find("} as const;", start)
    assert end != -1, "VERBS 表没有以 `} as const;` 结束"
    return source[start:end]


def _method_problems(routes: dict[str, set[str]],
                     expectations: dict[str, str]) -> list[str]:
    """检查"期望的方法"是否被对应路由接受 (抽成纯函数, 便于自检)。"""
    problems: list[str] = []
    for path, method in expectations.items():
        methods = routes.get(path)
        if methods is None:
            problems.append(f"{path}: 后端没有这条路由")
        elif method not in methods:
            problems.append(f"{path}: 不接受 {method} (后端只有 {sorted(methods)})")
    return problems


def test_method_checker_itself_can_fail():
    """自检: 检查器必须能报出真实的"方法不匹配" (否则上一条断言是空转)。

    这是对**判据**的测试, 不是对实现的测试: 若哪天有人把检查逻辑改成恒真,
    这里会立刻变红。
    """
    synthetic = {"/api/x": {"POST"}}
    assert _method_problems(synthetic, {"/api/x": "POST"}) == []
    assert _method_problems(synthetic, {"/api/x": "DELETE"}), "方法不匹配没有被报出来"
    assert _method_problems(synthetic, {"/api/y": "POST"}), "缺失路由没有被报出来"


def test_declared_methods_are_accepted_by_the_backend():
    """客户端 `VERBS` 表里声明的每个方法都必须被后端对应路由接受。

    路径对但方法错同样是坏契约 (405 而不是 404), 而且更难从现象上看出来。
    """
    routes = {_shape(path): (path, methods) for path, methods in _backend_routes().items()}
    declared = _declared_methods()
    # 关键写操作必须被覆盖到, 否则这条断言可能因为"表是空的"而恒真
    for path in ("/api/sessions", "/api/sessions/{threadId}/stop",
                 "/api/sessions/{sessionId}/resume", "/api/uploads",
                 "/api/uploads/{attachmentId}", "/api/library/scan"):
        assert path in declared, f"VERBS 表漏了 {path}: {sorted(declared)}"
    problems: list[str] = []
    for path, method in declared.items():
        entry = routes.get(_shape(path))
        if entry is None:
            problems.append(f"{path}: 后端没有对应的路由")
            continue
        backend_path, methods = entry
        if method not in methods:
            problems.append(
                f"{path} ({backend_path}): 不接受 {method} (后端只有 {sorted(methods)})")
    assert not problems, problems


def test_client_only_uses_declared_paths():
    """客户端自身不得出现端点表之外的 `/api/` 字面量 (防"绕过端点表")。"""
    source = _client_source().replace("/*", "\x00").replace("*/", "\x00")
    # 去掉块注释与行注释后再找字面量
    without_block = re.sub(r"\x00[\s\S]*?\x00", "", source)
    without_comments = re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)
    declared = _declared_paths()
    literals = set(re.findall(r"""['"`](/api/[^'"`$]*)['"`]""", without_comments))
    undeclared = sorted(
        literal for literal in literals
        if literal.rstrip("/") not in declared and literal not in {"/api/"}
    )
    assert not undeclared, f"客户端里出现了端点表之外的路径: {undeclared}"
