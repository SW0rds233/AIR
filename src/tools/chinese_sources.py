from __future__ import annotations

"""中文文献获取工具

两层策略 (均为真实元数据来源, 避免 LLM 编造作者/缺卷期页):
1. CNKI 开放平台 API (需 CNKI_API_KEY, 可选)
2. 万方开放平台 API (需 WANFANG_API_KEY, 可选)

另提供 _doi_resolves 供其他模块做 DOI 存在性快速验证。
"""

import logging
import os

from src.utils.http_client import get_with_retry

logger = logging.getLogger(__name__)

CNKI_API_KEY = os.getenv("CNKI_API_KEY", "")
WANFANG_API_KEY = os.getenv("WANFANG_API_KEY", "")


def _doi_resolves(doi: str) -> bool:
    """DOI 是否存在: 通过 doi.org 全球解析器验证

    中文期刊 DOI 注册机构多样 (万方 10.3969 / CNKI / ISTIC)，
    CrossRef 只覆盖其中一部分，故用 doi.org 全局解析:
    302/200 = 存在, 404 = 不存在。
    """
    if not doi:
        return False
    from urllib.parse import quote

    try:
        resp = get_with_retry(
            f"https://doi.org/{quote(doi.strip(), safe='')}",
            follow_redirects=False,
            read_timeout=15,
            max_retries=2,
            raise_on_status=False,
        )
        return resp.status_code in (200, 301, 302, 303, 307, 308)
    except Exception:
        return False


def cnki_search(query: str, max_results: int = 10) -> list[dict]:
    """CNKI 开放平台检索 (需在 .env 配置 CNKI_API_KEY)

    未配置 key 时返回空列表。key 申请: https://open.cnki.net
    """
    if not CNKI_API_KEY:
        return []

    try:
        resp = get_with_retry(
            "https://api.cnki.net/search/v1",
            params={
                "key": CNKI_API_KEY,
                "q": query[:100],
                "size": min(max_results, 20),
            },
            headers={"User-Agent": "AIR-AIResearch/0.1"},
            read_timeout=20,
            raise_on_status=False,
        )
        if resp.status_code != 200:
            logger.warning(f"CNKI API 返回 {resp.status_code}")
            return []
        data = resp.json()
        papers = []
        for item in (data.get("data") or {}).get("items", [])[:max_results]:
            papers.append({
                "title": item.get("title", ""),
                "authors": item.get("authors", ""),
                "year": str(item.get("year", "")),
                "source": item.get("source", "") or "CNKI",
                "venue": item.get("source", ""),
                "api_source": "CNKI",
                "abstract": item.get("abstract", "")[:1000],
                "citations": item.get("cited_count", 0),
                "url": item.get("url", ""),
                "doi": item.get("doi", "") or "",
                "bibtex": "",
            })
        return papers
    except Exception as e:
        logger.debug(f"CNKI 检索失败: {e}")
        return []


def wanfang_search(query: str, max_results: int = 10) -> list[dict]:
    """万方开放平台检索 (需在 .env 配置 WANFANG_API_KEY)

    未配置 key 时返回空列表。key 申请: https://open.wanfangdata.com.cn
    """
    if not WANFANG_API_KEY:
        return []

    try:
        resp = get_with_retry(
            "https://open.wanfangdata.com.cn/api/search",
            params={
                "appkey": WANFANG_API_KEY,
                "keyword": query[:100],
                "rows": min(max_results, 20),
            },
            headers={"User-Agent": "AIR-AIResearch/0.1"},
            read_timeout=20,
            raise_on_status=False,
        )
        if resp.status_code != 200:
            logger.warning(f"万方 API 返回 {resp.status_code}")
            return []
        data = resp.json()
        papers = []
        for item in (data.get("data") or [])[:max_results]:
            papers.append({
                "title": item.get("title", ""),
                "authors": item.get("authors", ""),
                "year": str(item.get("year", "")),
                "source": item.get("source", "") or "万方",
                "venue": item.get("source", ""),
                "api_source": "万方",
                "abstract": item.get("abstract", "")[:1000],
                "citations": item.get("cited_count", 0),
                "url": item.get("url", ""),
                "doi": item.get("doi", "") or "",
                "bibtex": "",
            })
        return papers
    except Exception as e:
        logger.debug(f"万方检索失败: {e}")
        return []
