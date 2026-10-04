from __future__ import annotations

"""publication 包: 文稿中间表示、写作输入与渲染入口 (合并计划 §8)。

模块分工:
- `schemas.py`: `WritingPacket` / `Manuscript` / `Block` (唯一文稿表示);
- `inputs.py`: 从研究状态快照组装 `WritingPacket` (替代"综述字典伪装");
- `rendering.py`: Markdown / LaTeX 入口 (内部复用现有渲染实现);
- `gates.py`: 发布检查入口 (复用科研与格式检查)。
"""

from src.publication.schemas import (
    BLOCK_ROLES,
    Block,
    BlockRole,
    Manuscript,
    ManuscriptStatus,
    RefKind,
    Section,
    WritingPacket,
    assign_render_numbers,
)

__all__ = [
    "BLOCK_ROLES",
    "Block",
    "BlockRole",
    "Manuscript",
    "ManuscriptStatus",
    "RefKind",
    "Section",
    "WritingPacket",
    "assign_render_numbers",
]
