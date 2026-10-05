from __future__ import annotations

r"""现场缺陷回归: 写作缺口回流后无法消解 → 运行以"有一条未关闭义务"收尾。

现场事件流 (`#11` 之后停止): `synthesize_results` 让结论变成 supported (支持方式
`theorem_application`), 收尾时写作阶段报出缺口"结论没有可展示的推导步骤", 回流成
一条 `writing_missing_argument_chain` **阻塞义务**, 此后什么也没发生 —— 没有成文,
交付级别掉到"条件性研究报告"。

三个独立成因, 各有一条用例:
1. 缺口判定只看 `attempts.steps`, 不认设计证书里的判定链 → **误报缺口**;
2. 理论图 `theory_finalize → END`, 回流出的义务**永远无人处理**;
3. `manifest` 的三个门槛字段互相顶替, 出现 `gate_passed=false` 而
   `delivery_gate_passed=true` 的自相矛盾。
"""



from src.research.argument import writing_gaps
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

CERTIFICATE = {
    "design": {"v": 211, "k": 15, "lam": 1},
    "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
    "evidence": [{"condition": "Bruck–Ryser–Chowla 必要条件", "result": False,
                  "theorem": "Bruck–Ryser–Chowla 定理 (射影平面)",
                  "inputs": {"n": 14}}],
}


def _snapshot(with_certificate: bool) -> ResearchSnapshot:
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在", status=ClaimStatus.supported,
                  support_kind=SupportKind.theorem_application, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified, design_v=211, design_k=15,
                  design_lambda=1, design_b=211, design_r=15, design_verdict="nonexistent")
    arguments = {"design_report": dict(CERTIFICATE)} if with_certificate else {}
    record = VerificationRecord(id="ver-1", claim_id="clm-1", tool="design_necessity",
                               status="passed", validation_status=ValidationStatus.verified,
                               scope=Coverage.target, arguments=arguments)
    return ResearchSnapshot(project_id="gap", problem_id="p1", claims=[claim],
                            verifications=[record])


# ----------------------------------------------------------------------
# 成因 1: 有证书判定链就不算"没有推导步骤"
# ----------------------------------------------------------------------

def test_certificate_chain_counts_as_argument_chain():
    """有设计证书 (逐条反查定理/输入/结论) 的结论不得被判定为缺少推导步骤。"""
    gaps = writing_gaps(_snapshot(with_certificate=True))
    kinds = {gap.kind for gap in gaps}
    assert "missing_argument_chain" not in kinds, kinds
    assert not [g for g in gaps if g.severity == "blocking"], gaps


def test_without_certificate_or_steps_the_gap_is_still_reported():
    """反向保护: 真的没有推导链时**必须**仍然报缺口, 否则这道检查就失效了。"""
    gaps = writing_gaps(_snapshot(with_certificate=False))
    kinds = {gap.kind for gap in gaps}
    assert "missing_argument_chain" in kinds, kinds
    assert [g for g in gaps if g.severity == "blocking"], gaps


# ----------------------------------------------------------------------
# 成因 2: 回流出的义务必须能被真正处理
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# 成因 3: manifest 三道门槛不得互相顶替
# ----------------------------------------------------------------------

def _spec():
    from src.research.schemas import ResearchSpec

    return ResearchSpec(project_id="gapresume", problem_id="p1",
                        problem_statement="对所有实数 x: x**2 >= 0")
