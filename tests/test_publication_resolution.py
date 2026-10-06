import unittest
from unittest.mock import patch
from types import SimpleNamespace

from src.kb.publication import verify_publication, _fetch, relevance_score
from src.publication.references import citation_eligible
from src.tools.search_tools import merge_papers
from src.agents.review import ReviewAgent, _issue_from, _needs_from, _draft_text, deterministic_issues
from src.agents.protocol import AgentTask, ContextPack, UsageRecord

PAPER = {"title": "Hadamard constructions", "authors": "John Smith",
         "url": "https://arxiv.org/abs/2401.12345", "abstract": "A construction of Hadamard matrices."}
ROW = {"title": [PAPER["title"]], "author": [{"given": "John", "family": "Smith"}],
       "type": "journal-article", "DOI": "10.1000/published", "container-title": ["Journal of Codes"],
       "published": {"date-parts": [[2025]]}, "volume": "2", "page": "10-20"}


class PublicationResolutionTest(unittest.TestCase):
    def test_title_lookup_resolves_preprint_to_publication_without_changing_excerpt(self):
        fetch = lambda url, params=None: {"message": {"items": [ROW]}}
        result = verify_publication(PAPER, fetch)
        self.assertTrue(citation_eligible(result))
        self.assertEqual(result["doi"], ROW["DOI"])
        self.assertEqual(result["abstract"], PAPER["abstract"])
        self.assertEqual(result["preprint_url"], PAPER["url"])

    def test_wrong_author_does_not_verify_same_title(self):
        def fetch(url, params=None):
            return {"message": {"items": [{**ROW, "author": [{"given": "John", "family": "Jones"}]}]}, "results": []}
        self.assertFalse(citation_eligible(verify_publication(PAPER, fetch)))

    def test_crossref_preprint_relation_is_followed(self):
        def fetch(url, params=None):
            if "10.1000%2Fpreprint" in url:
                return {"message": {"type": "posted-content", "relation": {
                    "is-preprint-of": [{"id": ROW["DOI"]}]}}}
            return {"message": ROW}
        result = verify_publication({**PAPER, "doi": "10.1000/preprint"}, fetch)
        self.assertEqual(result["publication_verified_by"], "crossref")

    def test_openalex_published_location_is_required(self):
        work = {"title": PAPER["title"], "type": "article", "publication_year": 2025,
                "authorships": [{"author": {"display_name": PAPER["authors"]}}],
                "locations": [{"is_published": True, "source": {
                    "type": "repository", "display_name": "arXiv"}}]}
        def fetch(url, params=None):
            return {"message": {"items": []}, "results": [work]}
        self.assertFalse(citation_eligible(verify_publication(PAPER, fetch)))
        work["locations"][0]["source"] = {"type": "journal", "display_name": "Journal of Codes"}
        self.assertTrue(citation_eligible(verify_publication(PAPER, fetch)))

    def test_http_failure_is_not_publication_confirmation(self):
        def fetch(url, params=None):
            raise TimeoutError()
        result = verify_publication(PAPER, fetch)
        self.assertFalse(citation_eligible(result))
        self.assertIn("核查失败", result["publication_note"])

    def test_http_client_uses_its_actual_retry_parameter(self):
        with patch("src.utils.http_client.get_with_retry", return_value=SimpleNamespace(json=lambda: {})) as get:
            _fetch("https://example.org")
        self.assertEqual(get.call_args.kwargs["max_retries"], 1)
        self.assertNotIn("retries", get.call_args.kwargs)

    def test_formal_duplicate_replaces_preprint_metadata_but_preserves_read_content(self):
        papers = merge_papers([dict(PAPER)], [{"title": PAPER["title"], "doi": ROW["DOI"],
            "authors": PAPER["authors"], "venue": "Journal of Codes", "published": True}])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["doi"], ROW["DOI"])
        self.assertEqual(papers[0]["abstract"], PAPER["abstract"])
        self.assertEqual(papers[0]["preprint_url"], PAPER["url"])

    def test_irrelevant_papers_are_not_selected_by_generic_code_word(self):
        self.assertEqual(relevance_score({"title": "Code generation for image segmentation"},
                                        "Hadamard equidistant codes"), 0)


class AcademicReviewTest(unittest.TestCase):
    def test_missing_semantic_reviewer_is_not_a_pass(self):
        result = ReviewAgent()._run(AgentTask(agent="review", objective="Review"), ContextPack(),
            SimpleNamespace(llm_available=lambda stage: False), UsageRecord(), [])
        self.assertEqual(result.outcome.value, "partial")
        self.assertEqual(result.followup_needs[0].kind.value, "review")

    def test_major_writing_issue_retains_block_identity_and_requests_revision(self):
        issue = _issue_from({"severity": "major", "category": "science", "summary": "Dimension mismatch",
            "affected_ids": ["block-1"], "suggested_owner": "writing", "acceptance": ["Use n+1"]})
        self.assertIsNotNone(issue)
        self.assertEqual(issue.affected_refs[0].id, "block-1")
        self.assertEqual(_needs_from([issue])[0].kind.value, "manuscript_revision")

    def test_review_receives_formulas_and_citation_identity(self):
        context = ContextPack(objects={"manuscript": [{"sections": [{"blocks": [
            {"block_id": "block-1", "math": "667-2*334=-1", "refs": [{"id": "source-1"}]}]}]}]})
        self.assertIn("667-2*334", _draft_text(context))
        self.assertIn("source-1", _draft_text(context))

    def test_unverified_claim_cannot_support_we_prove_abstract(self):
        context = ContextPack(objects={"manuscript": [{"abstract": "We prove the equivalence."}],
                                      "claim": [{"status": "proposed"}]})
        self.assertTrue(any(i.blocking and i.suggested_owner == "writing" for i in deterministic_issues(context)))
