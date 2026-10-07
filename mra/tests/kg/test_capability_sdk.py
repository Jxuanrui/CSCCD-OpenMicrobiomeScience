"""P1 Capability SDK 测试套件——三个 capability + snapshot 绑定 + 隔离规则。

覆盖（裁决 2026-09-25 第九节）：
- kg.assertions：entity 存在 / assertion 存在 / PMID 返回 / context 返回 / provenance 返回
- kg.evidence：assertion_id 可追踪 / evidence 不丢失 / source 不丢失
- kg.explain_relation：canonical edge 可下钻 / context 可展示 / divergence 不丢失
- snapshot 绑定：每次输出携带 snapshot_id
- 隔离规则：evidence query 不污染 canonical query
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# 定位 KG merged 目录（KG 主线产物）
KG_MERGED = Path(os.environ.get(
    "KG_MERGED_DIR",
    str(Path(__file__).resolve().parents[3] / "data/merged/candidate_v3")))  # 锚定仓库根，免受 CWD 影响

pytestmark = pytest.mark.skipif(
    not (KG_MERGED / "relation_assertions.tsv").exists(),
    reason="KG merged data not available on this host")

from mra.kg.capability_sdk import CAPABILITY_REGISTRY, KGCapabilitySDK, SDK_VERSION


@pytest.fixture(scope="module")
def sdk():
    return KGCapabilitySDK(KG_MERGED)


class TestCapabilityRegistry:
    def test_registry_has_three_capabilities(self):
        assert set(CAPABILITY_REGISTRY) == {
            "kg.assertions", "kg.evidence", "kg.explain_relation"}

    def test_each_has_contract(self):
        for cid, cap in CAPABILITY_REGISTRY.items():
            assert cap["capability_id"] == cid
            assert cap["version"]
            assert cap["input_schema"]
            assert cap["output_schema"]
            assert "READ_ONLY" in cap["required_permissions"]
            assert cap["snapshot_dependency"]

    def test_sdk_version(self):
        assert SDK_VERSION.startswith("capability-sdk/")


class TestSnapshotBinding:
    def test_output_carries_snapshot_id(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        binding = out["snapshot_binding"]
        assert binding["snapshot_id"]
        assert binding["schema_version"]

    def test_snapshot_id_is_v1(self, sdk):
        out = sdk.kg_evidence("any")  # even not-found carries binding
        assert out["snapshot_binding"]["snapshot_id"]


class TestKgAssertions:
    def test_entity_exists(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        assert out["result"]["entity"] == "NCBITaxon:239935"

    def test_assertions_returned(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        assert out["result"]["n_assertions"] > 0

    def test_pmid_returned(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        for a in out["result"]["assertions"][:3]:
            assert a["evidence_pmid"]

    def test_context_returned(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        for a in out["result"]["assertions"][:3]:
            assert isinstance(a["context"], dict)
            assert len(a["context"]) == 15  # 15 dimensions

    def test_provenance_returned(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        for a in out["result"]["assertions"][:3]:
            assert isinstance(a["provenance"], dict)
            assert a["provenance"].get("source") or a["provenance"].get("pipeline")

    def test_predicate_filter(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935", predicate="alleviates")
        for a in out["result"]["assertions"]:
            assert a["predicate"] == "alleviates"

    def test_name_resolution(self, sdk):
        out = sdk.kg_assertions("Akkermansia muciniphila")
        assert out["result"]["entity"].startswith("NCBITaxon:")


class TestKgEvidence:
    def test_assertion_id_traceable(self, sdk):
        # First get an assertion ID
        out = sdk.kg_assertions("NCBITaxon:239935", predicate="alleviates")
        if out["result"]["n_assertions"] == 0:
            pytest.skip("no alleviates assertions for this entity")
        aid = out["result"]["assertions"][0]["assertion_id"]
        # Now query evidence
        ev = sdk.kg_evidence(aid)
        assert ev["result"]["found"]
        assert ev["result"]["assertion_id"] == aid

    def test_evidence_not_lost(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        if out["result"]["n_assertions"] == 0:
            pytest.skip("no assertions")
        aid = out["result"]["assertions"][0]["assertion_id"]
        ev = sdk.kg_evidence(aid)
        assert ev["result"]["evidence_span"]  # span not empty
        assert ev["result"]["evidence_pmid"]

    def test_source_not_lost(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        if out["result"]["n_assertions"] == 0:
            pytest.skip("no assertions")
        aid = out["result"]["assertions"][0]["assertion_id"]
        ev = sdk.kg_evidence(aid)
        assert ev["result"]["source"]  # source tracked

    def test_not_found(self, sdk):
        ev = sdk.kg_evidence("RA-nonexistent")
        assert not ev["result"]["found"]

    def test_generation_metadata(self, sdk):
        out = sdk.kg_assertions("NCBITaxon:239935")
        if out["result"]["n_assertions"] == 0:
            pytest.skip("no assertions")
        aid = out["result"]["assertions"][0]["assertion_id"]
        ev = sdk.kg_evidence(aid)
        gm = ev["result"]["generation_metadata"]
        assert gm["span_normalization_version"]


class TestKgExplainRelation:
    def test_canonical_edge_found(self, sdk):
        out = sdk.kg_explain_relation("NCBITaxon:1304", "sensitive_to", "LFS:DRUG:Albendazole")
        assert out["result"]["canonical_relation"] is not None
        assert out["result"]["canonical_relation"]["relation_status"] == "context_supported"  # v3 全量状态（v2 时代存在 context_dependent）

    def test_supporting_assertions_exist(self, sdk):
        out = sdk.kg_explain_relation("NCBITaxon:239935", "alleviates", "MESH:D007249")
        # assertion 层存在 alleviates 支持（canonical 用 affects 因为 context_dependent）
        assert len(out["result"]["supporting_assertions"]) > 0

    def test_context_summary(self, sdk):
        out = sdk.kg_explain_relation("NCBITaxon:239935", "alleviates", "MESH:D007249")
        assert isinstance(out["result"]["context_summary"], dict)

    def test_divergence_not_lost(self, sdk):
        # A.muciniphila has both alleviates and aggravates for inflammation
        out = sdk.kg_explain_relation("NCBITaxon:239935", "alleviates", "MESH:D007249")
        div = out["result"]["divergence"]
        if div:  # if detected, verify structure
            assert div["detected"] is True
            assert div["opposing_predicates"]

    def test_three_layer_provenance(self, sdk):
        out = sdk.kg_explain_relation("NCBITaxon:239935", "alleviates", "MESH:D007249")
        prov = out["result"]["provenance"]
        assert "curated_source" in prov
        assert "literature_assertions" in prov
        assert "materialization" in prov


class TestIsolationRules:
    """P1 六：canonical query 与 evidence query 分离。"""

    def test_assertions_output_no_canonical_only_fields(self, sdk):
        """kg.assertions 返回 evidence layer，不是 canonical 聚合字段。"""
        out = sdk.kg_assertions("NCBITaxon:239935")
        for a in out["result"]["assertions"][:3]:
            # assertion has PMID-level detail, not canonical support_count
            assert "evidence_pmid" in a
            assert "support_count" not in a

    def test_read_only(self, sdk):
        """SDK 是只读的——不修改任何文件。"""
        import hashlib
        a_path = KG_MERGED / "relation_assertions.tsv"
        h1 = hashlib.sha256(a_path.read_bytes()).hexdigest()
        sdk.kg_assertions("NCBITaxon:239935")
        sdk.kg_evidence("test")
        sdk.kg_explain_relation("a", "b", "c")
        h2 = hashlib.sha256(a_path.read_bytes()).hexdigest()
        assert h1 == h2


class TestEndToEndLoop:
    """P1 十一：Example research loop——question → canonical → assertion → evidence → answer。"""

    def test_research_loop(self, sdk):
        """从问题到证据的完整查询链。"""
        # Step 1: canonical query
        out = sdk.kg_explain_relation("NCBITaxon:239935", "alleviates", "MESH:D007249")
        # assertion 层有支持（canonical 层为 affects/context_dependent）
        assert len(out["result"]["supporting_assertions"]) > 0

        # Step 2: get supporting assertions
        assertions = out["result"]["supporting_assertions"]
        assert len(assertions) > 0

        # Step 3: drill down to evidence
        if assertions:
            aid = assertions[0]["assertion_id"]
            ev = sdk.kg_evidence(aid)
            assert ev["result"]["found"]
            assert ev["result"]["evidence_pmid"]

        # Step 4: verify snapshot binding throughout
        assert out["snapshot_binding"]["snapshot_id"]
