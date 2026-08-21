from __future__ import annotations

"""PDF 下载工具：从 arXiv 和 OpenAlex OA 下载论文全文 PDF"""

import os
import re
import logging
from pathlib import Path
from typing import Optional

from src.utils.http_client import get_with_retry

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "pdfs"


def ensure_pdf_dir() -> Path:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    return PDF_DIR


def extract_arxiv_id(url: str) -> Optional[str]:
    """从 arXiv URL 提取 ID (支持 https://arxiv.org/abs/XXXX.XXXXX 和 abs/XXXX.XXXXXv2 及旧式 cs.CL/0011004)"""
    if not url:
        return None
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-]+\.?\d{4,5}(?:v\d+)?)", url)
    if m:
        return m.group(1)
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([a-z]+\.[A-Z]+/\d{4,7})", url)
    if m:
        return m.group(1)
    m = re.search(r"(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url)
    if m:
        return m.group(1)
    return None


def download_arxiv_pdf(url: str, save_dir: Optional[Path] = None) -> Optional[str]:
    """下载 arXiv 论文 PDF，返回本地文件路径（失败返回 None）"""
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        logger.debug(f"Cannot extract arXiv ID from URL: {url}")
        return None

    target_dir = save_dir or ensure_pdf_dir()
    safe_id = arxiv_id.replace("/", "_")
    pdf_path = target_dir / f"{safe_id}.pdf"

    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return str(pdf_path)

    pdf_url = f"https://export.arxiv.org/pdf/{arxiv_id}"
    headers = {"User-Agent": "AIR-AIResearch/0.1"}

    try:
        resp = get_with_retry(pdf_url, headers=headers, follow_redirects=True, read_timeout=45, max_retries=2)
    except Exception as e:
        logger.warning(f"Failed to download {pdf_url}: {e}")
        return None

    if not resp.content.startswith(b"%PDF"):
        logger.warning(f"Not a PDF response from {pdf_url}, size={len(resp.content)}")
        return None
    pdf_path.write_bytes(resp.content)
    logger.info(f"Downloaded {arxiv_id} -> {pdf_path} ({len(resp.content)} bytes)")
    return str(pdf_path)


# 已知拦截机器下载的出版商: 直接跳过, 不浪费重试/断路器配额
OA_HOST_BLACKLIST = (
    "ieeexplore.ieee.org",
    "www.sciencedirect.com",
    "linkinghub.elsevier.com",
    "link.springer.com",
    "onlinelibrary.wiley.com",
    "dl.acm.org",
)

# OA 下载浏览器 UA (部分 OA 出版商拒绝非浏览器 UA)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _is_downloadable_oa_url(url: str) -> bool:
    """判断 OA PDF URL 是否值得尝试"""
    if not url:
        return False
    if not url.lower().endswith(".pdf"):
        return False  # OpenAlex OA 字段可能返回图片/HTML 链接
    host = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url).hostname or ""
    if any(h in host for h in OA_HOST_BLACKLIST):
        return False
    return True


# OA 查询缓存 (DOI → OA PDF URL): 避免重复消耗 OpenAlex 额度
import json as _json

_OA_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "oa_cache.json"
_oa_cache: dict = {}
_oa_cache_loaded = False


def _load_oa_cache() -> dict:
    global _oa_cache, _oa_cache_loaded
    if not _oa_cache_loaded:
        _oa_cache_loaded = True
        try:
            if _OA_CACHE_FILE.exists():
                _oa_cache = _json.loads(_OA_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _oa_cache = {}
    return _oa_cache


def _save_oa_cache() -> None:
    try:
        _OA_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OA_CACHE_FILE.write_text(_json.dumps(_oa_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _resolve_pdf_from_doi(doi: str, save_dir: Path) -> Optional[str]:
    """通过 DOI 解析开放获取 PDF（OpenAlex best_oa_location）

    OA 查询结果按 DOI 缓存: 无 OA 链接的结果不重复消耗 OpenAlex 额度。
    """
    if not doi:
        return None
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    safe = doi_clean.replace("/", "_")
    pdf_path = save_dir / f"oa_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return str(pdf_path)

    cache = _load_oa_cache()
    cache_key = doi_clean.lower()
    cached_pdf_url = cache.get(cache_key, "")
    if cache_key in cache and not cached_pdf_url:
        return None  # 已确认无 OA 链接, 跳过 (不消耗 OpenAlex 额度)

    pdf_url = cached_pdf_url
    if not pdf_url:
        try:
            params: dict = {"select": "best_oa_location,open_access"}
            if os.getenv("OPENALEX_API_KEY", ""):
                params["api_key"] = os.getenv("OPENALEX_API_KEY", "")
            resp = get_with_retry(
                f"https://api.openalex.org/works/https://doi.org/{doi_clean}",
                params=params,
                headers={"User-Agent": "AIR-AIResearch/0.1 (mailto:contact@example.com)"},
                read_timeout=30,
                raise_on_status=False,
            )
            if resp.status_code == 404:
                # DOI 未收录于 OpenAlex: 确认无 OA, 缓存空结果避免每次重查
                cache[cache_key] = ""
                _save_oa_cache()
                return None
            resp.raise_for_status()
            data = resp.json()
            loc = data.get("best_oa_location") or {}
            pdf_url = loc.get("pdf_url") or ""
            cache[cache_key] = pdf_url  # 空字符串 = 确认无 OA (也缓存, 避免重复查)
            _save_oa_cache()
        except Exception as e:
            logger.debug(f"OpenAlex OA 查询失败 {doi_clean}: {e}")
            return None  # 网络失败: 不缓存, 下次重试

    if not _is_downloadable_oa_url(pdf_url):
        logger.debug(f"OA 链接不可下载 (黑名单/非PDF): {pdf_url[:80]}")
        return None

    try:
        pdf_resp = get_with_retry(
            pdf_url,
            follow_redirects=True,
            read_timeout=45,
            max_retries=2,
            headers={"User-Agent": BROWSER_UA},
        )
        if not pdf_resp.content.startswith(b"%PDF"):
            logger.warning(f"Not a PDF from OA location: {pdf_url}")
            return None
        pdf_path.write_bytes(pdf_resp.content)
        logger.info(f"Downloaded OA PDF via DOI {doi_clean} -> {pdf_path}")
        return str(pdf_path)
    except Exception as e:
        logger.debug(f"OA PDF resolve failed for {doi}: {e}")
        return None


def download_pdfs_for_papers(papers: list[dict], limit: int = 10) -> list[dict]:
    """批量下载论文 PDF，返回带 pdf_path 字段的论文列表

    下载顺序: arXiv 官方链接 → DOI 开放获取 (OpenAlex best_oa_location)
    - 仅在实际发起网络请求后 sleep 3s (纯跳过不空等)
    - 断路器开启时直接跳过该主机的尝试, 并一次性提示
    """
    import time

    from src.utils.http_client import circuit_remaining_cooldown

    downloaded = []
    count = 0
    total = min(limit, len(papers))
    arxiv_blocked_reported = False
    consecutive_arxiv_waits = 0  # 连续等待断路器冷却的次数 (用于放弃 arXiv)
    for p in papers:
        url = p.get("url", "")
        # 优先使用独立保存的 arxiv_id (S2/OpenAlex 检索结果中提取)
        arxiv_id = p.get("arxiv_id", "") or extract_arxiv_id(url)
        attempted = False
        reasons: list[str] = []
        path = None

        # 1) arXiv 路径
        if not url and not arxiv_id:
            reasons.append("无 URL")
        elif arxiv_id:
            remaining = circuit_remaining_cooldown("https://export.arxiv.org/pdf/")
            if remaining > 0:
                # 断路器开启: 等待冷却后重试, 优先获取高优先级 arXiv 论文 (而非永久跳过)
                if consecutive_arxiv_waits >= 3:
                    # 连续多次等待仍未恢复: 放弃 arXiv, 走 OA 回退
                    if not arxiv_blocked_reported:
                        print("  [pdf_download] export.arxiv.org 持续不可用, 跳过剩余 arXiv 下载尝试")
                        arxiv_blocked_reported = True
                    reasons.append("arXiv 持续不可用")
                else:
                    consecutive_arxiv_waits += 1
                    if not arxiv_blocked_reported:
                        print(f"  [pdf_download] export.arxiv.org 断路器开启, 等待 {remaining:.0f}s 冷却后重试...")
                        arxiv_blocked_reported = True
                    time.sleep(remaining + 1.0)
                    attempted = True
                    path = download_arxiv_pdf(f"https://arxiv.org/abs/{arxiv_id}")
                    if path:
                        consecutive_arxiv_waits = 0
                    else:
                        reasons.append("arXiv 下载失败")
            else:
                print(f"  [pdf_download] 下载中: {p.get('title', '')[:45]}")
                attempted = True
                path = download_arxiv_pdf(f"https://arxiv.org/abs/{arxiv_id}")
                if path:
                    consecutive_arxiv_waits = 0
                else:
                    reasons.append("arXiv 下载失败")
        else:
            reasons.append("非 arXiv 链接")

        # 2) DOI 开放获取回退
        if not path:
            doi = p.get("doi", "") or ""
            if doi:
                attempted = True
                path = _resolve_pdf_from_doi(doi, ensure_pdf_dir())
                if not path:
                    reasons.append("OA 下载失败")
            else:
                reasons.append("无 DOI")

        if path:
            p["pdf_path"] = path
            downloaded.append(p)
            count += 1
            print(f"  [pdf_download] {count}/{total}: {p.get('title', '')[:50]}")
            if count >= limit:
                break
        else:
            print(f"  [pdf_download] 跳过 ({'/'.join(reasons)}): {p.get('title', '')[:50]}")

        if attempted:
            time.sleep(3.0)  # 限流保护: arXiv 建议 ≥3s/请求
    return downloaded
