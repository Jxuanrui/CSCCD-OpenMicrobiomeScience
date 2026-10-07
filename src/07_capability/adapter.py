#!/usr/bin/env python3
"""KG Capability Adapter v0.1——KG ingestion 接入 Harness 治理闭环的最小信封。

目标（用户裁决 2026-09-25）：KG 管线不再是 Harness 外部 pipeline，成为第一
个完整接入 Capability——record-level provenance / ResourceUsage / workspace
scope / graph_snapshot_id bridge / persistent logs。

对齐 mra v1.2.0 契约（字段语义一致，KG 侧自包含实现）：
- provenance 五件套：source_id / retrieved_at / raw_hash / source_version /
  失败分类（fetch 侧）；
- ResourceUsage：model_calls / tokens(如可得) / 时长 / retry / cache ——账本
  data/registry/resource_usage.jsonl（append-only，凭据扫描同 mra 键名表）；
- 快照 manifest：data/merged/snapshot_manifest.json（snapshot_id /
  created_at / 内容 sha256 / source_registry_version / 统计）——主图物化
  （Neo4j）前的强制 QC 锚点；
- 日志：data/logs/（生产日志禁入 /tmp）。

v0.1 边界：token 计量依赖 API 返回（当前中转未提供 → 如实记 0 并标注）；
workspace scope 以 KG_WORKSPACE 环境变量声明（默认 kg-main 单库）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = ROOT / "data/registry"
LOG_DIR = ROOT / "data/logs"
import os as _os
MERGED_DIR = Path(_os.environ.get("KG_MERGED_DIR", str(ROOT / "data/merged/candidate_v3")))
USAGE_LEDGER = REGISTRY_DIR / "resource_usage.jsonl"
SOURCE_REGISTRY = REGISTRY_DIR / "source_registry.tsv"

CAP_FETCH = "kg.pubtator_fetch"
CAP_CLASSIFY = "kg.llm_relation_classify"
CAP_MERGE = "kg.merge_qc"

#: 与 mra.workspace._CREDENTIAL_KEY_NAMES 同口径（凭据永不入账本/日志）
CREDENTIAL_KEYS = frozenset({
    "api_key", "apikey", "api_token", "token", "secret", "secret_key",
    "password", "passwd", "authorization", "credentials", "private_key",
    "access_token", "refresh_token", "client_secret", "bearer"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_id() -> str:
    return os.environ.get("KG_WORKSPACE", "kg-main")


def new_execution(capability_id: str) -> dict:
    """一次管线执行的执行信封（execution_id 进 staging 记录与用量账本）。"""
    return {"capability_id": capability_id,
            "execution_id": f"EX-{capability_id}-{uuid.uuid4().hex[:8]}",
            "workspace_id": workspace_id(),
            "started_at": now_iso()}


def scan_credentials(obj, prefix: str = "") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in CREDENTIAL_KEYS:
                hits.append(path)
            hits.extend(scan_credentials(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(scan_credentials(v, f"{prefix}[{i}]"))
    return hits


def canonical_sha256(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def stamp_provenance(record: dict, source_id: str, source_version: str,
                     backfill: bool = False) -> dict:
    """记录级 provenance（P2 五件套口径）：retrieved_at / raw_hash /
    source_version；未来日期 → anomaly 标记（不自动修改原值）。"""
    iso = now_iso()
    record["_prov"] = {
        "source_id": source_id,
        "source_version": source_version,
        "retrieved_at": f"unknown<backfill@{iso}>" if backfill else iso,
        "raw_hash": canonical_sha256(record),
    }
    d = str(record.get("date", ""))
    if d[:10] > datetime.now(timezone.utc).date().isoformat():
        record.setdefault("_anomalies", []).append("future_date")
    return record


def append_usage(execution: dict, **measured) -> dict:
    """ResourceUsage-compatible 账本记录（append-only + 凭据扫描）。"""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    usage = {"usage_id": f"RU-{uuid.uuid4().hex[:10]}",
             "research_task_id": measured.pop("research_task_id", ""),
             "workspace_id": execution.get("workspace_id", workspace_id()),
             "capability_id": execution["capability_id"],
             "execution_id": execution["execution_id"],
             "kind": "execution",
             "model_calls": int(measured.get("model_calls", 0)),
             "input_tokens": int(measured.get("input_tokens", 0)),
             "output_tokens": int(measured.get("output_tokens", 0)),
             "total_tokens": int(measured.get("total_tokens", 0)),
             "external_api_calls": int(measured.get("external_api_calls", 0)),
             "compute_duration_ms": round(float(measured.get("compute_duration_ms", 0)), 3),
             "wall_duration_ms": round(float(measured.get("wall_duration_ms", 0)), 3),
             "retry_count": int(measured.get("retry_count", 0)),
             "cache_hit_count": int(measured.get("cache_hit_count", 0)),
             "cache_miss_count": int(measured.get("cache_miss_count", 0)),
             "measured_at": now_iso(),
             "note": measured.get("note", "")}
    leaked = scan_credentials(usage)
    if leaked:
        raise ValueError(f"凭据纪律违例：usage 携带 {leaked}")
    with USAGE_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(usage, ensure_ascii=False) + "\n")
    return usage


def write_snapshot_manifest(n_nodes: int, n_edges: int,
                            pending_review: int = 0, conflicts: int = 0,
                            context_metrics: dict | None = None,
                            assertion_set_hash: str = "",
                            annotation_set_hash: str = "",
                            assertion_counts: dict | None = None) -> dict:
    """graph_snapshot_id bridge：主图物化（Neo4j）前的 QC 锚点。

    裁决（2026-09-25）：批次结果禁止直接进入主图，必须经过 QC 与
    snapshot manifest——本 manifest 即快照身份（mra kg_snapshots 以
    snapshot_id 引用本清单，完成跨侧 bridge）。
    """
    MERGED_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seq = 1
    while (MERGED_DIR / f"snapshot_manifest_{day}-v{seq}.json").exists():
        seq += 1
    snapshot_id = f"{day}-v{seq}"
    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": now_iso(),
        "workspace_id": workspace_id(),
        # J（裁决）：冻结语义版本面——区分数据变化与知识表示规则变化
        "kg_schema_version": "1.0-rc1",
        "assertion_schema_version": "relation-assertion/0.5-atomic-content-addressed",
        "divergence_taxonomy_version": "taxonomy/0.2",
        "context_schema_version": "context/0.5-evidence-aware-15dim",
        "context_extractor_version": "extractor/0.6-wordboundary-objectfilter",
        "comparability_gate_version": "gate/0.6-generic-token-no-match",
        "ontology_version": "ncbitaxon+mesh+lfs-food@2026-09",
        "annotation_set_hash": annotation_set_hash,
        "assertion_set_hash": assertion_set_hash,
        # H（裁决）：context 指标随 manifest 报告；定位= context-aware（非 complete）
        "positioning": "context-aware representation (NOT context-complete)",
        "snapshot_status": "Context-aware Microbiome KG Snapshot v1",
        "P0_status": "RELEASED",
        "assertion_counts": assertion_counts or {},
        "retained_assertion_count": 0, "manual_hold_count": 0,
        "materialization_eligible_assertion_count": 0,
        "context_metrics": context_metrics or {},
        "n_nodes": n_nodes, "n_edges": n_edges,
        "pending_review": pending_review, "conflicts": conflicts,
        "merged_nodes_sha256": file_sha256(MERGED_DIR / "merged_nodes.tsv"),
        "merged_edges_sha256": file_sha256(MERGED_DIR / "merged_edges.tsv"),
        "source_registry_version": (file_sha256(SOURCE_REGISTRY)
                                    if SOURCE_REGISTRY.exists() else ""),
        "generator": f"{CAP_MERGE}@adapter-v0.1",
        "materialized_to_neo4j": False,  # 抽检通过前保持 False
    }
    path = MERGED_DIR / f"snapshot_manifest_{snapshot_id}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    (MERGED_DIR / "snapshot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def archive_log(src: Path, label: str) -> Path | None:
    """生产日志迁移：/tmp 禁止作为落位（观察期修复项 6）。"""
    if not src.exists():
        return None
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    dst = LOG_DIR / f"{label}-{src.stem}-{now_iso().replace(':', '').replace('-', '')[:15]}.log"
    dst.write_text(src.read_text(encoding="utf-8", errors="replace"),
                   encoding="utf-8")
    return dst
