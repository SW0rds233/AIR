from __future__ import annotations

"""出版出处解析器: 为参考文献解析真实期刊/会议出处

优先级:
1. 论文记录中已有的真实出处 (source/venue 字段)
2. DOI → CrossRef works/{doi} → container-title + 卷/期/页码
3. arXiv ID → arXiv API journal_ref
4. 全部失败 → 保持 arXiv 预印本标注
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from src.config import DATA_DIR
from src.utils.http_client import get_with_retry

logger = logging.getLogger(__name__)

CROSSREF_URL = "https://api.crossref.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"

CONTACT_EMAIL = __import__("os").getenv("CROSSREF_EMAIL", "")

# 非真实出处标记（检索 API 名称不是期刊名）
PLACEHOLDER_SOURCES = {"", "arXiv", "OpenAlex", "Semantic Scholar", "来源未标注"}

_CACHE_FILE = DATA_DIR / "venue_cache.json"
_cache: dict = {}
_cache_loaded = False
# 空结果缓存有效期 (秒): 网络失败导致的空结果不永久缓存, 24 小时后重试
EMPTY_CACHE_TTL = 24 * 3600


def _load_cache() -> dict:
    global _cache, _cache_loaded
    if not _cache_loaded:
        _cache_loaded = True
        try:
            if _CACHE_FILE.exists():
                _cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                # 清理过期空结果 (网络失败误缓存的)
                now = time.time()
                expired = [k for k, v in _cache.items()
                           if not v.get("venue") and not v.get("doi")
                           and now - v.get("_ts", 0) > EMPTY_CACHE_TTL]
                for k in expired:
                    del _cache[k]
                if expired:
                    _save_cache()
        except Exception:
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# 会议缩写+年份的垃圾出处 (arXiv journal_ref 常见 "INCC2024"/"ICCC2024" 等,
# 无真实会议名, 审稿人会判定出处存疑)
_JUNK_VENUE_RE = re.compile(r"^[A-Z]{2,10}\s?\d{4}$")


def looks_like_real_venue(source: str) -> bool:
    """判断字符串是否为真实出版出处（非检索 API 名称/空值/垃圾缩写）"""
    s = (source or "").strip()
    if s in PLACEHOLDER_SOURCES:
        return False
    if s.lower().startswith(("http://", "https://")):
        return False
    if _JUNK_VENUE_RE.match(s):
        return False
    return len(s) >= 3


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+\.?\d{4,5}(?:v\d+)?)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url or "")
    return m.group(1) if m else ""


def _resolve_via_crossref(doi: str) -> Optional[dict]:
    """DOI → CrossRef: 期刊/会议名 + 卷/期/页码

    Returns:
        None = 网络失败 (未确认, 不应缓存)
        {}   = 确认无记录 (可缓存)
    """
    from urllib.parse import quote

    try:
        resp = get_with_retry(
            f"{CROSSREF_URL}/{quote(doi, safe='')}",
            headers={"User-Agent": f"AIR-AIResearch/0.1 (mailto:{CONTACT_EMAIL or 'contact@example.com'})"},
            read_timeout=20,
            raise_on_status=False,
        )
        if resp.status_code == 404:
            return {}  # 确认 DOI 不存在
        if resp.status_code != 200:
            return None  # 限流/服务器错误: 未确认
    except Exception:
        return None  # 网络失败: 未确认

    try:
        m = resp.json().get("message", {})
        venue = (m.get("container-title") or [""])[0] or ""
        # CrossRef 的 issued 年份是"正式出版年"的权威来源, 可修正检索源的未来年份污染
        # (如 CSI-RFF 检索源误标 2026, 实际 TIFS.2024)
        year = ""
        issued = m.get("issued", {}).get("date-parts") or []
        # 注意: CrossRef 某些条目 issued.date-parts 为 [[null]], issued[0][0] 是 None,
        # str(None) 会得到 "None" 字符串, 下游 int("None") 崩溃
        if issued and issued[0] and issued[0][0] is not None:
            year = str(issued[0][0])
        return {
            "venue": venue,
            "volume": str(m.get("volume", "") or ""),
            "issue": str(m.get("issue", "") or ""),
            "pages": str(m.get("page", "") or ""),
            "year": year,
        }
    except Exception:
        return {}  # 响应解析失败: 确认无可用信息


def _resolve_via_arxiv(arxiv_id: str) -> Optional[dict]:
    """arXiv ID → journal_ref (已发表论文的期刊信息) + arXiv DOI

    Returns:
        None = 网络失败 (未确认, 不应缓存)
        {}   = 确认无 journal_ref/DOI (可缓存)
    """
    results = _resolve_arxiv_batch([arxiv_id])
    return results.get(arxiv_id)


def _resolve_arxiv_batch(arxiv_ids: list[str]) -> dict[str, Optional[dict]]:
    """批量解析 arXiv 论文出处 (id_list 支持逗号分隔, 单次请求)

    60 次单篇请求 → 3-4 次批量请求, 网络波动时成功率大幅提升。

    Returns: {arxiv_id: info} ; info 中 None=网络失败, {}=确认无记录
    """
    import xml.etree.ElementTree as ET

    result: dict[str, Optional[dict]] = {}
    if not arxiv_ids:
        return result
    # 每次最多 20 个 ID (arXiv API 建议)
    for i in range(0, len(arxiv_ids), 20):
        batch = arxiv_ids[i : i + 20]
        try:
            resp = get_with_retry(
                ARXIV_API_URL,
                params={"id_list": ",".join(batch)},
                read_timeout=30,
                raise_on_status=False,
            )
            if resp.status_code != 200:
                for aid in batch:
                    result[aid] = None  # 网络失败: 未确认
                continue
            ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
            root = ET.fromstring(resp.text)
            entries = {e.find("atom:id", ns).text: e for e in root.findall("atom:entry", ns)
                       if e.find("atom:id", ns) is not None}
            for aid in batch:
                entry = entries.get(aid) or entries.get(f"http://arxiv.org/abs/{aid}")
                if entry is None:
                    result[aid] = {}  # 无该记录
                    continue
                jr = entry.find("arxiv:journal_ref", ns)
                venue = jr.text.strip() if jr is not None and jr.text else ""
                # 过滤垃圾 journal_ref (如 "INCC2024" 会议缩写+年份, 无真实会议名)
                if venue and _JUNK_VENUE_RE.match(venue):
                    venue = ""
                doi_el = entry.find("arxiv:doi", ns)
                doi = doi_el.text.strip() if doi_el is not None and doi_el.text else ""
                info = {}
                if venue:
                    info["venue"] = venue
                if doi:
                    info["doi"] = doi
                result[aid] = info
        except Exception:
            for aid in batch:
                result[aid] = None  # 网络失败: 未确认
    return result


_REFERENCE_WORK_TERMS = (
    "encyclopedia", "encyclopaedia", "handbook", "dictionary", "compendium", "reference work",
)


def _is_reference_work_venue(venue: str) -> bool:
    """判断出处是否为工具书/参考书 (百科全书/手册/词典等), 非正式研究论文"""
    v = (venue or "").lower()
    return any(t in v for t in _REFERENCE_WORK_TERMS)


def _resolve_published_via_title(title: str) -> Optional[dict]:
    """标题检索 CrossRef, 找到 arXiv 预印本对应的**正式发表版本**。

    场景: 论文先发 arXiv、后正式发表 (如 FLAME → IEEE Internet of Things Journal,
    DOI 10.1109/jiot.2026.3710099)。arXiv 记录只含 arXiv DOI (DataCite 注册,
    CrossRef 查 404) 且无 journal_ref, 单靠 arXiv API 无法解析出正式出处。
    此时用标题做 CrossRef bibliographic 检索, 命中即回填真实 DOI/刊名/卷期页码。

    Returns: {"doi","venue","volume","issue","pages","year"} 或 None (未找到/未发表)
    """
    if not title:
        return None
    from src.tools.citation_verifier import verify_crossref, CitationRecord

    cache = _load_cache()
    key = f"title:{re.sub(r'[^a-z0-9]', '', title.lower())[:120]}"
    if key in cache:
        info = dict(cache[key])
        if not info.get("doi"):
            return None
        # 历史缓存可能由旧规则写入 (漏掉工具书/预印本出处), 命中时重新校验
        if _is_reference_work_venue(info.get("venue", "")):
            del cache[key]
            _save_cache()
            return None
        return info

    try:
        rec = CitationRecord(ref_number=0, title=title)
        data = verify_crossref(rec)
    except Exception:
        return None
    if not data or data.get("status") != "VERIFIED":
        cache[key] = {"_ts": time.time()}
        _save_cache()
        return None
    doi = (data.get("doi") or "").strip()
    if not doi or "arxiv" in doi.lower():
        cache[key] = {"_ts": time.time()}
        _save_cache()
        return None

    # 拒绝"工具书/参考书"类出处 (百科全书/手册/词典等): 标题检索可能误命中
    # 百科词条 (如 "Deep Open Set Identification" 被匹配到 Encyclopedia of Biometrics),
    # 这类不是正式研究论文的期刊/会议出处, 不能作为"已发表"证据
    if _is_reference_work_venue(data.get("venue", "")):
        cache[key] = {"_ts": time.time()}
        _save_cache()
        return None

    # 拿到真实 DOI 后再查 CrossRef works/{doi} 补全卷期页码
    info = _resolve_via_crossref(doi)
    if info is None:
        return None
    out = {"doi": doi}
    if data.get("venue"):
        out["venue"] = data["venue"]
    if info:
        out.update(info)
    cache[key] = out
    _save_cache()
    return out


def upgrade_arxiv_to_published(paper: dict) -> bool:
    """若 paper 是 arXiv 预印本但已正式发表, 标题检索 CrossRef 回填正式元数据。

    仅在 paper 无真实 DOI (无 DOI 或仅 arXiv DOI) 时尝试; 命中后回填
    doi/venue/volume/issue/pages/year 并置 published=True。
    Returns: True 若已升级为已发表。
    """
    doi = (paper.get("doi") or "").strip()
    if doi and "arxiv" not in doi.lower():
        return True  # 已有真实 DOI, 视为已发表
    title = (paper.get("title") or "").strip()
    if not title:
        return False
    info = _resolve_published_via_title(title)
    if not info:
        return False
    paper["doi"] = info.get("doi", "")
    if info.get("venue"):
        paper["venue"] = info["venue"]
    if info.get("volume"):
        paper["volume"] = info["volume"]
    if info.get("issue"):
        paper["issue"] = info["issue"]
    if info.get("pages"):
        paper["pages"] = info["pages"]
    if info.get("year"):
        paper["year"] = info["year"]
    paper["published"] = True
    logger.info(f"arXiv 预印本已解析为正式发表: {title[:50]} → {info.get('venue','')[:30]}")
    return True


def enrich_paper_from_doi(paper: dict) -> bool:
    """用 CrossRef 补全/修正论文的出处、卷期页码、年份。

    对有真实 DOI (非 arXiv) 的论文, 查 CrossRef works/{doi}, 回填缺失的
    venue/volume/issue/pages, 并用 CrossRef issued 年份修正检索源年份
    (审稿人常指出的"年份错误/卷期页缺失"即源于此)。
    Returns: True 若有字段被补全/修正
    """
    doi = (paper.get("doi") or "").strip()
    if not doi or "arxiv" in doi.lower():
        return False
    info = _resolve_via_crossref(doi)
    if not info:
        return False
    changed = False
    if info.get("venue") and not looks_like_real_venue(paper.get("venue", "")):
        paper["venue"] = info["venue"]
        paper["source"] = info["venue"]
        changed = True
    if info.get("volume") and not (paper.get("volume") or "").strip():
        paper["volume"] = info["volume"]
        changed = True
    if info.get("issue") and not (paper.get("issue") or "").strip():
        paper["issue"] = info["issue"]
        changed = True
    if info.get("pages") and not (paper.get("pages") or "").strip():
        paper["pages"] = info["pages"]
        changed = True
    if info.get("year"):
        cy = str(info["year"])
        if cy.isdigit() and str(paper.get("year", "")) != cy:
            paper["year"] = cy
            changed = True
    return changed


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
        if info is None:
            pass  # 网络失败: 不缓存, 下次重试
        elif info.get("venue"):
            cache[key] = info
            _save_cache()
            return info
        else:
            # 确认无记录: 缓存空结果 (24h 后可重试)
            cache[key] = {"_ts": time.time()}
            _save_cache()

    # 3) arXiv journal_ref
    if arxiv_id:
        key = f"arxiv:{arxiv_id}"
        if key in cache:
            return dict(cache[key])
        info = _resolve_via_arxiv(arxiv_id)
        if info is None:
            return {}  # 网络失败: 不缓存, 下次重试
        # 若 arXiv 提供了 DOI 且还没有 DOI, 补一次 CrossRef
        if info.get("doi") and not doi:
            cross = _resolve_via_crossref(info["doi"])
            if cross and cross.get("venue"):
                info.update(cross)
        if not info:
            info["_ts"] = time.time()
        cache[key] = info
        _save_cache()
        return info

    return {}


def resolve_venues_fast(papers: list[dict], max_papers: int = 80) -> int:
    """快速批量出处解析: arXiv 论文合并为批量请求 (每 20 篇 1 次 API 调用)

    优先处理已解析过的 (缓存命中零 API), 未解析的 arXiv 论文走批量查询。
    另对有 DOI 的论文做 CrossRef 补全 (卷/期/页码), 满足 GB/T 7714 完整著录。
    网络波动环境下, 3-4 次批量请求替代 60+ 次单篇请求, 成功率大幅提升。

    Returns: 新解析出出处的论文数
    """
    resolved_count = 0
    cache = _load_cache()

    # 1) 缓存命中 + 已有出处 (零 API)
    to_query: list[tuple[str, str]] = []  # (arxiv_id, key)
    for p in papers:
        if p.get("published") or looks_like_real_venue(p.get("venue", "")):
            continue
        arxiv_id = _arxiv_id_from_url(p.get("url", "") or "")
        if arxiv_id:
            key = f"arxiv:{arxiv_id}"
            if key in cache and cache[key].get("venue"):
                p["venue"] = cache[key]["venue"]
                cached_doi = cache[key].get("doi", "")
                if cached_doi and not (p.get("doi") or "").strip():
                    p["doi"] = cached_doi
                # 仅当有 DOI 才算正式发表 (journal_ref 无 DOI 仍是预印本)
                p["published"] = bool(cached_doi)
                resolved_count += 1
            elif key not in cache:
                to_query.append((arxiv_id, key))
        else:
            # 非 arXiv 论文: 单篇解析 (DOI→CrossRef)
            info = resolve_venue(p)
            if info.get("venue"):
                p["venue"] = info["venue"]
                p["published"] = True
                resolved_count += 1
        if len(to_query) >= max_papers:
            break

    # 2) arXiv 批量查询 (每 20 篇 1 次请求)
    if to_query:
        print(f"  [venue] 批量解析 {len(to_query)} 篇 arXiv 论文出处 ({len(to_query) // 20 + 1} 次请求)...")
        results = _resolve_arxiv_batch([aid for aid, _ in to_query])
        for aid, key in to_query:
            info = results.get(aid)
            if info is None:
                continue  # 网络失败: 不缓存, 下次重试
            if info.get("venue"):
                cache[key] = info
                for p in papers:
                    if _arxiv_id_from_url(p.get("url", "") or "") == aid:
                        p["venue"] = info["venue"]
                        # arXiv 提供的 DOI 回写 (此前只回写 venue, 丢失 DOI 导致
                        # 本已发表的论文被误判为预印本)
                        if info.get("doi") and not (p.get("doi") or "").strip():
                            p["doi"] = info["doi"]
                        # 关键: 仅当 arXiv 提供 DOI (证明已正式发表) 才标记 published。
                        # journal_ref 常为 "Accepted to XXX" 等未发表状态,
                        # 无 DOI 的 journal_ref 只能算预印本, 否则审稿人会判"预印本虚标"。
                        p["published"] = bool(info.get("doi"))
                        resolved_count += 1
                        break
            else:
                cache[key] = {"_ts": time.time()}
        _save_cache()

    # 2.5) 标题检索 CrossRef: 将"已正式发表但 arXiv 记录无 DOI/journal_ref"的预印本升级
    # (如 FLAME → IEEE Internet of Things Journal)。否则这类论文会被误判为预印本剔除。
    for p in papers:
        if p.get("published"):
            continue
        if not _arxiv_id_from_url(p.get("url", "") or ""):
            continue
        if upgrade_arxiv_to_published(p):
            resolved_count += 1

    # 3) CrossRef 补全卷/期/页码 (GB/T 7714 完整著录, 审稿人反复扣分项)
    # 已有 venue 但缺卷期页的论文也要补, 逐 DOI 查 CrossRef (带缓存)
    enriched = _enrich_volume_issue_pages(papers, cache)
    resolved_count += enriched
    return resolved_count


def _year_from_doi(doi: str) -> int:
    """从 DOI 内嵌年份提取出版年 (如 10.1109/TIFS.2024.3396375 → 2024)

    DOI 中的年份是期刊/会议命名惯例中的出版年, 是判断检索源年份污染的重要信号。
    常见位置: 期刊缩写后 (TIFS.2024)、DOI 中间 (10.1007/s00521-021-...)。
    """
    if not doi:
        return 0
    m = re.search(r"(?:[A-Za-z]+\.)(\d{4})(?:\.\d+)", doi)
    if m:
        y = int(m.group(1))
        if 1990 <= y <= 2100:
            return y
    return 0


def _enrich_volume_issue_pages(papers: list[dict], cache: dict) -> int:
    """对有 DOI 的论文, 逐条查 CrossRef 补全卷/期/页码, 并修正年份污染。

    年份修正: 检索源可能把论文标成错误年份 (如 CSI-RFF 检索源误标 2026,
    实际 TIFS.2024)。两个权威信号用于修正:
    1. DOI 内嵌年份 (期刊缩写.年份 惯例)
    2. CrossRef 的 issued 年份 (正式出版年)
    当检索源年份与二者均矛盾时, 以 CrossRef issued 为准。

    Returns: 补全/修正了元数据的论文数
    """
    import datetime

    enriched = 0
    current_year = datetime.date.today().year
    for p in papers:
        try:
            doi = (p.get("doi") or "").strip()
            if not doi:
                continue
            try:
                py = int(str(p.get("year") or "0")[:4])
            except (ValueError, TypeError):
                py = 0

            doi_year = _year_from_doi(doi)
            has_vol_pages = bool((p.get("volume") or "").strip() or (p.get("pages") or "").strip())
            # 年份可疑 = 未来年份 / 缺失 / 与 DOI 内嵌年份明显矛盾 (差 >= 2 年)
            year_suspect = (
                py <= 0
                or py > current_year
                or (doi_year and abs(py - doi_year) >= 2)
            )
            if has_vol_pages and not year_suspect:
                continue

            key = f"doi:{doi.lower()}"
            if key in cache:
                info = dict(cache[key])
                # 旧缓存 (加 year 字段前写入) 缺年份, 但年份可疑时需重新查询补年份
                if not info.get("year") and year_suspect:
                    fresh = _resolve_via_crossref(doi)
                    if fresh is not None:
                        info = {**info, **fresh}
                        cache[key] = info
            else:
                info = _resolve_via_crossref(doi)
                if info is None:
                    continue  # 网络失败: 不缓存, 下次重试
                if info:
                    cache[key] = info
                else:
                    cache[key] = {"_ts": time.time()}
            if info.get("volume"):
                p["volume"] = info["volume"]
            if info.get("issue"):
                p["issue"] = info["issue"]
            if info.get("pages"):
                p["pages"] = info["pages"]
            # 补全 venue (期刊/会议名): 检索/补录结果常有 DOI 但缺出处,
            # 否则 GB/T 7714 会退化成 "[EB/OL] 网络文献" (审稿人判预印本)
            if info.get("venue") and not looks_like_real_venue(p.get("venue", "")):
                p["venue"] = info["venue"]
                p["source"] = info["venue"]
            # 年份修正: 检索源年份可疑时, 优先 CrossRef issued, 其次 DOI 内嵌年份
            if year_suspect:
                fixed = doi_year
                cy = info.get("year")
                if cy:
                    try:
                        fixed = int(cy)
                    except (ValueError, TypeError):
                        fixed = doi_year
                if fixed:
                    p["year"] = str(fixed)
            if (p.get("volume") or p.get("pages") or year_suspect):
                enriched += 1
        except Exception as e:
            # 单篇异常不中断整批: 脏数据 (如 CrossRef year=null) / 网络波动
            # 不应让整个出处解析崩溃, 只影响当前这一篇
            logger.debug(f"卷期页补全失败 {p.get('title', '')[:40]}: {e.__class__.__name__}")
    _save_cache()
    return enriched
