#!/usr/bin/env python3
"""两层知识物化：canonical layer + RelationAssertion evidence layer → Neo4j。

架构（裁决 2026-09-25）：同一个 KG 的两层表示——
  Layer 1 Canonical: Entity 节点 + RELATED 边（derived summary, ~6,628 nodes/~20,701 edges）
  Layer 2 Evidence:  RelationAssertion 节点 + HAS_ASSERTION/TARGET 边（4,493 eligible）

严格排除：7 manual_hold + 所有 dropped_manual。
三层 provenance 分离：curated source (source_ref) / assertion (PMID+span) / materialization execution。
"""
import csv
import json
import os
import pandas as pd
import time
import uuid
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[2]
import os
MERGED = Path(os.environ.get("KG_MERGED_DIR", str(ROOT / "data/merged")))  # D1：可指 candidate_v2
EXECUTION_ID = f"EX-neo4j-materialize-{uuid.uuid4().hex[:8]}"


def load_data():
    nodes = list(csv.DictReader((MERGED / "merged_nodes.tsv").open(), delimiter="\t"))
    edges = list(csv.DictReader((MERGED / "merged_edges.tsv").open(), delimiter="\t"))
    assertions = []
    with (MERGED / "relation_assertions.tsv").open() as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get("manual_hold", ""):  # 严格排除 manual_hold
                continue
            assertions.append(row)
    return nodes, edges, assertions


def _selfcheck_edge_columns():
    """P2 启动自检：边表必须含溯源四列，缺任一则报错退出。"""
    REQUIRED = {"source_id", "retrieved_at", "version", "knowledge_layer"}
    df = pd.read_csv(MERGED / "merged_edges.tsv", sep="\t", nrows=1)
    missing = REQUIRED - set(df.columns)
    if missing:
        raise SystemExit(f"[selfcheck] merged_edges.tsv 缺少溯源列: {missing}")


def materialize(uri, password):
    _selfcheck_edge_columns()  # P2 自检
    nodes, edges, assertions = load_data()
    print(f"[load] nodes={len(nodes)} edges={len(edges)} eligible_assertions={len(assertions)}")
    driver = GraphDatabase.driver(uri, auth=("neo4j", password))
    driver.verify_connectivity()
    t0 = time.perf_counter()

    with driver.session(database="neo4j") as s:
        # ---- 清空项目隔离实例（幂等重建）----
        s.run("MATCH (n) DETACH DELETE n").consume()
        print("[clear] 隔离实例已清空")

        # ---- 约束 ----
        s.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE").consume()
        s.run("CREATE CONSTRAINT assertion_id IF NOT EXISTS FOR (n:RelationAssertion) REQUIRE n.assertion_id IS UNIQUE").consume()

        # ---- Layer 1: Canonical ----
        s.run("""
            UNWIND $rows AS r
            MERGE (n:Entity {id: r.id})
            SET n.name = r.name, n.category = r.category,
                n.aliases = r.aliases, n.xrefs = r.xrefs, n.tax_rank = r.tax_rank,
                n.knowledge_layer = r.knowledge_layer
        """, rows=nodes).consume()
        print(f"[canonical] nodes {len(nodes)} imported")

        s.run("""
            UNWIND $rows AS r
            MATCH (a:Entity {id: r.subject}), (b:Entity {id: r.object})
            MERGE (a)-[e:RELATED {predicate: r.predicate}]->(b)
            SET e.source_type = r.source_type,
                e.evidence_tier = r.evidence_tier,
                e.pmids = r.pmids,
                e.years = r.years,
                e.support_count = toInteger(r.support_count),
                e.confidence = toFloat(r.confidence),
                e.polarity = r.polarity,
                e.last_updated = r.last_updated,
                e.relation_status = r.relation_status,
                e.canonical_view = r.canonical_view,
                e.source_id = r.source_id,
                e.retrieved_at = r.retrieved_at,
                e.version = r.version,
                e.knowledge_layer = r.knowledge_layer
        """, rows=edges).consume()
        print(f"[canonical] edges {len(edges)} imported")

        # ---- Layer 2: Evidence (RelationAssertion) ----
        for row in assertions:
            ctx = {}
            try:
                ctx = json.loads(row.get("context", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            prov = {}
            try:
                prov = json.loads(row.get("provenance", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            # context 序列化为字符串（Neo4j property model）
            ctx_str = json.dumps(ctx, ensure_ascii=False)
            prov_str = json.dumps(prov, ensure_ascii=False)
            span = row.get("evidence_span_norm", "")[:2000]

            s.run("""
                MERGE (ra:RelationAssertion {assertion_id: $aid})
                SET ra.predicate = $pred, ra.direction = $dir,
                    ra.confidence = $conf, ra.evidence_pmid = $pmid,
                    ra.evidence_span = $span,
                    ra.context = $ctx, ra.context_completeness = toFloat($ctx_comp),
                    ra.provenance = $prov,
                    ra.divergence = $div,
                    ra.is_canonical_summary = false
            """, aid=row["assertion_id"], pred=row["predicate"],
                dir=row.get("direction", ""), conf=row.get("confidence", ""),
                pmid=row["evidence_pmid"], span=span,
                ctx=ctx_str, ctx_comp=row.get("context_completeness", "0"),
                prov=prov_str, div=row.get("divergence", "")).consume()

            # HAS_ASSERTION: subject → assertion
            s.run("""
                MATCH (e:Entity {id: $sid}), (ra:RelationAssertion {assertion_id: $aid})
                MERGE (e)-[:HAS_ASSERTION]->(ra)
            """, sid=row["subject"], aid=row["assertion_id"]).consume()

            # TARGET: assertion → object
            s.run("""
                MATCH (ra:RelationAssertion {assertion_id: $aid}), (e:Entity {id: $oid})
                MERGE (ra)-[:TARGET]->(e)
            """, aid=row["assertion_id"], oid=row["object"]).consume()

        print(f"[evidence] {len(assertions)} RelationAssertions + links imported")

        # ---- Materialization provenance ----
        manifest = json.loads((MERGED / "snapshot_manifest.json").read_text())
        s.run("""
            MERGE (mp:MaterializationProvenance {execution_id: $exec})
            SET mp.source_snapshot = $snap,
                mp.assertion_set_hash = $hash,
                mp.annotation_set_hash = $ahash,
                mp.materialized_at = datetime(),
                mp.node_count = $nodes, mp.edge_count = $edges,
                mp.assertion_count = $assertions,
                mp.kg_schema_version = $kgver
        """, exec=EXECUTION_ID, snap=manifest["snapshot_id"],
            hash=manifest["assertion_set_hash"],
            ahash=manifest["annotation_set_hash"],
            nodes=len(nodes), edges=len(edges), assertions=len(assertions),
            kgver=manifest["kg_schema_version"]).consume()

        # ---- QC queries ----
        qc = {}
        qc["total_nodes"] = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        qc["entity_nodes"] = s.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
        qc["assertion_nodes"] = s.run("MATCH (n:RelationAssertion) RETURN count(n) AS c").single()["c"]
        qc["canonical_edges"] = s.run("MATCH ()-[r:RELATED]->() RETURN count(r) AS c").single()["c"]
        qc["has_assertion_links"] = s.run("MATCH ()-[r:HAS_ASSERTION]->() RETURN count(r) AS c").single()["c"]
        qc["target_links"] = s.run("MATCH ()-[r:TARGET]->() RETURN count(r) AS c").single()["c"]
        qc["orphan_assertions"] = s.run(
            "MATCH (ra:RelationAssertion) WHERE NOT (()-[:HAS_ASSERTION]->(ra)) OR NOT (ra)-[:TARGET]->() RETURN count(ra) AS c"
        ).single()["c"]
        qc["duplicate_assertions"] = s.run(
            "MATCH (ra:RelationAssertion) WITH ra.assertion_id AS id, count(*) AS n WHERE n > 1 RETURN count(*) AS c"
        ).single()["c"]
        qc["manual_hold_leaked"] = s.run(
            "MATCH (ra:RelationAssertion) WHERE ra.divergence = 'manual_hold' RETURN count(ra) AS c"
        ).single()["c"]
        qc["context_dependent_canonical"] = s.run(
            "MATCH ()-[r:RELATED {relation_status: 'context_dependent'}]->() RETURN count(r) AS c"
        ).single()["c"]

        # cross-layer linkage: literature canonical edges with supporting assertions
        qc["linked_canonical_edges"] = s.run("""
            MATCH (a:Entity)-[r:RELATED]->(b:Entity)
            WHERE r.source_type = 'llm_extracted'
              AND EXISTS { (a)-[:HAS_ASSERTION]->(:RelationAssertion)-[:TARGET]->(b) }
            RETURN count(r) AS c
        """).single()["c"]

        elapsed = time.perf_counter() - t0

    driver.close()
    qc["execution_id"] = EXECUTION_ID
    qc["elapsed_seconds"] = round(elapsed, 1)
    return qc


if __name__ == "__main__":
    uri = os.getenv("NEO4J_URI", "bolt://127.0.0.1:17687")
    pw = os.getenv("NEO4J_PASSWORD")
    if not pw:
        raise SystemExit("NEO4J_PASSWORD required")
    # P0-6 写入准入（2026-09-29）：物化须显式授权键（registry 不预置——
    # 任何物化前用户须在 write_authorizations.tsv 登记 neo4j-materialize 授权，
    # 实现"授权物化与授权发布分离"的 fail-closed 控制）
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    from write_guard import guard_write
    guard_write("neo4j_materialize", EXECUTION_ID,
                os.getenv("KG_WRITE_AUTH", "neo4j-materialize"))
    result = materialize(uri, pw)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    (MERGED / "neo4j_materialization_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
