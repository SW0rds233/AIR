"""Read author lists from DOI-matched publisher metadata, without guessing names."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from src.publication.references import author_metadata_problems, normalize_doi


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.scripts = []
        self.exports = []
        self._script = None
        self._in_breadcrumbs = False
        self._issue_label = None
        self.issue_titles = []

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == 'nav':
            self._in_breadcrumbs = 'cmp_breadcrumbs' in (row.get('class') or '').split()
        elif tag == 'a' and self._in_breadcrumbs and '/issue/view/' in (row.get('href') or ''):
            self._issue_label = []
        elif tag == 'meta':
            self.rows.append((str(row.get('name') or row.get('property') or '').lower(),
                              row.get('content') or '', row.get('xml:lang') or row.get('lang') or ''))
        elif tag == 'script' and row.get('type') == 'application/ld+json':
            self._script = ''
        elif tag in {'link', 'a'} and row.get('type') in {
                'application/x-research-info-systems', 'application/x-ristype'}:
            self.exports.append(row.get('href', ''))

    def handle_data(self, data):
        if self._script is not None:
            self._script += data
        if self._issue_label is not None:
            self._issue_label.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._script is not None:
            self.scripts.append(self._script)
            self._script = None
        elif tag == 'a' and self._issue_label is not None:
            self.issue_titles.append(' '.join(''.join(self._issue_label).split()))
            self._issue_label = None
        elif tag == 'nav':
            self._in_breadcrumbs = False


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
    return fetch_publisher_metadata(doi)


def _jsonld_articles(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_articles(item)
    elif isinstance(value, dict):
        if value.get('@type') in ('ScholarlyArticle', 'Article', 'Report'):
            yield value
        yield from _jsonld_articles(value.get('@graph', []))


def _identifier(value):
    if isinstance(value, dict):
        value = value.get('value') or value.get('@id') or ''
    return normalize_doi(value).lower()


def publisher_metadata(html, doi, *, url=''):
    """Only article-level identifiers match: reference lists never verify identity."""
    parser = _Metadata()
    parser.feed(html)
    target = normalize_doi(doi).lower()
    if not target:
        return {}
    values = {key: value for key, value, _ in parser.rows}
    out = publisher_authors(html, doi, url=url)
    if any(_identifier(values.get(key, '')) == target for key in (
            'citation_doi', 'dc.identifier', 'dc.identifier.doi', 'prism.doi')):
        fields = {'title':'citation_title', 'venue':'citation_journal_title',
                  'volume':'citation_volume', 'issue':'citation_issue', 'issn':'citation_issn',
                  'publisher':'citation_publisher', 'published_date':'citation_publication_date'}
        out.update({key:values[tag] for key, tag in fields.items() if values.get(tag)})
        conference = values.get('citation_conference_title')
        if not conference:
            # OJS publishes journals and proceedings. Use the *current issue*
            # breadcrumb; a conference mentioned in references is unrelated.
            proceedings = {title for title in parser.issue_titles if re.search(
                r'\bproceedings?\s+of\b.*\b(conference|symposium|workshop|congress)\b', title, re.I)}
            if len(proceedings) == 1 and re.search(r'\bproceedings?\b', out.get('venue', ''), re.I):
                conference = proceedings.pop()
        if conference:
            out.update(venue=conference, conference_title=conference, publication_type='proceedings-article')
        if 'citation_volume' in values and not values['citation_volume']:
            out['volume'] = ''
        out['pages'] = '-'.join(dict.fromkeys(values[tag] for tag in (
            'citation_firstpage', 'citation_lastpage') if values.get(tag)))
    for script in parser.scripts:
        try:
            articles = list(_jsonld_articles(json.loads(script)))
        except (ValueError, TypeError):
            continue
        for article in articles:
            ids = article.get('identifier') or []
            ids = ids if isinstance(ids, list) else [ids]
            ids += [article.get('sameAs', ''), article.get('url', '')]
            if not any(_identifier(value) == target for value in ids if not isinstance(value, list)):
                continue
            author_rows = article.get('author') or []
            if isinstance(author_rows, (dict, str)):
                author_rows = [author_rows]
            # Reuse the author validator; do not pull names out of affiliations.
            from html import escape
            tags = '<meta name="citation_doi" content="' + escape(doi, quote=True) + '">'
            for author in author_rows:
                name = author.get('name') if isinstance(author, dict) else author
                if name:
                    tags += '<meta name="citation_author" content="' + escape(str(name), quote=True) + '">'
            authors = publisher_authors(tags, doi, url=url)
            if authors and not out.get('authors'):
                out.update(authors)
            parent = article.get('isPartOf') or {}
            venue = parent.get('name', '') if isinstance(parent, dict) else ''
            fields = {'title':article.get('headline') or article.get('name'), 'venue':venue,
                      'published_date':article.get('datePublished'), 'pages':article.get('pagination'),
                      'volume':article.get('volumeNumber'), 'issue':article.get('issueNumber'),
                      'issn':article.get('issn')}
            for key, value in fields.items():
                if isinstance(value, (str, int)) and not out.get(key):
                    out[key] = str(value)
    if out:
        out.update(doi=normalize_doi(doi), publication_source_url=url)
    return out


def ris_metadata(text, doi, *, url=''):
    """An export may contain several papers; select exactly one DOI-matched record."""
    for record in re.split(r'(?m)^ER\s*-.*$', text):
        fields = {}
        for key, value in re.findall(r'(?m)^([A-Z0-9]{2})[ \t]*-[ \t]*(.*)$', record):
            fields.setdefault(key, []).append(value.strip())
        if not any(_identifier(value) == normalize_doi(doi).lower() for value in fields.get('DO', [])):
            continue
        from html import escape
        tags = '<meta name="citation_doi" content="' + escape(doi, quote=True) + '">'
        for name in fields.get('AU') or fields.get('A1') or []:
            tags += '<meta name="citation_author" content="' + escape(name, quote=True) + '">'
        out = publisher_authors(tags, doi, url=url)
        for key, tags in {'title':('TI','T1'), 'venue':('JO','JF','T2'), 'volume':('VL',),
                          'issue':('IS',), 'issn':('SN',), 'publisher':('PB',),
                          'published_date':('PY','Y1')}.items():
            out[key] = next((fields[tag][0] for tag in tags if fields.get(tag)), '')
        out['pages'] = '-'.join(fields[tag][0] for tag in ('SP','EP') if fields.get(tag))
        return {**out, 'doi':normalize_doi(doi), 'publication_source_url':url}
    return {}


def fetch_publisher_metadata(doi, *, urls=(), fetch=None):
    from src.utils.http_client import get_with_retry
    fetch = fetch or get_with_retry
    direct = list(dict.fromkeys(url for url in urls if url and urlparse(url).scheme in {'https', 'http'}
                               and urlparse(url).hostname not in {'doi.org', 'dx.doi.org', 'arxiv.org'}))
    # At most one direct publisher request, one export, one DOI redirect fallback.
    errors = []
    for url in direct[:1] + ['https://doi.org/' + normalize_doi(doi)]:
        try:
            response = fetch(url, follow_redirects=True, read_timeout=10, max_retries=1)
            out = publisher_metadata(response.text, doi, url=str(response.url))
            if out.get('authors'):
                return out
            parser = _Metadata()
            parser.feed(response.text)
            for link in parser.exports[:1]:
                export_url = urljoin(str(response.url), link)
                if urlparse(export_url).scheme not in {'https', 'http'}:
                    continue
                exported = fetch(export_url, follow_redirects=True, read_timeout=10, max_retries=1)
                record = ris_metadata(exported.text, doi, url=str(exported.url))
                if record.get('authors'):
                    return {**out, **{k:v for k,v in record.items() if v}}
            if out:
                return out
        except Exception as error:
            errors.append(type(error).__name__)
    if errors:
        raise RuntimeError('出版元数据获取失败: ' + ', '.join(errors))
    return {}
