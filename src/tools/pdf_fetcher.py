from __future__ import annotations

"""PDF 下载工具：从 arXiv 下载论文全文 PDF

参考开源项目:
- papercast (https://github.com/papercast-dev/papercast): arXiv + GROBID + LangChain 流水线
- Corvus (https://github.com/YS0meone/Corvus): GROBID 解析 + RAG
- scipdf_parser (https://github.com/titipata/scipdf_parser): 科学文献 PDF 解析
"""

import os
import re
import logging
from pathlib import Path
from typing import Optional

import httpx

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

    # 优先使用 export.arxiv.org 的官方下载地址
    pdf_url = f"https://export.arxiv.org/pdf/{arxiv_id}"
    headers = {"User-Agent": "AIR-AIResearch/0.1"}

    import time

    for attempt in range(3):
        try:
            resp = httpx.get(pdf_url, headers=headers, follow_redirects=True, timeout=90)
            if resp.status_code == 429:
                time.sleep(3 * (attempt + 1))  # 限流: 3s/6s/9s 退避
                continue
            resp.raise_for_status()
            # 校验确实是 PDF
            if not resp.content.startswith(b"%PDF"):
                logger.warning(f"Not a PDF response from {pdf_url}, size={len(resp.content)}")
                return None
            pdf_path.write_bytes(resp.content)
            logger.info(f"Downloaded {arxiv_id} -> {pdf_path} ({len(resp.content)} bytes)")
            return str(pdf_path)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            logger.warning(f"Failed to download {pdf_url}: {e}")
            return None
        except Exception as e:
            # 超时/连接错误等: 重试而非立即放弃 (arXiv 限流时表现为 read timeout)
            logger.warning(f"Download attempt {attempt + 1}/3 failed for {pdf_url}: {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return None
    return None


def _resolve_pdf_from_doi(doi: str, save_dir: Path) -> Optional[str]:
    """通过 DOI 解析开放获取 PDF（OpenAlex best_oa_location）"""
    import time

    if not doi:
        return None
    doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    safe = doi_clean.replace("/", "_")
    pdf_path = save_dir / f"oa_{safe}.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return str(pdf_path)

    try:
        resp = httpx.get(
            f"https://api.openalex.org/works/https://doi.org/{doi_clean}",
            params={"select": "best_oa_location,open_access"},
            headers={"User-Agent": "AIR-AIResearch/0.1"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        loc = data.get("best_oa_location") or {}
        pdf_url = loc.get("pdf_url") or ""
        if not pdf_url:
            return None
        pdf_resp = httpx.get(pdf_url, follow_redirects=True, timeout=60)
        if pdf_resp.status_code == 429:
            time.sleep(3)
            return None
        pdf_resp.raise_for_status()
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
    每篇之间加 1s 延迟, 避免连续请求触发限流
    """
    import time

    downloaded = []
    count = 0
    for p in papers:
        url = p.get("url", "")
        path = download_arxiv_pdf(url)
        if not path:
            # arXiv 失败或无 arXiv URL 时, 尝试通过 DOI 解析开放 PDF
            doi = p.get("doi", "") or ""
            if doi:
                path = _resolve_pdf_from_doi(doi, ensure_pdf_dir())
        if path:
            p["pdf_path"] = path
            downloaded.append(p)
            count += 1
            if count >= limit:
                break
        time.sleep(1.0)  # 限流保护
    return downloaded
