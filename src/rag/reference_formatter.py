from __future__ import annotations

"""参考文献确定性生成 (GB/T 7714-2015)

格式做进结构而非 prompt。正文 [n] 标记保持不变，参考文献章节程序化生成。
"""

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 引用标记: [1] / [2,7] / [2, 5] / [1,4,8] —— 逗号分隔的多编号引用
# 注意不能用 \[(\d+)\]: 它只匹配单编号, 会漏掉 [2,7] 这类多编号引用,
# 导致"正文按出现顺序重编号"失效 (审稿人反复扣"编号与正文不一致")。
CITE_GROUP_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def find_citation_numbers(text: str) -> list[int]:
    """提取文本中所有引用编号 (含 [2,7] 这类多编号引用, 逐个展开)"""
    nums: list[int] = []
    for m in CITE_GROUP_RE.finditer(text or ""):
        nums.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    return nums


def _strip_writer_statistics(draft: str) -> str:
    """移除 writer 在文末输出的统计信息块 (如 "--- **统计信息**：总字数...")

    该块是写作阶段的元信息, 不应出现在最终论文里, 也会污染引用重编号的编号扫描。
    """
    if not draft:
        return draft
    return re.sub(r"(?m)^[^\n]*\*\*统计信息\*\*[^\n]*\n?", "", draft)


# 预印本 DOI 前缀: 这些注册机构只托管预印本/未审稿件, 不是正式出版
# - 10.48550 = arXiv (DataCite)
# - 10.36227 = TechRxiv (IEEE 预印本服务器)
# - 10.2139  = SSRN (社科/预印本)
# - 10.31219 = OSF Preprints, 10.31224 = engrXiv, 10.31234 = SocArXiv, 10.31237 = Preprints.org
_PREPRINT_DOI_PREFIXES = (
    "10.48550/arxiv",
    "10.36227/",
    "10.2139/",
    "10.31219/",
    "10.31224/",
    "10.31234/",
    "10.31237/",
)


def is_published_ref(ref: dict) -> bool:
    """判断一条文献是否为真实已发表文献（非预印本）

    判定口径与 citation_precheck 的 published 标记一致:
    - 人工导入 (人已确认存在/发表): 已发表
    - 学位论文 [D]: 已发表 (正式公开成果)
    - 有真实 DOI: 已发表 (DOI 是正式出版的权威标识)
    - 预印本 DOI (arXiv/TechRxiv/SSRN/OSF 等): 仍是预印本, 不算已发表
    - 其余 (无 DOI 预印本等): 预印本, 不进入最终参考文献
    """
    if (ref.get("api_source") or "") == "人工导入":
        return True
    if (ref.get("ref_type") or "").strip().upper() == "D":
        return True
    doi = (ref.get("doi") or "").strip()
    if not doi:
        return False
    dl = doi.lower()
    # arXiv DOI / TechRxiv / SSRN / OSF 等预印本 DOI 不代表正式发表
    if "arxiv" in dl:
        return False
    if any(dl.startswith(p) for p in _PREPRINT_DOI_PREFIXES):
        return False
    return True


def _format_authors(authors: str, max_authors: int = 3) -> str:
    """作者列表按 GB/T 7714 格式化。英文: 姓氏全大写+名首字母；中文: 保持原名。
    >3 人英文用 et al.，中文用等。
    """
    if not authors or not authors.strip():
        return ""  # 作者未知时留空, GB/T 7714 允许省略

    # 单对 "姓, 名" 形式 ("Vaswani, Ashish")
    if authors.count(",") == 1 and re.match(r"^\s*[A-Za-z]+,\s*[A-Za-z]+$", authors):
        surname, given = authors.split(",", 1)
        initials = "".join(w[0].upper() for w in given.split() if w)
        formatted = [f"{surname.strip().upper()} {initials}"]
        parts = [authors]
    else:
        # 逗号分隔的完整姓名列表 ("Ashish Vaswani, Noam Shazeer")
        parts = [p.strip() for p in authors.split(",") if p.strip()]
        formatted = []
        for p in parts[:max_authors]:
            if CJK_RE.search(p):
                formatted.append(p)
            else:
                words = p.split()
                if len(words) >= 2:
                    surname = words[-1].upper()
                    initials = "".join(w[0].upper() for w in words[:-1] if w)
                    formatted.append(f"{surname} {initials}")
                else:
                    formatted.append(p.upper())

    result = ", ".join(formatted)
    if len(parts) > max_authors:
        # GB/T 7714-2015: 外文文献用 et al.，中文文献用 等
        has_cjk = any(CJK_RE.search(p) for p in parts)
        result += ", et al." if not has_cjk else ", 等"
    return result


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+\.?\d{4,5}(?:v\d+)?)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url or "")
    return m.group(1) if m else ""


# 期刊特征词: 命中则标 [J]
JOURNAL_HINTS = (
    "journal", "transactions", "letters", "review", "magazine",
    "computing surveys", "intelligence", "applications", "networks", "communications",
)

# 会议特征词: 命中则标 [C]
CONFERENCE_HINTS = (
    "conference", "proceedings", "symposium", "workshop",
    "neurips", "icml", "iclr", "cvpr", "iccv", "eccv", "acl", "naacl", "emnlp", "aaai",
    "ijcai", "kdd", "sigir", "ijcnn", "globecom", "wcnc", "infocom", "mobicom", "sigcomm",
    "icassp", "sensys", "ipsn", "eurocrypt", "usenix", "ndss",
    "icc", "www", "ccs", "aistats", "coling", "interspeech", "asplos", "osdi", "sosp", "fast",
)

# 非真实出处标记（检索 API 名称/人工导入标记不是期刊名）
_PLACEHOLDER_SOURCES = ("", "arXiv", "OpenAlex", "Semantic Scholar", "来源未标注", "人工导入", "中文期刊")

# 会议缩写+年份的垃圾出处 (arXiv journal_ref 常见 "INCC2024", 无真实会议名)
_JUNK_VENUE_RE = re.compile(r"^[A-Z]{2,10}\s?\d{4}$")

# 常见国家名: 用于从会议 venue 尾部剥离 "城市, 国家" 地点信息
# (实测: "…(ETFA), Stuttgart, Germany" 被审稿人判"会议地点位置违规")
_COUNTRY_NAMES = frozenset((
    "germany", "usa", "u.s.a.", "united states", "china", "italy", "france",
    "japan", "uk", "u.k.", "united kingdom", "spain", "canada", "australia",
    "netherlands", "switzerland", "austria", "belgium", "sweden", "norway",
    "denmark", "finland", "portugal", "greece", "ireland", "israel",
    "singapore", "south korea", "korea", "india", "brazil", "mexico",
    "poland", "russia", "turkey", "egypt", "south africa", "new zealand",
    "hungary", "czech republic", "romania", "chile", "argentina",
))


def _clean_venue(venue: str, is_conference: bool) -> str:
    """清洗出处字符串中的年份残留与会议地点信息。

    实测格式缺陷 (审稿人连续扣参考文献分):
    - venue 尾部内嵌年份 ("IEEE Internet of Things Journal 2021") 与 year 字段
      叠加 → "…Journal 2021, 2020, 8(10)" 年份重复;
    - 会议 venue 尾部带 "城市, 国家" (GB/T 7714 会议条目不含地点)。
    """
    v = (venue or "").strip()
    if not v:
        return v
    # 尾部年份剥离 (带或不带逗号): 年份由 year 字段单独输出, 避免重复
    v = re.sub(r"[,\s]+\b(?:19|20)\d{2}\b\s*$", "", v).strip()
    # 会议地点剥离: "…(ETFA), Stuttgart, Germany" → "…(ETFA)"
    if is_conference:
        parts = [p.strip() for p in v.split(",")]
        if len(parts) >= 3 and parts[-1].lower().strip(".") in _COUNTRY_NAMES:
            v = ", ".join(parts[:-2])
    return v.strip().rstrip(".")


def _ref_type_marker(ref: dict) -> tuple[str, str]:
    """返回 (文献类型标识, 补充出处字符串)

    GB/T 7714-2015 文献类型: [J]期刊 [C]会议 [M]图书 [D]学位论文 [EB/OL]电子资源
    优先级: 显式类型标记 > 真实出处（期刊/会议） > arXiv 预印本 > DOI/URL 兜底
    """
    url = ref.get("url", "") or ""
    doi = ref.get("doi", "") or ""
    venue = (ref.get("venue") or ref.get("source") or "").strip()

    # 显式类型标记 (meta.json 的 type 字段, 如 "D"=学位论文)
    ref_type = (ref.get("ref_type") or "").strip().upper()
    if ref_type == "D":
        return "[D]", venue if venue and venue not in _PLACEHOLDER_SOURCES else ""

    if venue and venue not in _PLACEHOLDER_SOURCES and not venue.lower().startswith("http"):
        if _JUNK_VENUE_RE.match(venue):
            venue = ""
        else:
            venue_lower = venue.lower()
            # 会议特征词优先 ("IEEE Conference on Communications" 是会议不是期刊)
            if any(hint in venue_lower for hint in CONFERENCE_HINTS):
                return "[C]", _clean_venue(venue, is_conference=True)
            # 期刊特征词 (期刊名含 journal/transactions 等)
            if any(hint in venue_lower for hint in JOURNAL_HINTS):
                return "[J]", _clean_venue(venue, is_conference=False)
            # 默认期刊
            return "[J]", _clean_venue(venue, is_conference=False)

    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        return "[EB/OL]", f"arXiv: {arxiv_id}"

    if doi:
        return "[EB/OL]", "网络文献"

    if url:
        return "[EB/OL]", url

    return "[EB/OL]", "来源未标注"


def format_gbt7714_entry(ref: dict) -> str:
    """单条文献 → GB/T 7714-2015 条目

    标准格式:
    - 期刊: 作者. 题名[J]. 刊名, 年, 卷(期): 页码.
    - 会议: 作者. 题名[C]. 会议名, 年: 页码.
    - 预印本: 作者. 题名[EB/OL]. arXiv: xxx. 年.
    - DOI 去 https://doi.org/ 前缀: DOI: 10.1049/el.2018.6404.
    """
    num = ref.get("ref_number", "")
    title = (ref.get("title") or "").strip()
    authors = _format_authors(ref.get("authors", "") or "")
    year = str(ref.get("year", "") or "")
    doi = _normalize_doi(ref.get("doi", "") or "")

    marker, origin = _ref_type_marker(ref)
    # venue 内嵌年份兜底剥离 (_clean_venue 已处理期刊/会议出处; 此处覆盖其余路径):
    # 年份由 year 字段单独输出, 避免 "…Journal 2021, 2020, 8(10)" 年份重复
    # (实测被审稿人判格式异常, 参考文献维度无法上 4 分)
    origin = re.sub(r"[,\s]+\b(?:19|20)\d{2}\b\s*$", "", origin).strip().rstrip(".")

    # 作者 + 题名 + 类型标识
    sep = ". " if authors and not authors.endswith(".") else " "
    head = f"{authors}{sep}{title}{marker}." if authors else f"{title}{marker}."

    # 刊名/会议名 + 年 + 卷(期): 页码, 均用逗号分隔 (GB/T 7714-2015)
    volume = str(ref.get("volume", "") or "").strip()
    issue = str(ref.get("issue", "") or "").strip()
    pages = str(ref.get("pages", "") or "").strip()

    vol_issue = ""
    if volume or issue:
        # 有卷或有期: 卷(期); 只有期无卷时用 (期)
        vol_issue = volume
        if issue:
            vol_issue += f"({issue})"

    if marker == "[D]":
        # 学位论文: 作者. 题名[D]. 城市: 学校名, 年.
        # GB/T 7714-2015: 主要责任者. 题名[D]. 出版地: 授予单位, 出版年.
        city = (ref.get("city") or "").strip()
        tail_parts = []
        if city and origin:
            tail_parts.append(f"{city}: {origin}")
        elif origin:
            tail_parts.append(origin)
        if year:
            tail_parts.append(year)
        body = f"{head} {', '.join(tail_parts)}." if tail_parts else head
        return f"[{num}] {body}" if num else body

    if marker == "[EB/OL]":
        # 电子资源/预印本: 出处 + 年 (arXiv: id, 年.)
        tail_parts = []
        if origin and origin not in ("来源未标注",):
            tail_parts.append(origin)
        if year:
            tail_parts.append(year)
        if doi:
            tail_parts.append(f"DOI: {doi}")
        if tail_parts:
            return f"[{num}] {head} {', '.join(tail_parts)}." if num else f"{head} {', '.join(tail_parts)}."
        return f"[{num}] {head}" if num else head

    if marker == "[C]":
        # 会议论文 [C]: 题名[C]//会议录名. 年: 页码. (无卷号字段)
        # GB/T 7714-2015: 主要责任者. 题名[C]//会议录名. 出版地: 出版者, 年: 页码.
        tail_parts = []
        if origin and origin not in ("来源未标注",):
            tail_parts.append(origin)
        if year:
            tail_parts.append(year)
        if pages:
            tail_parts.append(pages)
        body = f"{head} {', '.join(tail_parts)}."
        if doi:
            body += f" DOI: {doi}."
        return f"[{num}] {body}" if num else body

    # 期刊 [J]: 刊名, 年, 卷(期): 页码. [DOI: xxx.]
    tail_parts = []
    if origin and origin not in ("来源未标注",):
        tail_parts.append(origin)
    if year:
        tail_parts.append(year)
    if vol_issue:
        # 有卷或有期: 卷(期): 页码
        page_part = f": {pages}" if pages else ""
        tail_parts.append(f"{vol_issue}{page_part}")
    else:
        # 无卷期 (IEEE Early Access 等"在线优先出版", 或 CrossRef 未收录卷期页):
        # 标注"在线优先出版[引用日期]", 而不是渲染成 ", : 1-1" 这类残缺格式。
        # GB/T 7714-2015 要求在线资源标注引用日期: 审稿人曾反复以
        # "在线优先出版缺引用日期/格式不规范"为由把参考文献维度判为 Critical,
        # 而参考文献章节由本函数程序化生成、Writer 无法修改 → 在源头补齐。
        import datetime as _dt

        tail_parts.append(f"在线优先出版[{_dt.date.today().isoformat()}]")
    body = f"{head} {', '.join(tail_parts)}."
    if doi:
        body += f" DOI: {doi}."
    return f"[{num}] {body}" if num else body


def _normalize_doi(doi: str) -> str:
    """DOI 规范化: 去 https://doi.org/ 前缀与尾部句点, 输出裸 DOI"""
    if not doi:
        return ""
    d = doi.strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.rstrip(".")


def build_references_section(verified_refs: list[dict]) -> str:
    """从可信清单生成完整参考文献章节（Markdown GB/T 7714）"""
    if not verified_refs:
        return ""
    lines = ["## 参考文献", ""]
    for ref in verified_refs:
        lines.append(format_gbt7714_entry(ref))
    return "\n".join(lines)


def strip_references_section(draft: str) -> str:
    """从初稿中移除已有参考文献章节（防 LLM 重复/混入垃圾条目）"""
    if not draft:
        return draft
    m = re.search(r"^#{1,3}\s*参考文献\s*$|^#{1,3}\s*References\s*$", draft, re.M | re.I)
    if m:
        return draft[: m.start()].rstrip() + "\n"
    return draft


# 草稿阶段的 abstention 占位标注 (writer 用来标记"无文献支撑"), 定稿时必须清除
_EVIDENCE_MARKER_RE = re.compile(r"\[缺证据(?:\s*[:：][^\]]*)?\]")


def strip_evidence_markers(draft: str) -> str:
    """移除正文中的 [缺证据] / [缺证据：xxx] 标注

    这些是 writer 的 abstention 信号, 不应出现在最终论文里。
    """
    if not draft:
        return draft
    return _EVIDENCE_MARKER_RE.sub("", draft)


def attach_references_section(draft: str, verified_refs: list[dict]) -> str:
    """清洗旧章节 + 仅附加**正文实际引用**的参考文献（GB/T 7714）

    只收录正文中出现的 [n] 编号对应的文献，未被引用的条目（含预印本）
    不进入参考文献章节，避免"参考文献未被文中引用"的扣分。
    """
    cleaned = strip_references_section(draft)
    cleaned = strip_evidence_markers(cleaned)
    cleaned = _strip_writer_statistics(cleaned)
    cited_nums = set(find_citation_numbers(cleaned))
    cited_refs = [r for r in verified_refs if int(r.get("ref_number", -1)) in cited_nums]
    refs = build_references_section(cited_refs)
    if not refs:
        return cleaned
    return cleaned.rstrip() + "\n\n" + refs + "\n"


def sort_and_merge_citation_groups(text: str) -> str:
    """引用标记升序排序与合并 (GB/T 7714 顺序编码制要求同一处多引用升序)。

    Writer 常输出相邻独立括号且无序的多引用 (如 [13][11][1]);
    按首次出现顺序重编号后这种乱序会更明显。确定性处理:
    - 相邻标记合并: [13][11][1] → [1,11,13]
    - 组内排序去重: [5,3] → [3,5]
    单个引用标记保持不变; 不影响 [图N]/[表N] 占位符与脚注 [^1]。
    """
    if not text:
        return text

    run_re = re.compile(r"\[\d+(?:\s*,\s*\d+)*\](?:\s*\[\d+(?:\s*,\s*\d+)*\])*")

    def _sort_run(m: re.Match) -> str:
        nums = sorted({int(x) for x in re.findall(r"\d+", m.group(0))})
        return "[" + ",".join(str(n) for n in nums) + "]"

    return run_re.sub(_sort_run, text)


def renumber_citations(draft: str) -> str:
    """按正文首次出现顺序重排引用编号 (1..N 连续)

    引用验证阶段 (guard/check) 均基于原始编号完成后调用此函数。
    - 正文 [旧编号] → [新编号]
    - 参考文献章节条目重排并重新编号
    - 正文未引用的参考文献条目直接丢弃
    """
    draft = _strip_writer_statistics(draft)
    body, ref_section = _split_body_and_refs(draft)
    if not body:
        return draft

    # 0. 先排序合并多引用组: 首次出现顺序按"升序阅读顺序"计算,
    # 避免 [13][11][1] 这类无序组把编号顺序带乱
    body = sort_and_merge_citation_groups(body)

    # 1. 正文引用按首次出现顺序映射 (含 [2,7] 多编号引用, 逐个编号展开)
    order: list[int] = []
    seen: set[int] = set()
    for n in find_citation_numbers(body):
        if n not in seen:
            seen.add(n)
            order.append(n)
    if not order:
        return draft
    mapping = {old: new for new, old in enumerate(order, 1)}

    # 2. 重写正文引用 (多编号引用 [2,7] → [1,2], 逐号映射)
    def _rewrite(m: re.Match) -> str:
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return "[" + ",".join(str(mapping.get(n, n)) for n in nums) + "]"

    new_body = CITE_GROUP_RE.sub(_rewrite, body)
    # 映射可能引入新的乱序 (如旧 [3,5] 分别映射为 [2,1]), 重映射后再排序一次
    new_body = sort_and_merge_citation_groups(new_body)

    # 3. 重排参考文献章节 (按新编号, 丢弃未引用条目)
    entries: dict[int, str] = {}
    if ref_section:
        for line in ref_section.split("\n"):
            m = re.match(r"^\[(\d+)\]\s*(.*)$", line.strip())
            if m:
                old = int(m.group(1))
                if old in mapping:
                    entries[mapping[old]] = m.group(2).strip()
        if entries:
            ref_lines = ["## 参考文献", ""]
            for n in sorted(entries):
                ref_lines.append(f"[{n}] {entries[n]}")
            return new_body.rstrip() + "\n\n" + "\n".join(ref_lines) + "\n"

    return new_body


def renumber_draft_and_refs(draft: str, verified_refs: list[dict]) -> tuple[str, list[dict]]:
    """按正文首次出现顺序重排引用编号, 并同步重排 verified_refs 的 ref_number

    关键: verified_refs 是**完整文献库** (writer 后续修订仍要从中查找引用),
    不能因本轮未引用就丢弃。处理方式:
    - 被引用的文献: 按首次出现顺序编号 1..N (与正文新编号一致)
    - 未被引用的文献: 保留在库中, 编号接在 N+1..M (保持原相对顺序)
    这样文献库完整, 且被引用文献编号与草稿正文一致。
    """
    draft = _strip_writer_statistics(draft)
    body, ref_section = _split_body_and_refs(draft)
    if not body:
        return draft, verified_refs

    # 先排序合并多引用组: 首次出现顺序按"升序阅读顺序"计算 (同 renumber_citations)
    body = sort_and_merge_citation_groups(body)

    order: list[int] = []
    seen: set[int] = set()
    for n in find_citation_numbers(body):
        if n not in seen:
            seen.add(n)
            order.append(n)
    if not order:
        return draft, verified_refs
    mapping = {old: new for new, old in enumerate(order, 1)}

    # 重写正文 (多编号引用 [2,7] → [1,2], 逐号映射)
    def _rewrite(m: re.Match) -> str:
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return "[" + ",".join(str(mapping.get(n, n)) for n in nums) + "]"

    new_body = CITE_GROUP_RE.sub(_rewrite, body)
    # 映射可能引入新的乱序, 重映射后再排序一次
    new_body = sort_and_merge_citation_groups(new_body)

    # 重排参考文献章节 (仅被引用的, 按新编号)
    entries: dict[int, str] = {}
    if ref_section:
        for line in ref_section.split("\n"):
            m = re.match(r"^\[(\d+)\]\s*(.*)$", line.strip())
            if m:
                old = int(m.group(1))
                if old in mapping:
                    entries[mapping[old]] = m.group(2).strip()
        ref_lines = ["## 参考文献", ""]
        for n in sorted(entries):
            ref_lines.append(f"[{n}] {entries[n]}")
        new_draft = new_body.rstrip() + "\n\n" + "\n".join(ref_lines) + "\n"
    else:
        new_draft = new_body

    # 重排 verified_refs (文献库完整保留):
    # 被引用的 → 编号 1..N (出现顺序); 未被引用的 → 编号 N+1.. (原顺序)
    cited = []
    uncited = []
    for r in verified_refs:
        old = int(r.get("ref_number", -1))
        if old in mapping:
            nr = dict(r)
            nr["ref_number"] = mapping[old]
            cited.append(nr)
        else:
            uncited.append(r)
    cited.sort(key=lambda x: int(x.get("ref_number", 0)))
    uncited.sort(key=lambda x: int(x.get("ref_number", 0)))
    next_num = len(cited) + 1
    for r in uncited:
        nr = dict(r)
        nr["ref_number"] = next_num
        next_num += 1
        cited.append(nr)

    return new_draft, cited


def _split_body_and_refs(draft: str) -> tuple[str, str]:
    """拆分正文与参考文献章节"""
    m = re.search(r"^#{1,3}\s*(?:参考文献|References)\s*$", draft, re.M | re.I)
    if m:
        return draft[: m.start()], draft[m.end():]
    return draft, ""
