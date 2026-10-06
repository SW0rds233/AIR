import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.kb.service import KnowledgeService
from src.research.acceptance import GateResult, delivery_gate, publication_gate, missing_section_references
from src.research.adversarial import review_claim, NOT_APPLICABLE
from src.research.package import _traceability, export_package, gate_manifest_fields
from src.research.schemas import Claim, ProofObligation, ResearchSnapshot
from src.agents.writing import WritingAgent, _manuscript_from_payload, finalise_manuscript, render_markdown
from src.agents.modeling import validate_model_payload
from src.publication.schemas import WritingPacket, Block, Section, Manuscript, RefKind
from src.publication.references import cited_reference_rows
from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.publication.render_latex import render_latex
from src.research.snapshot import snapshot_from_store


class RunQualityTest(unittest.TestCase):
    def test_symbol_section_references_do_not_require_chinese_suffix(self):
        text = "## 3 方法\n### 3.1 变量映射\n见 §3.1、第 3.1 节及 §3.2。"
        self.assertEqual(missing_section_references(text), ["3.2"])

    def test_post_compile_manifest_keeps_scientific_failure_reasons(self):
        from src.graph.team_session import _rewrite_manifest
        from src.research.delivery import DeliveryAssessment
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.json").write_text('{"theory_gate_passed": true}', encoding="utf-8")
            (root / "manuscript.pdf").write_bytes(b"%PDF-fixture")
            assessment = DeliveryAssessment(theory=GateResult(False, reasons=["Proof missing"]),
                delivery=GateResult(False, unresolved=["Pending claims"]), publication=GateResult(True))
            _rewrite_manifest(root, assessment, "ok", "")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["theory_gate_passed"])
        self.assertFalse(manifest["gate_passed"])
        self.assertIn("Proof missing", manifest["blocking"])
        self.assertIn("Pending claims", manifest["delivery_gate_unresolved"])
        self.assertEqual(manifest["pdf"], "manuscript.pdf")

    def test_failed_compile_does_not_advertise_stale_pdf(self):
        from src.graph.team_session import _rewrite_manifest
        from src.research.delivery import DeliveryAssessment
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.json").write_text('{"pdf": "manuscript.pdf"}', encoding="utf-8")
            (root / "manuscript.pdf").write_bytes(b"%PDF-partial")
            _rewrite_manifest(root, DeliveryAssessment(publication=GateResult(False)), "failed", "Error")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdf"], "")

    def test_formula_only_block_has_a_trace_anchor(self):
        block = Block(block_id="formula-one", math="x=x")
        manuscript = Manuscript(sections=[Section(heading="Lemma", blocks=[block])])
        self.assertIn("<!-- block:formula-one -->", render_markdown(manuscript))

    def test_authoring_repairs_bad_cross_reference_once_with_prior_draft(self):
        packet = WritingPacket()
        bad = _manuscript_from_payload({"sections": [{"heading": "方法", "blocks": [
            {"text": "见第 3.1 节。"}]}]}, packet)
        good = _manuscript_from_payload({"sections": [{"heading": "方法", "blocks": [
            {"text": "下面给出变量映射。"}]}]}, packet)
        agent = WritingAgent()
        with patch.object(agent, "_draft_with_llm", side_effect=[(bad, ""), (good, "")]) as draft:
            result = agent._run(AgentTask(agent="writing", objective="Write"), ContextPack(),
                               SimpleNamespace(llm_available=lambda stage: True), UsageRecord(), [])
        self.assertEqual(draft.call_count, 2)
        self.assertTrue(draft.call_args.args[-1].prior_manuscript)
        self.assertNotIn("第 3.1 节", json.dumps(result.proposed_changes[0].payload, ensure_ascii=False))

    def test_gate_contract_keeps_compilation_and_scientific_failures_separate(self):
        result = gate_manifest_fields(GateResult(False, reasons=["Proof missing"]),
                                      GateResult(False, unresolved=["Pending claims"]), GateResult(True))
        self.assertFalse(result["gate_passed"])
        self.assertTrue(result["publication_gate_passed"])
        self.assertIn("Proof missing", result["blocking"])
        self.assertIn("Pending claims", result["unresolved"])

    def test_reference_check_uses_body_order_and_only_referenced_sources(self):
        from src.research.schemas import ObjectRef
        block = Block(text="Related work", refs=[ObjectRef(id="source-b"), ObjectRef(id="source-a")],
                      ref_kinds=[RefKind.source.value, RefKind.source.value])
        manuscript = Manuscript(sections=[Section(blocks=[block])])
        rows = cited_reference_rows(manuscript, [{"id": "source-a"}, {"id": "unused"}, {"id": "source-b"}])
        self.assertEqual([row["source_id"] for row in rows], ["source-b", "source-a"])
        self.assertEqual([row["index"] for row in rows], [1, 2])

    def test_optional_open_obligation_does_not_block_a_mapped_claim(self):
        claim = Claim(id="clm-test", statement="A theorem")
        snapshot = ResearchSnapshot(project_id="p", claims=[claim], writing_map={claim.id: "block:one"},
            obligations=[ProofObligation(claim_id=claim.id, statement="Optional review", required=False)])
        result = delivery_gate("# Research\n" + "Text. " * 50, snapshot)
        self.assertFalse(any("未关闭义务" in reason for reason in result.reasons))

    def test_unverified_conclusion_requests_specific_claim_verification(self):
        claim = Claim(id="clm-test", statement="Candidate theorem")
        packet = WritingPacket(claims=[claim.model_dump(mode="json")])
        manuscript = _manuscript_from_payload({"sections": [{"heading": "结论", "blocks": [
            {"role": "claim", "text": "候选结论尚需核查。", "ref_ids": [claim.id]}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        agent = WritingAgent()
        needs = agent._needs_from(manuscript, packet, agent.audit_manuscript(manuscript, packet))
        self.assertTrue(any(need.kind.value == "derivation" and need.blocked_refs[0].id == claim.id for need in needs
                            if need.blocked_refs))

    def test_snapshot_map_cannot_pick_another_problem_manuscript(self):
        def draft(claim_id):
            return _manuscript_from_payload({"sections": [{"heading": "Results", "blocks": [
                {"text": "Candidate", "ref_ids": [claim_id]}]}]},
                WritingPacket(claims=[{"id": claim_id}])).model_dump(mode="json")
        rows = [{**draft("clm-own"), "_scope": {"problem_id": "own"}},
                {**draft("clm-other"), "_scope": {"problem_id": "other"}}]
        store = SimpleNamespace(list_latest=lambda kind: rows if kind == "manuscript" else [])
        snapshot = snapshot_from_store(store, project_id="p", problem_id="own", run_id="r")
        self.assertIn("clm-own", snapshot.writing_map)
        self.assertNotIn("clm-other", snapshot.writing_map)

    def test_real_subsections_render_in_both_formats(self):
        manuscript = _manuscript_from_payload({"sections": [
            {"heading": "方法", "level": 1, "blocks": [{"text": "见第 1.1 节。"}]},
            {"heading": "变量映射", "level": 2, "blocks": [{"text": "解释转换。"}]}]}, WritingPacket())
        finalise_manuscript(manuscript, WritingPacket())
        self.assertIn("### 1.1 变量映射", render_markdown(manuscript))
        self.assertIn(r"\subsection{变量映射}", render_latex(manuscript))

    def test_nonexistent_subsection_creates_revision_need_before_export(self):
        packet = WritingPacket()
        manuscript = _manuscript_from_payload({"sections": [
            {"heading": "方法", "blocks": [{"text": "见第 3.1 节。"}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        agent = WritingAgent()
        problems = agent.audit_manuscript(manuscript, packet)
        self.assertTrue(any("章节" in problem for problem in problems))
        self.assertTrue(any(need.kind.value == "manuscript_revision"
                            for need in agent._needs_from(manuscript, packet, problems)))

    def test_attached_question_is_input_provenance_not_a_missing_external_paper(self):
        model = {"name": "Model", "variables": [{"symbol": "n", "domain": "N"}],
                 "assumptions": [{"statement": "Input length", "origin": "user_assumption",
                                  "source_ids": ["problem3.md"]}]}
        problems = validate_model_payload(model, known_source_ids=set(), known_input_ids={"problem3.md"})
        self.assertFalse(any("未登记来源" in p for p in problems))
        model["assumptions"][0]["origin"] = "external"
        self.assertTrue(any("未登记来源" in p for p in validate_model_payload(
            model, known_source_ids=set(), known_input_ids={"problem3.md"})))

    def test_empirical_descriptive_claim_still_requires_measurement_checks(self):
        claim = Claim(statement="A matrix method raises measured efficiency", claim_type="descriptive",
                      strategy="derivation", study={"design": "observational", "population": "students"})
        self.assertTrue(any(f.check_id == "measurement_error" and f.blocked for f in review_claim(claim).findings))

    def test_document_ref_reads_real_sqlite_chunk_column(self):
        store = SimpleNamespace(get_document=lambda _: {"title": "A theorem"},
            get_chunks=lambda *args, **kwargs: [{"idx": 4, "page": 2, "text": "Theorem statement",
                                                "char_start": 100, "char_end": 117}])
        service = KnowledgeService("fixture", store=store)
        ref = service.document_ref("lit-1")
        self.assertEqual(ref.chunk_id, "lit-1#4")
        self.assertEqual(ref.page, 2)

    def test_no_core_claim_is_not_a_traceability_pass(self):
        result = _traceability(ResearchSnapshot(project_id="p", claims=[Claim(statement="Candidate")]),
                               "# Research\n" + "Text. " * 50)
        self.assertFalse(result["ok"])

    def test_missing_writing_map_cannot_silently_skip_open_obligations(self):
        claim = Claim(id="clm-test", statement="Candidate")
        snapshot = ResearchSnapshot(project_id="p", claims=[claim], obligations=[
            ProofObligation(claim_id=claim.id, statement="Check its scope")])
        result = delivery_gate("# Research\n" + "Text. " * 50, snapshot)
        self.assertTrue(any("映射" in reason for reason in result.reasons + result.unresolved))

    def test_math_derivation_does_not_require_sample_measurement_population(self):
        claim = Claim(statement="For binary vectors, the sign-vector inner product equals n-2d.",
                      claim_type="descriptive", strategy="derivation")
        review = review_claim(claim)
        for finding in review.findings:
            if finding.check_id in {"sample_selection_bias", "measurement_error", "external_validity"}:
                self.assertEqual(finding.status, NOT_APPLICABLE)

    def test_export_preserves_all_gate_reasons_and_object_counts(self):
        snapshot = ResearchSnapshot(project_id="p")
        with tempfile.TemporaryDirectory() as folder:
            root = export_package(snapshot, None, [], "Draft", base_dir=folder,
                gate=GateResult(passed=False, reasons=["Missing certificate"], unresolved=["Proof incomplete"]),
                delivery=GateResult(passed=False, reasons=["Missing map"]))
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("Missing certificate", manifest.get("theory_gate_reasons", []))
        self.assertIn("Missing map", manifest.get("delivery_gate_reasons", []))
        self.assertIn("Proof incomplete", manifest.get("unresolved", []))
        self.assertIn("models", manifest)

    def test_reference_section_does_not_repeat_seventh_section_number(self):
        packet = WritingPacket()
        manuscript = _manuscript_from_payload({"sections": [{"heading": "7 结论", "blocks": [
            {"text": "仍需核查。"}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        self.assertNotIn("## 7 参考文献", render_markdown(manuscript))

    def test_publication_gate_rejects_proposed_claim_as_completed_conclusion(self):
        claim = Claim(id="clm-test", statement="An exact equivalence")
        packet = WritingPacket(claims=[claim.model_dump(mode="json")])
        manuscript = _manuscript_from_payload({"abstract": "研究问题与方法。", "keywords": ["等价"],
            "sections": [{"heading": "结论", "blocks": [{"role": "claim", "text": "我们证明该等价成立。",
                "ref_ids": [claim.id]}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        result = publication_gate(render_markdown(manuscript), ResearchSnapshot(project_id="p", claims=[claim]),
                                  blocks=manuscript.all_blocks(), compile_status="ok")
        self.assertTrue(any("未定" in r or "未核验" in r for r in result.reasons))
