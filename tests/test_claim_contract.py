"""Regression for the state endpoint reading a strategy in claim_type."""
import tempfile
import unittest
from pathlib import Path

from src.agents.protocol import AgentTask, AgentResult, ChangeProposal
from src.research.commit import ResearchCommitService
from src.research.inspection import StoreInspection
from src.research.projection import object_rows
from src.research.schemas import Claim, ResearchSpec
from src.research.snapshot import snapshot_from_store
from src.research.store import ResearchStore


class ClaimContractTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ResearchStore("repro", Path(self.directory.name) / "research.sqlite")
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.store.close)
        self.task = AgentTask(agent="reasoning", objective="derive", project_id="repro",
                              problem_id="problem", run_id="run")

    def test_stored_derivation_claim_is_readable_and_exportable_without_rewriting_history(self):
        payload = {"id": "clm-repro", "statement": "A candidate mathematical implication",
                   "claim_type": "derivation", "_scope": {"problem_id": "problem", "run_id": "run"}}
        self.store.put("claim", "clm-repro", payload)
        view = StoreInspection(self.store, ResearchSpec(project_id="repro", problem_id="problem"))
        claim = view.claims("problem")[0]
        self.assertEqual(claim.claim_type.value, "descriptive")
        self.assertEqual(claim.strategy, "derivation")
        self.assertEqual(claim.status.value, "proposed")
        self.assertEqual(self.store.get("claim", "clm-repro")["claim_type"], "derivation")
        rows = object_rows(self.store, "claim", run_id="run")
        self.assertEqual(rows[0]["claim_type"], "descriptive")
        snapshot = snapshot_from_store(self.store, project_id="repro", problem_id="problem", run_id="run")
        self.assertEqual(len(snapshot.claims), 1)
        self.assertFalse(snapshot.gaps)

    def test_new_claim_is_normalised_before_commit(self):
        proposal = ChangeProposal(kind="claim", object_id="clm-new", payload={
            "statement": "Candidate implication", "claim_type": "derivation", "reasoning": ["premise"]})
        result = AgentResult(task_id=self.task.task_id, agent="reasoning", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(self.task, result, current_versions={})
        self.assertTrue(outcome.committed)
        stored = self.store.get("claim", "clm-new")
        self.assertEqual(stored["claim_type"], "descriptive")
        self.assertEqual(stored["strategy"], "derivation")
        self.assertEqual(stored["reasoning"], ["premise"])

    def test_unknown_claim_type_is_rejected_before_persistence(self):
        proposal = ChangeProposal(kind="claim", payload={"statement": "Invalid type", "claim_type": "invented"})
        result = AgentResult(task_id=self.task.task_id, agent="reasoning", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(self.task, result, current_versions={})
        self.assertFalse(outcome.accepted)
        self.assertEqual(len(outcome.rejected), 1)
        self.assertIn("claim_type", outcome.rejected[0]["reason"])
        self.assertFalse(self.store.list_latest("claim"))

    def test_canonical_claim_type_is_not_reclassified(self):
        claim = Claim(statement="A causal candidate", claim_type="causal")
        self.assertEqual(claim.claim_type.value, "causal")

    def test_classification_compatibility_never_accepts_agent_reported_proof_status(self):
        proposal = ChangeProposal(kind="claim", object_id="clm-unproved", payload={
            "statement": "An unverified derivation", "claim_type": "derivation", "status": "supported",
            "assurance": "formally_checked", "validation_status": "verified", "coverage": "target"})
        result = AgentResult(task_id=self.task.task_id, agent="reasoning", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(self.task, result, current_versions={})
        self.assertTrue(outcome.committed)
        stored = self.store.get("claim", "clm-unproved")
        self.assertNotEqual(stored["status"], "supported")
        self.assertEqual(stored["assurance"], "unverified")
        self.assertEqual(stored["claim_type_input"], "derivation")

    def test_malformed_claim_body_is_rejected_with_a_diagnostic(self):
        proposal = ChangeProposal(kind="claim", payload={"statement": {"text": "not a string"}})
        result = AgentResult(task_id=self.task.task_id, agent="reasoning", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(self.task, result, current_versions={})
        self.assertFalse(outcome.accepted)
        self.assertIn("statement", outcome.rejected[0]["reason"])
        self.assertFalse(self.store.list_latest("claim"))


if __name__ == "__main__":
    unittest.main()
