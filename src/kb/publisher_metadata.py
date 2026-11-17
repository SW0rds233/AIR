"""Read author lists from DOI-matched publisher metadata, without guessing names."""
import json
import re
from html.parser import HTMLParser

from src.publication.references import author_metadata_problems, normalize_doi


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            row = dict(attrs)
            self.rows.append((str(row.get('name') or row.get('property') or '').lower(),
                              row.get('content') or '', row.get('xml:lang') or row.get('lang') or ''))


def publisher_authors(html, doi, *, url=''):
    """Use citation/DC metadata only; references elsewhere in HTML cannot match DOI.

    Bilingual lists stay separate. Prefer the publisher's Chinese list when one
    is supplied; never concatenate translated author lists or scrape affiliations.
    """
    parser = _Metadata()
    parser.feed(html)
    identifiers = [normalize_doi(value).lower() for key, value, _ in parser.rows
                   if key in {'citation_doi', 'dc.identifier', 'dc.identifier.doi', 'prism.doi'}]
    if normalize_doi(doi).lower() not in identifiers:
        return {}
    groups = {}
    for key, value, lang in parser.rows:
        if key not in {'citation_author', 'citation_authors', 'dc.creator'}:
            continue
        separator = r'\s*[;；]\s*' if re.search(r'[;；]', value) else r'\s*[,、]\s*|\s+and\s+'
        names = re.split(separator, value) if key == 'citation_authors' else [value]
        # Singular tags may still explicitly contain the author's translated name;
        # retain them for validation rather than guessing which substring is real.
        groups.setdefault((key, lang.lower()), []).extend(name.strip() for name in names if name.strip())
    choices = sorted(groups.values(), key=lambda names: not all(re.fullmatch(r'[\u4e00-\u9fff\s·]+', n) for n in names))
    for names in choices:
        authors = []
        for name in dict.fromkeys(names):
            if re.fullmatch(r'[\u4e00-\u9fff\s·]+', name):
                authors.append({'family':re.sub(r'\s+', '', name)})
            elif re.search(r'\b(?:organization|organisation|society|group|collaboration|institute|university)\b', name, re.I):
                authors.append({'name':name})
            elif name.count(',') == 1:
                family, given = (part.strip() for part in name.split(','))
                authors.append({'family':family, 'given':given})
            else:
                parts = name.split()
                if len(parts) < 2:
                    authors.append({'family':name})
                elif parts[0].isupper():
                    authors.append({'family':parts[0], 'given':' '.join(parts[1:])})
                else:
                    authors.append({'family':parts[-1], 'given':' '.join(parts[:-1])})
        result = {'authors':', '.join(names), 'author_metadata':json.dumps(authors, ensure_ascii=False),
                  'author_verified_by':'publisher', 'author_source_url':url,
                  'author_note':'出版原页 DOI 匹配；按同一语言的完整署名列表核对'}
        if authors and not author_metadata_problems(result):
            return result
    return {}


def fetch_publisher_authors(doi):
    from src.utils.http_client import get_with_retry
    response = get_with_retry('https://doi.org/' + normalize_doi(doi),
                              follow_redirects=True, read_timeout=15, max_retries=1)
    # httpx decodes the response's declared charset; never execute publisher JS.
    return publisher_authors(response.text, doi, url=str(response.url))
