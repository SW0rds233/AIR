from __future__ import annotations

"""两种研究形态保留的**依据**必须是可核查的, 而不是一句解释。

背景: 仓库里有两支研究引擎 —— 形式化研究 (`graph/theory_pipeline.py` + `research/loop.py`)
与团队会话 (`graph/team_session.py` + `graph/research_graph.py`)。`server._resolve_engine`
的文档字符串写清了为什么这是**有意选择** (判定层必须零 LLM 且是唯一状态写入点, 而
`AgentTask` 的契约是"只提交候选、不做判断")。但光有解释不够: 它可能掩盖真正的漂移。

**共用底座**那部分已经有用例把守, 这里不重复
------------------------------------------------
- `tests/test_reasoning_kernel.py::test_engine_path_and_team_path_share_the_kernel`
  已经证明两条路径调的是同一个内核函数;
- 同文件的 `test_model_proposal_does_not_write_state` 已经证明内核**不写状态**。

**这条判据的边界 (不要高估它)**
--------------------------------
`test_research_state_writes_happen_only_in_the_judgment_layer` 只能在代码**写出**
研究对象的写入口时抓到它 (`ResearchStore.put(` / `.put_object(` / `save_snapshot(`)。
若某个模块拿到的是**注入进来的 store 句柄**、只调 `.put(...)`, 这条判据看不出来 ——
`.put(` 同时也是 `dict.put` / `queue.put` 的名字, 笼统匹配只会产生一堆假阳性 (实测:
一版实现把 `sessions/events.py` 的队列写入和 `server.py` 的字典写入全报了出来, 那种
清单没人会认真看)。因此真正的保证仍来自上面那两条内核用例与本条的组合, 而不是单单这一条。

本文件补的是**尚未有人守**的三件事:
1. 判定层的写入口清单 (谁写研究对象) + 清单自身不过期;
2. 旧撰写路径不得回来 (上一轮删掉的 905 行);
3. 设计说明写在决策点旁边, 否则下一个人看到分支只会以为"这里还没合并完"。
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

#: 允许写研究对象的模块: `{模块: 为什么}`。
#:
#: 注意区分「**装配**时造一个 store 实例并交给引擎」与「**判定后落盘**」——
#: 前者 (server / main / team_api 的入口装配) 不产生研究状态, 所以不在本清单里。
STATE_WRITERS: dict[str, str] = {
    "research/store.py": "对象存储自身的实现",
    "research/task_store.py": "任务台账 (团队侧的任务状态, 不是研究结论)",
    # 冻结快照: 给**已提交**的对象拍一张不可变照片, 不改任何结论状态。收在一处是为了
    # 让"谁在写存储"可枚举 —— 它曾经只在引擎与旧流水线里被调用, 团队运行因此"看起来
    # 冻结了"而对象库里没有快照 (派生接口直接报错)。
    "research/snapshot.py": "冻结已提交对象的快照 (派生/交付共用; 不改结论状态)",
}

#: 明确的"写研究对象"调用。
#:
#: 为什么不用笼统的 `.put(`: 那个名字被 `dict.put` / `queue.put` 之类无害调用占用,
#: 逐个加白名单只会让这条判据越来越像"解释现状"。`ResearchStore.put(` 才是写研究对象的
#: 入口, 而"造一个 ResearchStore 实例" (`ResearchStore(`) 只出现在入口装配处 ——
#: 装配不产生研究状态, 因此不算写入点。
WRITE_CALLS: tuple[str, ...] = (
    "ResearchStore(",
    ".put_object(",
    "save_snapshot(",
)

#: 允许出现上述调用的模块: `{模块: 为什么}`。
#:
#: 装配点 (server / main / team_api) 会**构造** store 实例并交给引擎, 那是接线不是判定。
ASSEMBLY_POINTS: dict[str, str] = {
    "server.py": "会话装配: 构造 store 实例交给引擎",
    "main.py": "CLI 装配",
    "team_api.py": "团队/资料库路由的装配",
    "research/projection.py": "只读投影 (读对象渲染视图)",
}


def _python_files(root: Path):
    return sorted(root.rglob("*.py"))


def _code_only(text: str) -> str:
    """去掉注释与字符串, 只看代码 —— 注释里提到某个名字不算"用它"。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover
        return text
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree)


def test_research_state_writes_happen_only_in_the_judgment_layer():
    """写研究对象的地方必须集中在判定层与入口装配处。"""
    offenders: list[str] = []
    for path in _python_files(SRC):
        relative = str(path.relative_to(SRC)).replace("\\", "/")
        if relative in STATE_WRITERS or relative in ASSEMBLY_POINTS:
            continue
        code = _code_only(path.read_text(encoding="utf-8"))
        for needle in WRITE_CALLS:
            if needle in code:
                offenders.append(f"{relative}: {needle}")
    assert not offenders, (
        "以下模块出现了「写研究对象」的调用, 但它们既不是判定层也不是入口装配。"
        "请确认它不是第二个权威 (若是装配, 加进 ASSEMBLY_POINTS 并写清原因):\n  "
        + "\n  ".join(offenders))


def test_state_writer_allowlist_is_not_stale():
    """登记为"写状态"的模块必须真的还在写 (否则清单会变成过期说明)。"""
    stale: list[str] = []
    for relative, why in STATE_WRITERS.items():
        path = SRC / relative
        if not path.is_file():
            stale.append(f"{relative}: 模块已不存在 ({why})")
            continue
        code = _code_only(path.read_text(encoding="utf-8"))
        if not any(needle in code for needle in WRITE_CALLS):
            stale.append(f"{relative}: 已不再写状态, 应从清单移除 ({why})")
    assert not stale, stale


def test_retired_writing_paths_stay_deleted():
    """上一轮删除的旧撰写路径不得回来 (默认配置下它不产出任何交付物)。"""
    assert not (SRC / "agents" / "paper_writer.py").exists(), "已退役的撰写模块回来了"
    assert not (SRC / "research" / "writing_bridge.py").exists(), "已退役的桥接模块回来了"
    for path in _python_files(SRC):
        code = _code_only(path.read_text(encoding="utf-8"))
        # 只看代码: 注释里解释"这个开关已被删除"是文档, 不是再用它
        assert "THEORY_LONG_FORM" not in code, f"{path.name} 的代码仍引用已删除的开关"
        assert "writing_bridge" not in code, f"{path.name} 的代码仍引用已删除的桥接模块"


def test_single_runtime_has_no_engine_choice():
    """**只有一张图**: 生产代码里不得再有"按 mode 分流"的入口 (G01)。

    这条用例过去断言"为什么保留两种研究形态必须写在 `_resolve_engine` 旁边" ——
    那个"有意保留的分支"已经按计划删除。现在断言的是它的**反面**: 选择点不存在,
    并且没有任何调用点在建第二张图。判据必须扫**代码**而不是注释 (解释"为什么删掉了"
    的注释是文档, 不是分流)。
    """
    import ast

    source = (SRC / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # 1) `_resolve_engine` 不接受任何形参 (一旦接受, 就承认可以按它分流)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_engine":
            assert not node.args.args and not node.args.kwonlyargs, \
                "引擎选择点又出现了形参"
    # 2) 代码里不得出现旧图工厂或按 mode 分流的分支
    code = _code_only(source)
    for needle in ("build_theory_pipeline", 'in ("theory", "team")', "SURVEY_ENGINE"):
        assert needle not in code, f"server.py 的代码仍引用 {needle}"
    # 3) 判定层边界本身没变: 智能体只提交候选, 唯一提交口仍在
    from src.research import commit

    assert hasattr(commit, "ResearchCommitService")


def test_dispatch_stays_conservative_about_formal_requests():
    """形式化请求不得被当成综述型画像 (判据本身是设计说明的一部分)。

    G01 之后分类器**不再决定用哪张图** (只有一张图), 但它仍然决定任务画像与角色
    配置 —— 把证明题当综述, 照样会让团队用错打法。因此保守性继续守住。
    """
    from src.research.intake import is_survey_request
    from src.server import _resolve_engine

    assert _resolve_engine() == "team"
    assert is_survey_request("检索并总结某方向研究进展, 写一篇综述") is True
    assert is_survey_request("证明 2-(211,15,1) 设计不存在") is False
    assert is_survey_request("综述一下 2-(211,15,1) 的设计是否存在的证明") is False
