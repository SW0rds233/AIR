from __future__ import annotations

"""领域候选模型的构造与比较 (计划书 §3 R2)。

计划书的判断: 现有 `candidate_scheme()` 只按命题类型给出**通用模板**, 形式化编码
主要借用命题已有表达式 —— 这能说明"需要哪类模型", 但不足以从一批领域资料中
构造并比较候选机制。

本模块把"建模"变成可审计的一组产物:

- 术语与量纲表: 变量 → 含义/单位/取值域 (单位缺失必须显式标出);
- 候选机制 ≥2 个, 每个带来源引用 (`source_refs`)、变量关系、噪声/边界条件、
  可观测预测 (可推导命题)、适用范围、被排除的替代解释;
- 比较维度: 问题忠实度、来源条件、可验证性、成本;
- **冲突预测 → 可区分检验**: 若候选模型给出互相冲突的可观测预测, 优先产出
  "能区分两个解释"的检验建议 (推导或仿真), 而不是随便挑一个模型。

硬约束
------
- 不臆造结论: 候选机制里的"预测"是**待核验命题**, 必须写成 obligation 才可能
  影响命题状态;
- 没有来源/没有可观测预测的候选会被显式标为"弱候选"并降低排序, 而不是静默丢弃;
- 单位/边界缺失记录为 `missing` 项, 供工作台与交付物展示。
"""

import re
from dataclasses import dataclass, field

from src.research.schemas import Claim, SourceEvidence, stable_id

# 单位识别 (与只读数据适配器同一份关键词表)
_UNIT_HINTS = {
    "亿元": "亿元", "万元": "万元", "元": "元", "美元": "美元", "%": "%",
    "kg": "kg", "km": "km", "ms": "ms", "hz": "Hz", "db": "dB", "s": "s", "m": "m",
}

# 噪声/边界条件的关键词 (出现在原文里说明有明确条件)
_NOISE_HINTS = ("噪声", "noise", "误差", "error", "方差", "variance", "干扰",
                "interference", "波动", "fluctuation")
_BOUNDARY_HINTS = ("边界", "boundary", "适用范围", "假设", "assumption", "条件",
                   "condition", "限于", "仅在")


@dataclass
class Term:
    """术语与量纲表的一行。"""

    name: str
    meaning: str = ""
    unit: str = ""
    domain: str = ""

    def describe(self) -> str:
        unit = f" [{self.unit}]" if self.unit else " [单位缺失]"
        dom = f" 域={self.domain}" if self.domain else ""
        return f"{self.name}{unit}{dom} — {self.meaning or '(含义待补)'}"


@dataclass
class Mechanism:
    """一个候选机制 (可比较、可审计)。"""

    id: str = ""
    name: str = ""
    relation: str = ""                    # 变量关系 (自然语言/公式)
    formal_encoding: str = ""
    assumptions: list[str] = field(default_factory=list)
    noise: str = ""
    boundaries: str = ""
    predictions: list[str] = field(default_factory=list)      # 可观测预测/可推导命题
    excluded_alternatives: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    fidelity: str = ""
    verifiability: str = ""
    cost: int = 1
    missing: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    origin: str = "template"              # template / literature / llm
    # 结构性差异与方向: 冲突判定按"同一(处理,结果)下方向相反", 不按措辞
    structural_key: str = ""
    sign: int = 0                         # +1 正向 / -1 负向 / 0 未知
    anchors: list[str] = field(default_factory=list)   # AnchoredResult.id

    @property
    def supported(self) -> bool:
        return bool(self.source_refs)

    def score(self) -> tuple:
        """比较用排序键 (高者优先): 来源、可验证预测、显式假设、成本与缺失项。"""
        return (
            1 if self.source_refs else 0,
            1 if self.predictions else 0,
            1 if self.formal_encoding else 0,
            len(self.assumptions),
            -int(self.cost),
            -len(self.missing),
        )

    def describe(self) -> str:
        bits = [f"{self.name} ({self.id})"]
        if self.relation:
            bits.append(f"关系: {self.relation[:80]}")
        if self.predictions:
            bits.append(f"预测 {len(self.predictions)} 条")
        if not self.source_refs:
            bits.append("无来源引用 (弱候选)")
        if self.missing:
            bits.append("缺失: " + ", ".join(self.missing))
        return "; ".join(bits)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "relation": self.relation,
            "formal_encoding": self.formal_encoding, "assumptions": list(self.assumptions),
            "noise": self.noise, "boundaries": self.boundaries,
            "predictions": list(self.predictions),
            "excluded_alternatives": list(self.excluded_alternatives),
            "source_refs": list(self.source_refs), "fidelity": self.fidelity,
            "verifiability": self.verifiability, "cost": self.cost,
            "missing": list(self.missing), "weak": list(self.weak),
            "origin": self.origin, "structural_key": self.structural_key,
            "sign": self.sign, "anchors": list(self.anchors),
        }


@dataclass
class DistinguishingTest:
    """能区分两个候选解释的检验建议 (计划书 R2 要求优先产出)。"""

    kind: str = "derivation"      # derivation / simulation / observation
    statement: str = ""
    discriminates: list[str] = field(default_factory=list)   # [机制A, 机制B]
    expected_if_a: str = ""
    expected_if_b: str = ""
    cost: int = 1

    def describe(self) -> str:
        a = self.discriminates[0] if self.discriminates else "?"
        b = self.discriminates[1] if len(self.discriminates) > 1 else "?"
        return (f"[{self.kind}] {self.statement} "
                f"(若 {a}: {self.expected_if_a or '待定'}; 若 {b}: {self.expected_if_b or '待定'})")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "statement": self.statement,
                "discriminates": list(self.discriminates),
                "expected_if_a": self.expected_if_a,
                "expected_if_b": self.expected_if_b, "cost": self.cost}


@dataclass
class ModelComparison:
    """候选模型的比较结果 (含选中理由与被排除的替代解释)。"""

    mechanisms: list[Mechanism] = field(default_factory=list)
    selected: str = ""
    why_selected: str = ""
    distinguishing: list[DistinguishingTest] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    terms: list[Term] = field(default_factory=list)
    # 无法形成竞争模型时如实说明原因 (不硬凑第二个候选)
    note: str = ""

    @property
    def weak(self) -> list[Mechanism]:
        return [m for m in self.mechanisms if not m.supported or not m.predictions]

    def describe(self) -> str:
        if not self.mechanisms:
            return "没有候选模型"
        head = f"候选模型 {len(self.mechanisms)} 个, 选中 {self.selected or '(未选中)'}: {self.why_selected}"
        if self.distinguishing:
            head += f"; 需可区分检验 {len(self.distinguishing)} 条"
        if self.note:
            head += f"; {self.note}"
        return head

    def to_dict(self) -> dict:
        return {
            "selected": self.selected, "why_selected": self.why_selected,
            "mechanisms": [m.to_dict() for m in self.mechanisms],
            "distinguishing": [t.to_dict() for t in self.distinguishing],
            "conflicts": list(self.conflicts),
            "terms": [{"name": t.name, "meaning": t.meaning, "unit": t.unit,
                       "domain": t.domain} for t in self.terms],
            "note": self.note,
            "description": self.describe(),
        }


# ----------------------------------------------------------------------
# 术语与量纲
# ----------------------------------------------------------------------
# 单位识别由只读数据适配器提供**同一份**实现 (避免"建模"与"读数据"两套规则漂移)
def infer_unit_from_text(*texts: str) -> str:
    from src.kb.adapters.readonly_data import infer_unit

    return infer_unit(*texts)


def build_terms(claim: Claim, evidence: list[SourceEvidence] | None = None) -> list[Term]:
    """从命题与证据文本里抽取术语表 (单位/域); 抽取不到的显式留空。

    单位只按**变量名**判断: 用整段命题文本去匹配会让所有变量得到同一个单位
    (甚至把 "separability" 里的 "s" 当成秒), 那等于臆造量纲。
    """
    evidence = list(evidence or [])
    terms: list[Term] = []
    for name in claim.variables or []:
        meaning = ""
        for e in evidence:
            if name in (e.excerpt or ""):
                meaning = (e.excerpt or "")[:120]
                break
        terms.append(Term(
            name=name,
            meaning=meaning or f"{claim.statement[:60]} 中的变量",
            unit=infer_unit_from_text(name),
            domain=str((claim.variable_domains or {}).get(name, "")),
        ))
    if claim.study.treatment and claim.study.outcome:
        terms.append(Term(name=claim.study.treatment, meaning="处理变量",
                          unit=infer_unit_from_text(claim.study.treatment),
                          domain=claim.scope_population))
        terms.append(Term(name=claim.study.outcome, meaning="结果变量",
                          unit=infer_unit_from_text(claim.study.outcome),
                          domain=claim.scope_period))
    return terms


# ----------------------------------------------------------------------
# 候选机制
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 原文结果锚点: 机制必须来自"有定位的原文结果", 不能靠模板套
# ----------------------------------------------------------------------
_PATH_HINTS = ("使", "导致", "引起", "造成", "通过", "经由", "作用", "机制", "原因",
               "causes", "leads to", "results in", "via")
# 机制因子: 用来区分"作用路径"的粗粒度词表 (不细化到具体措辞)
_FACTOR_HINTS = ("噪声", "干扰", "误差", "漂移", "均衡", "补偿", "多径", "非线性",
                 "饱和", "量化", "温度", "样本量", "选择偏差", "noise", "drift",
                 "equaliz", "multipath", "bias")
_UP_HINTS = ("提升", "提高", "增加", "上升", "增强", "改善", "变大", "increase",
             "improve", "higher", "larger")
_DOWN_HINTS = ("下降", "降低", "减少", "减弱", "恶化", "退化", "变小", "decrease",
               "reduce", "lower", "smaller")
_FORMULA_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\s*(?:=|<=|>=|<|>|\||\+|-|\*|/)\s*"
                         r"[A-Za-z_0-9(]")
_SENTENCE_SPLIT = re.compile(r"[。;；!?！？\n]")


@dataclass
class AnchoredResult:
    """一条有定位的原文结果 —— 机制合成的唯一锚点。"""

    id: str = ""
    statement: str = ""
    evidence_id: str = ""
    locator: str = ""
    source: str = ""
    treatment: str = ""
    outcome: str = ""
    path: str = ""                 # 作用路径 (自然语言)
    boundary: str = ""
    noise: str = ""
    encoding: str = ""             # 原文给出的形式化片段 (没有则留空)
    sign: int = 0                  # 结果方向 +1/-1/0
    origin: str = "literature"     # literature / user / proposed

    @property
    def key(self) -> str:
        """结构签名 = 作用路径(机制动词+因子) + 对象 + 条件。

        只看结构, 不看到具体措辞: 同一机制的两种表述必须落到同一组,
        否则"改写一句话"就会被当成两个竞争模型。
        """
        marker = next((h for h in _PATH_HINTS if h in (self.statement or "")), "")
        factors = sorted({h for h in _FACTOR_HINTS if h in (self.statement or "")})
        return "|".join([marker, ",".join(factors), _normalize(self.boundary),
                         _normalize(self.treatment), _normalize(self.outcome)])


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower()))


def _pick_sentence(excerpt: str, *needles: str) -> str:
    """取包含关注对象的那句原文 (定位到具体句子, 而不是整段摘要)。"""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(excerpt or "") if s.strip()]
    for sentence in sentences:
        if needles and any(n and n in sentence for n in needles):
            return sentence
    return sentences[0] if sentences else ""


def build_anchors(claim: Claim | None = None,
                  evidence: list[SourceEvidence] | None = None) -> list[AnchoredResult]:
    """把带定位的候选证据转成原文结果锚点 (无定位/无原文的不作为锚点)。"""
    anchors: list[AnchoredResult] = []
    treatment = claim.study.treatment if claim else ""
    outcome = claim.study.outcome if claim else ""
    for item in evidence or []:
        excerpt = (item.excerpt or "").strip()
        # 机制只能来自**读到的原文**: 没有原文片段或没有定位的不算锚点
        if not excerpt or not (item.location or item.page):
            continue
        sentence = _pick_sentence(excerpt, treatment, outcome)
        blob = f"{sentence} {excerpt}"
        sign = 0
        if any(h in blob for h in _UP_HINTS):
            sign = 1
        if any(h in blob for h in _DOWN_HINTS):
            sign = -1
        boundary = next((h for h in _BOUNDARY_HINTS if h in blob), "")
        noise = next((h for h in _NOISE_HINTS if h in blob), "")
        formula = next((m.group(0) for m in _FORMULA_RE.finditer(blob)), "")
        anchors.append(AnchoredResult(
            id=stable_id("anc", item.id, sentence[:120]),
            statement=sentence or excerpt[:200],
            evidence_id=item.id,
            locator=item.location or (f"p.{item.page}" if item.page else ""),
            source=item.source_id or item.title or item.literature_id,
            treatment=treatment or item.title,
            outcome=outcome or claim.statement[:40] if claim else outcome,
            path=sentence if any(h in sentence for h in _PATH_HINTS) else "",
            boundary=boundary,
            noise=noise,
            encoding=formula,
            sign=sign,
        ))
    return anchors


def _hypothesis_mechanism(contract=None, claim: Claim | None = None) -> Mechanism:
    """没有原文结果时的**待检假设** (不是"最佳模型")。"""
    objects = list(getattr(contract, "objects", []) or [])
    outcome = objects[0] if objects else (claim.study.outcome if claim else "")
    treatment = objects[1] if len(objects) > 1 else (claim.study.treatment if claim else "")
    relation = f"{treatment} → {outcome}" if treatment or outcome else ""
    mechanism = Mechanism(
        id="mdl-hypothesis",
        name="待检假设 (无带定位的原文机制)",
        relation=relation,
        predictions=[f"待检验: {treatment or '处理'} 与 {outcome or '结果'} 的关系方向未知"],
        fidelity="尚无原文支撑, 只能作为待检假设",
        verifiability="需要先取得可定位的原文结果或可核验的形式化片段",
        origin="template",
        structural_key="hypothesis",
        weak=["没有来源引用"],
    )
    _fill_missing(mechanism)
    return mechanism


def _mechanism_from_anchors(group_id: str, items: list[AnchoredResult],
                            contract=None) -> Mechanism:
    """由同一结构的一批原文结果构造机制 (来源/条件/预测都来自原文)。"""
    head = items[0]
    boundary = next((a.boundary for a in items if a.boundary), "")
    noise = next((a.noise for a in items if a.noise), "")
    encoding = next((a.encoding for a in items if a.encoding), "")
    direction = next((a.sign for a in items if a.sign), 0)
    name = f"{head.path or head.statement[:40]}"
    if boundary:
        name += f" [条件: {boundary}]"
    prediction = _prediction_text(head, direction, boundary)
    others = [a for a in items[1:]]
    mechanism = Mechanism(
        id=f"mdl-{group_id}",
        name=name[:120],
        relation=head.statement,
        formal_encoding=encoding,
        assumptions=[f"[文献] {head.locator}: {head.statement[:80]}"] if head.locator else [],
        noise=f"原文出现噪声/误差描述 ({noise})" if noise else "原文未说明噪声结构 (需补充)",
        boundaries=f"原文条件: {boundary}" if boundary else "原文未声明适用范围",
        predictions=[prediction],
        source_refs=[a.evidence_id for a in items if a.evidence_id],
        fidelity="直接取自带定位的原文结果",
        verifiability=("同源形式化片段可核验" if encoding
                       else "原文未给形式化片段, 需先构造可核验编码"),
        origin=head.origin,
        structural_key=head.key,
        sign=direction,
        anchors=[a.id for a in items],
    )
    if others:
        mechanism.assumptions.append(
            f"[文献] 同一结构下另有 {len(others)} 条结果支持该机制")
    _fill_missing(mechanism)
    return mechanism


def _prediction_text(anchor: AnchoredResult, direction: int, boundary: str) -> str:
    """由原文方向与条件生成**可比较**的预测 (而不是复述命题)。"""
    condition = boundary or "原文声明条件"
    if direction > 0:
        return f"在{condition}下, {anchor.outcome or '结果'} 随 {anchor.treatment or '处理'} 增大而提升"
    if direction < 0:
        return f"在{condition}下, {anchor.outcome or '结果'} 随 {anchor.treatment or '处理'} 增大而下降"
    return (f"在{condition}下, {anchor.outcome or '结果'} 与 {anchor.treatment or '处理'} 的"
            f"关系需经数值比较判定")


def _group_anchors(anchors: list[AnchoredResult]) -> dict[str, list[AnchoredResult]]:
    """按结构签名分组: 只有路径/条件不同才算不同机制。"""
    groups: dict[str, list[AnchoredResult]] = {}
    for anchor in anchors:
        groups.setdefault(anchor.key, []).append(anchor)
    return groups


def candidate_mechanisms(claim: Claim, evidence: list[SourceEvidence] | None = None,
                         contract=None) -> list[Mechanism]:
    """候选机制: 每个来自**结构不同的原文结果**; 没有原文时只给待检假设。"""
    groups = _group_anchors(build_anchors(claim, evidence))
    if not groups:
        return [_hypothesis_mechanism(contract, claim)]
    mechanisms: list[Mechanism] = []
    for index, items in enumerate(groups.values()):
        mechanisms.append(_mechanism_from_anchors(f"m{index + 1}", items, contract))
    return mechanisms


def synthesize_models(contract=None, anchored_results: list[AnchoredResult] | None = None,
                      *, claim: Claim | None = None,
                      evidence: list[SourceEvidence] | None = None) -> ModelComparison:
    """问题契约 + 有定位的原文结果 → 候选模型、条件映射与可区分预测。

    - 没有可靠来源 → 只输出待检假设, **不选中**任何"最佳模型";
    - 只有一个结构 → 如实报告"无法形成竞争模型", 不硬凑第二个候选;
    - 两个结构 → 比较来源/忠实度/可验证性/成本, 并在预测冲突时给出可区分检验。
    """
    anchors = list(anchored_results if anchored_results is not None
                   else build_anchors(claim, evidence))
    comparison = ModelComparison()
    comparison.terms = build_terms(claim, evidence) if claim is not None else []
    if contract is not None:
        comparison.terms = comparison.terms or []
    groups = _group_anchors(anchors)
    if not groups:
        comparison.mechanisms = [_hypothesis_mechanism(contract, claim)]
        comparison.selected = ""
        comparison.why_selected = "没有带定位的原文结果: 只给出待检假设, 不选中任何模型"
        comparison.note = "无法形成竞争模型: 缺少带定位的原文机制"
        return comparison

    comparison.mechanisms = [
        _mechanism_from_anchors(f"m{index + 1}", items, contract)
        for index, items in enumerate(groups.values())
    ]
    comparison.selected, comparison.why_selected = _select(comparison.mechanisms)
    comparison.conflicts = find_conflicts(comparison.mechanisms)
    comparison.distinguishing = [distinguishing_test(comparison.mechanisms, conflict)
                                 for conflict in comparison.conflicts]
    if len(comparison.mechanisms) < 2:
        comparison.note = "无法形成竞争模型: 原文只给出一条机制路径 (不硬凑对照)"
    elif not comparison.conflicts:
        comparison.note = "两条机制路径未出现方向冲突: 给出条件必要性检验"
        comparison.distinguishing = [_necessity_check(comparison.mechanisms)]
    return comparison


def _select(mechanisms: list[Mechanism]) -> tuple[str, str]:
    """只在**有来源且有可观测预测**的候选里选; 否则不选。"""
    eligible = [m for m in mechanisms if m.source_refs and m.predictions]
    if not eligible:
        return "", "所有候选都缺来源引用或可观测预测: 只输出待检假设, 不选中模型"
    chosen = max(eligible, key=lambda m: m.score())
    reasons = [f"有 {len(chosen.source_refs)} 条来源引用"]
    if chosen.predictions:
        reasons.append("有可观测预测")
    if chosen.formal_encoding:
        reasons.append("有形式化片段")
    if chosen.assumptions:
        reasons.append(f"{len(chosen.assumptions)} 条显式假设")
    return chosen.id, "; ".join(reasons)


def _necessity_check(mechanisms: list[Mechanism]) -> DistinguishingTest:
    a, b = mechanisms[0], mechanisms[1]
    return DistinguishingTest(
        kind="derivation" if (a.formal_encoding or b.formal_encoding) else "simulation",
        statement=f"检验条件是否必要: 去掉 {a.name} 的条件后结论是否仍成立",
        discriminates=[a.name, b.name],
        expected_if_a=a.predictions[0] if a.predictions else "",
        expected_if_b=b.predictions[0] if b.predictions else "",
        cost=1)


def _fill_missing(mechanism: Mechanism) -> None:
    missing: list[str] = []
    if not mechanism.formal_encoding:
        missing.append("形式化编码")
    if not mechanism.noise or "未说明" in mechanism.noise or "忽略" in mechanism.noise:
        missing.append("噪声/误差结构")
    if not mechanism.boundaries or "未声明" in mechanism.boundaries or "未限定" in mechanism.boundaries:
        missing.append("适用范围/边界")
    if not mechanism.predictions:
        missing.append("可观测预测")
    mechanism.missing = missing
    if not mechanism.source_refs:
        mechanism.weak.append("没有来源引用")
    if not mechanism.predictions:
        mechanism.weak.append("没有可观测预测")


def compare_mechanisms(mechanisms: list[Mechanism]) -> ModelComparison:
    """按"来源 + 可验证预测 + 形式化片段 + 成本"比较候选; 无来源则不选中。"""
    comparison = ModelComparison(mechanisms=list(mechanisms))
    if not mechanisms:
        comparison.why_selected = "没有候选机制"
        return comparison
    comparison.selected, comparison.why_selected = _select(mechanisms)
    comparison.conflicts = find_conflicts(mechanisms)
    comparison.distinguishing = [
        distinguishing_test(mechanisms, conflict) for conflict in comparison.conflicts
    ]
    return comparison


def find_conflicts(mechanisms: list[Mechanism]) -> list[dict]:
    """找出**真正冲突**的预测: 同一(处理,结果)下方向相反且条件相容。

    判定依据是原文给出的方向 (sign) 与条件, 不是"有条件/无条件"这类措辞。
    """
    conflicts: list[dict] = []
    for i in range(len(mechanisms)):
        for j in range(i + 1, len(mechanisms)):
            a, b = mechanisms[i], mechanisms[j]
            if not a.predictions or not b.predictions:
                continue
            if not a.sign or not b.sign or a.sign == b.sign:
                continue
            if not _conditions_compatible(a, b):
                continue
            conflicts.append({
                "a": a.id, "b": b.id,
                "a_prediction": a.predictions[0], "b_prediction": b.predictions[0],
                "kind": "prediction_conflict",
                "basis": f"同一对象下方向相反 (a={a.sign:+d}, b={b.sign:+d}), 条件相容",
            })
    return conflicts


def _conditions_compatible(a: Mechanism, b: Mechanism) -> bool:
    """条件相容: 同一条件, 或至少一方未声明范围 (此时才需要判定条件是否必要)。"""
    left, right = _normalize(a.boundaries), _normalize(b.boundaries)
    if not left or not right:
        return True
    return left == right or left in right or right in left


def distinguishing_test(mechanisms: list[Mechanism],
                        conflict: dict) -> DistinguishingTest:
    """为一条冲突预测生成可区分检验 (优先推导, 其次仿真)。"""
    a = next((m for m in mechanisms if m.id == conflict.get("a")), None)
    b = next((m for m in mechanisms if m.id == conflict.get("b")), None)
    a_name = a.name if a else str(conflict.get("a"))
    b_name = b.name if b else str(conflict.get("b"))
    encodable = bool((a and a.formal_encoding) or (b and b.formal_encoding))
    if encodable:
        kind = "derivation"
        statement = (f"在声明域内推导两机制的差异项并判定符号 "
                     f"(区分 {a_name} 与 {b_name})")
    else:
        kind = "simulation"
        statement = (f"设计仿真: 扫描变量范围, 检查 {a_name} 与 {b_name} 的预测何时分离")
    return DistinguishingTest(
        kind=kind, statement=statement, discriminates=[a_name, b_name],
        expected_if_a=conflict.get("a_prediction", ""),
        expected_if_b=conflict.get("b_prediction", ""),
        cost=3 if kind == "simulation" else 1,
    )


def compare_from_evidence(claim: Claim,
                          evidence: list[SourceEvidence] | None = None,
                          contract=None) -> ModelComparison:
    """端到端: 由原文锚点合成候选机制、比较, 并给出可区分(或必要性)检验。"""
    anchors = build_anchors(claim, evidence)
    comparison = synthesize_models(contract, anchors, claim=claim, evidence=evidence)
    if not comparison.distinguishing and len(comparison.mechanisms) >= 2:
        comparison.distinguishing = [_necessity_check(comparison.mechanisms)]
        comparison.note = comparison.note or "两条机制路径未出现方向冲突: 给出条件必要性检验"
    return comparison


__all__ = [
    "AnchoredResult",
    "DistinguishingTest",
    "Mechanism",
    "ModelComparison",
    "Term",
    "build_anchors",
    "build_terms",
    "candidate_mechanisms",
    "compare_from_evidence",
    "compare_mechanisms",
    "distinguishing_test",
    "find_conflicts",
    "infer_unit_from_text",
    "synthesize_models",
]
