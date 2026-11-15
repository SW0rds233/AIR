"""Convert conservative Markdown tables to canonical, numbered table blocks."""
import re

from src.publication.schemas import BlockRole, Manuscript
from src.research.schemas import stable_id


def table_title(value: str) -> str:
    return re.sub(r'^\s*(?:表\s*\d+|Table\s+\d+)\s*[:：.、]?\s*', '', value, flags=re.I).strip()


def _cells(line):
    line = line.strip()
    if not line.startswith('|') or not line.endswith('|'):
        return []
    return [cell.strip().replace(r'\|', '|') for cell in re.split(r'(?<!\\)\|', line[1:-1])]


def valid_table(data):
    return (isinstance(data, dict) and isinstance(data.get('header'), list) and bool(data['header'])
            and isinstance(data.get('rows'), list) and all(isinstance(row, list)
            and len(row) == len(data['header']) for row in data['rows']))


def normalize_tables(manuscript: Manuscript, *, inplace=False) -> Manuscript:
    result = manuscript if inplace else manuscript.model_copy(deep=True)
    for section in result.sections:
        blocks = []
        for block in section.blocks:
            if block.role == BlockRole.table or block.math:
                blocks.append(block)
                continue
            lines = block.text.splitlines()
            parts, pending, i = [], [], 0
            while i < len(lines):
                header = _cells(lines[i])
                separator = _cells(lines[i+1]) if i+1 < len(lines) else []
                if (len(header) < 2 or len(header) != len(separator)
                        or not all(re.fullmatch(r':?-{3,}:?', cell) for cell in separator)):
                    pending.append(lines[i]); i += 1
                    continue
                j, rows = i+2, []
                while j < len(lines) and _cells(lines[j]):
                    rows.append(_cells(lines[j])); j += 1
                data = {'header': header, 'rows': rows}
                if not valid_table(data):
                    pending.extend(lines[i:j]); i = j
                    continue
                while pending and not pending[-1].strip():
                    pending.pop()
                title = block.heading or '数据表'
                if pending and re.match(r'^\s*(?:表\s*\d+|Table\s+\d+)', pending[-1], re.I):
                    title = table_title(pending.pop())
                if '\n'.join(pending).strip():
                    parts.append({'text':'\n'.join(pending).strip()})
                parts.append({'role':BlockRole.table, 'heading':title, 'text':'', 'data':data})
                pending = []; i = j
            if not parts:
                blocks.append(block)
                continue
            if '\n'.join(pending).strip():
                parts.append({'text':'\n'.join(pending).strip()})
            for index, part in enumerate(parts):
                # Retain the original anchor and read set; deterministic new
                # anchors make repeated normalization idempotent.
                refs = [(kind, ref) for kind, ref in zip(block.ref_kinds, block.refs)
                        if kind != 'source' or index == len(parts)-1]
                blocks.append(block.model_copy(deep=True, update={**part,
                    'block_id':block.block_id if index == 0 else stable_id('tblpart', [block.block_id,index]),
                    'ref_kinds':[kind for kind, _ in refs], 'refs':[ref for _, ref in refs]}))
        section.blocks = blocks
    return result


def markdown_table(block, number):
    data = block.data
    if not valid_table(data):
        return []
    def line(cells):
        return '| ' + ' | '.join(str(cell).replace('|', r'\|').replace('\n', '<br>') for cell in cells) + ' |'
    return [f'**表 {number} {table_title(block.heading) or "数据表"}**', '',
            line(data['header']), line(['---']*len(data['header'])), *[line(row) for row in data['rows']], '']
