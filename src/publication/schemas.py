from __future__ import annotations

"""文稿中间表示: WritingPacket / Manuscript / Block (合并计划 §6.2 / §8)。

为什么单独建这一层
------------------
合并前有**两套**正文路径: 综述侧把"笔记字典"伪装成稿件, 理论侧在
`rag/theory_render.py` 里渲染结构化事实并另写一份附录长文 —— 合并计划 §7.3 要求
**退役"双正文"主路径**: 主文统一由 WritingAgent 形成, 证书留在附录。

因此这里定义唯一的文稿表示:

- `Block` 带**稳定 block ID**、段落角色 (`role`)、论断/来源/图表引用 (`claims`/
  `sources`/`figures`) 与所依赖的对象版本 —— 这样"从稿件句子回到来源/推导"是可查的,
  而不是靠再解析一段 Markdown;
- `Manuscript` 带版本与快照引用; 所依赖快照过期时能明确标 stale (§6.1、§9.6);
- `WritingPacket` 是写作**输入**契约: 任务画像 + 结果快照 + 来源/图表引用 +
  审阅意见。它替代"综述字典": 后端不再靠字段名猜这是个什么任务。

编号规则 (§6.3): 来源 ID/DOI/hash 是稳定身份; `[1]`、图 1 与公式编号**只在最终渲染
时分配**。因此这里只保存引用关系, 不保存渲染出来的编号。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.research.schemas import ObjectRef, new_id, utcnow

__all__ = [
    "BLOCK_ROLES",
    "Block",
    "Manuscript",
    "ManuscriptStatus",
    "Section",
    "WritingPacket",
    "assign_render_numbers",
]


class BlockRole(str, Enum):
    """段落角色: 决定渲染方式与"事实/解释"的区分。"""

    claim = "claim"              # 结论陈述
    reasoning = "reasoning"      # 推导/论证
    evidence = "evidence"        # 证据综述
    certificate = "certificate"  # 确定性证书渲染 (公式/条目)
    definition = "definition"
    method = "method"
    limitation = "limitation"
    background = "background"
    transition = "transition"
    figure_ref = "figure_ref"
    table = "table"


BLOCK_ROLES: frozenset[str] = frozenset(r.value for r in BlockRole)


class RefKind(str, Enum):
    source = "source"
    claim = "claim"
    obligation = "obligation"
    model = "model"
    figure = "figure"
    verification = "verification"
    block = "block"


class Block(BaseModel):
    """一个文稿块 (段落/公式/表格/图引用)。"""

    block_id: str = Field(default_factory=lambda: new_id("blk"))
    role: BlockRole = BlockRole.reasoning
    heading: str = ""
    text: str = ""
    #: 依赖的版本化对象 (论断/来源/图表/核验记录)。引用只带 id+version。
    refs: list[ObjectRef] = Field(default_factory=list)
    ref_kinds: list[str] = Field(default_factory=list)   # 与 refs 一一对应
    #: 该块所依据的**对象版本** (read-set): 依据变化则该块 stale (§6.3)。
    input_versions: dict[str, int] = Field(default_factory=dict)
    #: 待核查标记 (审阅问题定位到这里)。
    needs_check: bool = False
    review_issue_ids: list[str] = Field(default_factory=list)
    #: 公式 (LaTeX 片段; 渲染时才决定编号)。
    math: str = ""
    #: 表格/列表的原始结构 (渲染器按 profile 转 Markdown/LaTeX)。
    data: dict[str, Any] = Field(default_factory=dict)
    ordinal: int = 0             # 渲染时的序号 (由 assign_render_numbers 填写)

    def add_ref(self, kind: str, ref: ObjectRef) -> None:
        self.refs.append(ref)
        self.ref_kinds.append(kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id, "role": self.role.value,
            "heading": self.heading, "text": self.text,
            "refs": [r.model_dump() for r in self.refs],
            "ref_kinds": list(self.ref_kinds),
            "input_versions": dict(self.input_versions),
            "needs_check": self.needs_check,
            "review_issue_ids": list(self.review_issue_ids),
            "math": self.math, "data": dict(self.data), "ordinal": self.ordinal,
        }


class Section(BaseModel):
    section_id: str = Field(default_factory=lambda: new_id("sec"))
    heading: str = ""
    level: int = 1
    blocks: list[Block] = Field(default_factory=list)
    #: 章节角色 (引言/方法/结论…), 由任务画像与论证结构决定, 不套固定模板。
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"section_id": self.section_id, "heading": self.heading,
                "level": self.level, "role": self.role,
                "blocks": [b.to_dict() for b in self.blocks]}


class ManuscriptStatus(str, Enum):
    draft = "draft"
    revised = "revised"
    reviewed = "reviewed"
    superseded = "superseded"


class Manuscript(BaseModel):
    """版本化稿件。"""

    manuscript_id: str = Field(default_factory=lambda: new_id("ms"))
    version: int = 1
    title: str = ""
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    status: ManuscriptStatus = ManuscriptStatus.draft
    #: 稿件所依赖的研究快照 (过期即 stale, 界面须明确标记)。
    snapshot_id: str = ""
    input_versions: dict[str, int] = Field(default_factory=dict)
    #: 未解决项与写作缺口 (回流给主控, 不自行补研究)。
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)
    source_object_ref: ObjectRef | None = None

    def all_blocks(self) -> list[Block]:
        return [block for section in self.sections for block in section.blocks]

    def block_by_id(self, block_id: str) -> Block | None:
        return next((b for b in self.all_blocks() if b.block_id == block_id), None)

    def trace(self) -> dict[str, list[dict[str, Any]]]:
        """段落 -> 依据 的追溯映射 (计划书 §6.1 的可反查要求)。"""
        out: dict[str, list[dict[str, Any]]] = {}
        for block in self.all_blocks():
            out[block.block_id] = [
                {"kind": kind, "id": ref.id, "version": ref.version}
                for kind, ref in zip(block.ref_kinds, block.refs)
            ]
        return out

    def stale_against(self, current_versions: dict[str, int]) -> list[str]:
        """相对当前对象版本, 哪些块已过期 (§6.3: 模型换代使下游 stale)。"""
        stale: list[str] = []
        for block in self.all_blocks():
            for object_id, version in (block.input_versions or {}).items():
                current = current_versions.get(object_id)
                if current is not None and current > int(version):
                    stale.append(block.block_id)
                    break
        return stale

    def word_count(self) -> int:
        return sum(len(b.text) for b in self.all_blocks())

    def to_dict(self) -> dict[str, Any]:
        return {
            "manuscript_id": self.manuscript_id, "version": self.version,
            "title": self.title, "abstract": self.abstract,
            "keywords": list(self.keywords),
            "sections": [s.to_dict() for s in self.sections],
            "status": self.status.value, "snapshot_id": self.snapshot_id,
            "input_versions": dict(self.input_versions),
            "gaps": list(self.gaps), "created_at": self.created_at,
            "word_count": self.word_count(),
            "source_object_ref": (self.source_object_ref.model_dump()
                                  if self.source_object_ref else None),
        }


class WritingPacket(BaseModel):
    """写作输入契约 (合并计划 §3.1 的 `WritingPacket`)。

    后端不再靠字段名猜任务类型: `subquestion_kinds`/`deliverables` 显式说明这是
    综述、理论结论还是验证建议, 章节结构由写作智能体按论证需要设计。
    """

    packet_id: str = Field(default_factory=lambda: new_id("wp"))
    project_id: str = ""
    problem_id: str = ""
    run_id: str = ""
    original_request: str = ""
    main_question: str = ""
    subquestion_kinds: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    output_language: str = ""
    #: 结果快照: 命题/模型/义务/验证的**只读**投影 (带版本)。
    claims: list[dict[str, Any]] = Field(default_factory=list)
    models: list[dict[str, Any]] = Field(default_factory=list)
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    verifications: list[dict[str, Any]] = Field(default_factory=list)
    validations: list[dict[str, Any]] = Field(default_factory=list)
    #: 来源与图表引用 (编号在渲染时分配)。
    sources: list[dict[str, Any]] = Field(default_factory=list)
    figures: list[dict[str, Any]] = Field(default_factory=list)
    #: 审阅意见 (修订任务的输入)。
    review_issues: list[dict[str, Any]] = Field(default_factory=list)
    prior_manuscript: dict[str, Any] = Field(default_factory=dict)
    #: 未决项: 写作不得掩盖未决, 必须如实写出来 (§12)。
    unresolved: list[str] = Field(default_factory=list)
    snapshot_id: str = ""
    input_versions: dict[str, int] = Field(default_factory=dict)
    revision_of: str = ""        # 修订时指向上一版 manuscript_id
    focus_blocks: list[str] = Field(default_factory=list)

    def is_revision(self) -> bool:
        return bool(self.revision_of)

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id, "project_id": self.project_id,
            "problem_id": self.problem_id, "run_id": self.run_id,
            "original_request": self.original_request,
            "main_question": self.main_question,
            "subquestion_kinds": list(self.subquestion_kinds),
            "deliverables": list(self.deliverables),
            "output_language": self.output_language,
            "claims": list(self.claims), "models": list(self.models),
            "obligations": list(self.obligations),
            "verifications": list(self.verifications),
            "validations": list(self.validations),
            "sources": list(self.sources), "figures": list(self.figures),
            "review_issues": list(self.review_issues),
            "prior_manuscript": dict(self.prior_manuscript),
            "unresolved": list(self.unresolved),
            "snapshot_id": self.snapshot_id,
            "input_versions": dict(self.input_versions),
            "revision_of": self.revision_of,
            "focus_blocks": list(self.focus_blocks),
        }


def assign_render_numbers(manuscript: Manuscript) -> dict[str, Any]:
    """在**渲染时**分配引用/图表/公式编号 (身份不因重排而改变)。

    返回 `{"citations": {object_id: n}, "figures": {object_id: "1"}, "equations": {...}}`。
    同一来源多次引用复用同一编号; 编号按**首次出现顺序**分配。
    """
    citations: dict[str, int] = {}
    figures: dict[str, str] = {}
    equations: dict[str, str] = {}
    for block in manuscript.all_blocks():
        for kind, ref in zip(block.ref_kinds, block.refs):
            if kind == RefKind.source.value and ref.id not in citations:
                citations[ref.id] = len(citations) + 1
            elif kind == RefKind.figure.value and ref.id not in figures:
                figures[ref.id] = str(len(figures) + 1)
        if block.math and block.block_id not in equations:
            equations[block.block_id] = str(len(equations) + 1)
    return {"citations": citations, "figures": figures, "equations": equations}
