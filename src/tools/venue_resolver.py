from __future__ import annotations

"""出版出处解析器: 为参考文献解析真实期刊/会议出处（而非只标 arXiv）

用户诉求: 参考文献应标明出版出处（期刊/会议），而不是"arXiv"。
arXiv 论文往往已正式发表, 但 arXiv 记录本身不含出处, 需要解析:

优先级:
1. 论文记录中已有的真实出处 (source/venue 字段, 来自 S2/OpenAlex 检索结果)
2. DOI → CrossRef works/{doi} → container-title (期刊/会议名) + 卷/期/页码
3. arXiv ID → arXiv API journal_ref 元素 (已发表论文的期刊信息)
4. 全部失败 → 保持 arXiv 预印本标注

工程细节 (借鉴 AI-Research-SKILLs citation-workflow / academic-search):
- 结果按 DOI/arXiv ID 磁盘缓存 (data/venue_cache.json), 跨会话复用
- 单次请求带 polite User-Agent + 指数退避重试
- 任一源失败不中断, 优雅降级
"""

import json
import logging
import re
import time
from pathlib import Path

import httpx

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"

CONTACT_EMAIL = __import__("os").getenv("CROSSREF_EMAIL", "")

# 非真实出处标记（检索 API 名称不是期刊名）
PLACEHOLDER_SOURCES = {"", "arXiv", "OpenAlex", "Semantic Scholar", "来源未标注"}

_CACHE_FILE = DATA_DIR / "venue_cache.json"
_cache: dict = {}
_cache_loaded = False


def _load_cache() -> dict:
    global _cache, _cache_loaded
    if not _cache_loaded:
        _cache_loaded = True
        try:
            if _CACHE_FILE.exists():
                _cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def looks_like_real_venue(source: str) -> bool:
    """判断字符串是否为真实出版出处（非检索 API 名称/空值）"""
    s = (source or "").strip()
    if s in PLACEHOLDER_SOURCES:
        return False
    if s.lower().startswith(("http://", "https://")):
        return False
    return len(s) >= 3


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+\.?\d{4,5}(?:v\d+)?)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url or "")
    return m.group(1) if m else ""


def _resolve_via_crossref(doi: str) -> dict:
    """DOI → CrossRef: 期刊/会议名 + 卷/期/页码"""
    from urllib.parse import quote

    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(
                f"{CROSSREF_URL}/{quote(doi, safe='')}",
                headers={"User-Agent": f"AIR-AIResearch/0.1 (mailto:{CONTACT_EMAIL or 'contact@example.com'})"},
                timeout=20,
            )
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return {}
    if resp is None or resp.status_code != 200:
        return {}

    try:
        m = resp.json().get("message", {})
        venue = (m.get("container-title") or [""])[0] or ""
        return {
            "venue": venue,
            "volume": str(m.get("volume", "") or ""),
            "issue": str(m.get("issue", "") or ""),
            "pages": str(m.get("page", "") or ""),
        }
    except Exception:
        return {}


def _resolve_via_arxiv(arxiv_id: str) -> dict:
    """arXiv ID → journal_ref (已发表论文的期刊信息) + arXiv DOI"""
    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(ARXIV_API_URL, params={"id_list": arxiv_id}, timeout=20)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return {}
    if resp is None or resp.status_code != 200:
        return {}

    try:
        import xml.etree.ElementTree as ET

        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(resp.text)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}
        jr = entry.find("arxiv:journal_ref", ns)
        venue = jr.text.strip() if jr is not None and jr.text else ""
        doi_el = entry.find("arxiv:doi", ns)
        doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""
        info = {}
        if venue:
            info["venue"] = venue
        if doi:
            info["doi"] = doi
        return info
    except Exception:
        return {}


def resolve_venue(paper: dict) -> dict:
    """解析论文出版出处

    Returns: {"venue": str, "volume": str, "issue": str, "pages": str}
    """
    # 1) 已有真实出处
    venue = (paper.get("venue") or paper.get("source") or "").strip()
    if looks_like_real_venue(venue):
        return {"venue": venue}

    doi = (paper.get("doi") or "").strip()
    arxiv_id = _arxiv_id_from_url(paper.get("url", "") or "")

    cache = _load_cache()

    # 2) DOI 缓存 / CrossRef
    if doi:
        key = f"doi:{doi.lower()}"
        if key in cache:
            return dict(cache[key])
        info = _resolve_via_crossref(doi)
        if info.get("venue"):
            cache[key] = info
            _save_cache()
            return info
        # DOI 解析失败也缓存空结果, 避免反复请求
        cache[key] = {}
        _save_cache()

    # 3) arXiv journal_ref
    if arxiv_id:
        key = f"arxiv:{arxiv_id}"
        if key in cache:
            return dict(cache[key])
        info = _resolve_via_arxiv(arxiv_id)
        # 若 arXiv 提供了 DOI 且还没有 DOI, 补一次 CrossRef
        if info.get("doi") and not doi:
            cross = _resolve_via_crossref(info["doi"])
            if cross.get("venue"):
                info.update(cross)
        cache[key] = info
        _save_cache()
        return info

    return {}


def resolve_venue_batch(papers: list[dict], delay: float = 0.5) -> None:
    """批量解析并原地更新 papers（带限流, 单篇失败不中断）"""
    for p in papers:
        try:
            info = resolve_venue(p)
            if info.get("venue"):
                p["venue"] = info["venue"]
            if info.get("volume"):
                p["volume"] = info["volume"]
            if info.get("issue"):
                p["issue"] = info["issue"]
            if info.get("pages"):
                p["pages"] = info["pages"]
        except Exception as e:
            logger.debug(f"venue resolve failed for {p.get('title', '')[:40]}: {e}")
        time.sleep(delay)
