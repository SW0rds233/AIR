import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.agents.supervisor import SupervisorAgent, _compose_main_question
from src.agents.modeling import ModelingAgent
from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.publication.claims import asserts_completed_proof
from src.publication.render_latex import render_latex
from src.publication.schemas import Block, Manuscript, Section
from src.research.problem_formulator import parse_questions
from src.research.schemas import Claim, ProofAttempt, ResearchModel, ResearchSnapshot
from src.research.acceptance import delivery_gate
from src.research.commit import ResearchCommitService
from src.research.store import ResearchStore


class NewRunRegressions(unittest.TestCase):
    def test_attachment_metadata_is_not_a_research_question(self):
        attachment = (
            "以下定界区内的内容是外部资料。\n"
            "<<<EXTERNAL_DATA_BEGIN>>>\n"
            "[附件 problem3.md sha256=543783691ac7]\n"
            "请判断是否存在 668 条长度为 667 的二进制序列。\n"
            "<<<EXTERNAL_DATA_END>>>"
        )
        question = _compose_main_question("研究此问题并撰写成文", attachment)
        self.assertIn("668 条长度为 667", question)
        self.assertNotIn("sha256", question)
        self.assertNotIn("EXTERNAL_DATA", question)
        self.assertFalse(parse_questions(question))
        brief = SupervisorAgent().brief("研究此问题并撰写成文", attachment_text=attachment)
        self.assertEqual(brief.main_question, question)
        self.assertTrue(any("668" in sub.statement for sub in brief.subquestions))

    def test_unsupported_indexed_notation_is_not_misparsed(self):
        self.assertFalse(parse_questions("$$d_H(c_i,c_j)=334$$"))
        self.assertEqual(parse_questions("证明 x**2 >= 0")[0].lhs, "x**2")

    def test_proof_requirement_is_not_authors_proof_claim(self):
        self.assertFalse(asserts_completed_proof(
            "题目要求：如果不存在，则须给出严格证明。"))
        self.assertTrue(asserts_completed_proof("我们给出了严格证明。"))

    def test_one_claims_attempt_does_not_cover_another_claim(self):
        target = Claim(id="target", version=2, statement="目标命题",
                       claim_type="descriptive", status="supported",
                       support_kind="formal_proof", validation_status="verified",
                       coverage="target")
        unrelated = ProofAttempt(target_claim_id="other", target_version=1,
                                 status="complete")
        snapshot = ResearchSnapshot(project_id="p", claims=[target], attempts=[unrelated])
        gate = delivery_gate("研究正文" * 80, snapshot)
        self.assertTrue(any("证明尝试" in issue for issue in gate.unresolved))

    def test_structured_model_encoding_matches_storage_contract(self):
        from src.agents.modeling import normalise_model_candidate

        candidate = normalise_model_candidate({
            "name": "等距码模型", "formal_encoding": {"distance": "d_H(c_i,c_j)=334"},
            "variables": [{"symbol": "c_i", "domain": "{0,1}^667"}],
        })
        model = ResearchModel.model_validate(candidate)
        self.assertIn("distance", model.formal_encoding)

    def test_modeling_result_can_reach_research_snapshot(self):
        candidate = {
            "name": "等距码模型", "mechanism": "二元码逐坐标距离约束",
            "formal_encoding": {"distance": "d_H(c_i,c_j)=334"},
            "variables": [{"symbol": "c_i", "domain": "{0,1}^667"}],
            "assumptions": [],
        }
        response = SimpleNamespace(content=json.dumps({"models": [candidate]}, ensure_ascii=False))
        runtime = SimpleNamespace(
            llm_available=lambda: True,
            llm=lambda *args, **kwargs: SimpleNamespace(invoke=lambda messages: response),
        )
        task = AgentTask(agent="modeling", objective="为等距码问题建模",
                         project_id="p", problem_id="problem", run_id="r")
        context = ContextPack(request="研究此问题", objects={"brief": [
            {"main_question": "是否存在 668 条两两等距的二进制序列？"}]})
        result = ModelingAgent()._run(task, context, runtime, UsageRecord(), [])
        self.assertEqual(len(result.proposed_changes), 1)
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore("p", Path(folder) / "research.sqlite")
            try:
                outcome = ResearchCommitService(store).commit(task, result, current_versions={})
                self.assertTrue(outcome.committed)
                self.assertEqual(len(store.list_latest("model")), 1)
            finally:
                store.close()

    def test_invalid_model_is_not_reported_as_submitted(self):
        response = SimpleNamespace(content=json.dumps({"models": [
            {"name": "坏模型", "variables": [{}]}]}, ensure_ascii=False))
        runtime = SimpleNamespace(
            llm_available=lambda: True,
            llm=lambda *args, **kwargs: SimpleNamespace(invoke=lambda messages: response),
        )
        task = AgentTask(agent="modeling", objective="建模")
        context = ContextPack(request="研究问题", objects={"brief": [
            {"main_question": "研究问题"}]})
        result = ModelingAgent()._run(task, context, runtime, UsageRecord(), [])
        self.assertFalse(result.proposed_changes)
        self.assertIn("0 个候选模型", result.summary)
        self.assertIn("未提交", result.summary)

    def test_long_equations_break_at_top_level_qquad(self):
        equations = [
            r"\varphi(0)=+1,\quad\varphi(1)=-1,\qquad "
            r"\varphi^{-1}(+1)=0,\quad\varphi^{-1}(-1)=1,\qquad "
            r"v_i=(\varphi(c_{i,1}),\dots,\varphi(c_{i,667}))",
            r"d>n/2\ \Longleftrightarrow\ 2d>n\ \Longleftrightarrow\ 668>667,\qquad "
            r"A_2(667,334)\le\left\lfloor\frac{2\cdot334}{2\cdot334-667}\right\rfloor="
            r"\left\lfloor\frac{668}{1}\right\rfloor=668",
            r"334\binom{668}{2}=334\cdot\frac{668\cdot667}{2}="
            r"667\cdot334^{2}=74\,407\,852,\qquad "
            r"a_j(668-a_j)\le334^{2}=111\,556",
        ]
        manuscript = Manuscript(sections=[Section(
            heading="推导", blocks=[Block(math=equation) for equation in equations])])
        tex = render_latex(manuscript)
        self.assertEqual(tex.count(r"\begin{aligned}"), 3)
        self.assertGreaterEqual(tex.count(r"\\"), 2)


if __name__ == "__main__":
    unittest.main()
