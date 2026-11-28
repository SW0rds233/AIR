import json

from src.kb.publication import verify_publication
from src.kb.publisher_metadata import publisher_metadata
from src.publication.references import author_metadata_problems, citation_eligible, format_reference


def test_mixed_byline_with_chinese_postcode_requires_publisher_names():
    people = [{'given': 'First', 'family': 'Author'},
              {'name': '研究所 北京100101'}]
    item = {'author_metadata': json.dumps(people, ensure_ascii=False)}
    assert author_metadata_problems(item)
    assert not author_metadata_problems({'author_metadata': '[{"name":"紫金山实验室"},{"name":"北京邮电大学"}]'})


def test_publisher_issue_identifies_conference_contribution_despite_registry_type():
    doi = '10.1000/example'
    series = 'Sample Proceeding: Conference of Science'
    issue = 'Proceedings of the Third International Conference on Science'
    html = (f'<meta name="citation_doi" content="{doi}"><meta name="citation_journal_title" content="{series}">'
            f'<nav class="cmp_breadcrumbs"><a href="/journal/issue/view/5">{issue}</a></nav>')
    location = 'https://publisher.example/article/5'
    def publisher(*args, **kwargs):
        return publisher_metadata(html, doi, url=location)
    registry = {'title':['Research'], 'author':[{'given':'A', 'family':'Smith'}], 'DOI':doi,
                'type':'journal-article', 'container-title':[series], 'volume':'6', 'issue':'1',
                'page':'42-55', 'published':{'date-parts':[[2026]]}}
    paper = {'doi':doi, 'title':'Research', 'authors':'A Smith', 'relation':'background'}
    result = verify_publication(paper, lambda *a, **kw: {'message':registry}, publisher_fetch=publisher)
    assert citation_eligible(result)
    assert '[C]//' + issue in format_reference(result)
    assert json.loads(result['metadata_provenance'])['publication_type'] == location
    assert result['relation'] == 'background'
    assert 'publication_type' not in publisher_metadata(html, '10.1000/other')
    assert 'publication_type' not in publisher_metadata(html.replace('cmp_breadcrumbs', 'other'), doi)
    cached = verify_publication(result, lambda *a, **kw: {'message':registry}, force=True,
                                metadata_only=True, publisher_fetch=lambda *a, **kw: {})
    assert cached['venue'] == issue and cached['publication_type'] == 'proceedings-article'
