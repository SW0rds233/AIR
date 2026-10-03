from __future__ import annotations

"""受限统计/因果估计适配器 (因果影响分析).

仅做**描述性与设计内**的效应估计, 不把估计结果当作因果证明:
- mean_difference: 处理组/对照组均值差 (Welch 标准误, 正态近似 95% CI);
- difference_in_differences: 2x2 DiD 估计 + CI;
- describe: 分组描述统计。

结论始终限定在数据来源、人群/地区/时间与设计假设之内; 缺少数据时返回 unsupported。
仅依赖 numpy (随 chromadb/matplotlib 一并安装)。
"""

import math
from pathlib import Path

from src.verification.schemas import VerificationResult, VerificationStatus

_Z = 1.959963984540054  # 95% 正态分位


def _result(status, detail="", values=None, certificate="", raw="") -> VerificationResult:
    return VerificationResult(tool="stats", tool_version=_version(), status=status,
                              detail=detail, values=values or {}, certificate=certificate,
                              raw_output=raw)


def _version() -> str:
    try:
        import numpy

        return f"numpy-{numpy.__version__}"
    except Exception:  # noqa: BLE001
        return ""


def _authorized_roots() -> list[Path]:
    """允许读取数据文件的根目录 (计划书 §6.3 / §9.4)。

    实现已收敛到 `kb.adapters.readonly_data`: 授权目录、越界判定与只读打开
    只有一份实现, 避免"统计适配器"和"数据源适配器"两套规则漂移。
    """
    from src.kb.adapters.readonly_data import authorized_roots

    return authorized_roots()


def _resolve_data_ref(data_ref: str) -> tuple[Path | None, str]:
    """把不可信 data_ref 解析为授权根目录内的真实路径; 越界返回 (None, 原因)。"""
    from src.kb.adapters.readonly_data import resolve_readonly_path

    return resolve_readonly_path(data_ref)


def _load_rows(arguments: dict) -> tuple[list[dict], str]:
    """读取数据行: 内联 rows 优先, 否则按授权路径读 CSV 或 SQLite 单表。"""
    rows = arguments.get("rows")
    if rows:
        return list(rows), ""
    data_ref = arguments.get("data_ref")
    if not data_ref:
        return [], "缺少数据 (rows 或 data_ref)"
    from src.kb.adapters.readonly_data import load_rows

    items, reason, _meta = load_rows(str(data_ref), table=str(arguments.get("table", "")),
                                     where=str(arguments.get("where", "")))
    return items, reason


def _floats(rows: list[dict], col: str, *, group_col: str = "", group_val: str = "") -> list[float]:
    out: list[float] = []
    for row in rows:
        if group_col and str(row.get(group_col, "")).strip() != str(group_val):
            continue
        try:
            out.append(float(row.get(col)))
        except (TypeError, ValueError):
            continue
    return out


def _mean_ci(values: list[float]) -> tuple[float, float, float, float]:
    import numpy as np

    if not values:
        return (float("nan"), float("nan"), 0.0, float("nan"))
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    n = len(arr)
    if n < 2:
        return (mean, float("nan"), 0.0, float("nan"))
    var = float(arr.var(ddof=1))
    se = math.sqrt(var / n)
    return (mean, se, var, n)


def op_describe(arguments: dict) -> VerificationResult:
    rows, err = _load_rows(arguments)
    if err:
        return _result(VerificationStatus.unsupported, err)
    group_col = arguments.get("group_col", "")
    outcome_col = arguments.get("outcome_col", "")
    if not outcome_col:
        return _result(VerificationStatus.unsupported, "缺少 outcome_col")
    groups: dict[str, int] = {}
    for row in rows:
        key = str(row.get(group_col, "all")) if group_col else "all"
        groups[key] = groups.get(key, 0) + 1
    return _result(VerificationStatus.passed, "描述统计完成",
                   values={"n": len(rows), "groups": groups})


def op_mean_difference(arguments: dict) -> VerificationResult:
    rows, err = _load_rows(arguments)
    if err:
        return _result(VerificationStatus.unsupported, err)
    group_col = arguments.get("group_col", "")
    outcome_col = arguments.get("outcome_col", "")
    treated = arguments.get("treated_label", "")
    control = arguments.get("control_label", "")
    if not (group_col and outcome_col and treated and control):
        return _result(VerificationStatus.unsupported, "缺少 group_col/outcome_col/标签")
    t_vals = _floats(rows, outcome_col, group_col=group_col, group_val=treated)
    c_vals = _floats(rows, outcome_col, group_col=group_col, group_val=control)
    if len(t_vals) < 2 or len(c_vals) < 2:
        return _result(VerificationStatus.unsupported, "任一组样本量不足 (<2)")
    tm, tv, _, tn = _mean_ci(t_vals)
    cm, cv, _, cn = _mean_ci(c_vals)
    est = tm - cm
    se = math.sqrt(tv / tn + cv / cn)
    lo, hi = est - _Z * se, est + _Z * se
    ci_excludes_zero = lo > 0 or hi < 0
    values = {
        "design": "mean_difference", "estimate": est, "se": se,
        "ci_low": lo, "ci_high": hi, "n_treated": tn, "n_control": cn,
        "mean_treated": tm, "mean_control": cm, "ci_excludes_zero": ci_excludes_zero,
        "unit": arguments.get("unit", ""),
    }
    certificate = f"均值差 = {est:.4g} (95% CI [{lo:.4g}, {hi:.4g}])"
    return _result(VerificationStatus.passed, "均值差估计完成", values=values,
                   certificate=certificate)


def op_difference_in_differences(arguments: dict) -> VerificationResult:
    rows, err = _load_rows(arguments)
    if err:
        return _result(VerificationStatus.unsupported, err)
    group_col = arguments.get("group_col", "")
    time_col = arguments.get("time_col", "")
    outcome_col = arguments.get("outcome_col", "")
    treated = arguments.get("treated_label", "")
    control = arguments.get("control_label", "")
    pre = arguments.get("pre_label", "")
    post = arguments.get("post_label", "")
    if not (group_col and time_col and outcome_col and treated and control and pre and post):
        return _result(VerificationStatus.unsupported, "DiD 缺少列名或前后/分组标签")
    cells: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        g = str(row.get(group_col, "")).strip()
        t = str(row.get(time_col, "")).strip()
        if g in (treated, control) and t in (pre, post):
            try:
                cells.setdefault((g, t), []).append(float(row.get(outcome_col)))
            except (TypeError, ValueError):
                continue
    needed = [(treated, pre), (treated, post), (control, pre), (control, post)]
    if any(len(cells.get(k, [])) < 2 for k in needed):
        return _result(VerificationStatus.unsupported, "DiD 任一格样本量不足 (<2)")
    stats = {k: _mean_ci(cells[k]) for k in needed}
    t_pre, t_post = stats[(treated, pre)][0], stats[(treated, post)][0]
    c_pre, c_post = stats[(control, pre)][0], stats[(control, post)][0]
    est = (t_post - t_pre) - (c_post - c_pre)
    se = math.sqrt(sum(stats[k][1] ** 2 for k in needed))
    lo, hi = est - _Z * se, est + _Z * se
    values = {
        "design": "difference_in_differences", "estimate": est, "se": se,
        "ci_low": lo, "ci_high": hi,
        "treated_pre": t_pre, "treated_post": t_post,
        "control_pre": c_pre, "control_post": c_post,
        "ci_excludes_zero": lo > 0 or hi < 0,
        "unit": arguments.get("unit", ""),
    }
    certificate = f"DiD = {est:.4g} (95% CI [{lo:.4g}, {hi:.4g}])"
    return _result(VerificationStatus.passed, "DiD 估计完成", values=values, certificate=certificate)


def op_sensitivity(arguments: dict) -> VerificationResult:
    """稳健性检查。

    两种模式 (由参数决定, 结果中显式标注, 不混淆):
    - 给定 `cluster_col` (或 `unit_col`): **分组剔除**估计 —— 逐个留出该组后重估,
      报告估计范围与影响最大的组;
    - 未给定分组列: 退化为结果变量的**乘性扰动** (±5%) 检查, 用于反映测量扰动
      的敏感性, 不等同于分组稳健性。
    """
    rows, err = _load_rows(arguments)
    if err:
        return _result(VerificationStatus.unsupported, err)
    base = op_difference_in_differences(arguments)
    if base.status != VerificationStatus.passed:
        return base
    est = base.values.get("estimate")
    cluster_col = arguments.get("cluster_col") or arguments.get("unit_col") or ""

    if cluster_col:
        clusters = []
        for row in rows:
            key = str(row.get(cluster_col, "")).strip()
            if key and key not in clusters:
                clusters.append(key)
        if len(clusters) < 3:
            return _result(VerificationStatus.unsupported,
                           f"分组列 {cluster_col} 的分组数不足 (<3), 无法做剔除分析")
        estimates: list[dict] = []
        for key in clusters:
            subset = [r for r in rows if str(r.get(cluster_col, "")).strip() != key]
            res = op_difference_in_differences({**arguments, "rows": subset})
            if res.status == VerificationStatus.passed:
                estimates.append({"dropped": key, "estimate": res.values.get("estimate"),
                                  "ci_low": res.values.get("ci_low"),
                                  "ci_high": res.values.get("ci_high")})
        if not estimates:
            return _result(VerificationStatus.unsupported, "分组剔除后无可估计子集")
        values_list = [e["estimate"] for e in estimates]
        impact = max(estimates, key=lambda e: abs(e["estimate"] - est))
        values = {
            "design": "leave_one_group_out",
            "cluster_col": cluster_col,
            "groups": len(clusters),
            "estimate": est,
            "estimates": estimates,
            "range": max(values_list) - min(values_list),
            "most_influential": impact["dropped"],
        }
        return _result(
            VerificationStatus.passed,
            f"分组剔除敏感性完成 ({len(estimates)} 组)",
            values=values,
            certificate=(f"估计 {est:.4g}; 剔除后范围 "
                         f"[{min(values_list):.4g}, {max(values_list):.4g}]; "
                         f"影响最大分组 {impact['dropped']}"),
        )

    # 无分组列 → 乘性扰动 (明确标注, 不冒充分组稳健性)
    estimates = []
    outcome_col = arguments.get("outcome_col", "")
    for scale in (0.95, 1.0, 1.05):
        perturbed = [dict(r) for r in rows]
        for row in perturbed:
            try:
                row[outcome_col] = float(row.get(outcome_col)) * scale
            except (TypeError, ValueError):
                continue
        res = op_difference_in_differences({**arguments, "rows": perturbed})
        if res.status == VerificationStatus.passed:
            estimates.append(res.values.get("estimate"))
    values = {"design": "multiplicative_perturbation", "estimate": est,
              "perturbed_estimates": estimates,
              "perturbation_range": (max(estimates) - min(estimates)) if estimates else 0.0}
    return _result(VerificationStatus.passed,
                   "敏感性分析完成 (结果变量乘性扰动 ±5%, 非分组稳健性)",
                   values=values, certificate=f"估计 {est:.4g}; 扰动范围 "
                   f"{values['perturbation_range']:.4g}")


OPERATIONS = {
    "describe": op_describe,
    "mean_difference": op_mean_difference,
    "difference_in_differences": op_difference_in_differences,
    "sensitivity": op_sensitivity,
}


def run(operation: str, arguments: dict) -> VerificationResult:
    fn = OPERATIONS.get(operation)
    if fn is None:
        return _result(VerificationStatus.unsupported, f"stats 适配器不支持操作 {operation}")
    try:
        return fn(arguments)
    except Exception as e:  # noqa: BLE001
        return _result(VerificationStatus.error, f"stats 执行异常: {e}")
