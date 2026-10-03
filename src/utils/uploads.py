from __future__ import annotations

"""上传附件 (问题说明 / 人工补充文献)。

R1: 列表按**用途**分别过滤再合并 —— 问题附件只有项目字段、文献附件只有主题字段,
    用 (project_id AND topic) 做交集会让两个输入同时有值时列表恒为空。
R3: 附件 ID 由「用途 + 作用域 + 内容摘要」派生 (跨项目不碰撞); 读取/列表/删除/绑定
    一律校验归属; 路径组件做安全处理并确保落在上传根内。
R7: 先按声明大小拒绝, 再**分块流式落盘**; 同时限制文件数与单请求总量。
    声明大小缺失或与实际不符时由流式上限兜底; 任何失败路径都不留临时文件。
"""

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".docx"}
MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 20
MAX_REQUEST_BYTES = 200 * 1024 * 1024
# R4: 附件作为"候选要求"进入提示词的上限 (全文按相关片段有界传入, 不整份塞入)
MAX_ATTACHMENT_CHARS = 8000
KINDS = ("problem", "literature")
# R7: 单块进内存的大小 —— 太小会让大文件退化成大量 syscall, 太大则抬高内存峰值
CHUNK_BYTES = 1024 * 1024


class UploadTooLarge(Exception):
    """流式落盘时累计字节数超过单文件上限 (声明大小不可信或缺失)。

    调用方按类型即可识别并取到与 `check()` 同款的用户可读说明, 不必解析字符串。
    """

    def __init__(self, limit: int, seen: int):
        self.limit = limit
        self.seen = seen
        super().__init__(_too_large_message(seen, limit))


class UploadRequestTooLarge(UploadTooLarge):
    """本次请求的**实际**字节总量超过上限。

    声明大小可以撒谎, 因此请求级上限也必须按实际读到的字节数统计; 否则一批
    声明为 0 的大文件仍能把磁盘与请求预算撑爆。
    """

    def __init__(self, limit: int, seen: int):
        super().__init__(limit, seen)

    def __str__(self) -> str:
        return (f"本次上传总量过大 ({_size_text(self.seen)}), "
                f"上限 {_size_text(self.limit)}")


def _size_text(size: int) -> str:
    """人类可读的字节数: 低于 1 MB 时用 KB, 避免把 8 KiB 显示成 "0.0 MB"。"""
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.1f} KB"


@dataclass
class ByteBudget:
    """一次请求内**实际**可写入的字节预算 (声明值之外的独立上限)。"""

    limit: int = MAX_REQUEST_BYTES
    used: int = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def charge(self, size: int) -> None:
        self.used += int(size)

    def affordable(self, size: int) -> int:
        """本次最多还能读多少字节 (不超过单块上限)。"""
        return min(int(size), self.limit - self.used)


def _too_large_message(size: int, limit: int | None = None) -> str:
    cap = MAX_BYTES if limit is None else limit
    return f"文件过大 ({_size_text(size)}), 上限 {_size_text(cap)}"


def _config():
    from src import config

    return config


def upload_root() -> Path:
    return _config().DATA_DIR / "uploads"


def registry_path() -> Path:
    return upload_root() / "attachments.json"


def tmp_root() -> Path:
    """流式落盘的暂存目录: 与最终存放同处上传根之下, 便于整目录清理与归属判断。"""
    return upload_root() / "tmp"


def _safe_component(value: str, fallback: str = "default") -> str:
    """把用作路径片段的输入限制为安全字符; 拒绝 `..`、分隔符与绝对路径。"""
    text = str(value or "").strip().replace("\\", "/")
    text = text.split("/")[-1] if "/" in text else text
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "-_. ")
    cleaned = cleaned.strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:64]


def _safe_name(filename: str) -> str:
    return _safe_component(Path(str(filename or "upload")).name, "upload")


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def attachment_id(kind: str, scope: str, digest: str) -> str:
    """跨项目不碰撞: 用途 + 作用域 + 内容摘要共同决定 ID。"""
    seed = f"{kind}|{_safe_component(scope)}|{digest}".encode()
    return "att-" + hashlib.sha256(seed).hexdigest()[:16]


def load() -> list[dict]:
    path = registry_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save(items: list[dict]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def check(filename: str, size: int) -> str:
    """返回错误说明; 空串表示通过。R7: 用上传声明的大小, 读取前即可判定。"""
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = " / ".join(sorted(ALLOWED_SUFFIXES))
        return f"不支持的文件类型 {suffix or '(无扩展名)'}, 仅支持 {allowed}"
    if size <= 0:
        return "空文件"
    if size > MAX_BYTES:
        return _too_large_message(size)
    return ""


def declared_size(upload_file) -> int | None:
    """取客户端声明的大小; 缺失时返回 None。

    None 必须与 0 区分: 0 是"空文件"(读取前即可拒绝), 而缺失只能交给流式上限兜底。
    """
    size = getattr(upload_file, "size", None)
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    return size


def check_batch(count: int, declared_total: int) -> str:
    if count <= 0:
        return "没有收到文件"
    if count > MAX_FILES:
        return f"一次最多上传 {MAX_FILES} 个文件"
    if declared_total > MAX_REQUEST_BYTES:
        return (f"本次上传总量过大 ({_size_text(declared_total)}), "
                f"上限 {_size_text(MAX_REQUEST_BYTES)}")
    return ""


def _register(record: dict) -> dict:
    items = [i for i in load() if i.get("attachment_id") != record["attachment_id"]]
    items.append(record)
    save(items)
    return record


def _record(kind: str, scope: str, filename: str, stored: Path, size: int,
            digest: str, **extra) -> dict:
    return {
        "attachment_id": attachment_id(kind, scope, digest), "kind": kind,
        "project_id": scope if kind == "problem" else "",
        "problem_id": extra.pop("problem_id", ""),
        "topic": scope if kind == "literature" else "",
        "filename": _safe_name(filename), "size": size, "sha256": digest,
        "short_hash": digest[:12], "path": str(stored), **extra,
    }


def _discard(path: Path) -> None:
    """删除已无归属的暂存文件; 删除失败不影响调用方要报告的结果。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _digest_file(path: Path) -> tuple[int, str]:
    """按块算已落盘文件的 (大小, sha256): 复核落盘结果时同样不整份进内存。"""
    size = 0
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _adopt(source: Path, target: Path) -> None:
    """把暂存文件搬到最终位置; 同盘 rename 免去再拷一份。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:  # 跨卷时 rename 不可用, 退回流式搬运
        shutil.move(str(source), str(target))


def _stream_into(stream, sink, limit: int, budget: ByteBudget | None = None) -> tuple[int, str]:
    """边读边写边算摘要; 超过 limit 立即停下, 不把剩余内容读进内存。

    给了 `budget` 时同时按**实际**字节数扣减请求级预算: 声明大小不可信,
    因此请求级上限不能只靠声明值求和。判定顺序是先读一块再记账, 这样
    "预算刚好用尽但文件已结束"不会被误报成超限。
    """
    size = 0
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            break
        if budget is not None:
            if budget.affordable(len(chunk)) < len(chunk):
                # 报"已用满 + 还有内容待读", 而不是把本块也算进去 (会显示成超过上限几倍)
                raise UploadRequestTooLarge(budget.limit, budget.used + 1)
            budget.charge(len(chunk))
        size += len(chunk)
        if size > limit:
            raise UploadTooLarge(limit, size)
        digest.update(chunk)
        sink.write(chunk)
    return size, digest.hexdigest()


@dataclass(frozen=True)
class StagedUpload:
    """流式落盘的产物: 暂存路径 + 边写边算出的字节数与摘要。"""

    path: Path
    size: int
    sha256: str


def _upload_stream(upload_file):
    """拿到可读的二进制流: FastAPI 的 UploadFile 用 `.file`, 其余对象自身即流。"""
    stream = getattr(upload_file, "file", None)
    if not hasattr(stream, "read"):
        stream = upload_file
    try:
        stream.seek(0)
    except (AttributeError, OSError, ValueError):
        pass
    return stream


@contextlib.contextmanager
def staged_upload(upload_file, declared_size: int | None = None,
                  budget: ByteBudget | None = None):
    """R7: 分块流式落盘, 全程不把整份上传内容放进内存。

    成功时把暂存路径交给调用方, 由调用方最终落盘或删除 (`_adopt` / `_discard`);
    超限、读写出错或调用方中途失败都会删掉暂存文件, 不留无归属的残留。
    `budget` 用于跨文件累计**实际**字节数 (声明大小不可信时的请求级上限);
    预算用尽时读到的文件会被判为 `UploadRequestTooLarge`, 而不是伪装成"文件过大"。
    """
    limit = MAX_BYTES
    if declared_size is not None and declared_size > limit:
        # 声明值即使不可信, 也没必要先落一份盘再拒
        raise UploadTooLarge(limit, int(declared_size))
    if budget is not None and budget.remaining() <= 0:
        # 预算已用尽: 空文件仍可收下 (0 字节), 有内容的一律按请求级超限拒绝
        probe = _upload_stream(upload_file).read(1)
        if probe:
            raise UploadRequestTooLarge(budget.limit, budget.used)
    stream = _upload_stream(upload_file)
    staging = tmp_root()
    staging.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=str(staging), prefix="staged-", suffix=".part")
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as sink:
            size, digest = _stream_into(stream, sink, limit, budget)
    except BaseException:
        _discard(path)
        raise
    try:
        yield StagedUpload(path=path, size=size, sha256=digest)
    except BaseException:
        _discard(path)
        raise


def save_problem_attachment(project_id: str, problem_id: str, filename: str,
                            staged: str | Path) -> dict:
    """保存"补充问题说明"附件: 原件 + 解析文本 + sha256 溯源。

    R7: 入参是**已落盘的暂存路径** (分块流式写入的结果), 不再接收整份 bytes;
    该路径由本函数接管 —— 落盘则搬走, 其余失败路径一律删除。
    """
    source = Path(staged)
    size, digest = _digest_file(source)
    error = check(filename, size)
    if error:
        _discard(source)
        return {"ok": False, "filename": filename, "error": error}
    project = _safe_component(project_id, "")
    if not project:
        _discard(source)
        return {"ok": False, "filename": filename,
                "error": "缺少项目身份: 请先填写或生成项目 ID 再上传问题说明"}
    key = attachment_id("problem", project, digest)
    for item in load():
        if item.get("attachment_id") == key:
            _discard(source)
            return {"ok": True, "attachment": {k: v for k, v in item.items() if k != "text"},
                    "deduplicated": True, "filename": filename}

    root = upload_root() / project
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{digest[:12]}_{_safe_name(filename)}"
    if not _inside(upload_root(), target):
        _discard(source)
        return {"ok": False, "filename": filename, "error": "非法路径"}
    _adopt(source, target)

    from src.kb.parse import parse_document

    text, quality, flags = "", "failed", []
    try:
        parsed = parse_document(str(target))
        text, quality, flags = parsed.full_text, parsed.parse_quality, parsed.visibility_flags
    except Exception as e:  # noqa: BLE001 - 解析失败保留原件并如实报告
        quality, flags = f"failed: {type(e).__name__}", []

    record = _record("problem", project, filename, target, size, digest,
                     problem_id=str(problem_id or ""), parse_quality=quality,
                     chars=len(text), visibility_flags=flags, text=text)
    _register(record)
    return {"ok": True, "attachment": {k: v for k, v in record.items() if k != "text"},
            "filename": filename}


def save_literature(topic: str, filename: str, staged: str | Path) -> dict:
    """保存"人工补充文献": 复制进资料库主题后入库, 立即可检索。

    R7: 同 `save_problem_attachment`, 入参是暂存路径并由本函数接管。
    """
    source = Path(staged)
    size, digest = _digest_file(source)
    error = check(filename, size)
    if error:
        _discard(source)
        return {"ok": False, "filename": filename, "error": error}
    scope = _safe_component(topic, "")
    if not scope:
        _discard(source)
        return {"ok": False, "filename": filename, "error": "补充文献需要指定资料库主题"}
    from src.kb.ingest import ensure_topic, ingest_manual

    topic_dir = ensure_topic(scope)
    manual = topic_dir / "manual"
    manual.mkdir(parents=True, exist_ok=True)
    target = manual / _safe_name(filename)
    if target.exists() and _digest_file(target)[1] != digest:
        target = manual / f"{digest[:8]}_{_safe_name(filename)}"
    if target.exists():
        _discard(source)
    else:
        _adopt(source, target)

    try:
        stats = ingest_manual(scope, embed=False)
    except Exception as e:  # noqa: BLE001 - 保留文件, 让用户看到失败原因
        return {"ok": False, "filename": filename,
                "error": f"入库失败: {type(e).__name__}: {e}", "path": str(target)}

    doc = {}
    try:
        from src.kb.store import KBStore

        documents = KBStore(scope).list_documents()
        match = [d for d in documents
                 if str((d.get("identity") or {}).get("file_hash", "")).startswith(digest[:12])]
        if not match:
            stem = Path(_safe_name(filename)).stem
            match = [d for d in documents if stem and stem in str(d.get("title", ""))]
        if not match and documents:
            match = [max(documents, key=lambda d: d.get("updated_at", ""))]
        if match:
            newest = match[0]
            doc = {"doc_id": newest.get("doc_id", ""), "title": newest.get("title", ""),
                   "parse_quality": newest.get("parse_quality", ""),
                   "has_fulltext": bool(newest.get("has_fulltext")),
                   "visibility_flags": newest.get("visibility_flags") or []}
    except Exception:  # noqa: BLE001 - 查询失败不影响入库结果
        doc = {}

    record = _record("literature", scope, filename, target, size, digest,
                     doc_id=doc.get("doc_id", ""), parse_quality=doc.get("parse_quality", ""),
                     stats=stats)
    _register(record)
    return {"ok": True, "filename": filename, "document": doc, "stats": stats,
            "attachment": {k: v for k, v in record.items() if k != "stats"}}


def list_for(project_id: str = "", topic: str = "", kind: str = "",
             problem_id: str = "") -> list[dict]:
    """按用途分别过滤后**合并** (R1)。

    只给 project_id → 该项目的问题附件; 只给 topic → 该主题的文献附件;
    两者都给 → 两者的并集; kind 显式给出时只返回该用途。
    """
    project = _safe_component(project_id, "") if project_id else ""
    scope = _safe_component(topic, "") if topic else ""
    problem = _safe_component(problem_id, "") if problem_id else ""
    out: list[dict] = []
    for item in load():
        if kind and item.get("kind") != kind:
            continue
        if problem and item.get("problem_id") != problem:
            continue
        if project and item.get("project_id") == project:
            out.append(item)
            continue
        if scope and item.get("topic") == scope:
            out.append(item)
    return [{k: v for k, v in i.items() if k != "text"} for i in out]


def resolve_problem_attachments(attachment_ids: list[str], project_id: str,
                                problem_id: str = "") -> tuple[list[dict], list[str]]:
    """校验归属后返回 (可用附件, 被拒绝的 id)。R3: A 的附件不得挂到 B。"""
    project = _safe_component(project_id, "")
    wanted = [str(i) for i in (attachment_ids or []) if i]
    allowed: list[dict] = []
    rejected: list[str] = []
    known = {i.get("attachment_id") for i in load()}
    for item in load():
        if item.get("attachment_id") not in wanted:
            continue
        if item.get("kind") != "problem" or item.get("project_id") != project:
            rejected.append(str(item.get("attachment_id")))
            continue
        if problem_id and item.get("problem_id") and item.get("problem_id") != problem_id:
            rejected.append(str(item.get("attachment_id")))
            continue
        allowed.append(item)
    rejected.extend([i for i in wanted if i not in known and i not in rejected])
    return allowed, rejected


def problem_text(attachment_ids: list[str], project_id: str = "",
                 problem_id: str = "") -> str:
    """把**属于该项目**的问题说明附件拼成文本 (带文件名与 hash 便于溯源)。"""
    allowed, _ = resolve_problem_attachments(attachment_ids, project_id, problem_id)
    parts = []
    for item in allowed:
        body = (item.get("text") or "").strip()
        if not body:
            continue
        parts.append(f"[附件 {item.get('filename')} sha256={item.get('short_hash')}]\n{body}")
    return "\n\n".join(parts)


def delete(target_id: str, project_id: str = "", topic: str = "",
           remove_file: bool = False) -> bool:
    """删除登记; 给了作用域时校验归属 (R3)。"""
    items = load()
    target = next((i for i in items if i.get("attachment_id") == target_id), None)
    if target is None:
        return False
    if project_id and target.get("project_id") != _safe_component(project_id):
        return False
    if topic and target.get("topic") != _safe_component(topic):
        return False
    if remove_file and target.get("kind") == "problem":
        candidate = Path(str(target.get("path", "")))
        if candidate.is_file() and _inside(upload_root(), candidate):
            try:
                candidate.unlink()
            except OSError:
                pass
    save([i for i in items if i.get("attachment_id") != target_id])
    return True


__all__ = [
    "ALLOWED_SUFFIXES",
    "CHUNK_BYTES",
    "KINDS",
    "MAX_ATTACHMENT_CHARS",
    "MAX_BYTES",
    "MAX_FILES",
    "MAX_REQUEST_BYTES",
    "ByteBudget",
    "StagedUpload",
    "UploadRequestTooLarge",
    "UploadTooLarge",
    "attachment_id",
    "check",
    "check_batch",
    "declared_size",
    "delete",
    "list_for",
    "load",
    "problem_text",
    "registry_path",
    "resolve_problem_attachments",
    "save",
    "save_literature",
    "save_problem_attachment",
    "staged_upload",
    "tmp_root",
    "upload_root",
]
