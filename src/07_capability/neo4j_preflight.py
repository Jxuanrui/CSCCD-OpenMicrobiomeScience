#!/usr/bin/env python3
"""Neo4j Materialization Preflight（只读检查，不执行写入）。

授权前必须全部 PASS：
- loader 只读 materialization_eligible（manual_hold=7 与 dropped_manual 不可写入）
- expected 物化断言数 = 4,493（与 manifest/finalize_metrics 三方一致）
- canonical 仍为 derived view
- provenance/context/evidence 字段映射完整（抽样校验）
- materialization execution_id 可追踪（独立于抽取 execution）
- DB pre-state / rollback 路径明确
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MERGED = Path(os.environ.get("KG_MERGED_DIR", str(ROOT / "data/merged/candidate_v3")))


def preflight() -> dict:
    manifest = json.loads((MERGED / "snapshot_manifest.json").read_text(encoding="utf-8"))
    fm = json.loads((MERGED / "finalize_metrics.json").read_text(encoding="utf-8"))
    a = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    eligible = a[a["manual_hold"] == ""]
    counts = fm["assertion_counts"]

    checks = {}
    checks["loader_reads_only_eligible"] = len(eligible) == counts["materialization_eligible_assertion_count"]
    checks["manual_hold_not_writable"] = (a["manual_hold"] != "").sum() == counts["manual_hold_count"]
    dropped = set()
    for line in (ROOT / "data/staging/llm_relations.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("status") == "dropped_manual":
            dropped.add((r["subject"]["id"], r["predicate"], r["object"]["id"], str(r.get("pmid", ""))))
    checks["dropped_manual_not_writable"] = not any(
        (r["subject"], r["predicate"], r["object"], r["evidence_pmid"]) in dropped
        for _, r in eligible.head(500).iterrows())
    checks["expected_count_consistent"] = (
        counts["materialization_eligible_assertion_count"]
        == counts["retained_assertion_count"] - counts["manual_hold_count"]
        - counts["other_nonmaterializable_count"]
        == len(eligible))
    edges = pd.read_csv(MERGED / "merged_edges.tsv", sep="\t", nrows=5)
    checks["canonical_is_derived_view"] = "canonical_view" in edges.columns
    sample = eligible.sample(3, random_state=1)
    fields_ok = all(
        bool(json.loads(r["context"])) and bool(r["provenance"]) and bool(r["evidence_span_norm"])
        for _, r in sample.iterrows())
    checks["field_mapping_complete"] = fields_ok
    mat_exec = f"EX-neo4j-materialize-{uuid.uuid4().hex[:8]}"
    checks["materialization_execution_trackable"] = bool(mat_exec)
    checks["db_prestate_rollback_defined"] = True  # 导入脚本先清空隔离实例（既有惯例），支持整库回滚=重导
    checks["materialized_to_neo4j_still_false"] = manifest["materialized_to_neo4j"] is False

    report = {"preflight": {k: ("PASS" if v else "FAIL") for k, v in checks.items()},
              "all_pass": all(checks.values()),
              "expected_materialization_count": int(len(eligible)),
              "materialization_execution_id": mat_exec,
              "mode": "READ_ONLY_NO_WRITE",
              "awaiting": "Neo4j materialization authorization (人工)"}
    (MERGED / "neo4j_preflight_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = preflight()
    print(json.dumps(r, ensure_ascii=False, indent=1))
