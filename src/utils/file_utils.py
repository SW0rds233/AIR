from __future__ import annotations

from pathlib import Path
from datetime import datetime

from src.config import OUTPUT_DIR


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def save_file(content: str, filename: str) -> str:
    ensure_output_dir()
    filepath = OUTPUT_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)


def load_file(filename: str) -> str:
    filepath = OUTPUT_DIR / filename
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def sanitize_filename(name: str) -> str:
    invalid_chars = '<>:"/\\|?* '
    for c in invalid_chars:
        name = name.replace(c, "_")
    return name[:50].strip("_")


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
