from __future__ import annotations

"""参考文献确定性生成（GB/T 7714-2015）

设计思想（借鉴 OpenAI4S）: 把格式做进结构, 而不是做进 prompt。
LLM 写作时只输出正文 [n] 标记, 参考文献章节由本模块从
「可信参考文献清单」程序化生成, 保证:
- 每条文献都有出处（期刊/arXiv/DOI）
- 格式统一符合 GB/T 7714-2015
- 编号与正文引用一一对应（编号 = 清单 ref_number）

格式示例:
[1] VASWANI A, SHAZEER N, PARMAR N, 等. Attention is all you need[J]. NeurIPS, 2017. DOI: 10.5555/123.
[2] 张三, 李四. 基于深度学习的射频指纹识别[EB/OL]. arXiv: 2411.06925, 2024.
"""

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _format_authors(authors: str, max_authors: int = 3) -> str:
    """作者列表按 GB/T 7714 格式化

    - 英文作者: 姓氏全大写 + 名首字母 (VASWANI A)
    - 中文作者: 保持原名
    - 超过 max_authors 个作者用「, 等.」结尾

    兼容两种输入:
    - "Ashish Vaswani, Noam Shazeer" (流水线检索结果的完整姓名, 主路径)
    - "Vaswani, Ashish" (单对 姓, 名)
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
        result += ", 等."
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

# 非真实出处标记（检索 API 名称不是期刊名）
_PLACEHOLDER_SOURCES = ("", "arXiv", "OpenAlex", "Semantic Scholar", "来源未标注")


def _ref_type_marker(ref: dict) -> tuple[str, str]:
    """返回 (文献类型标识, 补充出处字符串)

    GB/T 7714-2015 文献类型: [J]期刊 [C]会议 [M]图书 [D]学位论文 [EB/OL]电子资源
    优先级: 真实出处（期刊/会议） > arXiv 预印本 > DOI/URL 兜底
    """
    url = ref.get("url", "") or ""
    doi = ref.get("doi", "") or ""
    venue = (ref.get("venue") or ref.get("source") or "").strip()

    if venue and venue not in _PLACEHOLDER_SOURCES and not venue.lower().startswith("http"):
        venue_lower = venue.lower()
        # 会议特征词优先 ("IEEE Conference on Communications" 是会议不是期刊)
        if any(hint in venue_lower for hint in CONFERENCE_HINTS):
            return "[C]", venue
        # 期刊特征词 (期刊名含 journal/transactions 等)
        if any(hint in venue_lower for hint in JOURNAL_HINTS):
            return "[J]", venue
        # 默认期刊
        return "[J]", venue

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

    格式示例:
    [1] VASWANI A, SHAZEER N, PARMAR N, 等. Attention is all you need[J].
        NeurIPS, 2017. DOI: 10.5555/123.
    [2] 张三, 李四. 射频指纹识别[J]. IEEE Transactions on Wireless Communications,
        2020, 43(5): 100-110. DOI: 10.1000/xyz.
    [3] 王五. 深度学习综述[EB/OL]. arXiv: 2411.06925, 2024.
    """
    num = ref.get("ref_number", "")
    title = (ref.get("title") or "").strip()
    authors = _format_authors(ref.get("authors", "") or "")
    year = str(ref.get("year", "") or "")
    doi = (ref.get("doi") or "").strip()

    marker, origin = _ref_type_marker(ref)

    parts = []
    if title:
        parts.append(f"{authors}. {title}{marker}." if authors else f"{title}{marker}.")
    else:
        parts.append(f"{authors}. 未命名文献{marker}." if authors else f"未命名文献{marker}.")
    if origin and origin not in ("来源未标注",):
        parts.append(origin + ".")
    if year:
        parts.append(year + ".")
    # 卷(期): 页码 (GB/T 7714: 刊名, 年, 卷(期): 页码.)
    volume = str(ref.get("volume", "") or "").strip()
    issue = str(ref.get("issue", "") or "").strip()
    pages = str(ref.get("pages", "") or "").strip()
    if volume or pages:
        vol_part = f"{volume}" if volume else ""
        if issue:
            vol_part += f"({issue})"
        page_part = f": {pages}" if pages else ""
        parts.append(f"{vol_part}{page_part}.")
    if doi:
        parts.append(f"DOI: {doi}.")

    body = " ".join(parts)
    return f"[{num}] {body}" if num else body


def build_references_section(verified_refs: list[dict]) -> str:
    """从可信清单生成完整参考文献章节（Markdown GB/T 7714）"""
    if not verified_refs:
        return ""
    lines = ["## 参考文献", ""]
    for ref in verified_refs:
        lines.append(format_gbt7714_entry(ref))
    return "\n".join(lines)


def build_bibtex_file(verified_refs: list[dict]) -> str:
    """从可信清单生成 BibTeX 文件（LaTeX 编译用）

    每条文献含 ref{n} 作为 cite key，与正文 \cite{ref1} 对应。
    输出: .bib 文件内容字符串。
    """
    lines = []
    for ref in verified_refs:
        n = ref.get("ref_number", "")
        key = f"ref{n}"
        title = (ref.get("title") or "").strip()
        authors = (ref.get("authors") or "").strip()
        year = (ref.get("year") or "").strip()
        doi = (ref.get("doi") or "").strip()
        venue = (ref.get("venue") or ref.get("source") or "").strip()
        url = (ref.get("url") or "").strip()

        # 作者列表: "A. Vaswani and N. Shazeer and N. Parmar" (BibTeX 标准)
        author_parts = [a.strip() for a in authors.split(",") if a.strip()]
        author_bib = " and ".join(f"{{{a}}}" if "," in a else a for a in author_parts)

        # 条目类型
        venue_lower = venue.lower()
        if any(h in venue_lower for h in ("conference", "proceedings", "symposium", "workshop",
                                             "neurips", "icml", "iclr", "cvpr", "iccv", "eccv",
                                             "acl", "naacl", "emnlp", "aaai", "ijcai", "kdd",
                                             "sigir", "ijcnn", "globecom", "wcnc", "infocom",
                                             "mobicom", "sigcomm", "icassp", "sensys", "ipsn",
                                             "eurocrypt", "usenix", "ndss", "icc", "www", "ccs")):
            entry_type = "inproceedings"
            venue_field = "booktitle"
        else:
            entry_type = "article"
            venue_field = "journal"

        lines.append(f"@{entry_type}{{{key},")
        if author_bib:
            lines.append(f"  author = {{{author_bib}}},")
        lines.append(f"  title = {{{title}}},")
        if venue and venue not in ("arXiv", "OpenAlex", "Semantic Scholar", ""):
            lines.append(f"  {venue_field} = {{{venue}}},")
        if year:
            lines.append(f"  year = {{{year}}},")
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        if url and "arxiv.org" in url:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s]+)", url)
            if m:
                lines.append(f"  eprint = {{{m.group(1)}}},")
                lines.append(f"  archivePrefix = {{arXiv}},")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def strip_references_section(draft: str) -> str:
    """从初稿中移除已有参考文献章节（防 LLM 重复/混入垃圾条目）"""
    if not draft:
        return draft
    m = re.search(r"^#{1,3}\s*参考文献\s*$|^#{1,3}\s*References\s*$", draft, re.M | re.I)
    if m:
        return draft[: m.start()].rstrip() + "\n"
    return draft


def attach_references_section(draft: str, verified_refs: list[dict]) -> str:
    """清洗旧章节 + 附加确定性参考文献章节（正文 [n] 编号保持不变）"""
    cleaned = strip_references_section(draft)
    refs = build_references_section(verified_refs)
    if not refs:
        return cleaned
    return cleaned.rstrip() + "\n\n" + refs + "\n"
