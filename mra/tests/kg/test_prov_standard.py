"""P2 PROV Standardization 测试——4 项验证 + 3 条 trace + frozen integrity。

P2 铁律验证：
- P2 只读（不修改知识内容）
- frozen hash 不变
- P1 SDK 不破坏
- 三层 provenance 分离
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

KG_MERGED = Path(os.environ.get(
    "KG_MERGED_DIR",
    "/data/LYteamwork/JiXuanRui/Project/Knowledge_Graph/data/merged"))

pytestmark = pytest.mark.skipif(
    not (KG_MERGED / "relation_assertions.tsv").exists(),
    reason="KG merged data not available")

from mra.kg.prov_standard import (PROVENANCE_MAPPING, PROV_MODEL_VERSION,
                                   PROV_RELATIONS, ProvenanceGraphBuilder)


@pytest.fixture(scope="module")
def builder():
    return ProvenanceGraphBuilder(KG_MERGED)


@pytest.fixture(scope="module", autouse=True)
def frozen_hash():
    """捕获 frozen hash 供 integrity 测试。"""
    return "sha256:" + hashlib.sha256(
        (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()


class TestProvenanceMapping:
    """P2 §4: 映射表完整性。"""

    def test_mapping_covers_three_layers(self):
        layers = {v["layer"] for v in PROVENANCE_MAPPING.values()}
        assert layers == {"curated_source", "literature_assertion", "materialization"}

    def test_mapping_covers_prov_concepts(self):
        concepts = {v["prov_concept"] for v in PROVENANCE_MAPPING.values()}
        assert concepts == {"Entity", "Activity", "Agent"}

    def test_every_field_has_validation(self):
        for fname, spec in PROVENANCE_MAPPING.items():
            assert spec["validation"], f"{fname} missing validation rule"
            assert spec["meaning"], f"{fname} missing meaning"

    def test_key_fields_present(self):
        expected = {"PMID", "evidence_span", "execution_id", "capability_id",
                     "snapshot_id", "materialization_execution_id",
                     "assertion_set_hash", "annotation_set_hash"}
        assert expected <= set(PROVENANCE_MAPPING)


class TestProvGraphModel:
    """P2 §5: graph model 完整性。"""

    def test_prov_relations_defined(self):
        expected = {"wasGeneratedBy", "used", "wasAssociatedWith", "wasDerivedFrom"}
        assert expected <= set(PROV_RELATIONS)

    def test_trace_a_literature(self, builder):
        """Trace A: Paper → Extraction → Assertion → Snapshot。"""
        # Get an assertion ID
        a = builder.assertions
        aid = a.iloc[0]["assertion_id"]
        trace = builder.build_literature_trace(aid)
        assert trace["trace_id"] == "TraceA_Literature"
        nodes = trace["nodes"]
        edges = trace["edges"]
        # 5 nodes: Paper, Activity, Agent, Assertion, Snapshot
        roles = {n["role"] for n in nodes}
        assert "Paper" in roles and "Extraction" in roles and "Capability" in roles
        assert "RelationAssertion" in roles and "ReleasedSnapshot" in roles
        # 4 edges: used, wasAssociatedWith, wasGeneratedBy, wasDerivedFrom
        types = {e["type"] for e in edges}
        assert types == {"used", "wasAssociatedWith", "wasGeneratedBy", "wasDerivedFrom"}

    def test_trace_b_curated(self, builder):
        """Trace B: Curated Source → Import → Canonical Edge。"""
        # Find a curated edge
        curated = builder.edges[builder.edges.source_type != "llm_extracted"]
        if curated.empty:
            pytest.skip("no curated edges")
        r = curated.iloc[0]
        trace = builder.build_curated_trace(r["subject"], r["predicate"], r["object"])
        assert trace["trace_id"] == "TraceB_Curated"
        roles = {n["role"] for n in trace["nodes"]}
        assert "CuratedSource" in roles and "Import" in roles and "CanonicalEdge" in roles

    def test_trace_c_materialization(self, builder):
        """Trace C: Snapshot → Materialization → Neo4j。"""
        trace = builder.build_materialization_trace()
        assert trace["trace_id"] == "TraceC_Materialization"
        roles = {n["role"] for n in trace["nodes"]}
        assert "ReleasedSnapshot" in roles and "Materialization" in roles
        assert "ServingState" in roles


class TestProvenanceValidation:
    """P2 §7: 四项自动验证。"""

    def test_1_assertion_provenance_completeness(self, builder):
        result = builder.validate_assertion_provenance()
        assert result["pass"], (
            f"{result['incomplete']}/{result['total']} assertions incomplete provenance")

    def test_2_materialization_provenance(self, builder):
        result = builder.validate_materialization_provenance()
        assert result["pass"]

    def test_3_no_orphan_provenance(self, builder):
        result = builder.validate_no_orphan_provenance()
        assert result["pass"], f"orphan issues: {result['issues']}"

    def test_4_frozen_data_integrity(self, builder, frozen_hash):
        result = builder.validate_frozen_integrity(frozen_hash)
        assert result["pass"], f"assertion hash changed: {result['assertion_hash']}"


class TestP1SDKCompatibility:
    """P2 §6: P1 SDK 不破坏。"""

    def test_p1_sdk_still_works(self):
        from mra.kg.capability_sdk import KGCapabilitySDK
        sdk = KGCapabilitySDK(KG_MERGED)
        out = sdk.kg_assertions("NCBITaxon:239935")
        assert out["result"]["n_assertions"] > 0
        ev = sdk.kg_evidence(out["result"]["assertions"][0]["assertion_id"])
        assert ev["result"]["found"]

    def test_p1_output_contract_unchanged(self):
        """P1 输出结构不变（capability/snapshot_binding/result 三键）。"""
        from mra.kg.capability_sdk import KGCapabilitySDK
        sdk = KGCapabilitySDK(KG_MERGED)
        out = sdk.kg_assertions("NCBITaxon:239935")
        assert set(out.keys()) == {"capability", "snapshot_binding", "result"}


class TestReadOnly:
    """P2 铁律：只读。"""

    def test_no_files_modified(self, builder, frozen_hash):
        """所有 PROV 操作后文件 hash 不变。"""
        # Run all traces and validations
        a = builder.assertions.iloc[0]["assertion_id"]
        builder.build_literature_trace(a)
        builder.build_curated_trace("NCBITaxon:853", "produces", "MESH:D002087")
        builder.build_materialization_trace()
        builder.validate_assertion_provenance()
        builder.validate_materialization_provenance()
        builder.validate_no_orphan_provenance()
        builder.validate_frozen_integrity(frozen_hash)
        # Verify hash unchanged
        current = "sha256:" + hashlib.sha256(
            (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
        assert current == frozen_hash
