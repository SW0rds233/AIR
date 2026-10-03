from __future__ import annotations

"""启动输入快照的可用性校验 (计划书 §3 R6 收尾)。

为什么单独一个模块: 快照校验是**与入口无关**的判据 —— Web 启动、CLI、断点续跑
都必须给出同一个结论, 把它写在 theory_pipeline 的节点里会让"续跑能不能继续"
取决于图跑到了哪一步 (违反"续跑前先判定"的语义), 也会让本已很长的流水线文件
继续膨胀。

为什么必须**读文件重算 sha256**: 只比较登记表里的 sha256 等于信任一份可能已经被
替换的元数据 —— 附件内容被改动后登记表并不会自动更新, 于是交付包会带着"输入未变"
的错误结论继续产出。这里以磁盘上的**实际字节**为准。

结论只有两类:
- 可用 (`ok=True`): 每个附件都能在登记表中找到, 路径落在上传根内, 且重算的
  sha256 与快照记录一致;
- 不可用 (`ok=False`): 至少一个附件缺失/内容变了/登记被删/路径越界 —— 此时
  调用方**不得**把这次运行当作"可完整复现", 应当拒绝续跑或显式降级并说明。
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# 附件状态: 只有 ok 表示"仍可用"。其余四种都必须让调用方明确知晓原因。
STATUS_OK = "ok"
STATUS_MISSING = "missing"            # 文件不存在 (原件被删/被移动)
STATUS_HASH_MISMATCH = "hash_mismatch"  # 内容变了 (或登记被同 id 改写)
STATUS_UNREGISTERED = "unregistered"  # 登记表中已无该附件 (登记被删)
STATUS_OUTSIDE_ROOT = "outside_root"  # 记录路径不在上传根内 (越界/被篡改)

_STATUS_TEXT = {
    STATUS_OK: "仍可用",
    STATUS_MISSING: "文件不存在",
    STATUS_HASH_MISMATCH: "内容摘要不一致",
    STATUS_UNREGISTERED: "登记已不存在",
    STATUS_OUTSIDE_ROOT: "路径越界",
}


@dataclass
class SnapshotCheck:
    """输入快照校验结论 (附件的可读状态 + 人类可读原因)。

    `ok` 只回答"附件是否仍可用"; `reproducible` 额外要求快照自身没有声明
    不可复现 (退化快照会显式带 `reproducible: False`)。两者分开的原因: 退化
    快照 (直接 CLI 调用, 没有附件上下文) 的附件列表是空的, 若把它也算作
    `ok=False` 就分不清"输入坏了"和"输入本来就没记录"。
    """

    ok: bool
    reproducible: bool
    attachments: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 判定依据摘要 (哪几份附件、依据什么判的), 供 manifest 复核
    basis: str = ""

    @property
    def reason(self) -> str:
        """一句话结论 (可直接进异常/notes/manifest)。"""
        if self.ok and self.reproducible:
            return "输入快照校验通过: 附件与启动时一致"
        if self.ok and not self.reproducible:
            return "输入快照自身声明不可复现: 不得作为可完整复现的输入"
        broken = [a for a in self.attachments if a.get("status") != STATUS_OK]
        head = (f"输入快照校验失败: {len(broken)}/{len(self.attachments)} 份附件不可用"
                if self.attachments else "输入快照校验失败")
        return head + ("; " + "; ".join(self.notes[:3]) if self.notes else "")

    def to_dict(self) -> dict:
        """写入 manifest 的形态 (保持纯 JSON, 不含自定义类型)。"""
        return {
            "ok": bool(self.ok),
            "reproducible": bool(self.reproducible),
            "reason": self.reason,
            "basis": self.basis,
            "attachments": [dict(a) for a in self.attachments],
            "notes": list(self.notes),
            "checked_at": _now(),
        }


class InputSnapshotRejected(RuntimeError):
    """续跑被拒绝: 输入快照里的附件已不可用 (内容变了/被删/登记被删/路径越界)。

    为什么用异常而不是返回一个"失败的状态字典": 续跑一旦放行, 后续节点就会写
    新的研究结论与交付包 —— 只有**在进入图之前**中断, 才能保证不会产出一个
    "看起来可完整复现"的包。异常携带结构化结论 (`check`), 调用方可以据此展示
    具体是哪份附件出了什么问题, 而不是一个笼统的失败。
    """

    def __init__(self, check: SnapshotCheck, snapshot: dict | None = None):
        self.check = check
        self.snapshot = dict(snapshot or {})
        super().__init__(check.reason)

    @property
    def reason(self) -> str:
        return self.check.reason


# 易读别名: 调用方按语义选名字即可
SnapshotRejected = InputSnapshotRejected


def _now() -> str:
    from src.research.schemas import utcnow

    return utcnow()


def _sha256_of(path) -> str:
    """按块读取重算摘要: 附件上限 50MB, 一次性读入没有必要。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_flag(snapshot: dict) -> tuple[bool, str]:
    """快照自身是否声明了不可复现 (显式 `reproducible: False` 才算)。

    只认显式的 False: 早期/入口自建的快照可能没有这个键, 缺键不等于"不可复现"。
    """
    if "reproducible" not in snapshot:
        return True, ""
    if snapshot.get("reproducible") is False:
        note = str(snapshot.get("note") or "").strip()
        return False, ("快照自身标记 reproducible=False"
                       + (f" ({note})" if note else ""))
    return True, ""


def verify_input_snapshot(snapshot: dict | None) -> SnapshotCheck:
    """校验启动输入快照里的附件是否仍可用 (内容 hash 一致)。

    判定依据 (缺一不可):
    1. 附件 `attachment_id` 仍在上传登记表中 —— 登记被删即视为不可用, 不能
       因为磁盘上还留着文件就当它"还在输入里";
    2. 登记记录的路径必须落在 `uploads.upload_root()` 内 (`uploads._inside`);
    3. 文件存在且**重新计算**的 sha256 与快照记录的一致。
    """
    from src.utils import uploads

    if not isinstance(snapshot, dict):
        snapshot = {}
    declared_reproducible, declared_note = _snapshot_flag(snapshot)
    items = list(snapshot.get("attachments") or [])
    notes: list[str] = []
    if declared_note:
        notes.append(declared_note)

    root = uploads.upload_root()
    registered: dict[str, dict] = {}
    try:
        for item in uploads.load():
            key = str(item.get("attachment_id") or "")
            if key:
                registered[key] = item
    except Exception as e:  # noqa: BLE001 - 登记不可读必须显式说明, 不能当作"没有附件"
        notes.append(f"附件登记表不可读 ({type(e).__name__}: {e}): 无法确认附件仍可用")

    statuses: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            notes.append("快照中的附件记录格式非法, 已按不可用处理")
            statuses.append({"attachment_id": "", "filename": "", "status": STATUS_UNREGISTERED,
                             "detail": "附件记录不是对象", "recorded_sha256": "",
                             "actual_sha256": ""})
            continue
        attachment_id = str(item.get("attachment_id") or "")
        recorded = str(item.get("sha256") or "")
        record = registered.get(attachment_id)
        entry = {
            "attachment_id": attachment_id,
            "filename": str(item.get("filename") or (record or {}).get("filename") or ""),
            "status": STATUS_OK,
            "detail": "",
            "kind": str((record or {}).get("kind") or ""),
            "recorded_sha256": recorded,
            "actual_sha256": "",
        }

        if record is None:
            entry["status"] = STATUS_UNREGISTERED
            entry["detail"] = "上传登记表里已没有该附件 (可能被删除或项目被清理)"
            notes.append(f"附件 {entry['filename'] or attachment_id} 的登记已不存在: "
                         "无法确认它仍是本次运行的输入")
            statuses.append(entry)
            continue

        registered_digest = str(record.get("sha256") or "")
        path_text = str(record.get("path") or "")
        if not path_text:
            entry["status"] = STATUS_MISSING
            entry["detail"] = "登记项没有记录文件路径"
            notes.append(f"附件 {entry['filename']} 的登记缺少路径: 原件无法定位")
            statuses.append(entry)
            continue

        path = Path(path_text)
        if not uploads._inside(root, path):
            entry["status"] = STATUS_OUTSIDE_ROOT
            entry["detail"] = f"记录路径不在上传根内: {path}"
            notes.append(f"附件 {entry['filename']} 的路径越界 ({path}), 拒绝当作本次输入")
            statuses.append(entry)
            continue

        if not path.is_file():
            entry["status"] = STATUS_MISSING
            entry["detail"] = f"文件不存在: {path}"
            notes.append(f"附件 {entry['filename']} 的原件已不存在 ({path})")
            statuses.append(entry)
            continue

        try:
            actual = _sha256_of(path)
        except OSError as e:
            entry["status"] = STATUS_MISSING
            entry["detail"] = f"文件不可读: {type(e).__name__}: {e}"
            notes.append(f"附件 {entry['filename']} 无法读取 ({type(e).__name__}), 不能视为可用")
            statuses.append(entry)
            continue

        entry["actual_sha256"] = actual
        if actual != recorded:
            entry["status"] = STATUS_HASH_MISMATCH
            entry["detail"] = (f"内容摘要已变: 快照记录 {recorded[:12] or '(空)'}, "
                               f"实际 {actual[:12]}")
            notes.append(f"附件 {entry['filename']} 的内容已改变 "
                         f"(快照 {recorded[:12] or '(空)'} ≠ 实际 {actual[:12]})")
        elif registered_digest and registered_digest != recorded:
            entry["status"] = STATUS_HASH_MISMATCH
            entry["detail"] = (f"登记摘要与快照不一致: 快照 {recorded[:12]}, "
                               f"登记 {registered_digest[:12]}")
            notes.append(f"附件 {entry['filename']} 的登记摘要与快照记录不一致")
        else:
            entry["detail"] = "sha256 一致"
        statuses.append(entry)

    # 快照声明了附件 id 却没有对应记录: 输入被截断, 不能悄悄忽略。
    # 这类附件没有 sha256 可比, 因此**不计入** `ok` 的判定 (ok 只回答"记录在案的
    # 附件是否仍可用"), 但必须留下说明, 让人看到"这份快照并不完整"。
    recorded_ids = {str(i.get("attachment_id") or "") for i in items if isinstance(i, dict)}
    for missing_id in [str(i) for i in (snapshot.get("attachment_ids") or []) if i]:
        if missing_id not in recorded_ids:
            notes.append(f"快照声明了附件 {missing_id} 但未记录其 sha256: 该附件无法校验, "
                         "本次输入快照并不完整")

    broken = [s for s in statuses if s["status"] != STATUS_OK]
    # `ok` 只回答"记录在案的附件是否仍可用"; 快照自身声明不可复现是另一件事,
    # 由 `reproducible` 表达 —— 两者混在一起就分不清"输入坏了"和"输入本来就没记录"。
    ok = not broken
    reproducible = ok and declared_reproducible
    basis = (f"逐份重算 sha256 并与快照比对; 附件 {len(statuses)} 份, "
             f"不可用 {len(broken)} 份; 登记表 {len(registered)} 项")
    return SnapshotCheck(ok=bool(ok), reproducible=bool(reproducible),
                         attachments=statuses, notes=notes, basis=basis)


def merge_verification(snapshot: dict | None, check: SnapshotCheck) -> dict:
    """把校验结论写进快照副本 (`input_snapshot.verification`), 供 manifest 复核。

    不改动附件记录本身: 快照的附件条目是"启动时的事实", 校验结论是"事后的判定",
    两者混在一起会让 manifest 无法区分"记录"与"结论"。
    """
    merged = dict(snapshot or {})
    merged["verification"] = check.to_dict()
    # `reproducible` 由**实际校验**决定 (一致且快照自身未声明不可复现才为 True)
    merged["reproducible"] = bool(check.reproducible)
    return merged


def describe_status(status: str) -> str:
    return _STATUS_TEXT.get(status, status)


__all__ = [
    "STATUS_HASH_MISMATCH",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_OUTSIDE_ROOT",
    "STATUS_UNREGISTERED",
    "InputSnapshotRejected",
    "SnapshotCheck",
    "SnapshotRejected",
    "describe_status",
    "merge_verification",
    "verify_input_snapshot",
]
