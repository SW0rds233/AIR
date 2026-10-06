import unittest
from src.publication.references import format_reference
from src.publication.schemas import WritingPacket
from src.agents.writing import _manuscript_from_payload, finalise_manuscript, render_markdown
from src.publication.render_latex import render_latex

ARTICLE = {"source_id": "journal", "title": "Orthogonal codes", "authors": "John Smith, Jane Doe",
           "year": "2020", "venue": "Journal of Codes", "volume": "12", "issue": "2", "pages": "10-20",
           "doi": "https://doi.org/10.1000/test", "publication_status": "published",
           "publication_type": "journal-article", "publication_verified_by": "crossref",
           "locator": "abstract chars 0-30", "content_level": "abstract"}

class AcademicPublicationTest(unittest.TestCase):
    def draft(self, source):
        packet = WritingPacket(sources=[source])
        manuscript = _manuscript_from_payload({"sections": [{"heading": "Related work", "blocks": [
            {"text": "Smith studied orthogonal codes.", "ref_ids": [source["source_id"]]}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        return manuscript

    def test_journal_reference_has_gbt_fields_not_internal_locator(self):
        entry = format_reference(ARTICLE)
        self.assertIn("Orthogonal codes[J]", entry)
        self.assertIn("2020, 12(2): 10-20", entry)
        self.assertIn("DOI:10.1000/test", entry)
        self.assertNotIn("定位", entry)

    def test_citations_are_integrated_into_the_paragraph(self):
        manuscript = self.draft(ARTICLE)
        md = render_markdown(manuscript)
        tex = render_latex(manuscript, references={"journal": format_reference(ARTICLE)})
        self.assertNotIn("依据:", md)
        self.assertNotIn("依据：", tex)
        self.assertIn("codes[1].", md)

    def test_unverified_preprint_cannot_enter_formal_bibliography(self):
        source = {**ARTICLE, "publication_status": "preprint", "publication_verified_by": "",
                  "url": "https://arxiv.org/abs/2401.12345", "doi": "10.48550/arXiv.2401.12345"}
        manuscript = self.draft(source)
        self.assertNotIn("[1]", render_markdown(manuscript))
        self.assertTrue(any(g.get("kind") == "ineligible_reference" for g in manuscript.gaps))
