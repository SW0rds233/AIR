from src.agents.protocol import AgentTask, ContextPack
from src.agents.reasoning import ReasoningAgent
from src.agents.runtime import AgentRuntime
from src.research.commit import ResearchCommitService
from src.research.problem_formulator import obligations_for_claim
from src.research.schemas import Claim, ProofObligation, SourceEvidence
from src.research.store import ResearchStore
from src.research.verification_service import VerificationService


def test_unmeasured_parameters_keep_a_conditional_proof_executable():
    claim = Claim(
        id="threshold", version=2, statement="若 q2>q1 且 q1F≥C 则 q2F≥C",
        claim_type="descriptive", study={"design": "theory", "measurement_notes": "参数尚未实测"},
        formal_spec={"variables": {k: "Real" for k in ("q1", "q2", "F", "C")},
                     "premises": ["q2>q1", "F>0", "C>0", "q1*F>=C"], "conclusion": "q2*F>=C"},
        evidence_requirements=["将模型用于船舶行为时需观测检出概率"],
        scope_conditions="仅对所列模型前提成立")
    duties = obligations_for_claim(claim)
    assert [o.kind for o in duties] == ["check_implication"]
    assert duties[0].claim_version == 2

    claim.study.rows = [{"q1": .4}]
    kinds = {o.kind for o in obligations_for_claim(claim)}
    assert "evidence_support" in kinds and "check_implication" not in kinds


def test_rule_certificate_survives_reasoning_and_commit(tmp_path):
    claim = Claim(id="scope", statement="样本范围内的观测", claim_type="descriptive",
                  study={"design": "observational"}, scope_conditions="限于已登记样本")
    scope = ProofObligation(id="scope-duty", statement="研究对象", kind="scope_check",
                            acceptance_method="rule", claim_id=claim.id)
    task = AgentTask(agent="reasoning", objective="核对适用范围", project_id="p", problem_id="q",
                     run_id="r", hints={"claim_id": claim.id, "verification_only": True})
    context = ContextPack(objects={"claim": [claim.model_dump(mode="json")],
                                   "obligation": [scope.model_dump(mode="json")]})
    store = ResearchStore("p", tmp_path / "scope.sqlite")
    try:
        store.put("claim", claim.id, claim.model_dump(mode="json"))
        store.put("obligation", scope.id, scope.model_dump(mode="json"))
        result = ReasoningAgent().run(task, context, AgentRuntime())
        commit = ResearchCommitService(store).commit(task, result)
        assert commit.committed
        assert store.get("obligation", scope.id)["status"] == "closed"
        record, = store.list_latest("verification")
        assert record["usable"] and record["certificate"] == claim.scope_conditions
    finally:
        store.close()


def test_unbound_or_old_evidence_cannot_close_another_targets_rule():
    claim = Claim(id="target", version=2, statement="观测结论", claim_type="descriptive")
    duty = ProofObligation(kind="evidence_support", statement="来源支持", acceptance_method="rule",
                           claim_id=claim.id, claim_version=2)
    ev = SourceEvidence(title="Study", support="supports", credibility="high", existence_verified=True,
                        peer_reviewed=True, claim_id="another", claim_version=2)
    service = VerificationService()
    for claim_id, version in (("", 2), ("another", 2), (claim.id, 1), (claim.id, 2)):
        evidence = ev.model_copy(update={"claim_id": claim_id, "claim_version": version})
        assert service.check_rule(claim, duty, evidence=[evidence]).closed == (
            claim_id == claim.id and version == 2)
