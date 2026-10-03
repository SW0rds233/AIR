from __future__ import annotations

"""测试隔离: 全部数据目录重定向到每个测试自己的临时目录。

计划书 §5「发布前的最小回归门槛」要求测试与真实运行可区分; 早期套件会把
`data/research/`、`data/kb/`、`data/conversations/` 写进仓库, 让"清理工作区"
与"测试是否污染"变成两件事。这里在**每个测试**开始前统一把 `DATA_DIR` 与
`OUTPUT_DIR` 指向 `tmp_path`, 并对已经 `from src.config import DATA_DIR`
的模块逐个改写为同一个临时目录 —— 该写法在导入时绑定了路径, 只改 config
对它们无效。
"""

from pathlib import Path

import pytest

# 在导入期绑定 DATA_DIR / OUTPUT_DIR 的模块 (只改 config 对它们无效)
_DATA_BOUND_MODULES = (
    "src.utils.conversation_store",
    "src.utils.session_memory",
    "src.utils.pipeline_cache",
    "src.rag.manual_pdfs",
    "src.tools.venue_resolver",
    "src.experiments",
    # 下面三个此前漏了, 于是测试**真的**往仓库写东西:
    # - src.server: `CHECKPOINT_DIR = DATA_DIR / "checkpoints"` 在导入期求值,
    #   综述/工作台用例的每个会话都会在 data/checkpoints/ 留下 SQLite;
    # - src.rag.figure_llm: `FIGURE_DIR` 在导入期求值, 图表用例把测试图写进
    #   outputs/figures/ (test_*.png);
    # - src.tools.pdf_fetcher: `PDF_DIR` / `_OA_CACHE_FILE` 同样在导入期求值。
    "src.server",
    "src.rag.figure_llm",
    "src.tools.pdf_fetcher",
)


@pytest.fixture(autouse=True)
def _isolate_data_dirs(tmp_path, monkeypatch):
    from src import config

    # 先记下"仓库里"的真实根目录: 下面要按这个前缀做重映射, 改完 config 就取不到了
    real_data = Path(config.DATA_DIR).resolve()
    real_out = Path(config.OUTPUT_DIR).resolve()

    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(config, "OUTPUT_DIR", out_dir, raising=False)

    import importlib

    def _remap(module) -> None:
        """把模块里**已经算好**的仓库路径常量重指到临时目录。

        只改 `DATA_DIR` 对 `CHECKPOINT_DIR = DATA_DIR / "checkpoints"` 这类
        模块级常量无效 —— 它们在导入期就求值完了, 于是测试会写回仓库
        (表现为 data/checkpoints/ 与 outputs/figures/ 里冒出测试残留)。
        这里按"是否落在原 DATA_DIR / OUTPUT_DIR 之下"判断, 不逐个硬编码常量名,
        新增同类常量也会被覆盖。
        """
        for name, value in list(vars(module).items()):
            if not isinstance(value, Path):
                continue
            try:
                resolved = value.resolve()
            except OSError:  # noqa: PERF203 - 路径不可解析时跳过
                continue
            for root, target in ((real_data, data_dir), (real_out, out_dir)):
                try:
                    rel = resolved.relative_to(root)
                except ValueError:
                    continue
                monkeypatch.setattr(module, name, target / rel, raising=False)
                break

    for name in _DATA_BOUND_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - 可选模块导入失败不影响隔离
            continue
        _remap(module)

    # 已经用 DATA_DIR 算好的路径常量同样要跟着走, 否则数据仍写回仓库
    from src.utils import conversation_store, session_memory

    monkeypatch.setattr(conversation_store, "CONVERSATIONS_DIR",
                        data_dir / "conversations", raising=False)
    if hasattr(session_memory, "MEMORY_FILE"):
        monkeypatch.setattr(session_memory, "MEMORY_FILE",
                            data_dir / "session_memory.json", raising=False)

    # 测试默认不启用"模型提议器": 正式入口的提议器会**真实调用 LLM API**,
    # 让离线测试变慢且产生费用。R1 的验收测试自行注入假 LLM 并显式打开。
    monkeypatch.setenv("THEORY_PROPOSER", "0")

    # 理论流水线默认**离线**: 只关提议器还不够 —— 引擎仍会拿到真实 LLM 并用它推导
    # 步骤, 于是同一条代数验收题会因为模型每次给的步骤不同而时通时不通 (实测约占
    # 1/5), 且真的花钱。离线后规则层 6/6 稳定通过, 单次从 ~50s 降到 ~1.5s。
    monkeypatch.setenv("THEORY_LLM", "0")

    # 研究报告库的路径函数直接改写: 后台图线程可能在 fixture 之外创建 store
    from src.research import store as research_store

    real_default_db_path = research_store.default_db_path
    isolated_root = data_dir.resolve()
    real_init = research_store.ResearchStore.__init__

    def _isolated_default_db_path(project_id):
        return data_dir / "research" / f"{project_id}.sqlite"

    def _guarded_init(self, project_id, db_path=None, **kwargs):
        """后台图线程可能在夹具恢复之后才创建 store: 那时把仓库路径改回隔离目录。

        计划书 §5 的回归门槛要求测试不写回工作区; 图线程生命周期长于测试,
        因此这里在 store 构造处兜底, 而不是指望线程一定先结束。
        """
        try:
            candidate = Path(db_path) if db_path else _isolated_default_db_path(project_id)
            resolved = candidate.resolve()
            resolved.relative_to(isolated_root)
        except ValueError:
            candidate = _isolated_default_db_path(project_id)
        except Exception:  # noqa: BLE001 - 路径异常时退回隔离目录
            candidate = _isolated_default_db_path(project_id)
        return real_init(self, project_id, candidate, **kwargs)

    monkeypatch.setattr(research_store, "default_db_path", _isolated_default_db_path,
                        raising=False)
    monkeypatch.setattr(research_store.ResearchStore, "__init__", _guarded_init,
                        raising=False)
    # 兜底之后仍需原函数引用, 避免被垃圾回收影响闭包
    del real_default_db_path
    yield
