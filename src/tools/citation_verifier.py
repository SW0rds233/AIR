from __future__ import annotations

"""引文真实性核查工具

参考开源项目:
- opendraft (https://github.com/federicodeponte/opendraft): CrossRef/OpenAlex/arXiv 三源验证
- sisyphus-academica: 引用验证与对抗性审查
- research-paper-lifecycle-skills: citation verification agent skill

数据源（全部免费，无需 API Key）:
- CrossRef REST API: https://api.crossref.org/works
- OpenAlex API: https://api.openalex.org/works
- arXiv API: https://export.arxiv.org/api/query
"""

import os
import re
import difflib
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"

CONTACT_EMAIL = os.getenv("CROSSREF_EMAIL", "")


class CitationRecord:
    """一条待验证的引用记录"""

    def __init__(
        self,
        ref_number: int,
        title: str = "",
        authors: str = "",
        year: str = "",
        raw: str = "",
    ):
        self.ref_number = ref_number
        self.title = title.strip()
        self.authors = authors.strip()
        self.year = year.strip()
        self.raw = raw.strip()

    def to_dict(self) -> dict:
        return {
            "ref_number": self.ref_number,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "raw": self.raw,
        }


class VerificationResult:
    """单条引用的验证结果"""

    def __init__(self, record: CitationRecord):
        self.record = record
        self.status: str = "PENDING"  # VERIFIED / NOT_FOUND / AMBIGUOUS / ERROR
        self.matched_title: str = ""
        self.doi: str = ""
        self.venue: str = ""
        self.similarity: float = 0.0
        self.detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ref_number": self.record.ref_number,
            "status": self.status,
            "claimed_title": self.record.title,
            "matched_title": self.matched_title,
            "doi": self.doi,
            "venue": self.venue,
            "similarity": round(self.similarity, 3),
            "detail": self.detail,
        }


def _crossref_headers() -> dict:
    headers = {"User-Agent": "AIR-AIResearch/0.1 (mailto:%s)" % (CONTACT_EMAIL or "contact@example.com")}
    if CONTACT_EMAIL:
        headers["X-ELS-APIKey"] = ""
    return headers


def _title_similarity(a: str, b: str) -> float:
    """归一化标题字符串并计算相似度"""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def verify_crossref(record: CitationRecord) -> Optional[dict]:
    """通过 CrossRef API 验证引用是否存在"""
    params = {
        "query.title": record.title,
        "rows": 10,
        "select": "title,DOI,container-title,issued,author",
    }
    if record.authors:
        params["query.author"] = record.authors
    if record.year:
        params["filter"] = (
            f"from-pub-date:{int(record.year) - 1}-01-01,"
            f"until-pub-date:{int(record.year) + 1}-12-31"
        )

    try:
        resp = httpx.get(CROSSREF_URL, params=params, headers=_crossref_headers(), timeout=30)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as e:
        logger.warning(f"CrossRef request failed: {e}")
        return None

    if not items:
        return {"status": "NOT_FOUND", "similarity": 0.0}

    best = None
    best_score = 0.0
    for item in items:
        title = (item.get("title") or [""])[0]
        score = _title_similarity(record.title, title)
        if score > best_score:
            best_score = score
            best = item

    if best is None:
        return {"status": "NOT_FOUND", "similarity": 0.0}

    year = (best.get("issued", {}).get("date-parts") or [[None]])[0][0]

    # 撤稿检测 (借鉴 OpenAI4S literature-review skill):
    # CrossRef 的 update-to 字段标记 retraction/withdrawal
    retracted = False
    for upd in best.get("update-to", []):
        if str(upd.get("type", "")).lower() in ("retraction", "withdrawal"):
            retracted = True
            break

    result = {
        "status": "VERIFIED" if best_score >= 0.7 else "AMBIGUOUS",
        "matched_title": (best.get("title") or [""])[0],
        "doi": best.get("DOI", ""),
        "venue": (best.get("container-title") or [""])[0],
        "similarity": best_score,
        "year": year,
    }
    if retracted:
        result["status"] = "RETRACTED"
        result["retracted"] = True
    return result


def verify_openalex(record: CitationRecord) -> Optional[dict]:
    """通过 OpenAlex API 验证引用是否存在（CrossRef 失败时的备选）"""
    import time

    search_term = record.title[:200]
    params = {"search": search_term, "per-page": 5, "select": "title,doi,primary_location,publication_year"}

    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(OPENALEX_URL, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            logger.warning(f"OpenAlex request failed: {e}")
            return None
    if resp is None:
        return None
    items = resp.json().get("results", [])

    if not items:
        return {"status": "NOT_FOUND", "similarity": 0.0}

    best = None
    best_score = 0.0
    for item in items:
        title = item.get("title") or ""
        score = _title_similarity(record.title, title)
        if score > best_score:
            best_score = score
            best = item

    if best is None:
        return {"status": "NOT_FOUND", "similarity": 0.0}

    venue = ""
    loc = best.get("primary_location") or {}
    source = loc.get("source") or {}
    venue = source.get("display_name") or ""

    return {
        "status": "VERIFIED" if best_score >= 0.7 else "AMBIGUOUS",
        "matched_title": best.get("title") or "",
        "doi": best.get("doi") or "",
        "venue": venue,
        "similarity": best_score,
        "year": best.get("publication_year"),
    }


def verify_arxiv(record: CitationRecord) -> Optional[dict]:
    """通过 arXiv API 验证（针对 arXiv 预印本）"""
    # 清洗查询: 去 markdown 标记 + 截断 (草稿参考文献行可能含 ** 等格式)
    # 下划线替换为空格而非删除, 避免词拼接导致 0 命中
    title_clean = re.sub(r"[*#`>\[\]]", "", record.title)
    title_clean = title_clean.replace("_", " ")
    title_clean = re.sub(r"\s+", " ", title_clean).strip()[:100]
    params = {
        "search_query": f"ti:{title_clean}",
        "start": 0,
        "max_results": 5,
    }
    try:
        resp = httpx.get(ARXIV_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        logger.warning(f"arXiv request failed: {e}")
        return None

    if not isinstance(text, str) or not text.strip():
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    best = None
    best_score = 0.0
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = " ".join(title_el.text.split()) if title_el is not None and title_el.text else ""
        score = _title_similarity(record.title, title)
        if score > best_score:
            best_score = score
            best = (title, entry.find("atom:id", ns).text if entry.find("atom:id", ns) is not None else "")

    if best is None:
        return {"status": "NOT_FOUND", "similarity": 0.0}

    return {
        "status": "VERIFIED" if best_score >= 0.7 else "AMBIGUOUS",
        "matched_title": best[0],
        "doi": "",
        "venue": "arXiv",
        "similarity": best_score,
    }


def verify_single_citation(record: CitationRecord) -> VerificationResult:
    """对单条引用依次尝试 CrossRef → OpenAlex → arXiv，取最优匹配

    策略:
    - 任一源 VERIFIED (sim >= 0.7) → 直接返回 VERIFIED
    - 三源都查过后取相似度最高的结果
    - 最高相似度 < 0.4 → NOT_FOUND
    - AMBIGUOUS 且匹配到 DOI → 追加 DOI 存在性确认（借鉴 OpenAI4S:
      doi.org/CrossRef 404 = 伪造；存在性判定是"真伪"维度的独立证据）
    """
    result = VerificationResult(record)

    if not record.title:
        result.status = "ERROR"
        result.detail = "Empty title"
        return result

    candidates = []
    for verifier, name in [
        (verify_crossref, "CrossRef"),
        (verify_openalex, "OpenAlex"),
        (verify_arxiv, "arXiv"),
    ]:
        data = verifier(record)
        if data is None:
            continue
        data["source"] = name
        candidates.append(data)
        if data["status"] == "VERIFIED" and data["similarity"] >= 0.7:
            result.status = "VERIFIED"
            result.matched_title = data.get("matched_title", "")
            result.doi = data.get("doi", "")
            result.venue = data.get("venue", "")
            result.similarity = data.get("similarity", 0.0)
            result.detail = f"Verified via {name}"
            return result
        if data["status"] == "RETRACTED":
            # 已撤稿论文: 直接标记, 不再继续查
            result.status = "RETRACTED"
            result.matched_title = data.get("matched_title", "")
            result.doi = data.get("doi", "")
            result.venue = data.get("venue", "")
            result.similarity = data.get("similarity", 0.0)
            result.detail = "RETRACTED: 该论文已被撤稿"
            return result

    if not candidates:
        result.status = "NOT_FOUND"
        result.detail = "All sources failed"
        return result

    # 取相似度最高的候选
    best = max(candidates, key=lambda c: c.get("similarity", 0.0))
    result.matched_title = best.get("matched_title", "")
    result.doi = best.get("doi", "")
    result.venue = best.get("venue", "")
    result.similarity = best.get("similarity", 0.0)

    if result.similarity >= 0.7:
        result.status = "VERIFIED"
        result.detail = f"Verified via {best['source']}"
    elif result.similarity >= 0.4:
        result.status = "AMBIGUOUS"
        result.detail = f"Ambiguous match via {best['source']}"
        # DOI 存在性确认: 标题相似度存疑时，DOI 本身是否存在是独立判据
        if result.doi:
            exists = _doi_exists_via_crossref(result.doi)
            if exists is True:
                result.status = "VERIFIED"
                result.detail = f"Ambiguous title, but DOI exists ({result.doi})"
            elif exists is False:
                result.detail = f"Ambiguous match via {best['source']} + DOI NOT_FOUND"
    else:
        result.status = "NOT_FOUND"
        result.detail = "Best match below 0.4 similarity"

    return result


def _doi_exists_via_crossref(doi: str) -> Optional[bool]:
    """DOI 存在性判定: CrossRef works/{doi} → 200=存在 / 404=不存在 / None=不可判定

    借鉴 OpenAI4S 的三值语义: None 严格区别于 False —
    网络/限流失败时不得把论文误判为"不存在"（伪造）。
    """
    if not doi:
        return None
    from urllib.parse import quote

    try:
        resp = httpx.get(
            f"https://api.crossref.org/works/{quote(doi, safe='')}",
            headers=_crossref_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        return None
    except Exception:
        return None


def _is_pseudo_ref_content(content: str) -> bool:
    """伪参考文献条目过滤: 修订说明/自检清单/统计信息混入的条目

    (含 markdown 加粗标记 ** 或 超长文本 或 以指令性文字开头)
    """
    if "**" in content or len(content) > 200:
        return True
    if content.startswith(("请", "建议", "注意", "修订", "删除", "增加", "改为", "标题", "修改")):
        return True
    if content.startswith(("[x]", "[X]", "- [ ]", "- [x]")):
        return True
    return False


def _looks_like_citation(content: str) -> bool:
    """引用条目判定: 必须含 4 位年份 (19xx/20xx) 或 DOI

    修复: 修订说明的编号条目（"4. 摘要精简至约280字…"）不含年份/DOI，
    不能当作参考文献；这是区分"引用条目"与"正文编号"的确定性判据。
    注意不能用 \\b 词边界: BibTeX key "vaswani2017" 中 2017 前是字母,
    词边界不成立。
    """
    if re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", content):
        return True
    if re.search(r"\b10\.\d{4,9}/", content, re.IGNORECASE):
        return True
    if re.search(r"doi\s*[:=]", content, re.IGNORECASE):
        return True
    return False


def extract_references_from_draft(
    draft: str, return_has_section: bool = False
):
    """从论文初稿中提取参考文献条目

    支持两种格式:
    1. 数字编号引用 [1], [2]  + 文末参考文献列表
    2. 从参考文献列表中按行解析 (title, authors, year)

    return_has_section=True 时返回 (records, has_ref_section) 元组。
    """
    records: list[CitationRecord] = []
    seen_numbers = set()
    has_ref_section = False

    # 方法1: 尝试解析文末参考文献列表（常见于 AI 生成的 Markdown 草稿）
    ref_section_match = re.search(r"##+\s*参考文献|References|Bibliography", draft)
    if ref_section_match:
        has_ref_section = True
        ref_section = draft[ref_section_match.end():]
        lines = [l.strip() for l in ref_section.split("\n") if l.strip()]
        num_pattern = re.compile(r"^\[?(\d+)\]?[\s\.\):]*\s*(.*)$")
        for line in lines:
            m = num_pattern.match(line)
            if not m:
                continue

            num = int(m.group(1))
            content = m.group(2)
            if num in seen_numbers:
                continue

            # 过滤"伪参考文献": 修订说明/审稿意见混入的条目
            if _is_pseudo_ref_content(content) or not _looks_like_citation(content):
                continue

            seen_numbers.add(num)
            records.append(_parse_ref_content(num, content))

    # 方法2: 若未找到参考文献列表，则扫描全文中的 [n] 标记
    if not records and not has_ref_section:
        inline_nums = sorted(set(int(n) for n in re.findall(r"\[(\d+)\]", draft)))
        if inline_nums:
            # 从全文里找 "n. Title (year)" 模式的行 (仅行首编号, 且必须含年份/DOI)
            for n in inline_nums:
                pat = re.compile(rf"^\s*\[?{n}\]?[\.\):]\s*(.{{20,200}})")
                matches = pat.findall(draft, re.MULTILINE)
                for candidate in matches:
                    if _is_pseudo_ref_content(candidate):
                        continue
                    if not _looks_like_citation(candidate):
                        continue
                    records.append(_parse_ref_content(n, candidate))
                    break

    return (records, has_ref_section) if return_has_section else records


def _parse_ref_content(num: int, content: str) -> CitationRecord:
    """从引用文本中提取 title/authors/year

    支持格式:
    - "Author, A., Author, B. (2020). Title. Journal"
    - "Title (2020): 期刊信息"
    - BibTeX "@article{key, title={...}}"
    - GB/T 7714: "VASWANI A, SHAZEER N, 等. Attention is all you need[J]. NeurIPS, 2017. DOI: 10.x."
    """
    # 年份必须是 19xx/20xx (避免 arXiv id "2411.06925" 里的 2411 干扰)
    year_match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", content)
    year = year_match.group(0) if year_match else ""

    title = ""
    authors = ""

    bibtex_match = re.search(r"title\s*=\s*[{]([^}]+)[}]", content, re.IGNORECASE)
    if bibtex_match:
        title = bibtex_match.group(1)
        author_match = re.search(r"author\s*=\s*[{]([^}]+)[}]", content, re.IGNORECASE)
        if author_match:
            authors = author_match.group(1)
    else:
        # 去掉编号前缀
        body = re.sub(r"^\[?\d+\]?[\.\):]?\s*", "", content)
        # GB/T 7714: "作者列表. 题名[J/EB/OL/C]. 出处, 年." — 题名在 [X] 标记前,
        # 且位于作者列表之后（作者列表以 ", 等." 或英文大写姓氏结尾）
        gb_marker = re.search(r"\s*\[(?:EB/OL|J|C|D|N|M|R|OL|DB)\]\s*", body)
        if gb_marker:
            head = body[: gb_marker.start()].strip()
            # 取最后一个 ". " 之后作为题名（作者列表在前）
            segs = head.rsplit(". ", 1)
            if len(segs) == 2 and segs[1].strip():
                authors_part, title = segs[0].strip(), segs[1].strip()
                # 作者列表近似: 大写字幕模式/中文顿号分隔
                if re.match(r"^[A-Z0-9 ,.]+(等\.)?$", authors_part) or CJK_AUTHORS_RE.match(authors_part):
                    authors = authors_part
            else:
                title = head
        else:
            # 去掉结尾年份 "(2017)"
            body = re.sub(r"\s*\(\d{4}\)\s*$", "", body)
            # 尝试 "Title. Journal" 模式 - 取第一个句号前的内容
            if ". " in body:
                title = body.split(". ")[0]
            else:
                title = body[:120]
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()

    return CitationRecord(
        ref_number=num,
        title=title.strip(" ,."),
        authors=authors.strip(" ,."),
        year=year,
        raw=content,
    )


CJK_AUTHORS_RE = re.compile(r"^[\u4e00-\u9fff\u00b7 ,、]+$")


def check_inline_citation_coverage(draft: str, ref_count: int) -> dict:
    """检查文中引用标记 [n] 与参考文献条目的对应关系"""
    inline_nums = set()
    for m in re.finditer(r"\[(\d+)\]", draft):
        inline_nums.add(int(m.group(1)))

    if not inline_nums:
        return {
            "inline_citations": 0,
            "ref_entries": ref_count,
            "unreferenced_citations": [],
            "uncited_refs": [],
            "coverage_ok": False,
        }

    unreferenced = sorted(n for n in inline_nums if n > ref_count)
    ref_nums = set(range(1, ref_count + 1))
    uncited = sorted(ref_nums - inline_nums)

    return {
        "inline_citations": len(inline_nums),
        "ref_entries": ref_count,
        "unreferenced_citations": unreferenced,
        "uncited_refs": uncited,
        "coverage_ok": len(unreferenced) == 0 and len(uncited) == 0,
    }


def verify_draft_citations(draft: str, verified_refs: list[dict] = None) -> dict:
    """完整核查流程：提取引用 → 逐条验证 → 检查覆盖率 → 输出报告

    verified_refs: 可选的可信参考文献清单（写作前的预验证结果）。
    提供时先按标题相似度匹配清单（命中即 VERIFIED, 零 API 调用——
    清单内论文已通过预验证），未命中的才走 CrossRef/OpenAlex/arXiv 外部验证。
    """
    records, has_ref_section = extract_references_from_draft(draft, return_has_section=True)

    if not records:
        # 参考文献章节缺失是必须修复的结构性问题 (修订循环据此触发)
        ref_section_missing = not has_ref_section
        return {
            "total_refs": 0,
            "verified": 0,
            "ambiguous": 0,
            "not_found": 0,
            "errors": 0,
            "ref_section_missing": ref_section_missing,
            "coverage": {"coverage_ok": False, "inline_citations": 0, "ref_entries": 0},
            "results": [],
            "hallucinated_refs": [],
            "report_md": (
                "## 引文核查报告\n\n**未检测到可解析的参考文献条目。**\n\n"
                "请确认初稿包含数字编号引用（[1], [2], ...）和文末参考文献列表。"
                if has_ref_section
                else "## 引文核查报告\n\n**初稿缺少「参考文献」章节！**\n\n"
                      "文中使用了数字编号引用（[1], [2], ...），但未在文末输出参考文献列表。"
                      "修订时必须补全参考文献章节（格式: [n] 标题. 作者. 年份）。"
            ),
        }

    # 预验证清单索引（按归一化标题 + 按编号）
    ref_index = {}
    ref_by_number = {}
    if verified_refs:
        for e in verified_refs:
            t = e.get("title", "")
            if t:
                ref_index[re.sub(r"[^a-z0-9]", "", t.lower())] = e
            n = e.get("ref_number")
            if n is not None:
                ref_by_number[int(n)] = e

    results = []
    for rec in records:
        vr = verify_single_citation(rec)

        # 与可信清单匹配: 命中即 VERIFIED（清单论文已预验证, 无需外部 API）
        if verified_refs and vr.status != "VERIFIED":
            # 1) 编号直连: 参考文献章节由系统按清单编号生成, 编号即证据
            entry = ref_by_number.get(rec.ref_number)
            if entry is None:
                # 2) 归一化标题精确匹配
                norm = re.sub(r"[^a-z0-9]", "", (rec.title or "").lower())
                entry = ref_index.get(norm)
            if entry is None:
                # 3) 模糊匹配: 归一化标题互相包含
                for key, cand in ref_index.items():
                    if norm and key and (key in norm or norm in key) and min(len(key), len(norm)) >= 15:
                        entry = cand
                        break
            if entry is not None:
                vr.status = "VERIFIED"
                vr.matched_title = entry.get("title", "")
                vr.doi = entry.get("doi", "")
                vr.similarity = 1.0
                vr.detail = "Matched verified reference list"

        results.append(vr)

    coverage = check_inline_citation_coverage(draft, len(records))

    verified = sum(1 for r in results if r.status == "VERIFIED")
    ambiguous = sum(1 for r in results if r.status == "AMBIGUOUS")
    not_found = sum(1 for r in results if r.status == "NOT_FOUND")
    errors = sum(1 for r in results if r.status == "ERROR")
    retracted = sum(1 for r in results if r.status == "RETRACTED")

    hallucinated = [
        r.record.title
        for r in results
        if r.status in ("NOT_FOUND",)
    ]
    retracted_list = [
        r.record.title
        for r in results
        if r.status == "RETRACTED"
    ]
    ambiguous_list = [
        r.record.title
        for r in results
        if r.status == "AMBIGUOUS"
    ]

    lines = [
        "# 引文核查报告",
        "",
        f"**核查时间**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**核查源**: CrossRef / OpenAlex / arXiv",
        f"**引用总数**: {len(records)} | "
        f"**已验证**: {verified} | "
        f"**存疑**: {ambiguous} | "
        f"**未找到**: {not_found} | "
        f"**已撤稿**: {retracted} | "
        f"**解析错误**: {errors}",
        "",
        f"**覆盖率检查**: {'通过' if coverage['coverage_ok'] else '未通过'}",
        f"- 文中引用标记: {coverage['inline_citations']} 处",
        f"- 参考文献条目: {coverage['ref_entries']} 条",
        f"- 文中引用但参考文献缺失: {coverage['unreferenced_citations']}",
        f"- 参考文献未被文中引用: {coverage['uncited_refs']}",
        "",
        "## 逐条核查结果",
        "",
        "| # | 状态 | 相似度 | 论文标题 | 匹配到的真实文献 | DOI/来源 |",
        "|---|------|--------|---------|----------------|---------|",
    ]

    for r in results:
        status_icon = {"VERIFIED": "✅", "AMBIGUOUS": "⚠️", "NOT_FOUND": "❌", "RETRACTED": "🚫", "ERROR": "🔧"}.get(r.status, "?")
        lines.append(
            f"| {r.record.ref_number} | {status_icon} {r.status} | {r.similarity:.2f} | "
            f"{r.record.title[:60]} | {r.matched_title[:60]} | {r.doi or r.venue or '—'} |"
        )

    if retracted_list:
        lines.append("")
        lines.append("## 🚫 已撤稿论文（必须删除，引用撤稿论文会严重影响可信度）")
        for t in retracted_list:
            lines.append(f"- {t}")

    if hallucinated:
        lines.append("")
        lines.append("## ⚠️ 疑似虚构引用（建议删除或替换）")
        for h in hallucinated:
            lines.append(f"- {h}")

    if ambiguous_list:
        lines.append("")
        lines.append("## ⚠️ 存疑引用（标题相似度较低，需人工确认）")
        for h in ambiguous_list:
            lines.append(f"- {h}")

    return {
        "total_refs": len(records),
        "verified": verified,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "retracted": retracted,
        "errors": errors,
        "coverage": coverage,
        "results": [r.to_dict() for r in results],
        "hallucinated_refs": hallucinated,
        "retracted_refs": retracted_list,
        "report_md": "\n".join(lines),
    }


if __name__ == "__main__":
    sample = """
# 测试论文

大语言模型 [1] 近年来取得显著进展 [2]。

## 参考文献

[1] Attention Is All You Need (2017)
[2] BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (2019)
"""
    report = verify_draft_citations(sample)
    print(report["report_md"])
