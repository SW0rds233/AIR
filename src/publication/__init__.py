from __future__ import annotations

"""publication 包: 文稿中间表示、写作输入与渲染入口。"""

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
