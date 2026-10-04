from __future__ import annotations

"""按用户指定路径构建人工文献库 (合并计划 §13)。

需求
----
用户直接给出**本机文件或文件夹路径**, 由系统扫描并登记为文献库, 供检索、建模、
推理与引用使用; 不再要求先把 PDF 手工拷进固定目录。

为什么不能"复制到 data/manual_pdfs"
-----------------------------------
复制会产生第二份真相源、占双倍磁盘, 并在用户更新原件后让库静默过期。因此采用
**引用 + hash + mtime**: 库只登记路径与内容摘要, 由 `PathImportRef.missing_since`
承担失效检测。

安全规则 (必须实现, 不能只靠提示词)
----------------------------------
1. 默认不放开任意路径: 只接受位于 `readonly_data.authorized_roots()` 之下
   (data/、outputs/ 或 `DATA_READ_ROOTS` 显式放行项) 的路径, 其余一律 `denied`
   并给出"需维护者显式放行"的说明 —— 与只读数据适配器同一语义, 不新写一套规则;
2. 敏感目标硬拒绝 (即使在被授权根内): `.env`、`*.pem/*.key/*.pfx`、`.ssh/`、
   `.git/`、`.aws/`、`credentials*`、`node_modules/`、`.venv/`, 以及符号链接指向
   上述内容者;
3. 只读: 不复制、不改名、不修改、不执行被导入文件; 不跟随符号链接出授权根
   (越界即 denied);
4. 路径回显: 对外只给 basename (界面/日志用 `display_path`), 完整绝对路径只进
   本地登记记录与审计;
5. 体量与数量上限: 默认 500 文件 / 2 GB; 超限截断并标记 `truncated=True`;
6. 不引入新的提权入口: 导入只构建文献库, 不因为给了路径就获得数据查询、执行或
   联网的额外权限。
"""

import fnmatch
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.kb.adapters.readonly_data import authorized_roots
from src.kb.cards import extract_cards
from src.kb.identity import build_identity, candidate_keys, resolve_doc_id
from src.kb.parse import parse_document
from src.kb.schema import LitRecord, Provenance, compute_file_hash
from src.kb.store import KBStore, topic_dir
from src.utils.file_utils import get_timestamp, sanitize_filename
from src.utils.uploads import ALLOWED_SUFFIXES

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FILES",
    "IMPORT_ORIGIN",
    "SENSITIVE_DIR_NAMES",
    "SENSITIVE_NAME_PATTERNS",
    "SENSITIVE_SUFFIXES",
    "ImportReport",
    "ImportedRecord",
    "PathImportRef",
    "ScanReport",
    "ScanRequest",
    "ScannedFile",
    "detect_missing",
    "display_path",
    "import_paths",
    "load_path_refs",
    "path_refs_file",
    "scan_paths",
]

DEFAULT_MAX_FILES = 500
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024      # 2 GB

#: 登记来源标记: 与 `readonly_data` 的"授权根"语义配套, 便于审计区分人工上传/路径导入。
IMPORT_ORIGIN = "path_import"

#: 敏感扩展名 (硬拒绝): 凭据与私钥类。
SENSITIVE_SUFFIXES: frozenset[str] = frozenset({
    ".pem", ".key", ".pfx", ".p12", ".crt", ".cer", ".jks", ".keystore",
})

#: 敏感目录名 (硬拒绝, 整个子树)。
SENSITIVE_DIR_NAMES: frozenset[str] = frozenset({
    ".ssh", ".git", ".aws", ".gnupg", ".docker", ".kube", ".venv", "venv",
    "node_modules", "__pycache__", ".idea", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "site-packages",
})

#: 敏感文件名/前缀 (硬拒绝): 环境文件与凭据文件。
SENSITIVE_NAME_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.env", "credentials", "credentials.*", "*credentials*",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.secret", "*.secrets",
    "*.token", "*.password", "*.kdbx", ".netrc", "_netrc", ".npmrc", ".pypirc",
    ".git-credentials", "*.sqlite-wal",
)


class FileStatus(str, Enum):
    ok = "ok"
    skipped = "skipped"          # 后缀不在白名单
    denied = "denied"            # 未授权根 / 敏感 / 符号链接越界
    duplicate = "duplicate"      # 同一 hash 已在库中
    unreadable = "unreadable"    # 权限/IO 失败


class ScannedFile(BaseModel):
    """扫描到的一个条目 (文件或目录)。"""

    path: str                                  # 绝对路径 (仅本地登记; 对外用 display_path)
    size: int = 0
    file_hash: str = ""                        # 扫描阶段留空, 导入时计算
    kind: Literal["file", "dir"] = "file"
    status: FileStatus = FileStatus.ok
    reason: str = ""
    mtime: float = 0.0
    #: 是否位于授权根内 (拒绝项也带上, 便于界面解释"为什么被拒")。
    authorized_root: str = ""
    symlink: bool = False

    def display_path(self) -> str:
        """对外展示用的路径 (只有 basename) —— 不外发完整绝对路径。"""
        return display_path(self.path)

    @property
    def usable(self) -> bool:
        return self.status == FileStatus.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.display_path(),
            "size": self.size,
            "file_hash": self.file_hash,
            "kind": self.kind,
            "status": self.status.value,
            "reason": self.reason,
            "mtime": self.mtime,
            "authorized_root": self.authorized_root,
            "symlink": self.symlink,
        }


def display_path(path: str | Path) -> str:
    """界面与日志只展示 basename (合并计划 §13.3 规则 4)。"""
    text = str(path or "")
    if not text:
        return ""
    return Path(text).name or text


class ScanRequest(BaseModel):
    """一次路径扫描/导入请求。"""

    paths: list[str] = Field(default_factory=list)      # 文件或文件夹, 逐条
    recursive: bool = True
    suffixes: list[str] = Field(default_factory=lambda: sorted(ALLOWED_SUFFIXES))
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    label: str = ""                                     # 用户可读的库名
    #: 追加授权根 (等价于 DATA_READ_ROOTS 的按任务版本; 由维护者/运行时注入,
    #: 不是模型可以自行扩权的地方)。
    allow_roots: list[str] = Field(default_factory=list)

    def normalized_suffixes(self) -> set[str]:
        out: set[str] = set()
        for item in self.suffixes or []:
            text = str(item or "").strip().lower()
            if not text:
                continue
            out.add(text if text.startswith(".") else f".{text}")
        return out or set(ALLOWED_SUFFIXES)

    def roots(self) -> list[Path]:
        return _effective_roots(self.allow_roots)


class ScanReport(BaseModel):
    """扫描结果 (只预览, 不导入)。"""

    files: list[ScannedFile] = Field(default_factory=list)
    denied: list[ScannedFile] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    truncated: bool = False
    #: 逐条输入路径的处置说明 (输入不存在/是通配但无命中, 都要如实说明)。
    notes: list[str] = Field(default_factory=list)

    @property
    def ok_files(self) -> list[ScannedFile]:
        return [f for f in self.files if f.status == FileStatus.ok]

    def counts(self) -> dict[str, int]:
        counts = {"ok": 0, "skipped": 0, "denied": len(self.denied),
                  "duplicate": 0, "unreadable": 0}
        for item in self.files:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return counts

    def total_bytes(self) -> int:
        return sum(f.size for f in self.files if f.status == FileStatus.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [f.to_dict() for f in self.files],
            "denied": [f.to_dict() for f in self.denied],
            "roots": list(self.roots),
            "truncated": self.truncated,
            "notes": list(self.notes),
            "counts": self.counts(),
            "total_bytes": self.total_bytes(),
        }


class PathImportRef(BaseModel):
    """一个已登记文件的来源引用 (原文件被移动/删除时标记 stale, 不静默丢失)。"""

    path: str
    doc_id: str = ""
    file_hash: str = ""
    size: int = 0
    mtime: float = 0.0
    imported_at: str = Field(default_factory=get_timestamp)
    missing_since: str = ""
    batch_id: str = ""

    @property
    def stale(self) -> bool:
        return bool(self.missing_since)

    def display_path(self) -> str:
        return display_path(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.display_path(),
            "doc_id": self.doc_id,
            "file_hash": self.file_hash,
            "size": self.size,
            "mtime": self.mtime,
            "imported_at": self.imported_at,
            "missing_since": self.missing_since,
            "batch_id": self.batch_id,
            "stale": self.stale,
        }


class ImportedRecord(BaseModel):
    """单条导入结果 (新增 / 合并 / 跳过 / 拒绝 / 失败逐条可见)。"""

    path: str
    doc_id: str = ""
    title: str = ""
    status: FileStatus = FileStatus.ok
    reason: str = ""
    file_hash: str = ""
    size: int = 0
    pages: int = 0
    cards: int = 0
    parse_quality: str = ""
    #: 本次是否新建了文献 (False 表示命中既有身份并合并来源)。
    created: bool = False

    def display_path(self) -> str:
        return display_path(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.display_path(),
            "doc_id": self.doc_id,
            "title": self.title,
            "status": self.status.value,
            "reason": self.reason,
            "file_hash": self.file_hash,
            "size": self.size,
            "pages": self.pages,
            "cards": self.cards,
            "parse_quality": self.parse_quality,
            "created": self.created,
        }


class ImportReport(BaseModel):
    """导入结果汇总。

    **部分失败必须显式返回**: `failed`/`denied` 逐条列出, 调用方不得把它当整体成功。
    """

    topic: str = ""
    label: str = ""
    batch_id: str = ""
    origin: str = IMPORT_ORIGIN
    roots: list[str] = Field(default_factory=list)
    imported: list[ImportedRecord] = Field(default_factory=list)    # 新建
    merged: list[ImportedRecord] = Field(default_factory=list)      # 合并到既有文献
    skipped: list[ImportedRecord] = Field(default_factory=list)
    denied: list[ImportedRecord] = Field(default_factory=list)
    failed: list[ImportedRecord] = Field(default_factory=list)
    duplicates: list[ImportedRecord] = Field(default_factory=list)  # 同 hash 同库幂等命中
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """整体是否"完全成功": 有任何拒绝/失败/截断即为 False。"""
        return not (self.denied or self.failed or self.truncated)

    def counts(self) -> dict[str, int]:
        return {
            "imported": len(self.imported),
            "merged": len(self.merged),
            "duplicates": len(self.duplicates),
            "skipped": len(self.skipped),
            "denied": len(self.denied),
            "failed": len(self.failed),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "label": self.label,
            "batch_id": self.batch_id,
            "origin": self.origin,
            "roots": list(self.roots),
            "imported": [r.to_dict() for r in self.imported],
            "merged": [r.to_dict() for r in self.merged],
            "skipped": [r.to_dict() for r in self.skipped],
            "denied": [r.to_dict() for r in self.denied],
            "failed": [r.to_dict() for r in self.failed],
            "duplicates": [r.to_dict() for r in self.duplicates],
            "truncated": self.truncated,
            "notes": list(self.notes),
            "counts": self.counts(),
            "ok": self.ok,
        }


# ----------------------------------------------------------------------
# 授权与敏感判定
# ----------------------------------------------------------------------
def _resolve_root(raw: str) -> Path | None:
    """把一条追加授权根解析为绝对路径; 无效/空返回 None (不是异常)。

    逐条跳过而不是整体失败: 一条写错的本机路径不应让其余授权根失效。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return Path(text).resolve()
    except Exception:  # noqa: BLE001 - 无效根被忽略并如实反映在 roots 里
        return None


def _effective_roots(extra: Iterable[str] = ()) -> list[Path]:
    roots: list[Path] = list(authorized_roots())
    roots.extend(resolved for raw in (extra or ())
                 if (resolved := _resolve_root(raw)) is not None)
    # 去重但保序
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _root_of(target: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        return root
    return None


def _sensitive_reason(path: Path) -> str:
    """敏感目标判定 (在被授权根内也要拒绝)。"""
    name = path.name
    lower = name.lower()
    if lower in (".env", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials"):
        return f"敏感文件 {name!r} 一律拒绝导入"
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return f"敏感扩展名 {path.suffix.lower()!r} (凭据/私钥类) 一律拒绝导入"
    for pattern in SENSITIVE_NAME_PATTERNS:
        if fnmatch.fnmatch(lower, pattern):
            return f"文件名 {name!r} 命中敏感规则 {pattern!r}"
    for part in path.parts:
        if part.lower() in SENSITIVE_DIR_NAMES:
            return f"路径中含敏感目录 {part!r}"
    return ""


def _iter_candidates(raw: str, request: ScanRequest) -> tuple[list[Path], list[str]]:
    """把一条输入展开为候选文件路径; 返回 (文件, 说明)。"""
    notes: list[str] = []
    text = str(raw or "").strip().strip('"')
    if not text:
        return [], ["输入为空条目, 已忽略"]
    # 通配便利能力: 语义与"目录递归"一致, 只是写法更短
    if any(ch in text for ch in "*?["):
        base = Path(text)
        anchor = base.anchor or "."
        try:
            matches = sorted(Path(anchor).glob(str(base.relative_to(anchor))))
        except Exception as e:  # noqa: BLE001
            return [], [f"通配 {display_path(text)!r} 无法展开: {e}"]
        files = [m for m in matches if m.is_file()]
        if not files:
            notes.append(f"通配 {display_path(text)!r} 没有命中任何文件")
        return files, notes

    candidate = Path(text)
    if not candidate.exists():
        return [], [f"路径不存在: {display_path(text)}"]
    if candidate.is_file():
        return [candidate], notes
    if not candidate.is_dir():
        return [], [f"路径既不是文件也不是目录: {display_path(text)}"]
    if request.recursive:
        try:
            files = sorted(p for p in candidate.rglob("*") if p.is_file())
        except Exception as e:  # noqa: BLE001
            return [], [f"目录 {display_path(text)!r} 递归失败: {e}"]
    else:
        try:
            files = sorted(p for p in candidate.iterdir() if p.is_file())
        except Exception as e:  # noqa: BLE001
            return [], [f"目录 {display_path(text)!r} 无法列举: {e}"]
    if not files:
        notes.append(f"目录 {display_path(text)!r} 下没有文件")
    return files, notes


def scan_paths(request: ScanRequest) -> ScanReport:
    """扫描路径, 返回**预览**报告 (不写入任何库)。

    逐条给出 `ok / skipped / denied / unreadable` 与原因; 拒绝项单独列出,
    不做静默丢弃。
    """
    roots = request.roots()
    suffixes = request.normalized_suffixes()
    report = ScanReport(roots=[str(r) for r in roots])
    seen_paths: set[str] = set()
    total_bytes = 0

    for raw in request.paths:
        files, notes = _iter_candidates(raw, request)
        report.notes.extend(notes)
        for path in files:
            key = str(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            entry = _classify(path, roots, suffixes)
            if entry.status == FileStatus.ok:
                if len(report.files) >= max(1, request.max_files):
                    report.truncated = True
                    continue
                if total_bytes + entry.size > request.max_bytes:
                    report.truncated = True
                    entry.status = FileStatus.skipped
                    entry.reason = (
                        f"体量上限 {_human_bytes(request.max_bytes)} 已到, 本文件未纳入"
                    )
                    report.files.append(entry)
                    continue
                total_bytes += entry.size
            if entry.status == FileStatus.denied:
                report.denied.append(entry)
            else:
                report.files.append(entry)
    if report.truncated:
        report.notes.append(
            f"已达上限 (最多 {request.max_files} 个文件 / {_human_bytes(request.max_bytes)}), "
            f"存在未纳入的文件 —— 报告如实标记 truncated"
        )
    return report


def _classify(path: Path, roots: list[Path], suffixes: set[str]) -> ScannedFile:
    """给单个候选文件定级 (denied / skipped / unreadable / ok)。"""
    entry = ScannedFile(path=str(path), kind="file")

    def deny(reason: str) -> ScannedFile:
        entry.status = FileStatus.denied
        entry.reason = reason
        return entry

    try:
        entry.symlink = path.is_symlink()
        resolved = path.resolve()
    except OSError as e:
        entry.status = FileStatus.unreadable
        entry.reason = f"路径无法解析: {e}"
        return entry

    root = _root_of(resolved, roots)
    if root is None:
        return deny(
            "不在授权读取范围内 (只允许 data/ 或 outputs/ 之下, 或由 DATA_READ_ROOTS "
            "显式放行); 需由维护者显式放行"
        )
    entry.authorized_root = str(root)

    # 符号链接: 解析后的真实路径必须在授权根内 (上面已判), 但它指向敏感目标仍拒绝
    sensitive = _sensitive_reason(resolved)
    if sensitive:
        return deny(sensitive)
    if path.is_symlink() and _sensitive_reason(path):
        return deny(f"符号链接指向敏感目标: {display_path(path)}")

    if path.suffix.lower() not in suffixes:
        entry.status = FileStatus.skipped
        entry.reason = (f"后缀 {path.suffix.lower() or '(无)'} 不在白名单 "
                        f"({', '.join(sorted(suffixes))})")
        try:
            entry.size = path.stat().st_size
        except OSError:
            entry.size = 0
        return entry

    try:
        stat = path.stat()
    except OSError as e:
        entry.status = FileStatus.unreadable
        entry.reason = f"无法读取文件属性: {e}"
        return entry
    entry.size = int(stat.st_size)
    entry.mtime = float(stat.st_mtime)
    if not os.access(path, os.R_OK):
        entry.status = FileStatus.unreadable
        entry.reason = "文件不可读 (权限不足)"
        return entry
    return entry


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# ----------------------------------------------------------------------
# 登记记录 (引用 + hash + mtime; 不复制文件)
# ----------------------------------------------------------------------
def path_refs_file(topic: str) -> Path:
    return topic_dir(topic) / "path_refs.json"


def load_path_refs(topic: str) -> list[PathImportRef]:
    """读取该库已登记的路径引用 (不存在时返回空列表, 不建空文件)。"""
    target = path_refs_file(topic)
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 登记文件损坏时如实放弃, 不假装没有
        return []
    items = raw.get("refs") if isinstance(raw, dict) else raw
    # 单条登记损坏不丢掉其余引用 (逐条还原, 坏的那条不进结果)
    return [ref for ref in (_load_ref(item) for item in items or []) if ref is not None]


def _load_ref(item: Any) -> PathImportRef | None:
    try:
        return PathImportRef(**item)
    except Exception:  # noqa: BLE001 - 登记项结构与当前模型不符时跳过
        return None


def _save_path_refs(topic: str, refs: list[PathImportRef]) -> None:
    target = path_refs_file(topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"topic": topic, "updated_at": get_timestamp(),
               "refs": [r.model_dump() for r in refs]}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_missing(topic: str, *, save: bool = True) -> list[PathImportRef]:
    """检查已登记文件是否仍在原处; 消失的标记 `missing_since`。

    返回**当前所有**引用 (含刚被标记 stale 的)。原文件回来时清除标记 —— 但内容
    变化要靠 hash 比对, 这里只处理"不见了"。
    """
    refs = load_path_refs(topic)
    changed = False
    for ref in refs:
        exists = False
        try:
            exists = Path(ref.path).is_file()
        except OSError:
            exists = False
        if not exists and not ref.missing_since:
            ref.missing_since = get_timestamp()
            changed = True
        elif exists and ref.missing_since:
            ref.missing_since = ""
            changed = True
    if changed and save:
        _save_path_refs(topic, refs)
    return refs


# ----------------------------------------------------------------------
# 导入
# ----------------------------------------------------------------------
@dataclass
class _ImportContext:
    topic: str
    batch_id: str
    store: KBStore
    embed: bool
    refs: list[PathImportRef]


def import_paths(store: KBStore | None, topic: str, request: ScanRequest,
                 embed: bool = False) -> ImportReport:
    """按 `ScanRequest` 导入; 逐条返回结果, 部分失败显式呈现。

    - 幂等: 同一路径 + 同一 hash 再次导入命中既有身份, 不重复解析与计费;
    - 同一 hash 出现在多个路径 → 同一文献的多个 `PathImportRef`, 文献只入库一次;
    - 只读: 不改动被导入文件 (校验项见模块 docstring)。
    """
    name = (topic or request.label or "").strip()
    batch_id = f"batch-{get_timestamp().replace(' ', 'T').replace(':', '')}"
    report = ImportReport(topic=name, label=request.label or name, batch_id=batch_id,
                          roots=[str(r) for r in request.roots()])
    if not name:
        report.notes.append("未指定库名 (topic/label 均为空), 无法导入")
        return report

    scan = scan_paths(request)
    report.truncated = scan.truncated
    report.notes.extend(scan.notes)

    # 拒绝项: 逐条如实返回, 不给可执行暗示
    for item in scan.denied:
        report.denied.append(ImportedRecord(
            path=item.path, status=FileStatus.denied, reason=item.reason,
            size=item.size))
    for item in scan.files:
        if item.status == FileStatus.skipped:
            report.skipped.append(ImportedRecord(
                path=item.path, status=FileStatus.skipped, reason=item.reason,
                size=item.size))
        elif item.status == FileStatus.unreadable:
            report.failed.append(ImportedRecord(
                path=item.path, status=FileStatus.unreadable, reason=item.reason,
                size=item.size))

    if store is None:
        store = KBStore(name)
    ctx = _ImportContext(topic=name, batch_id=batch_id, store=store, embed=embed,
                         refs=load_path_refs(name))
    by_path = {ref.path: ref for ref in ctx.refs}

    for item in scan.ok_files:
        record = _import_one(ctx, Path(item.path), report)
        if record is None:
            continue
        # 登记/更新路径引用 (同一路径只保留一条, 重复导入幂等)
        prior = by_path.get(item.path)
        ref = PathImportRef(
            path=item.path, doc_id=record.doc_id, file_hash=record.file_hash,
            size=record.size, mtime=item.mtime, batch_id=batch_id)
        if prior is not None:
            ref.imported_at = prior.imported_at
        by_path[item.path] = ref

    ctx.refs = list(by_path.values())
    _save_path_refs(name, ctx.refs)
    report.notes.append(f"已登记 {len(ctx.refs)} 条路径引用 (不复制原文件)")
    return report


def _import_one(ctx: _ImportContext, path: Path,
                report: ImportReport) -> ImportedRecord | None:
    """导入单个文件; 失败按类型登记并返回 None。"""
    text = str(path)
    file_hash = compute_file_hash(text)
    if not file_hash:
        report.failed.append(ImportedRecord(
            path=text, status=FileStatus.unreadable, reason="无法计算文件 hash"))
        return None
    try:
        size = int(path.stat().st_size)
    except OSError:
        size = 0

    from src.rag.manual_pdfs import _parse_filename

    title, year = _parse_filename(path.name)
    info: dict[str, Any] = {
        "title": title or path.stem,
        "year": str(year or ""),
        "language": _guess_language(path.name),
    }
    identity = build_identity(info, file_hash)
    keys = candidate_keys(identity)
    existing = ctx.store.find_by_identity(keys)
    doc_id = existing or resolve_doc_id(keys)
    is_new = existing is None

    # 同一 hash 已在库中: 幂等命中, 不重复解析 (这是"不重复计费"的关键)
    if not is_new:
        prior = ctx.store.get_document(doc_id) or {}
        prior_hash = str((prior.get("identity") or {}).get("file_hash", "") or "")
        if prior_hash and prior_hash == file_hash and prior.get("has_fulltext"):
            ctx.store.add_provenance(doc_id, [Provenance(
                origin=IMPORT_ORIGIN,
                detail=f"{path.name}#{ctx.batch_id}",
                file=text, fetched_at=get_timestamp())])
            ctx.store.append_event("ingest_manual",
                                   {"doc_id": doc_id, "file": path.name,
                                    "origin": IMPORT_ORIGIN, "idempotent": True})
            report.duplicates.append(ImportedRecord(
                path=text, doc_id=doc_id, title=str(prior.get("title", "")),
                status=FileStatus.duplicate, created=False, file_hash=file_hash,
                size=size, reason="同一内容已入库 (幂等命中, 未重复解析)"))
            return ImportedRecord(
                path=text, doc_id=doc_id, title=str(prior.get("title", "")),
                status=FileStatus.ok, created=False, file_hash=file_hash, size=size,
                pages=int(prior.get("num_pages", 0) or 0),
                cards=len(ctx.store.get_cards(doc_id)))

    try:
        parsed = parse_document(text, doc_id=doc_id)
    except Exception as e:  # noqa: BLE001 - 单文件失败不中断其余文件
        report.failed.append(ImportedRecord(
            path=text, doc_id=doc_id, status=FileStatus.unreadable, size=size,
            file_hash=file_hash, reason=f"解析失败: {e}"))
        return None

    from src.kb.ingest import (
        _build_search_text,
        _chunks_from_sections,
        _credibility,
        _doc_type_from,
    )

    doc_type = _doc_type_from(info)
    peer, credibility = _credibility(doc_type, manual=True)
    prior = ctx.store.get_document(doc_id) or {}
    record = LitRecord(
        doc_id=doc_id,
        title=info["title"] or path.stem,
        year=info["year"],
        doc_type=doc_type,
        language=info.get("language", "") or parsed.language,
        manual_asserted=True, existence_verified=True,
        peer_reviewed=peer, credibility=credibility,
        parse_quality=parsed.parse_quality,
        num_pages=len(parsed.pages) or (1 if parsed.full_text else 0),
        has_fulltext=bool(parsed.full_text),
        identity=identity,
        visibility_flags=list(parsed.visibility_flags),
    )
    if prior.get("created_at"):
        record.created_at = str(prior["created_at"])
    ctx.store.upsert_document(
        record, search_text=_build_search_text(
            record, "\n".join(s.text for s in parsed.sections)))
    ctx.store.set_identity(keys, doc_id)
    ctx.store.add_provenance(doc_id, [Provenance(
        origin=IMPORT_ORIGIN, detail=f"{path.name}#{ctx.batch_id}",
        file=text, fetched_at=get_timestamp())])

    cards = 0
    if parsed.full_text:
        ctx.store.add_sections(parsed.sections)
        chunks = _chunks_from_sections(doc_id, parsed.sections)
        ctx.store.add_chunks(chunks)
        extracted = extract_cards(doc_id, parsed)
        if extracted:
            ctx.store.add_cards(extracted)
        cards = len(extracted)
        from src.kb.ingest import _embed_chunks

        _embed_chunks(ctx.topic, doc_id, chunks, record, ctx.embed)
    ctx.store.append_event("ingest_manual",
                           {"doc_id": doc_id, "file": path.name,
                            "origin": IMPORT_ORIGIN, "quality": parsed.parse_quality})
    entry = ImportedRecord(
        path=text, doc_id=doc_id, title=record.title, status=FileStatus.ok,
        file_hash=file_hash, size=size, pages=record.num_pages, cards=cards,
        parse_quality=parsed.parse_quality, created=is_new,
        reason="" if parsed.full_text else "已登记元数据但未取得正文 (无全文)",
    )
    (report.imported if is_new else report.merged).append(entry)
    return entry


def _guess_language(name: str) -> str:
    """文件名含中文即标 zh, 否则留空 (不猜)。"""
    return "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in name) else ""


# ----------------------------------------------------------------------
# 库级操作
# ----------------------------------------------------------------------
def library_summary(topic: str) -> dict[str, Any]:
    """单个路径导入库的可查信息: 来源类型、根目录、文件数、hash 清单、失效文件。

    **不返回任何绝对路径** —— 只给 basename, 与 §13.3 规则 4 一致。
    """
    name = (topic or "").strip()
    if not name:
        return {"source_set_id": "", "readable": False, "note": "未指定资料库"}
    db_path = topic_dir(name) / "kb.sqlite"
    if not db_path.exists():
        return {"source_set_id": name, "readable": False,
                "note": f"资料库不存在: 没有 {db_path.name}"}
    refs = detect_missing(name)
    store = KBStore(name, db_path=db_path, create_if_missing=False)
    try:
        stats = store.stats()
    finally:
        store.close()
    return {
        "source_set_id": name,
        "origin": IMPORT_ORIGIN if refs else "managed",
        "readable": True,
        "roots": sorted({str(Path(r.path).parent) for r in refs if r.path}) if refs else [],
        "documents": int(stats.get("documents", 0)),
        "cards": int(stats.get("cards", 0)),
        "files": len(refs),
        "stale_files": [r.display_path() for r in refs if r.stale],
        "hashes": [{"file": r.display_path(), "file_hash": r.file_hash,
                    "doc_id": r.doc_id, "stale": r.stale} for r in refs],
    }


def unregister_library(topic: str, *, delete_records: bool = False) -> dict[str, Any]:
    """解除**该库**的登记。

    默认只清路径引用 (原始文件与共享向量库不受影响)。`delete_records=True` 时同时
    删除该库自身的 kb.sqlite —— 仍然是**库级**操作, 不动用户原文件, 不动共享向量库。
    """
    name = (topic or "").strip()
    if not name:
        return {"ok": False, "reason": "未指定资料库"}
    refs = load_path_refs(name)
    target = path_refs_file(name)
    removed_refs = 0
    if target.exists():
        try:
            target.unlink()
            removed_refs = len(refs)
        except OSError as e:
            return {"ok": False, "reason": f"解除登记失败: {e}", "removed_refs": 0}
    removed_db = False
    if delete_records:
        db_path = topic_dir(name) / "kb.sqlite"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    continue
        removed_db = not db_path.exists()
    return {"ok": True, "reason": "", "removed_refs": removed_refs,
            "removed_records": removed_db,
            "note": "仅解除库登记; 用户原文件与共享向量库未被修改"}


def sanitized_topic(label: str) -> str:
    """把用户可读的库名转成安全的主题目录名 (与 KB 目录规则一致)。"""
    return sanitize_filename((label or "").strip()) or "path-import"
