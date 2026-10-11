"""Open the operating system's folder chooser for a local AIR server."""

from __future__ import annotations

import threading
import secrets
import time
from pathlib import Path

_picker_lock = threading.Lock()
_grant_lock = threading.Lock()
_grants: dict[str, tuple[Path, float]] = {}
_GRANT_SECONDS = 15 * 60


def pick_folder() -> Path | None:
    """Return the selected directory, or ``None`` when the user cancels.

    The dialog runs in the request worker thread, which owns and destroys its
    own Tk root.  Only one dialog may be open at once.
    """
    if not _picker_lock.acquire(blocking=False):
        raise RuntimeError("已有文件夹选择窗口，请先完成或取消")
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        from src import config

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        initial = config.DATA_DIR if config.DATA_DIR.is_dir() else Path.home()
        selected = filedialog.askdirectory(
            parent=root, title="选择要导入文献的文件夹", initialdir=str(initial),
            mustexist=True,
        )
        return Path(selected).resolve() if selected else None
    finally:
        if root is not None:
            root.destroy()
        _picker_lock.release()


def issue_grant(folder: Path) -> str:
    """Authorize exactly the directory selected in the local native dialog."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError("所选文件夹不存在")
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _grant_lock:
        for old, (_, expiry) in list(_grants.items()):
            if expiry <= now:
                del _grants[old]
        _grants[token] = (folder, now + _GRANT_SECONDS)
    return token


def granted_roots(tokens: list[str]) -> list[str]:
    """Resolve native-dialog grants; arbitrary client-supplied roots are ignored."""
    if len(tokens) > 20:
        raise ValueError("一次最多选择 20 个文件夹")
    now = time.monotonic()
    roots: list[str] = []
    with _grant_lock:
        for token in tokens:
            item = _grants.get(token)
            if item is None or item[1] <= now:
                raise ValueError("文件夹选择授权已过期，请重新选择")
            roots.append(str(item[0]))
    return roots
