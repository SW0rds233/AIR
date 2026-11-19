"""Normalize model-supplied math without putting prose inside math mode."""
import re

MATH_ENVIRONMENT = re.compile(
    r'\\begin\{(?P<env>aligned|alignedat|gathered|split|cases|[pbBvV]?matrix|'
    r'equation\*?|align\*?|gather\*?|multline\*?)\}.*?\\end\{(?P=env)\}', re.S)
_DELIMITED = re.compile(MATH_ENVIRONMENT.pattern +
    r'|\$\$(?P<display>.*?)\$\$|\\\[(?P<bracket>.*?)\\\]|'
    r'(?<!\\)\$(?P<inline>[^$]*?)(?<!\\)\$|\\\((?P<paren>.*?)\\\)', re.S)


def normalize_scripts(value: str) -> str:
    """Normalize shorthand indices, keeping literal TeX text and labels intact."""
    literals = re.compile(r'(\\(?:text|textrm|textnormal|textbf|textit|mbox|operatorname|label|ref|cite|url)\{[^{}]*\})')
    def fix(part):
        part = re.sub(r'(?<!\\)_([A-Za-z][A-Za-z0-9]+)(?=\^)', r'_{\1}', part)
        return re.sub(r'(?<!\\)\^(\([^()]*\))', r'^{\1}', part)
    return ''.join(part if index % 2 else fix(part)
                   for index, part in enumerate(literals.split(value)))


def normalize_environment(value: str) -> str:
    """Keep an environment atomic, removing redundant math-mode switches only.

    Dollar-delimited math inside a text group is legitimate and stays intact.
    Outer display environments become bodies suitable for an equation wrapper.
    """
    out, i = [], 0
    while i < len(value):
        group = re.match(r'\\(?:text|textrm|textnormal|textbf|textit|mbox)\s*\{', value[i:])
        if group:
            end, depth = i + group.end(), 1
            while end < len(value) and depth:
                if value[end] == '\\':
                    end += 2
                    continue
                depth += (value[end] == '{') - (value[end] == '}')
                end += 1
            out.append(value[i:end]); i = end
        elif value[i] == '\\' and i + 1 < len(value):
            out.append(value[i:i+2]); i += 2
        elif value[i] == '$':
            i += 1
        else:
            out.append(value[i]); i += 1
    body = ''.join(out)
    match = MATH_ENVIRONMENT.fullmatch(body)
    if match:
        env = match.group('env')
        inner = body[len('\\begin{' + env + '}'):-len('\\end{' + env + '}')]
        if env.rstrip('*') == 'equation':
            return inner.strip()
        replacement = {'align':'aligned', 'gather':'gathered', 'multline':'gathered'}.get(env.rstrip('*'))
        if replacement:
            return '\\begin{' + replacement + '}' + inner + '\\end{' + replacement + '}'
    return body


def math_parts(value: str) -> list[tuple[bool, str]]:
    text = (value or '').strip()
    if not text:
        return []
    environment = MATH_ENVIRONMENT.match(text)
    if environment and (environment.end() == len(text)
                        or text[environment.end():].lstrip().startswith((r'\qquad', r'\quad'))):
        return [(True, normalize_environment(text))]
    matches = list(_DELIMITED.finditer(text))
    if not matches:
        return [(True, text)]
    parts = []
    cursor = 0
    for match in matches:
        prose = text[cursor:match.start()].strip()
        if prose:
            parts.append((False, prose))
        if match.group('env'):
            parts.append((True, normalize_environment(match.group(0))))
        else:
            body = next(match.group(key) for key in ('display','bracket','inline','paren')
                        if match.group(key) is not None).strip()
            if body:
                parts.extend(math_parts(body))
        cursor = match.end()
    if text[cursor:].strip():
        parts.append((False, text[cursor:].strip()))
    return parts


def normalize_manuscript_math(manuscript) -> None:
    for section in manuscript.sections:
        normalized = []
        for block in section.blocks:
            parts = math_parts(block.math)
            if not parts:
                normalized.append(block)
                continue
            for index, (is_math, content) in enumerate(parts):
                item = block if index == 0 else block.model_copy(deep=True, update={
                    'block_id': f'{block.block_id}-part-{index + 1}', 'text': ''})
                prior = block.text if index == 0 else ''
                item.math = content if is_math else ''
                item.text = prior if is_math else '\n'.join(filter(None, [prior, content]))
                normalized.append(item)
        section.blocks = normalized
