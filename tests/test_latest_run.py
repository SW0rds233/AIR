import unittest
import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.reasoning import ReasoningAgent, classify_strategy, _formal_subject
from src.agents.protocol import AgentTask, AgentResult, ContextPack, ObjectRef, UsageRecord
from src.agents.runtime import AgentRuntime
from src.publication.render_latex import escape_latex, escape_math, render_latex
from src.publication.schemas import Block, Manuscript, Section
from src.research.acceptance import GateResult, publication_gate
from src.research.package import gate_manifest_fields, _render_unresolved
from src.research.problem_formulator import _obligations_for
from src.research.schemas import Claim, ResearchSnapshot
from src.research.question_planner import build_spec_from_input
from src.research.commit import ResearchCommitService
from src.research.store import ResearchStore
from src.research.snapshot import snapshot_from_store
from src.research.verification_service import VerificationService
from src.verification.runner import VerificationRunner
from src.agents.writing import WritingAgent
from src.publication.schemas import WritingPacket
from src.graph.research_graph import TeamRun
from src.agents.modeling import ModelingAgent, _modelling_subject


class LatestRunTest(unittest.TestCase):
    def test_math_model_does_not_require_an_empirical_mechanism_source(self):
        claim = Claim(id="math", statement="整数矩阵的等价关系", study={"design": "theory"},
                      strategy="derivation", claim_type="descriptive")
        context = ContextPack(objects={"claim": [claim.model_dump(mode="json")]})
        result = ModelingAgent().propose_via_kernel(AgentTask(agent="modeling", objective="形式化"),
            context, SimpleNamespace(), UsageRecord())
        self.assertIsNone(result, "应交给数学建模提案路径，而不是以缺乏机制文献阻塞")

    def test_modeling_return_task_uses_requested_claim(self):
        context = ContextPack(objects={"claim": [
            Claim(id="first", statement="背景").model_dump(mode="json"),
            Claim(id="target", statement="目标").model_dump(mode="json")]})
        task = AgentTask(agent="modeling", objective="补编码", hints={"claim_id": "target"})
        try:
            claim, _ = _modelling_subject(context, task)
        except TypeError:
            self.fail("建模目标选择未接入具体返工任务")
        self.assertEqual(claim.id, "target")

    def test_frozen_input_spec_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("test", Path(folder) / "research.sqlite")
            try:
                original = build_spec_from_input("已确认题目", project_id="test", problem_id="p")
                original.confirmed = True
                original.contract.frozen_version = 1
                store.put("spec", "p", original.model_dump(mode="json"))
                fake_team = SimpleNamespace(task_store=SimpleNamespace(store=store), problem_id="p",
                    project_id="test", request="已确认题目", attachment_text="新题面",
                    source_set_ids=[], source_policy="both", runtime=SimpleNamespace(emit=lambda *args: None))
                TeamRun._persist_spec(fake_team)
                self.assertEqual(store.get("spec", "p")["version"], 1)
                self.assertEqual(store.get("spec", "p")["original_request"], "已确认题目")
            finally:
                store.close()

    def generic_result(self, claims, existing=None, question="证明整数矩阵的等价关系", strategy=None):
        context = ContextPack(request="研究此问题并撰写成文", objects={"brief": [{
            "main_question": question}], "claim": existing or []})
        agent = ReasoningAgent()
        task = AgentTask(agent="reasoning", objective="分析问题", project_id="test", problem_id="p", run_id="r")
        runtime = SimpleNamespace(llm_available=lambda *args: True)
        with patch.object(agent, "formal_closure", return_value=None), patch.object(agent, "tool_loop",
             return_value=(json.dumps({"claims": claims, "strategy": strategy}, ensure_ascii=False), [])):
            return agent._run(task, context, runtime, UsageRecord(), [])

    def test_model_selected_strategy_is_not_discarded_by_keyword_fallback(self):
        result = self.generic_result([{"statement": "抽象结构的等价关系", "claim_type": "descriptive",
            "study": {"design": "theory"}}], question="分析该抽象结构的问题", strategy="derivation")
        self.assertEqual(result.payload["strategy"], "derivation")

    def test_math_candidate_type_and_verification_need_are_consistent(self):
        result = self.generic_result([{"statement": "整数矩阵的等价关系", "claim_type": "causal"}])
        proposal = result.proposed_changes[0]
        self.assertEqual(proposal.payload["claim_type"], "descriptive")
        self.assertEqual(proposal.payload["study"]["design"], "theory")
        self.assertEqual(proposal.payload["claim_type_input"], "causal")
        need = next(need for need in result.followup_needs if need.kind.value == "derivation")
        self.assertEqual(need.blocked_refs[0].id, proposal.object_id)
        self.assertEqual(need.hints["claim_id"], proposal.object_id)

    def test_repeat_claim_does_not_reset_previously_registered_candidate(self):
        existing = {"id": "c-old", "version": 2, "statement": "整数矩阵的等价关系",
                    "claim_type": "descriptive", "study": {"design": "theory"}}
        result = self.generic_result([dict(existing)], [existing])
        self.assertFalse(result.proposed_changes)

    def test_new_encoding_keeps_identity_and_increments_version(self):
        existing = {"id": "c-old", "version": 2, "statement": "整数矩阵的恒等式",
                    "claim_type": "descriptive", "study": {"design": "theory"},
                    "model_ref": {"id": "model-1", "version": 3}}
        result = self.generic_result([{"id": "c-old", "statement": existing["statement"],
            "claim_type": "descriptive", "lhs": "x+x", "rhs": "2*x", "relation": "=="}], [existing])
        proposal = result.proposed_changes[0]
        self.assertEqual(proposal.object_id, "c-old")
        self.assertEqual(proposal.payload["version"], 3)
        self.assertEqual(proposal.payload["model_ref"], existing["model_ref"])

    def test_empirical_candidate_is_not_relabelled_because_topic_mentions_math(self):
        result = self.generic_result([{"statement": "算法处理降低耗时", "claim_type": "causal",
            "study": {"design": "observational", "treatment": "算法", "outcome": "耗时"}}])
        self.assertEqual(result.proposed_changes[0].payload["claim_type"], "causal")

    def test_proof_negation_is_not_a_completed_proof_assertion(self):
        from src.publication.claims import asserts_completed_proof
        self.assertFalse(asserts_completed_proof("尚未完成证明，不能称为已完成证明。"))
        self.assertTrue(asserts_completed_proof("不是存在性证明；归约已完成证明。"))

    def test_publication_guard_checks_completed_proof_phrase(self):
        block = Block(role="claim", text="这是一个已完成证明的等价命题。")
        block.add_ref("claim", ObjectRef(id="pending"))
        gate = publication_gate("## 1 结论\n这是一个已完成证明的等价命题。",
            ResearchSnapshot(project_id="test", claims=[Claim(id="pending", statement="候选")]), blocks=[block],
            compile_status="ok")
        self.assertTrue(any("已完成证明" in reason for reason in gate.reasons))

    def test_completed_proof_phrase_is_rejected_for_pending_claim(self):
        block = Block(role="claim", text="这是一个已完成证明的等价命题。")
        block.add_ref("claim", ObjectRef(id="pending"))
        manuscript = Manuscript(sections=[Section(heading="结论", blocks=[block])])
        problems = WritingAgent().audit_manuscript(manuscript, WritingPacket(claims=[{
            "id": "pending", "status": "proposed"}]))
        self.assertTrue(any("越权措辞" in problem for problem in problems))

    def test_old_obligations_are_not_reused_for_a_changed_claim(self):
        from src.research.schemas import ProofObligation
        claim = Claim(id="target", version=2, statement="新命题", lhs="x+x", rhs="2*x", relation="==")
        old = ProofObligation(claim_id="target", claim_version=1, statement="核验旧命题", kind="prove_identity")
        context = ContextPack(objects={"claim": [claim.model_dump(mode="json")],
                                       "obligation": [old.model_dump(mode="json")]})
        task = AgentTask(agent="reasoning", objective="核验", hints={"claim_id": "target"})
        self.assertFalse(_formal_subject(task, context)[1])

    def test_unconfirmed_empty_spec_is_repaired_from_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("test", Path(folder) / "research.sqlite")
            try:
                original = build_spec_from_input("研究此问题并撰写成文", project_id="test", problem_id="p")
                store.put("spec", "p", original.model_dump(mode="json"))
                fake_team = SimpleNamespace(task_store=SimpleNamespace(store=store), problem_id="p",
                    project_id="test", request="研究此问题并撰写成文",
                    attachment_text="是否存在给定两两 Hamming 距离的二进制序列？",
                    source_set_ids=[], source_policy="both", runtime=SimpleNamespace(emit=lambda *args: None))
                TeamRun._persist_spec(fake_team)
                row = store.get("spec", "p")
                self.assertIn("Hamming", row["problem_statement"])
                self.assertEqual(row["research_type"], "formal_proof")
                self.assertGreater(row["version"], 1)
            finally:
                store.close()

    def test_formal_contract_uses_natural_language_mathematical_problem(self):
        question = "研究是否存在给定长度、给定两两 Hamming 距离的二进制序列族"
        spec = build_spec_from_input(question, project_id="test")
        self.assertEqual(spec.contract.task_kind.value, "formal_proof")
        self.assertEqual(spec.problem_statement, question)

    def test_reasoning_strategy_uses_attachment_question_in_brief(self):
        context = ContextPack(request="研究此问题并撰写成文", objects={"brief": [{
            "main_question": "是否存在给定两两 Hamming 距离的二进制序列？"}]})
        agent = ReasoningAgent()
        task = AgentTask(agent="reasoning", objective="分析问题")
        runtime = SimpleNamespace(llm_available=lambda *args: False)
        with patch.object(agent, "formal_closure", return_value=AgentResult(task_id=task.task_id)) as formal:
            agent._run(task, context, runtime, UsageRecord(), [])
        formal.assert_called_once()

    def test_verification_return_task_uses_requested_claim_not_first_claim(self):
        a = Claim(id="c1", statement="候选一", study={"design": "theory"})
        b = Claim(id="c2", statement="候选二", study={"design": "theory"}, lhs="x+x", rhs="2*x", relation="==")
        context = ContextPack(objects={"claim": [a.model_dump(mode="json"), b.model_dump(mode="json")]})
        task = AgentTask(agent="reasoning", objective="核验正文结论", input_refs=[ObjectRef(id="c2")],
                         hints={"need_kind": "derivation", "claim_id": "c2"})
        subject = _formal_subject(task, context)
        self.assertEqual(subject[0].id, "c2")

    def test_formal_closure_commits_real_verification_and_proof_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("test", Path(folder) / "research.sqlite")
            try:
                runtime = AgentRuntime()
                runtime.research_store = store
                service = VerificationService(store=store, runner=VerificationRunner(inproc=True))
                claim = Claim(id="c-expand", statement="(x+1)^2=x^2+2x+1",
                              lhs="(x+1)**2", rhs="x**2+2*x+1", relation="==", variables=["x"],
                              variable_domains={"x": "real"}, study={"design": "theory"},
                              claim_type="descriptive", strategy="derivation")
                task = AgentTask(agent="reasoning", objective="核验恒等式", project_id="test",
                                 problem_id="p", run_id="r", input_refs=[ObjectRef(id=claim.id)])
                context = ContextPack(objects={"claim": [claim.model_dump(mode="json")]})
                with patch.object(runtime, "verification_service", return_value=service):
                    result = ReasoningAgent().formal_closure(task, context, runtime, UsageRecord())
                self.assertIsNotNone(result)
                committed = ResearchCommitService(store).commit(task, result, current_versions={})
                self.assertFalse(committed.rejected)
                snapshot = snapshot_from_store(store, project_id="test", problem_id="p", run_id="r")
                self.assertTrue(snapshot.attempts, "推导尝试必须和核验一起持久化")
                self.assertTrue(snapshot.verifications, "必须真实运行适配器，不以文案代替核验")
                self.assertEqual(snapshot.claims[0].status.value, "supported")
            finally:
                store.close()

    def test_explicit_theory_claim_does_not_require_a_population(self):
        claim = Claim(statement="整数矩阵满足给定秩界", claim_type="descriptive",
                      study={"design": "theory"}, strategy="derivation")
        obligations = _obligations_for(claim, False, {"sympy": True})
        self.assertNotIn("scope_check", [item.kind for item in obligations])
        self.assertTrue(obligations, "理论断言仍需证明义务，不能免检")

    def test_formal_existence_question_is_not_synthesis(self):
        self.assertEqual(classify_strategy(
            "是否存在若干二进制序列，每条长度为 n，两两 Hamming 距离为 d？"), "derivation")

    def test_failed_gate_has_a_standalone_explanation(self):
        fields = gate_manifest_fields(theory=GateResult(False, unresolved=["结论未定 c1"]))
        self.assertIn("结论未定 c1", fields["theory_gate_reasons"])
        self.assertFalse(fields["gate_passed"])

    def test_prime_is_math_in_both_modes(self):
        self.assertNotIn("′", escape_latex("矩阵 X′"))
        self.assertNotIn("′", escape_math("X′X"))

    def test_dense_inline_equation_stays_in_one_math_span(self):
        rendered = escape_latex("于是 XXᵀ=nI_M+(n−2d)(J_M−I_M)，得到结论。")
        self.assertNotIn(r"\_", rendered)
        self.assertNotIn("$=", rendered)
        self.assertIn("$XX", rendered)

    def test_dense_math_detection_does_not_create_unbalanced_set_fragments(self):
        rendered = escape_latex("取 X_i={0,1}^n。")
        spans = re.findall(r"\$([^$]*)\$", rendered)
        self.assertTrue(all(span.count("{") == span.count("}") for span in spans))

    def test_snake_case_metadata_is_not_a_mathematical_equation(self):
        rendered = escape_latex("source_id=abc，version_tag=record_b")
        self.assertNotIn("$", rendered)

    def test_long_display_relations_can_break_without_rescaling(self):
        manuscript = Manuscript(sections=[Section(heading="恒等式", blocks=[Block(
            math="nM-2(M-1)d=(v-2k)^2; r(k-1)=lambda(v-1); bk=vr")])])
        tex = render_latex(manuscript)
        self.assertIn(r"\begin{aligned}", tex)
        self.assertNotIn("; r(k-1)", tex)

    def test_repeated_note_wrappers_do_not_duplicate_gap_detail(self):
        snapshot = ResearchSnapshot(project_id="test")
        report = _render_unresolved(snapshot, [
            "more_sources: 缺少原始定理出处", "need: more_sources: 缺少原始定理出处",
            "clause: 需要明确整数域", "clause: 需要明确整数域"])
        self.assertEqual(report.count("缺少原始定理出处"), 1)
        self.assertEqual(report.count("需要明确整数域"), 1)


if __name__ == "__main__":
    unittest.main()
