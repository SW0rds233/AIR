from __future__ import annotations

"""G18 / P1 回归: **唯一图表产物服务** (合并计划 §3.3 G18)。

锁四件事:

1. 图的落盘目录按 `(run, task, 产物版本)` 分层 —— 两次运行、以及同一次运行里的
   两张图都不会互相覆盖 (修复前共用 `OUTPUT_DIR/figures`, 按 `kind-purpose` 命名);
2. 校验 (来源 / 单位 / 模型域 / 权限) 在**渲染之前**生效: 不通过就**不落盘**, 并
   把结构化失败交回主控 ("不支持/绘制失败可见", 而不是记录问题后照样画);
3. `input_versions` 记录本图由哪个对象的哪个版本产出 (修复前恒为空);
4. 公式只走白名单解析 + 受限执行: `__import__('os').system(...)` / `open(...)`
   这类任意 Python 在解析阶段被拒绝, **不会被执行** (修复前经 `parse_expr` 执行)。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.agents.figures import (
    FigureAgent,
    figure_input_versions,
    figure_source_index,
    render_figure_spec,
    validate_figure_spec,
)
from src.agents.protocol import AgentTask, ContextPack, grant_for
from src.agents.runtime import AgentRuntime, InProcessExecutor
from src.rag.figure_formula import FormulaRejected, parse_formula
from src.rag.figure_generator import figure_artifact_dir, figure_scope_bound


# ----------------------------------------------------------------------
# 素材
# ----------------------------------------------------------------------
def _task(**overrides) -> AgentTask:
    base = {"agent": "figures", "objective": "画出读取误差随样本量的变化",
            "project_id": "proj-g18", "problem_id": "prob-g18", "run_id": "run-A"}
    base.update(overrides)
    return AgentTask(**base)


def _dataset_card(**overrides) -> dict:
    card = {
        "id": "ds-1",
        "version": 3,
        "name": "示波器实测读数",
        "columns": [{"name": "样本量", "unit": "个"}, {"name": "读取误差", "unit": "mV"}],
        "series": [
            {"label": "设备A", "values": [0.9, 1.4, 1.1, 1.7, 1.2, 1.5]},
            {"label": "设备B", "values": [1.2, 1.1, 1.6, 1.3, 1.8, 1.4]},
            {"label": "设备C", "values": [0.7, 1.0, 0.9, 1.3, 1.1, 1.2]},
        ],
        "x_unit": "个",
        "y_unit": "mV",
    }
    card.update(overrides)
    return card


def _model_card(**overrides) -> dict:
    card = {
        "id": "model-1",
        "version": 2,
        "name": "读取误差模型",
        "mechanism": "误差随样本量按幂律下降",
        "equations": ["1/x + 0.2"],
        "variables": [{"symbol": "x", "meaning": "样本量", "unit": "个"},
                      {"symbol": "y", "meaning": "读取误差", "unit": "mV"}],
    }
    card.update(overrides)
    return card


def _spec(**overrides) -> dict:
    spec = {
        "purpose": "展示读取误差随样本量的变化",
        "kind": "bar",
        "data_source": "ds-1",
        "x": "样本量",
        "y": "读取误差",
        "x_unit": "个",
        "y_unit": "mV",
        "series": [{"label": "设备A", "values": [0.9]},
                   {"label": "设备B", "values": [1.2]},
                   {"label": "设备C", "values": [0.7]}],
        "caption": "读取误差对比 (数据来源: 示波器实测)",
    }
    spec.update(overrides)
    return spec


def _context(objects: dict, grant=None) -> ContextPack:
    return ContextPack(task_id="task-g18", agent="figures", objects=objects,
                       grant=grant, request="比较各设备的读取误差")


def _render(spec: dict, *, task: AgentTask, objects: dict,
            version: int = 1) -> dict:
    context = _context(objects, grant=grant_for(task))
    with figure_scope_bound(task.run_id, task_id=task.task_id,
                            artifact_version=version):
        return render_figure_spec(spec, context=context,
                                  sources=figure_source_index(context),
                                  grant=context.grant)


def _png_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.png")) if root.exists() else []


# ----------------------------------------------------------------------
# (a) 两次运行 / 同一次运行的两张图都不得互相覆盖
# ----------------------------------------------------------------------
def test_two_runs_get_separate_figure_directories():
    task_a, task_b = _task(run_id="run-A"), _task(run_id="run-B")
    outcomes = [
        _render(_spec(), task=task_a, objects={"dataset": [_dataset_card()]}),
        _render(_spec(), task=task_b, objects={"dataset": [_dataset_card()]}),
    ]
    assert all(o["ok"] is True for o in outcomes), outcomes
    first, second = Path(outcomes[0]["path"]), Path(outcomes[1]["path"])
    assert first != second
    assert first.exists() and second.exists()

    out = Path(config.OUTPUT_DIR)
    assert first.parent == out / "figures" / "run-A" / task_a.task_id / "v1"
    assert second.parent == out / "figures" / "run-B" / task_b.task_id / "v1"
    # 归属于本次运行 + 本次任务 (而不是共用目录)
    assert outcomes[0]["scope"] == {"run_id": "run-A", "task_id": task_a.task_id,
                                    "artifact_version": "1"}


def test_two_artifacts_of_one_run_do_not_overwrite_each_other():
    task = _task(run_id="run-A")
    objects = {"dataset": [_dataset_card()]}
    first = _render(_spec(), task=task, objects=objects, version=1)
    second = _render(_spec(series=[{"label": "设备D", "values": [2.5]},
                                   {"label": "设备E", "values": [3.1]}],
                           caption="另一批设备的读取误差"),
                     task=task, objects=objects, version=2)
    assert first["ok"] is True and second["ok"] is True
    assert Path(first["path"]) != Path(second["path"])
    assert Path(first["path"]).exists() and Path(second["path"]).exists()
    out = Path(config.OUTPUT_DIR)
    assert Path(first["path"]).parent == out / "figures" / "run-A" / task.task_id / "v1"
    assert Path(second["path"]).parent == out / "figures" / "run-A" / task.task_id / "v2"
    # 产物名带内容摘要: 同一次运行里两张 bar 图也不会同名
    assert Path(first["path"]).name != Path(second["path"]).name
    assert first["sha256"] != second["sha256"]


def test_figure_artifact_dir_requires_task_and_version_identity():
    """没有任务/版本身份时必须**拒绝**, 而不是退回共用目录 (退回就会互相覆盖)。"""
    directory, reason = figure_artifact_dir(scope=None, output_dir=None)
    assert directory is None
    assert "任务身份" in reason

    from src.rag.figure_generator import FigureScope

    directory, reason = figure_artifact_dir(
        scope=FigureScope(run_id="run-A", task_id="task-1"), output_dir=None)
    assert directory is None
    assert "产物版本" in reason


def test_render_refuses_to_write_without_bound_identity():
    """未绑定归属就直接渲染 → 可见失败 (status=no_write_permission), 不写文件。"""
    task = _task()
    context = _context({"dataset": [_dataset_card()]}, grant=grant_for(task))
    outcome = render_figure_spec(_spec(), context=context,
                                 sources=figure_source_index(context),
                                 grant=context.grant)
    assert outcome["ok"] is False
    assert outcome["status"] == "no_write_permission"
    assert outcome["problems"]
    assert _png_files(Path(config.OUTPUT_DIR)) == []


# ----------------------------------------------------------------------
# (b) 校验问题阻止渲染并如实上报
# ----------------------------------------------------------------------
def test_unregistered_source_prevents_rendering():
    task = _task()
    context = _context({"dataset": [_dataset_card()]}, grant=grant_for(task))
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(_spec(data_source="ds-unknown"),
                                     context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is False
    assert outcome["status"] == "rejected"
    assert any("来源" in p for p in outcome["problems"]), outcome["problems"]
    # 校验先于建目录: 被拒的图连产物目录都不会创建
    assert _png_files(Path(config.OUTPUT_DIR)) == []
    assert not (Path(config.OUTPUT_DIR) / "figures").exists()


def test_missing_units_prevents_rendering():
    task = _task()
    context = _context({"dataset": [_dataset_card()]}, grant=grant_for(task))
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(_spec(x_unit="", y_unit=""), context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is False
    assert sum("单位" in p for p in outcome["problems"]) == 2, outcome["problems"]
    assert _png_files(Path(config.OUTPUT_DIR)) == []


def test_missing_or_foreign_permission_prevents_rendering():
    """权限先校验: 没有令牌时拒绝渲染 (即使其余字段都合规)。"""
    task = _task()
    context = _context({"dataset": [_dataset_card()]})
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(_spec(), context=context,
                                     sources=figure_source_index(context), grant=None)
    assert outcome["ok"] is False
    assert any("令牌" in p for p in outcome["problems"])
    assert _png_files(Path(config.OUTPUT_DIR)) == []


def test_agent_reports_rejected_figure_instead_of_rendering():
    """服务层拒绝 → 角色**不提交**图候选, 而是把结构化的未渲染原因交回主控。"""
    task = _task(run_id="run-Z")
    # 数据卡既没有数值系列, 也没有单位: 定量图不该画
    card = _dataset_card(series=[], x_unit="", y_unit="")
    context = _context({"dataset": [card]})
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(FigureAgent())

    result = executor.execute(task, context)

    assert result.outcome.value == "partial"
    assert result.payload["rejected"], result.payload
    assert result.payload["figures"] == []
    assert result.proposed_changes == []
    assert result.unresolved and any("未渲染" in item for item in result.unresolved)
    rejected = result.payload["rejected"][0]
    assert rejected["status"] == "rejected"
    assert any("数值系列" in p for p in rejected["problems"])
    assert any("单位" in p for p in rejected["problems"])
    # "图被拒"必须可见 (事件), 且盘上没有任何图
    assert any(e["kind"] == "figure_rejected" for e in runtime.events)
    assert _png_files(Path(config.OUTPUT_DIR)) == []


def test_unknown_kind_is_a_visible_failure():
    task = _task()
    context = _context({"dataset": [_dataset_card()]}, grant=grant_for(task))
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(_spec(kind="pie"), context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is False
    assert any("未登记的图种" in p for p in outcome["problems"])
    assert _png_files(Path(config.OUTPUT_DIR)) == []


# ----------------------------------------------------------------------
# (c) input_versions 非空: 图必须记住自己依据的对象版本
# ----------------------------------------------------------------------
def test_rendered_figure_records_input_versions():
    task = _task()
    objects = {"dataset": [_dataset_card()]}
    context = _context(objects, grant=grant_for(task))
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(_spec(), context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is True, outcome
    assert outcome["input_versions"] == {"ds-1": 3}
    assert figure_input_versions(_spec(), {"ds-1": 3}) == {"ds-1": 3}


def test_agent_proposal_carries_input_versions_and_real_artifact():
    task = _task(run_id="run-Q")
    context = _context({"dataset": [_dataset_card()]})
    executor = InProcessExecutor(AgentRuntime())
    executor.register(FigureAgent())

    result = executor.execute(task, context)

    assert result.outcome.value == "completed", (result.outcome, result.summary,
                                                 result.unresolved)
    proposal = result.proposed_changes[0]
    assert proposal.kind == "figure"
    assert proposal.input_versions == {"ds-1": 3}
    # 角色把 run/task 身份传到了产物路径上 (uri 相对产物根)
    render = proposal.payload["render"]
    assert render["ok"] is True
    assert render["uri"].startswith(f"figures/run-Q/{task.task_id}/v1/"), render["uri"]
    artifact_path = Path(config.OUTPUT_DIR) / render["uri"]
    assert artifact_path.exists()
    # 产物引用指向真实文件, 且不是绝对路径
    assert result.artifact_refs and result.artifact_refs[0].uri == render["uri"]
    assert result.artifact_refs[0].sha256 == render["sha256"]


def test_agent_renders_curve_from_model_card_with_declared_units():
    """模型卡声明了单位与符号 → 曲线图可以渲染; 公式在白名单解析下求值。"""
    task = _task(run_id="run-M")
    context = _context({"model": [_model_card()]})
    executor = InProcessExecutor(AgentRuntime())
    executor.register(FigureAgent())

    result = executor.execute(task, context)

    assert result.outcome.value == "completed", (result.summary, result.unresolved)
    proposal = result.proposed_changes[0]
    assert proposal.payload["kind"] == "curve"
    assert proposal.input_versions == {"model-1": 2}
    artifact = Path(config.OUTPUT_DIR) / proposal.payload["render"]["uri"]
    assert artifact.exists()
    assert artifact.parent.name == "v1"


# ----------------------------------------------------------------------
# (d) 任意 Python 被显式拒绝且不会被执行
# ----------------------------------------------------------------------
@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo x')",
    "__import__('os').remove('x')",
    "open('x', 'w')",
    "os.system('echo x')",
    "eval('1+1')",
    "lambda: 1",
    "[x for x in range(3)]",
    "x.__class__",
])
def test_formula_whitelist_rejects_arbitrary_python(expression):
    with pytest.raises(FormulaRejected):
        parse_formula(expression)


def test_formula_whitelist_accepts_plain_math():
    formula = parse_formula("y = sin(x) + x^2", variables=("x", "y"))
    assert formula.variables == ("x",)
    assert abs(formula.evaluate({"x": 0.0})) < 1e-12
    assert formula.series("x", low=-1.0, high=1.0, points=3)[0] == [-1.0, 0.0, 1.0]


def test_curve_with_arbitrary_python_is_rejected_without_executing_it(tmp_path):
    """方程里夹带任意 Python → 校验拒绝 (不渲染); **副作用文件不得出现**。

    修复前这条方程会被送进 `sympy.parse_expr` (内部 `eval`), `open(...)` 会真的
    执行并创建文件; 因此这条用例在修复前会失败。
    """
    task = _task()
    probe = (tmp_path / "g18_should_not_exist.txt").as_posix()
    objects = {"model": [_model_card()]}
    context = _context(objects, grant=grant_for(task))
    spec = {
        "purpose": "展示模型的函数形式", "kind": "curve", "data_source": "model-1",
        "equations": [f"open(r'{probe}', 'w')",
                      "__import__('os').system('echo x')"],
        "caption": "读取误差模型", "x_unit": "个", "y_unit": "mV",
    }
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(spec, context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is False
    assert outcome["status"] == "rejected"
    assert sum("未被支持" in p for p in outcome["problems"]) == 2, outcome["problems"]
    assert not Path(probe).exists(), "公式里的任意 Python 被执行了"
    assert _png_files(Path(config.OUTPUT_DIR)) == []


def test_curve_outside_model_domain_is_rejected():
    task = _task()
    objects = {"model": [_model_card()]}
    context = _context(objects, grant=grant_for(task))
    spec = {"purpose": "展示模型的函数形式", "kind": "curve", "data_source": "model-1",
            "equations": ["q**2"], "caption": "读取误差模型",
            "x_unit": "个", "y_unit": "mV"}
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(spec, context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is False
    assert any("模型域外的符号" in p and "q" in p for p in outcome["problems"]), \
        outcome["problems"]
    assert _png_files(Path(config.OUTPUT_DIR)) == []


def test_curve_renders_through_whitelist_parser_not_sympy_parse_expr(monkeypatch):
    """曲线必须走白名单解析: 渲染全程不得调用 `sympy.parse_expr`。"""
    import sympy.parsing.sympy_parser as sympy_parser

    def _boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("曲线渲染仍在调用 sympy.parse_expr")

    monkeypatch.setattr(sympy_parser, "parse_expr", _boom)
    task = _task()
    objects = {"model": [_model_card()]}
    context = _context(objects, grant=grant_for(task))
    spec = {"purpose": "展示模型的函数形式", "kind": "curve", "data_source": "model-1",
            "equations": ["1/x + 0.2"], "caption": "读取误差模型",
            "x_unit": "个", "y_unit": "mV"}
    with figure_scope_bound(task.run_id, task_id=task.task_id, artifact_version=1):
        outcome = render_figure_spec(spec, context=context,
                                     sources=figure_source_index(context),
                                     grant=context.grant)
    assert outcome["ok"] is True, outcome
    assert Path(outcome["path"]).exists()


def test_free_script_execution_path_is_gone():
    """`figure_llm` / `figures` 不得再有任何"接受源码字符串并执行"的入口 (G18)。"""
    import ast
    import inspect

    import src.agents.figures as figures_module
    from src.rag import figure_generator, figure_llm

    for module in (figure_llm, figure_generator):
        for name in ("_execute_code", "generate_figure_with_llm", "execute_code",
                     "run_python"):
            assert not hasattr(module, name), f"{module.__name__}.{name} 仍在"
    # 结构检查: 没有任何函数把源码字符串当参数收下
    for module in (figure_llm, figure_generator, figures_module):
        for name, func in vars(module).items():
            if not inspect.isfunction(func):
                continue
            params = set(inspect.signature(func).parameters)
            assert not (params & {"code", "script", "source_code"}), \
                f"{module.__name__}.{name} 仍接受源码字符串"
    source = inspect.getsource(figure_llm)
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "subprocess" not in imported, "figure_llm 仍在起子进程执行绘图脚本"
    assert "matplotlib" not in imported, "figure_llm 仍在执行绘图脚本"


def test_validation_reports_every_required_category():
    """校验覆盖 来源/模型域/单位/权限 四类 (缺一即视为缺口未闭合)。"""
    context = _context({"model": [_model_card()]})
    spec = {"purpose": "", "kind": "curve", "data_source": "model-1",
            "equations": ["1/x"], "caption": ""}
    problems = validate_figure_spec(spec, context=context,
                                    sources=figure_source_index(context), grant=None)
    text = " | ".join(problems)
    assert "用途" in text
    assert "图注" in text
    assert "单位" in text
    assert "令牌" in text
