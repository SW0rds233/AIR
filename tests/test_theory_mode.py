"""Formal research invariants retained under the unified team architecture.

The old standalone theory engine is gone. These checks exercise its still-live
dependency, persistence and verification services directly.
"""

import pytest


def test_dependency_cycle_is_rejected_and_changes_propagate():
    from src.research.dependency_graph import CyclicDependencyError, DependencyGraph

    graph = DependencyGraph([("asm", "L1"), ("L1", "L2"), ("L2", "T")])
    assert graph.stale_closure("asm") == {"L1", "L2", "T"}
    with pytest.raises(CyclicDependencyError):
        graph.add_edge("T", "asm")


def test_research_step_and_tool_run_are_idempotent(tmp_path):
    from src.research.store import (KIND_CLAIM, KIND_OBLIGATION, ResearchStore,
                                    StepAlreadyApplied)

    store = ResearchStore("formal-ledger", db_path=tmp_path / "research.sqlite")
    try:
        versions = store.submit_step(
            writes=[(KIND_CLAIM, "c1", {"v": 1}),
                    (KIND_OBLIGATION, "o1", {"v": 1})],
            events=[("formulated", {"claims": 1, "problem_id": "p1"})],
            idempotency_key="step-1")
        assert versions == {"c1": 1, "o1": 1}
        with pytest.raises(StepAlreadyApplied):
            store.submit_step(writes=[(KIND_CLAIM, "c1", {"v": 2})],
                              idempotency_key="step-1")
        assert store.get(KIND_CLAIM, "c1")["v"] == 1
        assert store.begin_tool_run("tool-1", "sympy", "simplify", {"expr": "x"},
                                    idempotency_key="same-tool") is True
        assert store.begin_tool_run("tool-1", "sympy", "simplify", {"expr": "x"},
                                    idempotency_key="same-tool") is False
    finally:
        store.close()


def test_strict_claim_cannot_use_equality_as_a_proof():
    from src.verification.runner import VerificationRunner

    runner = VerificationRunner(inproc=True)
    question = {"lhs": "x**2 + y**2", "rhs": "2*x*y", "variables": ["x", "y"],
                "assumptions": {"x": "real", "y": "real"}}
    non_strict = runner.sympy("prove_inequality", {**question, "relation": ">="})
    strict = runner.sympy("prove_inequality", {**question, "relation": ">"})
    assert non_strict.status.value == "passed"
    assert strict.status.value == "failed"
    assert strict.counterexample == {"x": 0, "y": 0}
