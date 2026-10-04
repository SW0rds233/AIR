from __future__ import annotations

"""2-(v,k,λ) 设计的可行性判定与命题化 (计划书 §4 S2)。

只做**与构造无关的必要条件**: 计数整除、Fisher 不等式、对称性、Bruck–Ryser–Chowla。
结论三态: nonexistent(必要条件被违反) / necessary_met(必要条件满足, 存在性未定) /
insufficient(信息不足)。**任何情况下都不把计数吻合当作存在**。

本模块不含任何具体题目的常量: 输入是 (v,k,λ[,b,r]), 判定逻辑对全部参数通用。

每条判定都带**出处要求**: 记录它引用的经典必要性定理 (名称 + 形式陈述 + 输入 + 结论),
使"结论"能逐条反查到定理, 而不是空口断言。判定结果可转成可复核证书
(`FeasibilityReport.evidence` / `certificate_json`) 供规则型义务验收使用。
"""

import hashlib
import json
import re
from dataclasses import dataclass, field


@dataclass
class DesignParams:
    v: int              # 对象数
    k: int              # 每块大小
    lam: int = 1        # 每对共现次数
    b: int | None = None   # 块数 (未给出时由计数关系推出)
    r: int | None = None   # 每点出现次数 (未给出时由计数关系推出)

    def summary(self) -> str:
        parts = [f"v={self.v}", f"k={self.k}", f"λ={self.lam}"]
        for name, value in (("r", self.r), ("b", self.b)):
            if value is not None:
                parts.append(f"{name}={value}")
        return ", ".join(parts)


@dataclass
class NecessityCheck:
    """一条**已执行**的必要条件判定 (带定理出处, 供证书反查)。"""

    key: str                    # 稳定标识 (证书字段名)
    theorem_cn: str             # 所用经典必要性定理 (中文名)
    claim_cn: str               # 判定的是哪条必要条件
    statement: str              # 定理的形式陈述
    inputs: dict                # 该判定的输入 (数值一律来自题面抽取)
    result: bool                # True=必要条件满足; False=被违反
    conclusion: str             # 本次判定的结论 (含实际数值)
    citation: str = ""          # 出处/参考 (不冒充已核验的文献记录)
    detail: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "theorem": self.theorem_cn, "condition": self.claim_cn,
                "statement": self.statement, "inputs": dict(self.inputs),
                "result": self.result, "conclusion": self.conclusion,
                "citation": self.citation, "detail": self.detail}

    def render(self) -> str:
        mark = "通过" if self.result else "违反"
        head = f"- {self.claim_cn} [{mark}] 依据: {self.theorem_cn}"
        if self.detail:
            head += f" (ref: {self.detail})"
        return f"{head}\n  定理: {self.statement}\n  结论: {self.conclusion}"


@dataclass
class UncheckedEntry:
    """**未判定**的必要条件: 必须如实报出"缺哪一步", 不得冒充已检查完。"""

    condition: str
    reason: str
    needed_for: str = ""

    def render(self) -> str:
        tail = f"; 影响: {self.needed_for}" if self.needed_for else ""
        return f"- 未判定: {self.condition} ({self.reason}){tail}"


@dataclass
class FeasibilityReport:
    verdict: str                                  # nonexistent / necessary_met / insufficient
    params: DesignParams | None = None
    r: int | None = None
    b: int | None = None
    symmetric: bool = False
    order: int | None = None                      # 对称且 λ=1 时 = k-1 (射影平面阶)
    checks: list[NecessityCheck] = field(default_factory=list)     # 已执行的判定
    unchecked: list[UncheckedEntry] = field(default_factory=list)  # 未判定的必要条件
    violations: list[str] = field(default_factory=list)            # 被违反的必要条件 (简述)
    checked: list[str] = field(default_factory=list)               # 已检查通过的条件 (简述)

    # ---- 汇总 ----
    @property
    def limitations(self) -> list[str]:
        """未判定项 (兼容字段, 与 `unchecked` 同源, 不再各写一份)。"""
        return [item.render() for item in self.unchecked]

    def sources(self) -> list[str]:
        """本判定链引用了哪些经典必要性定理 (去重, 保持出场顺序)。"""
        out: list[str] = []
        for item in self.checks:
            if item.theorem_cn not in out:
                out.append(item.theorem_cn)
        return out

    def evidence(self) -> list[dict]:
        return [item.to_dict() for item in self.checks]

    def certificate_dict(self) -> dict:
        return {
            "version": 1,
            "design": (self.params.__dict__ if self.params is not None else None),
            "counts": {"r": self.r, "b": self.b, "symmetric": self.symmetric,
                       "order": self.order},
            "verdict": self.verdict,
            "evidence": self.evidence(),
            "unchecked": [{"condition": u.condition, "reason": u.reason,
                           "needed_for": u.needed_for} for u in self.unchecked],
            "theorem_sources": self.sources(),
        }

    def certificate_json(self) -> str:
        return json.dumps(self.certificate_dict(), ensure_ascii=False, sort_keys=True)

    def certificate_digest(self) -> str:
        basis = self.certificate_json().encode("utf-8")
        return hashlib.sha256(basis).hexdigest()

    def conclusion(self) -> str:
        """一句话结论 (不含过程): 供命题陈述引用, 保证"证书 = 陈述"。"""
        head = {"nonexistent": "不存在", "necessary_met": "必要条件满足 (存在性未定)",
                "insufficient": "信息不足"}[self.verdict]
        if self.params is None:
            return head
        p = self.params
        return f"参数 {p.summary()} 的 2-设计: {head}"

    def describe(self) -> str:
        head = {"nonexistent": "不存在", "necessary_met": "必要条件满足 (存在性未定)",
                "insufficient": "信息不足"}[self.verdict]
        lines = [f"结论: {head}"]
        if self.r is not None and self.b is not None:
            lines.append(f"计数关系: r={self.r}, b={self.b}, 对称={self.symmetric}")
        if self.order is not None:
            lines.append(f"等价于 {self.order} 阶射影平面")
        for item in self.violations:
            lines.append(f"- 违反必要条件: {item}")
        for item in self.checked:
            lines.append(f"- 通过: {item}")
        for item in self.unchecked:
            lines.append(f"- 未判定: {item.condition} ({item.reason})")
        return "\n".join(lines)


def is_design_claim(claim) -> bool:
    """该命题是否是设计/计数类存在性命题 (参数由题面抽取, 不含题目常量)。"""
    return getattr(claim, "design_v", None) is not None


def certificate_of(record) -> dict:
    """从验证记录取回判定证书 (无证书返回空 dict)。"""
    arguments = getattr(record, "arguments", None) or {}
    certificate = arguments.get("design_report")
    return certificate if isinstance(certificate, dict) else {}


def render_certificate_chain(certificate: dict) -> str:
    """把证书渲染成可逐条反查的判定链: 定理 → 条件 → 输入 → 结论。"""
    if not certificate:
        return ""
    lines: list[str] = []
    design = certificate.get("design") or {}
    counts = certificate.get("counts") or {}
    lines.append(f"判定对象: v={design.get('v')}, k={design.get('k')}, "
                 f"λ={design.get('lam')}; 推出 r={counts.get('r')}, b={counts.get('b')}, "
                 f"对称={counts.get('symmetric')}"
                 + (f", 等价于 {counts.get('order')} 阶射影平面"
                    if counts.get("order") is not None else ""))
    for item in certificate.get("evidence") or []:
        mark = "必要条件满足" if item.get("result") else "必要条件被违反"
        inputs = "、".join(f"{k}={v}" for k, v in (item.get("inputs") or {}).items())
        lines.append(
            f"- [{mark}] {item.get('condition')}: 依据定理「{item.get('theorem')}」"
            f"({item.get('citation') or '-'}); 定理陈述: {item.get('statement')}; "
            f"输入: {inputs}; 结论: {item.get('conclusion')}")
    for item in certificate.get("unchecked") or []:
        lines.append(f"- [未判定] {item.get('condition')}: {item.get('reason')}")
    if certificate.get("sha256"):
        lines.append(f"证书 sha256={certificate['sha256']}")
    return "\n".join(lines)


def success_conditions(report: FeasibilityReport) -> list[str]:
    """该问题的**成功判据** (通用, 不含题目常量; 供规格与判据核对使用)。

    两条来自验收口径的原则, 不依赖命中的是哪条定理:
    - 结论必须明确 (不存在 / 存在性未定), 不得用"计数吻合所以应该可以生成"收尾;
    - 结论的适用范围必须写清 (该参数成立 / 该参数下未定), 不得推广到其他参数;
    - 判定依据必须点名所用必要条件与本次输入数值, 且不得用穷举冒充证明。
    """
    params = report.params
    scope = params.summary() if params is not None else "给定参数"
    conditions = [
        "明确给出结论: 该参数的设计是否存在 (或存在性未定), 不得以计数关系自洽代替判定",
        f"点名所依据的必要条件 (如 BRC / Fisher 等) 并写出本次输入数值: {scope}",
        "区分「计数必要条件满足」与「存在性」, 不得用穷举或数值检查冒充证明",
        f"适用范围必须限定在该参数 (不得推广到其他 (v,k,λ)): {scope}",
    ]
    if report.verdict == "nonexistent":
        conditions.append("若判定不存在, 指出真正阻止它存在的数学条件")
    elif report.verdict == "necessary_met":
        conditions.append("若所有已检查的必要条件都满足, 必须保持「存在性未定」并说明还缺哪一步")
    return conditions


def theorem_application_steps(certificate: dict) -> list[tuple[str, str]]:
    """证书 → 可审查的推导步骤 [(陈述, 规则)], 供证明记录逐步反查。"""
    steps: list[tuple[str, str]] = []
    for item in certificate.get("evidence") or []:
        inputs = "、".join(f"{k}={v}" for k, v in (item.get("inputs") or {}).items())
        steps.append((
            f"{item.get('condition')}: 依据「{item.get('theorem')}」"
            f"({item.get('citation') or '-'}) 在输入 {inputs} 下判定 —— "
            f"{item.get('conclusion')}", "theorem_application"))
    if not steps:
        steps.append(("无可复核的判定步骤 (证书为空), 结论不得采信", "no_certificate"))
    return steps


def is_sum_of_two_squares(n: int) -> bool:
    """n = a²+b² (允许 0): 素数 p≡3 (mod 4) 的指数必须全为偶。"""
    if n < 0:
        return False
    rest = n
    p = 2
    while p * p <= rest:
        if rest % p == 0:
            exponent = 0
            while rest % p == 0:
                rest //= p
                exponent += 1
            if p % 4 == 3 and exponent % 2:
                return False
        p += 1 if p == 2 else 2
    return rest % 4 != 3


def _is_square(n: int) -> bool:
    if n < 0:
        return False
    root = int(n ** 0.5)
    return any(candidate * candidate == n for candidate in (root - 1, root, root + 1))


def check(params: DesignParams) -> FeasibilityReport:
    v, k, lam = params.v, params.k, params.lam
    report = FeasibilityReport(verdict="insufficient", params=params)
    if v <= 0 or k <= 0 or lam <= 0:
        report.violations.append("参数必须为正整数")
        report.verdict = "insufficient"
        return report
    if k >= v or (k < 2):
        report.violations.append(f"块大小必须满足 2 ≤ k < v (得到 k={k}, v={v})")
        report.verdict = "nonexistent"
        return report

    # 1) 计数整除: r = λ(v-1)/(k-1), b = vr/k 必须是整数
    r_ok = (lam * (v - 1)) % (k - 1) == 0
    if r_ok:
        report.r = lam * (v - 1) // (k - 1)
    b_ok = report.r is not None and (v * report.r) % k == 0
    if b_ok:
        report.b = v * report.r // k
    counts_inputs: dict = {"v": v, "k": k, "λ": lam,
                           "λ(v-1)": lam * (v - 1), "k-1": k - 1}
    if report.r is not None:
        counts_inputs["v·r"] = v * report.r
    counts_result = bool(r_ok and b_ok)
    if not r_ok:
        conclusion = (f"r=λ(v-1)/(k-1)={lam * (v - 1)}/{k - 1} 不是整数, "
                      "不存在这样的 2-设计 (每条块大小与对数给出固定重数)")
        report.violations.append(
            f"每点出现次数 r=λ(v-1)/(k-1)={lam * (v - 1)}/{k - 1} 非整数")
    elif not b_ok:
        conclusion = (f"r={report.r} 是整数, 但 b=vr/k={v}·{report.r}/{k} 不是整数")
        report.violations.append(f"块数 b=vr/k={v * report.r}/{k} 非整数")
    else:
        conclusion = (f"r=λ(v-1)/(k-1)={lam * (v - 1)}/{k - 1}={report.r} 为整数, "
                      f"b=vr/k={v}·{report.r}/{k}={report.b} 为整数")
        report.checked.append(f"计数整除 r={report.r}, b={report.b}")
    report.checks.append(NecessityCheck(
        key="count_relations", theorem_cn="计数恒等式 (设计定义)", claim_cn="计数整除条件",
        statement="2-(v,k,λ) 设计中 r(k-1)=λ(v-1) 且 bk=vr 必须成立, 因此 r、b 必须是整数",
        inputs=counts_inputs, result=counts_result, conclusion=conclusion,
        citation="组合设计基本计数 (定义直接推论)",
        detail="r=(v-1)λ/(k-1), b=vr/k"))

    # 声明值 (题面已给出的 b/r) 与计数关系推出的值必须一致
    if report.b is not None or report.r is not None:
        declared_inputs: dict = {"声明 b": params.b, "声明 r": params.r,
                                 "推出 b": report.b, "推出 r": report.r}
        declared_ok = True
        declared_notes: list[str] = []
        if params.b is not None and report.b is not None and params.b != report.b:
            declared_ok = False
            declared_notes.append(f"给定块数 {params.b} 与计数关系推出的 {report.b} 不一致")
        if params.r is not None and report.r is not None and params.r != report.r:
            declared_ok = False
            declared_notes.append(f"给定重复数 {params.r} 与计数关系推出的 {report.r} 不一致")
        if not declared_ok:
            report.violations.extend(declared_notes)
        report.checks.append(NecessityCheck(
            key="declared_consistency", theorem_cn="计数恒等式 (设计定义)",
            claim_cn="题面声明值与计数关系一致",
            statement="题面显式给出的 b、r 必须与 r(k-1)=λ(v-1)、bk=vr 推出的值相同",
            inputs=declared_inputs, result=declared_ok,
            conclusion=("声明的 b、r 与计数关系推出的值一致"
                        if declared_ok else "; ".join(declared_notes)),
            citation="组合设计基本计数 (定义直接推论)"))

    # 2) Fisher 不等式: 2-设计必须 b ≥ v
    if report.b is not None:
        fisher_ok = report.b >= v
        if not fisher_ok:
            report.violations.append(f"Fisher 不等式被违反: b={report.b} < v={v}")
        else:
            report.checked.append(f"Fisher 不等式 b={report.b} ≥ v={v}")
        report.symmetric = report.b == v
        if report.symmetric:
            report.checked.append("对称设计 (b=v)")
        report.checks.append(NecessityCheck(
            key="fisher_inequality", theorem_cn="Fisher 不等式",
            claim_cn="块数不少于对象数 (b ≥ v)",
            statement="任何 2-(v,k,λ) 设计 (k<v) 都满足 b ≥ v; 等号成立时称为对称设计",
            inputs={"b": report.b, "v": v}, result=fisher_ok,
            conclusion=(f"b={report.b} ≥ v={v}" if fisher_ok
                        else f"b={report.b} < v={v}, 不存在这样的设计"),
            citation="Fisher (1940), 2-设计的基本不等式",
            detail="b ≥ v; b = v ⇒ 对称设计"))

        if report.symmetric:
            # 对称设计的必要条件 (v 偶时 k-λ 必须是平方数)
            if v % 2 == 0:
                square_ok = _is_square(k - lam)
                if not square_ok:
                    report.violations.append(
                        f"Bruck–Ryser–Chowla (v 偶): k-λ={k - lam} 必须是平方数, 但不是")
                else:
                    report.checked.append(f"BRC (v 偶): k-λ={k - lam} 是平方数")
                report.checks.append(NecessityCheck(
                    key="brc_even_order", theorem_cn="Bruck–Ryser–Chowla 定理 (对称设计)",
                    claim_cn="v 为偶数时 k-λ 必须是平方数",
                    statement=("对称 2-(v,k,λ) 设计在 v 为偶数时, 必有 k-λ 为完全平方数"),
                    inputs={"v": v, "k": k, "λ": lam, "k-λ": k - lam},
                    result=square_ok,
                    conclusion=(f"k-λ={k - lam} 是完全平方数"
                                if square_ok else
                                f"k-λ={k - lam} 不是平方数, 不存在这样的对称设计"),
                    citation="Bruck–Ryser–Chowla (1949/1950)"))
            if lam == 1:
                report.order = k - 1
                n = report.order
                if n % 4 in (1, 2):
                    two_squares_ok = is_sum_of_two_squares(n)
                    if not two_squares_ok:
                        report.violations.append(
                            f"Bruck–Ryser–Chowla: 阶 n={n} ≡ {n % 4} (mod 4) 必须是两个平方数"
                            f"之和, 但 {n} 不是 —— 该射影平面不存在")
                    else:
                        report.checked.append(
                            f"BRC (射影平面): 阶 n={n} ≡ {n % 4} (mod 4) 且是两平方和")
                    report.checks.append(NecessityCheck(
                        key="brc_projective_plane",
                        theorem_cn="Bruck–Ryser–Chowla 定理 (射影平面)",
                        claim_cn="n≡1,2 (mod 4) 的射影平面阶必须是两平方和",
                        statement=("若 n 阶射影平面存在且 n ≡ 1, 2 (mod 4), "
                                   "则 n 必为两个整数平方之和"),
                        inputs={"n = k-1": n, "n mod 4": n % 4,
                                "n 的素因数分解": _factor_text(n)},
                        result=two_squares_ok,
                        conclusion=(f"n={n} 可写成两个平方数之和 (p≡3 (mod 4) 的素因子指数全为偶)"
                                    if two_squares_ok else
                                    f"n={n} 不是两个平方数之和, 因此 {n} 阶射影平面不存在"),
                        citation="Bruck–Ryser–Chowla (1949/1950); 两平方和判定用 Fermat 两平方定理",
                        detail=f"n={n}"))
                    # 把"该参数就是射影平面问题"这一步也写成可核验的判定,
                    # 否则 BRC 的适用前提只停留在自然语言里, 复核者无法逐项确认
                    v_expect = k * (k - 1) + 1
                    plane_inputs = {"λ": lam, "v": v, "k(k-1)+1": v_expect,
                                    "r=λ(v-1)/(k-1)": report.r, "b=vr/k": report.b}
                    if v == v_expect:
                        plane_conclusion = (
                            f"λ=1 且 v={v}=k(k-1)+1={v_expect}, 且 r=k={k}=b (对称): "
                            f"该 2-设计正是 {n} 阶射影平面 (射影平面即 2-(n²+n+1, n+1, 1) 设计)")
                        plane_ok = report.r == k and report.b == v
                    else:
                        plane_conclusion = (
                            f"v={v} ≠ k(k-1)+1={v_expect}: 本参数不是射影平面, "
                            "不能套用射影平面形式的 BRC (需用对称设计的一般形式)")
                        plane_ok = False
                    report.checks.append(NecessityCheck(
                        key="projective_plane_equivalence",
                        theorem_cn="射影平面与对称设计等价 (定义)",
                        claim_cn="该参数等价于射影平面问题",
                        statement=("λ=1 的对称 2-(v,k,1) 设计满足 v=k(k-1)+1, "
                                   "且与 n=k-1 阶射影平面互相等价 (点=对象, 线=区组)"),
                        inputs=plane_inputs, result=plane_ok, conclusion=plane_conclusion,
                        citation="射影平面 = 2-(n²+n+1, n+1, 1) 设计 (组合设计标准等价)"))
                else:
                    report.unchecked.append(UncheckedEntry(
                        condition=f"BRC 对 n={n} ≡ {n % 4} (mod 4) 不适用",
                        reason="BRC 仅在 n ≡ 1, 2 (mod 4) 时排除射影平面",
                        needed_for="该阶射影平面的存在性仍需显式构造或更强的排除定理"))
            elif v % 2 == 1:
                # 对称、奇 v、λ>1: BRC 的一般形式需要整数解判定, 未实现 → 如实报未判定
                report.unchecked.append(UncheckedEntry(
                    condition="BRC 的奇 v 一般形式 (λ>1)",
                    reason=("需要判定整数方程 x²+(k-λ)y²=(-1)^((v-1)/2)λz² 是否有非平凡解; "
                            "该整数解判定未实现"),
                    needed_for="奇 v 对称设计的排除"))
        else:
            # 非对称设计: BRC 是**对称设计**的定理, 此处不适用 (不得冒充已检查)
            report.unchecked.append(UncheckedEntry(
                condition="Bruck–Ryser–Chowla 定理",
                reason="BRC 只适用于对称设计 (b=v); 本参数 b≠v, 该定理不适用",
                needed_for="非对称设计的存在性仍需显式构造或其他排除定理"))
    elif v % 2 == 1:
        # 对称性前提未定: 不能声称已用对称设计的必要条件
        report.unchecked.append(UncheckedEntry(
            condition="对称设计的必要条件 (含 BRC)",
            reason="块数 b 未能由计数关系推出, 无法判定是否为对称设计 (b=v)",
            needed_for="对称性判定与相应的排除定理"))

    report.verdict = "nonexistent" if report.violations else "necessary_met"
    if report.verdict == "necessary_met":
        report.unchecked.append(UncheckedEntry(
            condition="存在性结论",
            reason="必要条件全部满足不等于存在: 需要显式构造或更强的排除定理",
            needed_for="把「必要条件满足」升级为「存在/不存在」"))
    return report


def _factor_text(n: int) -> str:
    """n 的素因数分解 (供证书展示两平方和判定的依据)。"""
    if n <= 1:
        return str(n)
    rest, parts, p = n, [], 2
    while p * p <= rest:
        if rest % p == 0:
            count = 0
            while rest % p == 0:
                rest //= p
                count += 1
            parts.append(f"{p}^{count}")
        p += 1 if p == 2 else 2
    if rest > 1:
        parts.append(str(rest))
    return " × ".join(parts) or str(n)


# ---------------------------------------------------------------------------
# S1 抽取: 从自然语言题面取出计数约束 (通用词表, 不含任何具体题目数值)
# ---------------------------------------------------------------------------

_OBJECTS = r"(?:节点|对象|样点|样本|元素|点|设备|台站|处理|品种|nodes?|objects?|points?|elements?|treatments?|varieties)"
_BLOCKS = r"(?:轮|组|块|批|次|blocks?|rounds?|groups?|plots?)"
_REPEAT = r"(?:参加|参与|出现|包含|属于|重复|appear|occur|contain|belong)"

_PATTERNS: tuple[tuple[str, str], ...] = (
    # 对象总数: "211 个节点" / "7 elements" / "共有 30 个对象"
    (rf"(\d+)\s*(?:个|台|种|名)?\s*{_OBJECTS}", "v"),
    # 每块大小: "每轮恰好选择 15 个" / "每个区组包含 3 个对象" / "each line contains 7 points"
    (rf"每(?:一)?[^。.;\n]{{0,8}}?{_BLOCKS}[^。.;\n]{{0,16}}?(\d+)\s*(?:个|台|种|名)?", "k"),
    (r"(?:contain|contains|has|hold|holds|with)\s+(\d+)\s*"
     r"(?:points|objects|nodes|elements|treatments|varieties)", "k"),
    # 重复数: "每个节点恰好参加 15 轮" / "每个对象出现在 3 组中" / "each point occurs in 7 lines"
    (rf"每(?:一)?(?:个|台|种)?[^。.;\n]{{0,12}}?{_REPEAT}[^。.;\n]{{0,12}}?(\d+)\s*{_BLOCKS}", "r"),
    (r"(?:occurs?|appears?|is contained)\s+in\s+(\d+)\s*"
     r"(?:lines?|blocks?|rounds?|groups?|plots?)", "r"),
    # 块数: 必须带总数标记 ("安排恰好 211 轮" / "共 7 块"), 避免把"每个对象出现在 3 组中"
    # 里的"3 组"误读成总块数 —— 那是重复数, 不是块数
    (rf"(?:恰好|正好|总共|一共|共|安排(?:恰好|正好)?)\s*(\d+)\s*{_BLOCKS}", "b"),
    # 配对重数: "任意两个节点恰好共同参加 1 轮" / "any two points ... 1 line"
    (rf"任意(?:两个|两|2\s*个|一对)[^。.;\n]{{0,24}}?(\d+)\s*{_BLOCKS}", "lam"),
    (r"(?:any|every)\s+two\s+\w+[^.;\n]{0,60}?(\d+)\s*"
     r"(?:lines?|blocks?|rounds?|groups?|plots?)", "lam"),
)


def extract_design_params(text: str) -> DesignParams | None:
    """从题面抽取 (v,k,λ[,b,r]); 信息不足时返回 None (由调用方如实报未决)。

    纯词表 + 数字抽取, 不针对任何具体题目: 换一组参数、换一种说法同样适用。
    """
    body = (text or "").replace("\n", " ")
    found: dict[str, int] = {}
    # 标准参数记号 `t-(v,k,λ)` (组合设计文献的通用写法) 与 `(v,k,λ)` + 设计关键词。
    # 为什么单独处理: 上面那组模式靠"每轮/每个节点恰好…"这类**叙述性**措辞抽参数,
    # 而题目常常只给记号 —— 实测"参数 2-(211,15,1) 的设计是否存在"抽不出 v/k,
    # 于是一个有唯一确定答案的判定题被当成"研究方向"并停在未决。
    # 记号本身的含义是通用的 (与具体题目无关), 因此这不是给某道题打补丁。
    notation = re.search(r"(\d+)\s*[-–]\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", body)
    if notation:
        found.setdefault("v", int(notation.group(2)))
        found.setdefault("k", int(notation.group(3)))
        found.setdefault("lam", int(notation.group(4)))
    else:
        bare = re.search(
            rf"(?:设计|区组|平衡|{_BLOCKS}|design|BIBD)[^。.;\n]{{0,16}}?"
            rf"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
            rf"|\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)[^。.;\n]{{0,16}}?"
            rf"(?:设计|区组|平衡|design|BIBD)",
            body, re.IGNORECASE)
        if bare:
            groups = [g for g in bare.groups() if g is not None]
            found.setdefault("v", int(groups[0]))
            found.setdefault("k", int(groups[1]))
            found.setdefault("lam", int(groups[2]))
    # 循环变量不要叫 `field`: 会遮蔽 `dataclasses.field` (本模块用它声明的数据类字段),
    # 后续在该作用域里再加字段就会静默拿到字符串而不是 dataclass 字段。
    for pattern, name in _PATTERNS:
        match = re.search(pattern, body, re.IGNORECASE)
        if match and name not in found:
            found[name] = int(match.group(1))
    if "v" not in found or "k" not in found:
        return None
    if "lam" not in found:
        # "任意两个…恰好一次/恰好在同一轮" 这类没有显式数字的写法等价于 λ=1
        found["lam"] = 1 if re.search(
            rf"任意(?:两个|两|2\s*个|一对)[^。.;\n]{{0,24}}?(?:恰好|正好)?[^。.;\n]{{0,8}}?"
            rf"(?:一|同一|1)\s*{_BLOCKS}", body) else -1
    if found.get("lam", -1) <= 0:
        return None
    params = DesignParams(v=found["v"], k=found["k"], lam=found["lam"],
                          b=found.get("b"), r=found.get("r"))
    report = check(params)
    return params if report.verdict != "insufficient" else None


def feasibility_from_text(text: str) -> FeasibilityReport | None:
    """题面 → 判定报告; 抽不出计数约束时返回 None (不要编造参数)。"""
    params = extract_design_params(text)
    return check(params) if params is not None else None


# ---------------------------------------------------------------------------
# S1 命题化: 判定 → 命题 + 必要性义务 (可复核证书 + 来源要求)
# ---------------------------------------------------------------------------

# 义务验收方式: 由确定性规则按证书关闭 (nonexistent 才允许关闭)
ACCEPTANCE_METHOD = "design_necessity"
OBLIGATION_KIND = "design_necessity"


def design_claim_statement(params: DesignParams, report: FeasibilityReport) -> str:
    """命题陈述 = 证书结论 (同一句话, 不允许改述)。"""
    p = f"v={params.v}, k={params.k}, λ={params.lam}"
    if params.r is not None:
        p += f", r={params.r}"
    if params.b is not None:
        p += f", b={params.b}"
    if report.verdict == "nonexistent":
        return (f"计数约束参数 ({p}) 的 2-设计不存在: "
                "计数关系虽自洽, 但存在一条被违反的经典必要条件")
    if report.verdict == "necessary_met":
        return (f"计数约束参数 ({p}) 的 2-设计满足已检查的全部必要条件, "
                "但存在性未定 (必要条件满足不等于存在)")
    return f"计数约束参数 ({p}) 的存在性判定信息不足, 不能给出结论"


def design_obligation_statement(params: DesignParams, report: FeasibilityReport) -> str:
    if report.verdict == "nonexistent":
        return (f"核验「不存在」结论所依据的必要条件判定链 (参数 {params.summary()}): "
                "每条证据必须是可由 (v,k,λ) 重算的经典必要条件")
    return (f"确定参数 {params.summary()} 的 2-设计是否**存在**: "
            "已检查的必要条件全部满足, 存在性仍需显式构造或更强的排除定理")


def formulation_from_report(report: FeasibilityReport, source_text: str = "") -> "object":
    """判定报告 → Formulation(命题 + 必要性义务)。不含任何题目常量。

    - 命题与义务都是**通用**的: 结论语句直接取自证书, 参数全部来自题面抽取;
    - 义务记录 `acceptance_method="design_necessity"` 与证据链 (含引用定理名),
      由 `loop._evaluate_design_necessity` 按证书验收:
      **仅 nonexistent 允许关闭**; necessary_met 必须保持未关闭。
    """
    from src.research.problem_formulator import Formulation
    from src.research.schemas import (
        Claim,
        ClaimType,
        Origin,
        ProofObligation,
        SupportKind,
        ValidationStatus,
    )

    params = report.params
    if params is None:
        raise ValueError("判定报告缺少设计参数, 不能生成命题")
    statement = design_claim_statement(params, report)
    claim = Claim(
        statement=statement,
        claim_type=ClaimType.definitional,
        origin=Origin.derived,
        # 支持方式由义务关闭后的中央状态规则写入 (此处不预支等级)
        support_kind=SupportKind.none,
        design_v=params.v, design_k=params.k, design_lambda=params.lam,
        design_b=params.b, design_r=params.r,
        design_verdict=report.verdict,
        notes=(f"来源: {' / '.join(filter(None, [source_text[:200], '计数约束抽取']))}; "
               f"判定链引用定理: {', '.join(report.sources()) or '-'}"
               f"; 证书 sha256={report.certificate_digest()[:16]}"),
    )
    obligation = ProofObligation(
        statement=design_obligation_statement(params, report),
        kind=OBLIGATION_KIND,
        acceptance_method=ACCEPTANCE_METHOD,
        claim_id=claim.id,
        claim_version=claim.version,
        detail=(f"依据: {', '.join(report.sources()) or '-'}; "
                f"证据 {len(report.checks)} 条; "
                f"证书 sha256={report.certificate_digest()[:16]}"),
        validation_status=ValidationStatus.unchecked,
        # 证书里记录的定理与输入决定这条义务能否关闭
        local_context=report.certificate_json(),
    )
    form = Formulation()
    form.claims.append(claim)
    form.obligations.append(obligation)
    form.notes.append(f"判定 (通用必要条件, 未针对具体题目): {report.describe()}")
    return form


def formulate_from_text(text: str) -> "object | None":
    """题面 → Formulation; 抽不出计数约束时返回 None (保持"请求澄清"行为不变)。"""
    report = feasibility_from_text(text)
    if report is None:
        return None
    return formulation_from_report(report, source_text=text)


__all__ = ["ACCEPTANCE_METHOD", "OBLIGATION_KIND", "DesignParams", "FeasibilityReport",
           "NecessityCheck", "UncheckedEntry", "check", "design_claim_statement",
           "design_obligation_statement", "extract_design_params",
           "feasibility_from_text", "formulate_from_text", "formulation_from_report",
           "is_sum_of_two_squares", "success_conditions"]
