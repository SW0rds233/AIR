from __future__ import annotations

"""人工 PDF 导入: 用户预先下载的中文文献全文

用法:
1. 将 PDF 放入 data/manual_pdfs/ (文件名即论文标题, 可用 "_年份" 后缀)
   例: data/manual_pdfs/射频指纹识别技术研究进展_2017.pdf
2. (可选) data/manual_pdfs/meta.json 补充作者/年份/期刊:
   {"射频指纹识别技术研究进展_2017.pdf": {"authors": "张三, 李四", "year": "2017", "venue": "电子学报"}}

人工导入的文献信任度最高 (人已确认), 跳过外部 API 验证。
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

MANUAL_PDF_DIR = DATA_DIR / "manual_pdfs"
META_FILE = MANUAL_PDF_DIR / "meta.json"


def ensure_manual_dir() -> Path:
    MANUAL_PDF_DIR.mkdir(parents=True, exist_ok=True)
    return MANUAL_PDF_DIR


def _load_sidecar_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"manual meta.json 解析失败: {e}")
    return {}


def _parse_filename(name: str) -> tuple[str, str]:
    """文件名 → (标题, 年份): "射频指纹识别_2017.pdf" → ("射频指纹识别", "2017")

    兼容两种后缀:
    - "_年份" (4位数字): 年份
    - "_作者名" (中文字符, 非年份): 作者名, 从标题中剥离 (作者信息由 meta.json 提供,
      但标题不应残留 "_陈翔" 之类的作者后缀, 否则参考文献题名错误)
    """
    stem = name.rsplit(".", 1)[0].strip()
    m = re.search(r"[_\-]\s*((?:19|20)\d{2})\s*$", stem)
    if m:
        return stem[: m.start()].strip(), m.group(1)
    # 剥离末尾 "_作者名" 后缀 (中文字符, 常见于用户命名惯例 "标题_作者.pdf")
    m2 = re.search(r"[_\-]\s*([\u4e00-\u9fff]{2,4})\s*$", stem)
    if m2:
        return stem[: m2.start()].strip(), ""
    return stem, ""


def scan_manual_pdfs() -> list[dict]:
    """扫描 data/manual_pdfs/ 下的 PDF, 返回待导入清单

    Returns: [{path, title, year, authors, venue, city, type}, ...]
    meta.json 支持的字段:
    - authors: 作者 (逗号分隔)
    - year: 年份
    - venue: 期刊名 或 学位论文的授予单位 (学校名)
    - city: 学位论文的城市 (可选)
    - type: 文献类型, "D"=学位论文 (缺省时由出处关键词自动判定)
    """
    ensure_manual_dir()
    meta = _load_sidecar_meta()
    results = []
    for f in sorted(MANUAL_PDF_DIR.glob("*.pdf")):
        title, year = _parse_filename(f.name)
        if not title:
            continue
        info = meta.get(f.name, {})
        results.append({
            "path": str(f),
            "title": title,
            "year": str(info.get("year", year) or ""),
            "authors": str(info.get("authors", "") or ""),
            "venue": str(info.get("venue", "") or ""),
            "city": str(info.get("city", "") or ""),
            "type": str(info.get("type", "") or ""),
            "volume": str(info.get("volume", "") or ""),
            "issue": str(info.get("issue", "") or ""),
            "pages": str(info.get("pages", "") or ""),
            "doi": str(info.get("doi", "") or ""),
        })
    return results


def _extract_year_from_pdf(pdf_path: str) -> str:
    """从 PDF 首页文本提取年份 (文件名无年份时的兜底)

    只接受合理出版年份 (1950 ≤ year ≤ 当前年+1)。PDF 文本里可能出现
    "2095" 这类乱码/编号 (如 "20" 后跟页码 "95"), 直接提取会污染年份字段,
    进而让 LaTeX 编译失败 (未来年份) 或审稿人判"数据错误"。
    """
    import datetime

    try:
        import fitz  # pymupdf

        with fitz.open(pdf_path) as doc:
            text = doc[0].get_text()[:2000]
    except Exception:
        return ""

    current_year = datetime.date.today().year
    # 逐个候选年份, 取第一个落在合理区间的 (避免 [19|20]\d{2} 抓到 2095 这类)
    for m in re.finditer(r"(?:19|20)\d{2}", text):
        y = int(m.group(0))
        if 1950 <= y <= current_year + 1:
            return str(y)
    return ""


def _enrich_from_title_search(title: str) -> dict:
    """人工文献缺年份/作者/期刊/DOI 时, 用标题检索 CrossRef/OpenAlex 回填元数据

    返回 {authors, year, venue, doi} 中命中的字段 (未命中的为空字符串)。
    中文文献 OpenAlex 覆盖较全, 优先 OpenAlex 标题检索; 失败回退 CrossRef。
    """
    if not title:
        return {}
    try:
        from src.tools.citation_verifier import verify_openalex, verify_crossref, CitationRecord

        rec = CitationRecord(ref_number=0, title=title)
        for verifier in (verify_openalex, verify_crossref):
            try:
                data = verifier(rec)
            except Exception:
                continue
            if not data or data.get("status") not in ("VERIFIED", "AMBIGUOUS"):
                continue
            out = {}
            if data.get("doi"):
                out["doi"] = data["doi"]
            if data.get("venue"):
                out["venue"] = data["venue"]
            if data.get("year"):
                out["year"] = str(data["year"])
            # 检索结果通常不含作者, 通过 DOI→CrossRef 补作者
            if data.get("doi") and not out.get("authors"):
                try:
                    from src.tools.venue_resolver import _resolve_via_crossref
                    from src.utils.http_client import get_with_retry
                    from urllib.parse import quote

                    resp = get_with_retry(
                        f"https://api.crossref.org/works/{quote(data['doi'], safe='')}",
                        headers={"User-Agent": "AIR-AIResearch/0.1 (mailto:contact@example.com)"},
                        read_timeout=15, raise_on_status=False,
                    )
                    if resp.status_code == 200:
                        msg = resp.json().get("message", {})
                        auths = msg.get("author", []) or []
                        names = []
                        for a in auths[:5]:
                            fam = a.get("family", "") or ""
                            giv = a.get("given", "") or ""
                            if fam and giv:
                                names.append(f"{giv} {fam}")
                            elif fam:
                                names.append(fam)
                        if names:
                            out["authors"] = ", ".join(names)
                except Exception:
                    pass
            return out
    except Exception as e:
        logger.debug(f"人工文献标题检索回填失败 {title[:40]}: {e}")
    return {}


def enrich_manual_paper(item: dict) -> dict:
    """对缺元数据的人工文献, 依次尝试: meta.json → PDF 首页年份 → 标题检索回填

    返回补齐后的 item (title/authors/year/venue/doi)。
    """
    # 年份兜底: PDF 首页
    if not item.get("year"):
        item["year"] = _extract_year_from_pdf(item["path"])

    # 仍缺关键字段 (年份/作者/期刊/DOI) 时, 标题检索回填
    if not (item.get("year") and item.get("authors") and item.get("venue") and item.get("doi")):
        fill = _enrich_from_title_search(item.get("title", ""))
        for k, v in fill.items():
            if v and not item.get(k):
                item[k] = v
        if fill:
            logger.info(f"人工文献元数据回填: {item.get('title','')[:40]} -> { {k:v for k,v in fill.items() if v} }")
    return item


def ingest_manual_pdf(item: dict, chunk_size: int = 500, overlap: int = 100) -> dict:
    """人工 PDF → 解析全文 → 分块 → 向量入库

    Returns: {"title": str, "num_chunks": N} 或 {"title": str, "error": str}
    """
    from src.rag.paper_parser import extract_text_from_pdf
    from src.rag.chunker import chunk_paper_fulltext
    from src.rag.vector_store import add_fulltext_chunks, embedding_available

    pdf_path = item["path"]
    title = item["title"]
    if not embedding_available():
        return {"title": title, "error": "embedding 不可用"}

    try:
        from src.config import PDF_FULLTEXT_MAX_CHARS

        full_text = extract_text_from_pdf(pdf_path, max_chars=PDF_FULLTEXT_MAX_CHARS)
        if len(full_text.strip()) < 200:
            return {"title": title, "error": "文本过短, 可能为扫描版 PDF"}

        chunks = chunk_paper_fulltext(title=title, full_text=full_text,
                                      chunk_size=chunk_size, overlap=overlap)
        meta = {
            "authors": item.get("authors", ""),
            "year": item.get("year", ""),
            "source": item.get("venue", "") or "人工导入",
            "url": "",
            "bibtex": "",
        }
        n = add_fulltext_chunks(chunks, paper_meta=meta)
        return {"title": title, "num_chunks": n}
    except Exception as e:
        logger.warning(f"人工 PDF 摄入失败 {title}: {e}")
        return {"title": title, "error": str(e)}


def ingest_all_manual_pdfs() -> tuple[list[dict], list[dict]]:
    """扫描并摄入所有人工 PDF

    Returns:
        (papers, ingested): papers = 人工文献元数据 (进入引用清单),
                            ingested = 摄入结果 (报告用)
    """
    items = scan_manual_pdfs()
    if not items:
        return [], []

    print(f"  [manual_pdfs] 发现 {len(items)} 篇人工导入 PDF")
    papers = []
    ingested = []
    for item in items:
        # 元数据补齐: meta.json → PDF 首页年份 → 标题检索回填 (年份/作者/期刊/DOI)
        item = enrich_manual_paper(item)
        result = ingest_manual_pdf(item)
        if "num_chunks" in result:
            ingested.append(result)
            print(f"  [manual_pdfs] 已入库: {item['title'][:50]} ({result['num_chunks']} chunks)")
        else:
            print(f"  [manual_pdfs] 跳过: {item['title'][:40]} ({result.get('error', '')})")

        papers.append({
            "title": item["title"],
            "authors": item["authors"],
            "year": item["year"],
            "source": item["venue"] or "人工导入",
            "venue": item["venue"],
            "city": item.get("city", ""),
            "ref_type": item.get("type", ""),
            "volume": item.get("volume", ""),
            "issue": item.get("issue", ""),
            "pages": item.get("pages", ""),
            "api_source": "人工导入",
            "abstract": "",
            "citations": 0,
            "url": "",
            "doi": item.get("doi", "") or "",
            "bibtex": "",
            "published": True,  # 人工提供, 视为已发表
        })
    return papers, ingested
