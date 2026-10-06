"""Regression coverage for model records used by the workbench and export."""

import copy
import tempfile
import unittest
from pathlib import Path

from src.agents.protocol import AgentResult, AgentTask, ChangeProposal
from src.experiments.planner import design_experiment
from src.research.commit import ResearchCommitService
from src.research.inspection import StoreInspection
from src.research.projection import object_rows
from src.research.schemas import Claim, ResearchModel, ResearchSpec
from src.research.snapshot import snapshot_from_store
from src.research.store import ResearchStore


MODEL = {
    "id": "mdl-regression",
    "name": "Order-14 model",
    "variables": [
        {"symbol": "n", "meaning": "order", "unit": "1", "domain": "n=14"},
        {"symbol": "n mod 4", "meaning": "residue", "domain": "2"},
        {"symbol": "S", "meaning": "sum of two squares", "domain": "integers"},
        {"symbol": "14", "meaning": "fixed order", "domain": "14"},
    ],
    "assumptions": [
        {"statement": "Assume the plane exists", "origin": "proposed", "source_ids": []},
        {"statement": "Apply the necessary condition", "origin": "external",
         "source_ids": ["ev-theorem"]},
        {"statement": "14 is not a sum of two squares", "origin": "derived", "source_ids": []},
    ],
    "applicability": "finite planes of order 14",
    "open_conditions": ["Check the theorem hypotheses"],
    "_scope": {"project_id": "repro", "problem_id": "problem", "run_id": "run-repro"},
}


class ModelContractTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ResearchStore("repro", Path(self.directory.name) / "research.sqlite")
        self.spec = ResearchSpec(project_id="repro", problem_id="problem")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_persisted_structured_model_can_be_read_and_exported(self):
        self.store.put("model", MODEL["id"], copy.deepcopy(MODEL))
        model = StoreInspection(self.store, self.spec).models()[0].model_dump(mode="json")
        self.assertEqual(model["variables"][0]["meaning"], "order")
        self.assertEqual(model["assumptions"][1]["source_ids"], ["ev-theorem"])
        self.assertEqual(model["open_conditions"], MODEL["open_conditions"])
        snapshot = snapshot_from_store(self.store, project_id="repro", problem_id="problem",
                                       run_id="run-repro")
        self.assertEqual(len(snapshot.models), 1)
        self.assertFalse(snapshot.gaps)
        exported = snapshot.models[0].model_dump(mode="json")
        self.assertEqual(exported["variables"][0]["domain"], "n=14")
        self.assertEqual(exported["source_refs"], ["ev-theorem"])

    def test_existing_string_records_have_the_same_agent_contract(self):
        self.store.put("model", "mdl-old", {
            "id": "mdl-old", "variables": ["x"], "variable_domains": {"x": "real"},
            "units": {"x": "m"}, "assumptions": ["asm-given", "x is positive"],
            "_scope": MODEL["_scope"],
        })
        row = object_rows(self.store, "model", run_id="run-repro")[0]
        self.assertEqual(row["variables"][0]["symbol"], "x")
        self.assertEqual(row["variables"][0]["domain"], "real")
        self.assertEqual(row["variables"][0]["unit"], "m")
        self.assertEqual(row["assumptions"][0]["id"], "asm-given")
        self.assertEqual(row["assumptions"][1]["statement"], "x is positive")
        self.assertIsNone(row["assumptions"][1]["origin"])
        self.assertEqual(row["_scope"], MODEL["_scope"])

    def test_commit_writes_a_model_readable_by_the_workbench(self):
        task = AgentTask(agent="modeling", objective="Build a model", project_id="repro",
                         problem_id="problem", run_id="run-repro")
        proposal = ChangeProposal(kind="model", object_id=MODEL["id"], payload=copy.deepcopy(MODEL))
        result = AgentResult(task_id=task.task_id, agent="modeling", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(task, result, current_versions={})
        self.assertTrue(outcome.committed)
        model = StoreInspection(self.store, self.spec).models()[0].model_dump(mode="json")
        self.assertEqual(model["variables"][0]["symbol"], "n")
        self.assertEqual(model["assumptions"][1]["origin"], "external")

    def test_invalid_model_is_rejected_before_it_enters_the_store(self):
        task = AgentTask(agent="modeling", objective="Build a model", project_id="repro")
        proposal = ChangeProposal(kind="model", payload={"name": "Bad model", "variables": [{}]})
        result = AgentResult(task_id=task.task_id, agent="modeling", proposed_changes=[proposal])
        outcome = ResearchCommitService(self.store).commit(task, result, current_versions={})
        self.assertFalse(outcome.accepted)
        self.assertEqual(len(outcome.rejected), 1)
        self.assertIn("symbol", outcome.rejected[0]["reason"])
        self.assertFalse(self.store.list_latest("model"))

    def test_malformed_nested_values_are_rejected_without_crashing_commit(self):
        for payload in ({"variables": [{"symbol": {}}]},
                        {"source_refs": [{}]},
                        {"assumptions": [{"statement": "A", "source_ids": [{}]}]}):
            with self.subTest(payload=payload):
                task = AgentTask(agent="modeling", objective="Build a model", project_id="repro")
                proposal = ChangeProposal(kind="model", payload=payload)
                result = AgentResult(task_id=task.task_id, agent="modeling", proposed_changes=[proposal])
                outcome = ResearchCommitService(self.store).commit(task, result, current_versions={})
                self.assertFalse(outcome.accepted)
                self.assertEqual(len(outcome.rejected), 1)
        self.assertFalse(self.store.list_latest("model"))

    def test_validation_advice_uses_variable_symbols_units_and_domains(self):
        model = ResearchModel.model_validate(MODEL).model_dump(mode="json")
        plan = design_experiment(Claim(statement="A condition needs verification"), model=model)
        variable = next(v for v in plan.variables if v.name == "n")
        self.assertEqual(variable.unit, "1")
        self.assertEqual(variable.range, "n=14")
        self.assertFalse(any(v.name.startswith("{") for v in plan.variables))


if __name__ == "__main__":
    unittest.main()
