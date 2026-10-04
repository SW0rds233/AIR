from __future__ import annotations

"""会话记忆: 记录**按归属**保存的研究主题与已完成阶段。

用于支持"继续撰写"类指令: 用户说「结合已有的数据和报告，撰写综述」而未指明
主题时, Planner 从会话记忆里取回该会话上次的主题, 复用其检索缓存。

归属 (合并计划 §7.4 / M4)
------------------------
这里此前只有一个 `session_memory.json`, 内容永远等于"最近一次写" —— 两个会话并行时,
A 的"继续撰写"会拿到 B 的主题, 这正是计划书禁止的"依靠全局最近一次续研"。

现在:
- 文件按**会话**分片 (`data/session_memory/<session_id>.json`), 各会话互不覆盖;
- 同时继续维护一份 `session_memory.json` 汇总 (仍是最近一次, 供旧调用点与人工查看),
  但读取时**优先按当前运行身份**取分片, 取不到才退回汇总;
- 运行身份来自 `run_scope` (由 `server._run_session` 在其工作线程里绑定)。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.utils.run_scope import current_run_scope

logger = logging.getLogger(__name__)

#: 旧的汇总文件 (最近一次写入的记忆) —— 保留以兼容旧调用点与人工查看。
MEMORY_FILE = DATA_DIR / "session_memory.json"
#: 按会话分片的记忆目录。
MEMORY_DIR = DATA_DIR / "session_memory"


def _safe_key(value: str) -> str:
    """会话身份用作文件名: 只留安全字符, 空则返回空串 (不落盘)。"""
    text = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_.")
    return text.strip("._-")[:96]


def memory_path(session_id: str) -> Path | None:
    """该会话的记忆文件; 身份为空/非法时返回 None (不写无归属的文件)。"""
    key = _safe_key(session_id)
    return (MEMORY_DIR / f"{key}.json") if key else None


def save_session_memory(topic: str, stages: list[str] | None = None,
                        session_id: str = "") -> None:
    """保存会话记忆 (该会话最近一次研究的主题与已完成阶段)。

    `session_id` 不传时取当前运行身份; 都取不到时只写汇总文件 (旧行为)。
    """
    topic = (topic or "").strip()
    if not topic:
        return
    scope = current_run_scope()
    owner = str(session_id or scope.session_id or scope.run_id or "")
    payload = {
        "topic": topic,
        "stages": list(stages or []),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": owner,
        "run_id": scope.run_id,
        "project_id": scope.project_id,
    }
    path = memory_path(owner)
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        except Exception as e:  # noqa: BLE001 - 记忆写失败不影响研究
            logger.warning(f"会话记忆保存失败 ({path.name}): {e}")
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"会话记忆保存失败: {e}")


def _read(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def load_session_memory(session_id: str = "") -> dict | None:
    """加载会话记忆。

    取用顺序: 显式 `session_id` → 当前运行身份的会话分片 → 汇总 (旧的"最近一次")。
    这样"继续撰写"不会串到别的会话, 同时不破坏既有单会话行为。
    """
    scope = current_run_scope()
    for candidate in (session_id, scope.session_id, scope.run_id):
        memory = _read(memory_path(candidate))
        if memory:
            return memory
    return _read(MEMORY_FILE)
