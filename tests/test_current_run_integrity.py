import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.protocol import AgentResult, AgentTask, ChangeProposal, ContextPack, UsageRecord
from src.agents.reasoning import ReasoningAgent
from src.agents.writing import WritingAgent
from src.graph.research_graph import context_read_versions
from src.publication.claims import asserts_completed_proof
from src.publication.schemas import Block, Manuscript, Section, WritingPacket
from src.research.commit import ResearchCommitService
from src.research.projection import object_rows
from src.research.reasoning_kernel import claim_state_for
from src.research.schemas import Claim, ProofObligation, ResearchSpec
from src.research.snapshot import snapshot_from_store
from src.research.store import ResearchStore
from src.verification.sympy_adapter import (
    op_equality_condition, op_find_counterexample, op_prove_identity,
    op_prove_inequality, op_prove_monotonicity,
)


class CurrentRunIntegrityTest(unittest.TestCase):
    def test_old_refuted_obligation_cannot_refute_revised_claim(self):
        claim = Claim(id="c", version=2, statement="修订后命题")
        old = ProofObligation(claim_id="c", claim_version=1,
                              statement="旧命题反例", kind="find_counterexample",
                              status="refuted", counterexample={"x": 0})
        disposition = claim_state_for(claim, [old], [], aligned=lambda *_: True)
        self.assertNotEqual(disposition.status.value, "refuted")

    def test_writing_audit_flags_wrong_code_length(self):
        manuscript = Manuscript(sections=[Section(heading="结果", blocks=[Block(
            role="reasoning", text="二元码的码长 n = 668、码字数 M = 668。")])])
        packet = WritingPacket(main_question=(
            "是否存在 668 条长度为 667、两两 Hamming 距离为 334 的二进制序列？"))
        problems = WritingAgent().audit_manuscript(manuscript, packet)
        self.assertTrue(any("码长冲突" in item for item in problems))

    def test_context_read_set_uses_storage_revision_not_claim_version(self):
        context = ContextPack(objects={"claim": [{"id": "c", "version": 1,
            "_storage_revision": 3}], "brief": [{"id": "b", "version": 9}]})
        self.assertEqual(context_read_versions(context), {"c": 3})

    def test_commit_rejects_candidate_conflicting_with_stored_problem(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("p", Path(folder) / "research.sqlite")
            try:
                store.put("spec", "problem", ResearchSpec(
                    project_id="p", problem_id="problem",
                    problem_statement="是否存在 668 条长度为 667、两两 Hamming 距离为 334 的二进制序列？",
                ).model_dump(mode="json"))
                task = AgentTask(agent="reasoning", objective="derive", project_id="p",
                                 problem_id="problem", run_id="r")
                proposal = ChangeProposal(kind="claim", object_id="wrong", payload={
                    "statement": "码长 n = 668、码字数 M = 668、距离 d = 334 的二元码存在"})
                result = AgentResult(task_id=task.task_id, agent="reasoning",
                                     proposed_changes=[proposal])
                outcome = ResearchCommitService(store, problem_id="problem").commit(
                    task, result, current_versions={})
                self.assertFalse(outcome.accepted)
                self.assertIn("码长冲突", outcome.rejected[0]["reason"])
                self.assertIsNone(store.get("claim", "wrong"))
            finally:
                store.close()

    def test_claim_content_version_survives_state_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("p", Path(folder) / "research.sqlite")
            try:
                task = AgentTask(agent="reasoning", objective="derive", project_id="p",
                                 problem_id="problem", run_id="r")
                proposal = ChangeProposal(kind="claim", object_id="claim-1", payload={
                    "id": "claim-1", "version": 2, "statement": "x+x=2x",
                    "lhs": "x+x", "rhs": "2*x", "relation": "=="})
                result = AgentResult(task_id=task.task_id, agent="reasoning",
                                     proposed_changes=[proposal])
                ResearchCommitService(store, problem_id="problem").commit(
                    task, result, current_versions={})
                stored = store.get("claim", "claim-1")
                self.assertEqual(stored["version"], 2)
                store.put("claim", "claim-1", {**stored, "notes": "状态审阅，不改变陈述"})
                view = object_rows(store, "claim", run_id="r")[0]
                self.assertEqual(view["version"], 2)
                self.assertEqual(view["_storage_revision"], 2)
                snapshot = snapshot_from_store(store, project_id="p",
                                               problem_id="problem", run_id="r")
                self.assertEqual(snapshot.claims[0].version, 2)
            finally:
                store.close()
    def test_true_non_strict_inequality_has_no_counterexample(self):
        result = op_find_counterexample({
            "lhs": "667*334", "rhs": "668*668/2", "relation": "<=",
            "variables": ["n", "M", "d"],
            "assumptions": {"n": "{667}", "M": "{668}", "d": "{334}"},
        })
        self.assertNotEqual(result.status.value, "failed")
        self.assertFalse(result.counterexample)

    def test_counterexample_witness_is_sampled_point_not_expression_value(self):
        result = op_find_counterexample({
            "lhs": "x", "rhs": "0", "relation": "<=",
            "variables": ["x"], "assumptions": {"x": "positive"},
        })
        self.assertEqual(result.status.value, "failed")
        self.assertGreater(Fraction(str(result.counterexample["x"])), 0)

    def test_strict_counterexample_search_checks_negative_diff(self):
        result = op_find_counterexample({
            "lhs": "-x*x-1", "rhs": "0", "relation": ">",
            "variables": ["x"], "assumptions": {"x": "real"},
        })
        self.assertEqual(result.status.value, "failed")
        self.assertLess(-Fraction(str(result.counterexample["x"])) ** 2 - 1, 0)

    def test_stationary_point_is_not_refutation_of_strict_monotonicity(self):
        args = {"expr": "x**3", "wrt": "x", "direction": "increasing",
                "variables": ["x"], "assumptions": {"x": "real"}}
        self.assertNotEqual(op_find_counterexample(args).status.value, "failed")
        self.assertNotEqual(op_prove_monotonicity(args).status.value, "failed")

    def test_counterexample_must_obey_fixed_domain(self):
        result = op_find_counterexample({
            "lhs": "x", "rhs": "0", "relation": ">=",
            "variables": ["x"], "assumptions": {"x": "{1}"},
        })
        self.assertNotEqual(result.status.value, "failed")

    def test_strict_inequality_does_not_use_out_of_domain_equality(self):
        arguments = {"lhs": "x", "rhs": "0", "relation": ">",
                     "variables": ["x"], "assumptions": {"x": "positive"}}
        self.assertNotEqual(op_find_counterexample(arguments).status.value, "failed")
        self.assertNotEqual(op_prove_inequality(arguments).status.value, "failed")

    def test_identity_equality_condition_is_all_assignments(self):
        result = op_equality_condition({"lhs": "668", "rhs": "4*167",
                                        "variables": ["n"]})
        self.assertEqual(result.status.value, "passed")
        self.assertIn("恒成立", result.certificate)

    def test_long_negation_does_not_claim_a_completed_proof(self):
        self.assertFalse(asserts_completed_proof(
            "既没有显式证书，也没有排除全部 668 阶 ±1 矩阵的严格证明。"))
        self.assertTrue(asserts_completed_proof("我们给出了严格证明。"))

    def test_concrete_dot_is_verified_but_symbolic_vectors_are_not_assumed(self):
        concrete = op_prove_identity({
            "lhs": "dot((1,-1),(1,1))", "rhs": "0", "variables": []})
        symbolic = op_prove_identity({
            "lhs": "dot(u,v)", "rhs": "0", "variables": ["u", "v"]})
        self.assertEqual(concrete.status.value, "passed")
        self.assertNotEqual(symbolic.status.value, "passed")

    def test_candidate_with_wrong_code_length_is_not_submitted(self):
        candidate = {
            "statement": "存在二元等距码：码长 n = 668、码字数 M = 668、两两 Hamming 距离 d = 334",
            "claim_type": "descriptive", "study": {"design": "theory"},
        }
        context = ContextPack(request="研究此问题", objects={"brief": [{
            "main_question": "是否存在 668 条长度为 667、两两 Hamming 距离为 334 的二进制序列？"}]})
        task = AgentTask(agent="reasoning", objective="分析等距码存在性",
                         project_id="p", problem_id="problem", run_id="r")
        agent = ReasoningAgent()
        runtime = SimpleNamespace(llm_available=lambda: True)
        with patch.object(agent, "formal_closure", return_value=None), patch.object(
                agent, "tool_loop", return_value=(json.dumps({"claims": [candidate]},
                                                     ensure_ascii=False), [])):
            result = agent._run(task, context, runtime, UsageRecord(), [])
        self.assertFalse(any(change.kind == "claim" for change in result.proposed_changes))
        self.assertTrue(any("码长" in item for item in result.unresolved))


if __name__ == "__main__":
    unittest.main()
