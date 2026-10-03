from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config import OUTPUT_DIR


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def save_file(content: str, filename: str, subdir: str | None = None) -> str:
    ensure_output_dir()
    d = OUTPUT_DIR / subdir if subdir else OUTPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    filepath = d / filename
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)



def sanitize_filename(name: str) -> str:
    invalid_chars = '<>:"/\\|?* '
    for c in invalid_chars:
        name = name.replace(c, "_")
    return name[:50].strip("_")


# 主题长度上限: 够表达"研究的是什么", 又不会把整段请求贴成标题
TOPIC_MAX_CHARS = 40
_TOPIC_TRIM = " \t\r\n#*`「」《》【】:：,，.。;；-—("


def derive_topic(text: str, *, limit: int = TOPIC_MAX_CHARS) -> str:
    """从请求文本取一个**简短主题** (供标题/日志/产物名使用)。

    为什么需要: CLI/Web 的 `--request` 常常是整段问题描述 (可能带 Markdown 标题、
    多段落、公式)。直接拿它当主题会让交付物标题变成一整段粘贴文本 (换行、公式、
    "请判断…" 全进标题), 既不可读也不像论文题目。这里只取首行/首句并设上限;
    取不到任何内容时返回空串, 由调用方决定回退 (通常用 project_id)。
    """
    body = (text or "").strip()
    if not body:
        return ""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    first = first.lstrip("#").strip()
    # 首行过长时按句末标点截到第一句, 再按长度上限截断
    if len(first) > limit:
        cut = next((i for i, ch in enumerate(first) if ch in "。.;；!！?？\n"), -1)
        if 0 < cut < limit:
            first = first[:cut]
        else:
            first = first[:limit]
    return first.strip(_TOPIC_TRIM).strip()


def safe_join(root: Path | str, *parts: str) -> Path | None:
    """把不可信路径片段拼到 root 下, 越界返回 None。

    不能用 `str(path).startswith(str(root))` 做包含性判断: 那会把
    `outputs_backup/` 这类**同前缀兄弟目录**误判为在 `outputs/` 之内
    (计划书 §9.4/§9.5: 网页与文件名都是外部资料, 不得越出授权目录)。
    这里按路径分量判断, 并且拒绝绝对路径与 `..` 上跳。
    """
    root_path = Path(root).resolve()
    candidate = Path(parts[0]) if len(parts) == 1 else Path(*parts)
    if candidate.is_absolute():
        return None
    target = (root_path / candidate).resolve()
    try:
        target.relative_to(root_path)
    except ValueError:
        return None
    return target


def is_within(root: Path | str, target: Path | str) -> bool:
    """target 是否确实位于 root 目录之内 (含等号), 按路径分量比较。"""
    try:
        Path(target).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False



def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
