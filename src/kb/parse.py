from __future__ import annotations

"""文档解析: 保留页码与章节结构, 与 embedding 解耦。

支持 PDF (pymupdf, 逐页)、txt/md、docx (zip 内 document.xml, 无需额外依赖)。
扫描版/无文本 PDF 标记 parse_quality=failed/ocr, 不静默丢弃。

P1-4: PDF 解析同时保存**可见性元数据** (字号/颜色/坐标) 并标出视觉异常片段
(接近白字、极小字、页外字、被图形覆盖), 供上层标"需核对"; 文本本身保留,
但默认不作为问题要求、研究行动或强证据。
"""

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.kb.schema import Section

_HEADING_NUM = re.compile(r"^\s*(?:第\s*[一二三四五六七八九十百\d]+\s*[章节部分]|\d+(?:\.\d+)*)\s*[、.．]?\s*\S")
_HEADING_MD = re.compile(r"^\s*#{1,6}\s+\S")
_HEADING_CN = re.compile(r"^\s*(摘要|关键词|引言|绪论|相关工作|研究方法|方法|实验|结果|讨论|结论|参考文献|附录)\b")
_REFS = re.compile(r"^\s*(参考文献|References|REFERENCES)\s*$")

# 可见性阈值: 只把**接近背景色**的文字判为异常, 浅灰图注/正常彩色标题不误伤
NEAR_WHITE_LUMINANCE = 0.85   # 相对亮度高于此值视为"接近白字"
TINY_FONT_PT = 4.0            # 小于该字号视为极小字
OFF_PAGE_TOLERANCE = 2.0      # 超出页面边界的容差 (点)


@dataclass
class Span:
    """一个文本片段的样式与位置 (页面渲染时的可见状态)。"""

    text: str = ""
    page: int = 1
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    size: float = 0.0
    color: int = 0              # sRGB 整数 (pymupdf 口径)
    flags: list[str] = field(default_factory=list)

    @property
    def locator(self) -> str:
        x0, y0, x1, y1 = self.bbox
        return f"p.{self.page} [{x0:.0f},{y0:.0f}-{x1:.0f},{y1:.0f}]"

    def to_dict(self) -> dict:
        return {"text": self.text[:200], "page": self.page, "bbox": list(self.bbox),
                "size": round(self.size, 2), "color": self.color,
                "flags": list(self.flags), "locator": self.locator}


def _luminance(color: int) -> float:
    """sRGB 整数 → 相对亮度 (0 黑, 1 白)。"""
    r = ((color >> 16) & 0xFF) / 255.0
    g = ((color >> 8) & 0xFF) / 255.0
    b = (color & 0xFF) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def flag_span(span: Span, page_rect: tuple[float, float, float, float] | None = None,
              covered: list[tuple[float, float, float, float]] | None = None) -> list[str]:
    """标出一个片段的可见性异常 (只做可复核的几何/样式判断)。"""
    flags: list[str] = []
    text = (span.text or "").strip()
    if not text:
        return flags
    if span.color and _luminance(span.color) >= NEAR_WHITE_LUMINANCE:
        flags.append("near_white")
    if 0 < span.size < TINY_FONT_PT:
        flags.append("tiny_font")
    if page_rect is not None:
        x0, y0, x1, y1 = span.bbox
        px0, py0, px1, py1 = page_rect
        if (x0 < px0 - OFF_PAGE_TOLERANCE or y0 < py0 - OFF_PAGE_TOLERANCE
                or x1 > px1 + OFF_PAGE_TOLERANCE or y1 > py1 + OFF_PAGE_TOLERANCE):
            flags.append("off_page")
    for rect in covered or []:
        cx0, cy0, cx1, cy1 = rect
        x0, y0, x1, y1 = span.bbox
        if x0 >= cx0 and y0 >= cy0 and x1 <= cx1 and y1 <= cy1:
            flags.append("covered")
            break
    return flags


@dataclass
class ParsedDoc:
    pages: list[tuple[int, str]] = field(default_factory=list)
    full_text: str = ""
    sections: list[Section] = field(default_factory=list)
    language: str = ""
    references: list[str] = field(default_factory=list)
    parse_quality: str = "ok"
    # 带样式与定位的片段 + 视觉异常清单 (供上层标"需核对")
    spans: list[Span] = field(default_factory=list)
    visibility_flags: list[dict] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.visibility_flags)


def _read_pdf(path: Path) -> list[tuple[int, str]]:
    try:
        import fitz  # pymupdf

        pages: list[tuple[int, str]] = []
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc, start=1):
                pages.append((i, page.get_text() or ""))
        return pages
    except Exception:  # noqa: BLE001
        return []


def _covered_rects(page) -> list[tuple[float, float, float, float]]:
    """页面上"可能盖住文字"的填充矩形 (尽力而为: 拿不到绘图信息就返回空)。"""
    rects: list[tuple[float, float, float, float]] = []
    try:
        for drawing in page.get_drawings() or []:
            fill = drawing.get("fill")
            if not fill:
                continue
            rect = drawing.get("rect")
            if rect is not None and rect.width > 1 and rect.height > 1:
                rects.append((rect.x0, rect.y0, rect.x1, rect.y1))
    except Exception:  # noqa: BLE001 - 无绘图信息时不影响文本解析
        return []
    return rects


def read_pdf_spans(path: Path) -> list[Span]:
    """逐 span 读取文本与样式 (字号/颜色/坐标), 并标出视觉异常。"""
    try:
        import fitz  # pymupdf
    except Exception:  # noqa: BLE001
        return []
    spans: list[Span] = []
    try:
        with fitz.open(str(path)) as doc:
            for index, page in enumerate(doc, start=1):
                rect = page.rect
                page_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
                covered = _covered_rects(page)
                data = page.get_text("dict") or {}
                for block in data.get("blocks", []) or []:
                    for line in block.get("lines", []) or []:
                        for item in line.get("spans", []) or []:
                            span = Span(text=str(item.get("text", "") or ""),
                                        page=index,
                                        bbox=tuple(float(v) for v in (item.get("bbox")
                                                                     or (0, 0, 0, 0))),
                                        size=float(item.get("size", 0.0) or 0.0),
                                        color=int(item.get("color", 0) or 0))
                            span.flags = flag_span(span, page_rect, covered)
                            spans.append(span)
    except Exception:  # noqa: BLE001
        return spans
    return spans


@dataclass
class SourceReading:
    """`read_source()` 的返回: 文本 + 定位 + 可见性 + 信任标记。"""

    text: str = ""
    spans: list[Span] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    path: str = ""

    @property
    def trust(self) -> str:
        """needs_review = 含视觉异常片段; trusted = 未发现异常 (不等于内容正确)。"""
        return "needs_review" if self.flags else "trusted"

    def to_dict(self) -> dict:
        return {"path": self.path, "trust": self.trust, "flags": list(self.flags),
                "spans": [s.to_dict() for s in self.spans if s.flags]}


def read_source(path: str) -> SourceReading:
    """读取一份资料: 带定位、可见性与信任标记的片段 (P1-4 接口)。

    异常片段**保留原文**, 但会被标出 (含页码与坐标), 由上层决定是否采纳。
    """
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        pages = _read_text(p) if p.suffix.lower() in (".txt", ".md", ".markdown") else []
        text = "\n".join(t for _, t in pages)
        return SourceReading(text=text, path=str(p))
    spans = read_pdf_spans(p)
    text = "\n".join(s.text for s in spans if s.text.strip())
    flags = [s.to_dict() for s in spans if s.flags]
    return SourceReading(text=text, spans=spans, flags=flags, path=str(p))


def _read_text(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [(1, text)]


def _read_docx(path: Path) -> list[tuple[int, str]]:
    try:
        with zipfile.ZipFile(str(path)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    # 段落 → 换行; 去除标签
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return [(1, text)]


def detect_language(text: str) -> str:
    if not text:
        return ""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk / max(len(text), 1) > 0.1 else "en"


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60:
        return False
    return bool(_HEADING_MD.match(s) or _HEADING_NUM.match(s) or _HEADING_CN.match(s))


def segment_sections(pages: list[tuple[int, str]], doc_id: str = "") -> list[Section]:
    lines: list[tuple[str, int]] = []
    for page_no, text in pages:
        for line in (text or "").splitlines():
            lines.append((line.rstrip(), page_no))
    sections: list[Section] = []
    cur_heading, cur_page, buf = "正文", (pages[0][0] if pages else 1), []
    idx = 0
    # 记录本节实际覆盖的页范围与字符范围 (节可能跨页, 因此不能只记首页)
    state = {"page_end": cur_page, "char_start": 0, "cursor": 0}

    def flush():
        nonlocal idx, buf
        body = "\n".join(buf).strip()
        if body or cur_heading != "正文":
            sections.append(Section(
                doc_id=doc_id, index=idx, heading=cur_heading,
                page=cur_page, page_end=max(cur_page, state["page_end"]),
                text=body,
                char_start=state["char_start"],
                char_end=state["cursor"],
            ))
            idx += 1
        buf = []

    for line, page_no in lines:
        if _REFS.match(line.strip()):
            flush()
            cur_heading, cur_page = "参考文献", page_no
            state["page_end"] = page_no
            state["char_start"] = state["cursor"]
            continue
        if _looks_like_heading(line):
            flush()
            cur_heading, cur_page = line.strip().lstrip("#").strip(), page_no
            state["page_end"] = page_no
            state["char_start"] = state["cursor"]
            continue
        buf.append(line)
        state["page_end"] = max(state["page_end"], page_no)
        state["cursor"] += len(line) + 1
    flush()
    return sections


def _extract_references(full_text: str) -> list[str]:
    m = re.search(r"(?:^|\n)\s*(参考文献|References)\s*\n(.*)$", full_text, re.DOTALL)
    if not m:
        return []
    block = m.group(2)
    items = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\[?\d+\]?[\.、．]?\s+", line):
            items.append(re.sub(r"^\[?\d+\]?[\.、．]?\s+", "", line))
    return items[:500]


def parse_document(path: str, doc_id: str = "") -> ParsedDoc:
    p = Path(path)
    suffix = p.suffix.lower()
    spans: list[Span] = []
    if suffix == ".pdf":
        pages = _read_pdf(p)
        spans = read_pdf_spans(p)
    elif suffix in (".txt", ".md", ".markdown"):
        pages = _read_text(p)
    elif suffix == ".docx":
        pages = _read_docx(p)
    else:
        return ParsedDoc(parse_quality="failed")

    full_text = "\n".join(t for _, t in pages).strip()
    if not full_text:
        return ParsedDoc(pages=pages, parse_quality="failed")
    if len(full_text) < 200:
        quality = "short"
    else:
        quality = "ok"
    return ParsedDoc(
        pages=pages,
        full_text=full_text,
        sections=segment_sections(pages, doc_id=doc_id),
        language=detect_language(full_text),
        references=_extract_references(full_text),
        parse_quality=quality,
        spans=spans,
        visibility_flags=[s.to_dict() for s in spans if s.flags],
    )
