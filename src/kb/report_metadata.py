"""Recover bounded institutional report identity from locatable front matter.

This records the supplied document's identity, not independent publication
authentication, peer review, or support for any empirical effect.
"""
import json
import re

from src.publication.references import citation_eligible


def recover_report_metadata(row):
    out = dict(row)
    if citation_eligible(row) or row.get('publication_type') not in {None, '', 'report'}:
        return out
    try:
        page = int(row.get('page') or 0)
    except (ValueError, TypeError):
        return out
    if (row.get('content_level') != 'fulltext' or not row.get('file_hash')
            or page != 1):
        return out
    cover = re.sub(r'\s+', '', str(row.get('excerpt') or '')[:2000])
    candidates = [re.sub(r'\s+', '', part) for part in re.split(r'[_/\\]', str(row.get('title') or ''))]
    titles = [part for part in candidates if 5 <= len(part) <= 80
              and part.endswith('白皮书') and '系列' not in part and part in cover]
    year = re.search(r'白皮书[（(](20\d{2})[）)]', cover)
    units = re.search(r'主要编写单位[：:](.+?)主要编写人员[：:]', cover)
    owner = re.search(r'本白皮书版权属于(.{2,40}?)(?:及其|所有)', cover)
    if not (titles and year and units and owner):
        return out
    institutions = re.findall(r'[\u4e00-\u9fff]{2,30}?(?:实验室|大学|研究院|研究所|协会|学会|委员会)', units.group(1))
    if (not institutions or len(institutions)>6 or ''.join(institutions) != units.group(1)
            or owner.group(1) not in institutions):
        return out
    title = max(titles, key=len)
    locator = 'file-hash:' + str(row['file_hash']) + '#page=1'
    values = {'title':title, 'authors':', '.join(institutions), 'year':year.group(1),
        'author_metadata':json.dumps([{'name':name} for name in institutions],ensure_ascii=False),
        'publisher':owner.group(1), 'publication_type':'report', 'source_kind':'report',
        'publication_status':'published', 'publication_verified_by':'document-frontmatter',
        'author_verified_by':'document-frontmatter', 'author_source_url':locator,
        'publication_source_url':locator,
        'publication_note':'已核对所提供全文的封面、版权、机构编写单位与系列年份；非外部出版认证，不代表同行评议或效果证据',
        'author_note':'保留机构编写单位，个人编写人员未替换为作者；来源为同一文件首页',
        'metadata_provenance':json.dumps({key:locator for key in
            ['title','authors','author_metadata','publisher','year','publication_type']},ensure_ascii=False)}
    # Do not silently overwrite a conflicting bibliographic identity.
    for key in ('authors','year','publisher'):
        if row.get(key) and row[key] != values[key]:
            return out
    out.update(values)
    return out
