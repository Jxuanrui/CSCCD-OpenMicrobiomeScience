"""P3 Backup / Restore Automation——Scientific State Snapshot，不是文件复制。

第一原则（用户裁决 2026-09-25）：恢复后的系统必须是**同一个科研系统**——
identity / sequence / lineage / replay 语义全部一致才算完成备份。

Backup 内容边界：
- 必须备份：ledger 全记录（Task/Plan/Candidate/Decision/Evidence/Mutation/
  ResourceUsage/Budget/LoopEvent）+ provenance 元数据（graph_snapshot_id/
  policy_version/registry 版本，随记录携带或由 manifest 汇总）+ 配置状态
  （schema_version）。
- 禁止备份：凭据（创建时对账本二次扫描）、临时缓存、runtime memory——
  备份目录只含 events.jsonl + manifest.json（结构上无他物可泄）。

Restore fail-closed 流水线（任何一步失败 → 目标工作区零改动）：
  load manifest → schema 兼容判定（compatible | migration_required，
  禁止静默升级）→ 逐文件 checksum → ledger/snapshot hash → staging 拷贝 →
  staging 上 replay 摘要 + seq 连续性验证 → 原子换入目标位置。

增量备份（最小实现）：full 为基础正确性目标；incremental 只备份
seq > from_seq 的段并引用 base_backup_id，恢复 = base + 段拼接后整体校验。

P3 不做：多节点复制 / RAFT / 分布式数据库 / cloud orchestration——
single-host scientific workspace 的可靠恢复。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import Workspace, find_credential_keys

#: 账本 schema 版本（记录类型集合的兼容代际）；破坏性变更时 bump 并更新兼容表
LEDGER_SCHEMA_VERSION = "1.2"
#: 可被当前代码直接恢复（无迁移）的历史备份版本——1.1→1.2 为加性演进
COMPATIBLE_SCHEMA_VERSIONS = ("1.1", "1.2")

_EVENTS_FILE = "events.jsonl"
_MANIFEST_FILE = "manifest.json"


class BackupError(RuntimeError):
    """备份/恢复失败（fail-closed：抛出即零副作用或已回滚）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def check_schema_compatibility(backup_version: str) -> str:
    """compatible（直接恢复）/ migration_required（拒绝静默升级）。"""
    if backup_version == LEDGER_SCHEMA_VERSION:
        return "compatible"
    if backup_version in COMPATIBLE_SCHEMA_VERSIONS:
        return "compatible"  # 加性演进代际：旧备份在新代码下 replay 语义不变
    return "migration_required"


def _registry_version() -> str:
    try:
        from .capability import build_default_registry
        inventory = sorted(f"{i['capability_id']}@{i['capability_version']}"
                           for i in build_default_registry().catalog())
        return _sha256_bytes("\n".join(inventory).encode("utf-8"))
    except Exception:
        return ""


def _ledger_facts(ws: Workspace) -> dict[str, Any]:
    """从账本提取 manifest 汇总事实（policy/snapshot 版本 + replay 摘要）。"""
    events = ws.events()
    policy = next((e["record"].get("policy_version") for e in reversed(events)
                   if e["record_type"] == "GovernanceDecision"), "")
    snapshot = next((e["record"].get("graph_snapshot_id") for e in reversed(events)
                     if e["record_type"] in ("Evidence", "CandidateResult")
                     and e["record"].get("graph_snapshot_id")), "")
    st = ws.replay()
    replay_summary = {"n_events": st.n_events, "tasks": len(st.tasks),
                      "research_plans": st.research_plans,
                      "candidate_results": st.candidate_results,
                      "governance_decisions": st.governance_decisions,
                      "evidence": len(st.evidence),
                      "resource_usages": st.resource_usages,
                      "budgets": st.budgets, "loop_events": st.loop_events,
                      "cross_workspace_references": st.cross_workspace_references,
                      "external_write_records": st.external_write_records}
    return {"policy_version": policy, "graph_snapshot_id": snapshot,
            "replay_summary": replay_summary}


def _scan_events_for_credentials(events_path: Path) -> None:
    for raw in events_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rec = json.loads(raw).get("record", {})
        leaked = find_credential_keys(rec)
        if leaked:
            raise BackupError(
                f"备份凭据纪律违例：账本事件携带疑似凭据字段 {leaked}——拒绝备份")


def create_backup(ws: Workspace, backup_root: Path | str, *,
                  backup_id: str | None = None, kind: str = "full",
                  base_backup_id: str | None = None,
                  incremental_from_seq: int | None = None) -> dict[str, Any]:
    """创建备份，返回 manifest dict（同时落盘 manifest.json）。

    full：完整 events.jsonl；incremental：仅 seq > incremental_from_seq 的段
    （须提供 base_backup_id；段内首 seq 必须 = from_seq+1，无重叠无空洞）。
    """
    if kind not in ("full", "incremental"):
        raise BackupError(f"未知备份类型 {kind}")
    events = ws.events()
    if not events:
        raise BackupError("空账本无可备份状态")
    head_seq = events[-1]["seq"]
    backup_id = backup_id or _now().replace(":", "").replace("-", "")[:15] + \
        "-" + uuid.uuid4().hex[:6]
    bdir = Path(backup_root) / backup_id
    if bdir.exists():
        raise BackupError(f"备份目录已存在 {bdir}")
    _scan_events_for_credentials(ws.events_path)  # 凭据二次扫描（append 守卫外再一道）

    if kind == "full":
        payload_lines = ws.events_path.read_text(encoding="utf-8").splitlines()
        payload_lines = [l for l in payload_lines if l.strip()]
    else:
        if not base_backup_id or incremental_from_seq is None:
            raise BackupError("incremental 备份须提供 base_backup_id + incremental_from_seq")
        seg = [e for e in events if e["seq"] > incremental_from_seq]
        if seg and seg[0]["seq"] != incremental_from_seq + 1:
            raise BackupError("增量段与基准不衔接（首 seq 须为 from_seq+1）")
        payload_lines = [json.dumps(e, ensure_ascii=False) for e in seg]
        head_seq = (seg[-1]["seq"] if seg else incremental_from_seq)
    bdir.mkdir(parents=True)
    events_copy = bdir / _EVENTS_FILE
    events_copy.write_text("\n".join(payload_lines) + "\n", encoding="utf-8")

    facts = _ledger_facts(ws)
    ledger_hash = _sha256_file(events_copy)
    manifest = {
        "backup_id": backup_id, "kind": kind, "created_at": _now(),
        "source_workspace_id": ws.study_dir.name,
        "ledger_head_seq": head_seq,
        "ledger_hash": ledger_hash,
        "snapshot_hash": _sha256_bytes(ledger_hash.encode("utf-8")),
        "schema_version": LEDGER_SCHEMA_VERSION,
        "capability_registry_version": _registry_version(),
        "policy_version": facts["policy_version"],
        "graph_snapshot_id": facts["graph_snapshot_id"],
        "base_backup_id": base_backup_id,
        "incremental_from_seq": incremental_from_seq,
        "replay_summary": facts["replay_summary"],
        "file_inventory": [{"path": _EVENTS_FILE, "size": events_copy.stat().st_size,
                            "sha256": ledger_hash}],
    }
    (bdir / _MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def _load_and_verify(backup_root: Path, backup_id: str) -> tuple[dict, Path]:
    bdir = Path(backup_root) / backup_id
    mpath = bdir / _MANIFEST_FILE
    if not mpath.is_file():
        raise BackupError(f"备份 manifest 缺失：{bdir}")
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackupError(f"manifest 损坏：{exc}") from None
    for f in manifest.get("file_inventory", []):
        fpath = bdir / f["path"]
        if not fpath.is_file():
            raise BackupError(f"备份文件缺失：{f['path']}")
        actual = _sha256_file(fpath)
        if actual != f["sha256"]:
            raise BackupError(f"checksum 不符：{f['path']} 期望 {f['sha256']} 实际 {actual}")
    if manifest["ledger_hash"] != _sha256_file(bdir / _EVENTS_FILE):
        raise BackupError("ledger_hash 校验失败（账本被篡改）")
    # manifest 自身一致性：head seq 与段尾一致
    return manifest, bdir


def _validate_staged(staged_study_dir: Path, manifest: dict) -> None:
    """staging 上的语义验证：replay 摘要一致 + seq 严格连续。"""
    study_id = staged_study_dir.name
    ws = Workspace(study_id, root=staged_study_dir.parent)
    events = ws.events()
    if not events:
        raise BackupError("恢复后账本为空")
    seqs = [e["seq"] for e in events]
    if seqs != list(range(1, len(seqs) + 1)):
        raise BackupError("seq 不连续/重复——恢复拒绝")
    if seqs[-1] != manifest["ledger_head_seq"]:
        raise BackupError(f"head seq 不符：恢复 {seqs[-1]} vs manifest {manifest['ledger_head_seq']}")
    st = ws.replay()
    expect = manifest["replay_summary"]
    actual = {"n_events": st.n_events, "tasks": len(st.tasks),
              "research_plans": st.research_plans,
              "candidate_results": st.candidate_results,
              "governance_decisions": st.governance_decisions,
              "evidence": len(st.evidence),
              "resource_usages": st.resource_usages,
              "budgets": st.budgets, "loop_events": st.loop_events,
              "cross_workspace_references": st.cross_workspace_references,
              "external_write_records": st.external_write_records}
    if actual != expect:
        raise BackupError(f"replay 摘要不符（恢复 {actual} vs 备份 {expect}）")


def restore_backup(backup_root: Path | str, backup_id: str,
                   target_root: Path | str, *, study_id: str | None = None,
                   allow_migration: bool = False) -> dict[str, Any]:
    """fail-closed 恢复：staging 全部校验通过后才原子换入目标位置。

    恢复为时间点语义：目标位置的后续事件（备份之后新增）被显式丢弃，
    恢复到 manifest.ledger_head_seq；历史事件零改写。
    """
    manifest, bdir = _load_and_verify(Path(backup_root), backup_id)
    compat = check_schema_compatibility(manifest.get("schema_version", ""))
    if compat == "migration_required" and not allow_migration:
        raise BackupError(
            f"schema 不兼容（备份 {manifest.get('schema_version')} vs 当前 "
            f"{LEDGER_SCHEMA_VERSION}）——migration_required，禁止静默升级恢复")
    study = study_id or manifest["source_workspace_id"]
    target_root = Path(target_root)
    staging = target_root / f".restore-staging-{study}-{uuid.uuid4().hex[:8]}"
    trash = target_root / f".restore-trash-{study}-{uuid.uuid4().hex[:8]}"

    def _assemble(src: Path, dst: Path) -> None:
        dst.mkdir(parents=True)
        shutil.copy2(src / _EVENTS_FILE, dst / _EVENTS_FILE)

    try:
        if manifest["kind"] == "incremental":
            if not manifest.get("base_backup_id"):
                raise BackupError("增量备份缺 base_backup_id")
            base_manifest, base_dir = _load_and_verify(Path(backup_root),
                                                        manifest["base_backup_id"])
            if base_manifest["ledger_head_seq"] != manifest["incremental_from_seq"]:
                raise BackupError("增量链不衔接：base head ≠ incremental_from_seq")
            _assemble(base_dir, staging)
            seg = (bdir / _EVENTS_FILE).read_text(encoding="utf-8")
            with (staging / _EVENTS_FILE).open("a", encoding="utf-8") as fh:
                fh.write(seg if seg.endswith("\n") or not seg else seg + "\n")
        else:
            _assemble(bdir, staging)
        _scan_events_for_credentials(staging / _EVENTS_FILE)  # 恢复侧凭据纪律
        _validate_staged(staging, manifest)
        target = target_root / study
        if target.exists():
            target.rename(trash)
        staging.rename(target)
        if trash.exists():
            shutil.rmtree(trash)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if trash.exists() and not (target_root / study).exists():
            trash.rename(target_root / study)  # 回滚：目标位置原样归还
        raise
    return {"restored_study": study, "head_seq": manifest["ledger_head_seq"],
            "schema_compatibility": compat, "manifest": manifest}


__all__ = ["BackupError", "COMPATIBLE_SCHEMA_VERSIONS", "LEDGER_SCHEMA_VERSION",
           "check_schema_compatibility", "create_backup", "restore_backup"]
