"""P3 Graph Validation 测试——frozen v1 全量验证 + 5 项故意违规 regression。

P3 铁律：只读（frozen hash 不变）；违规测试在内存副本上执行，不动源文件。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

KG_MERGED = Path(os.environ.get(
    "KG_MERGED_DIR",
    "./data/merged"))

pytestmark = pytest.mark.skipif(
    not (KG_MERGED / "relation_assertions.tsv").exists(),
    reason="KG merged data not available")

from mra.kg.graph_validation import (GraphValidationEngine, Severity,
                                      ValidationRule)


@pytest.fixture(scope="module")
def engine():
    return GraphValidationEngine(KG_MERGED)


@pytest.fixture(scope="module")
def frozen_hash():
    return "sha256:" + hashlib.sha256(
        (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()


class TestFrozenV1FullValidation:
    """Test 5: 正常 frozen v1 → 全部 PASS。"""

    def test_full_validation_no_blockers(self, engine):
        """frozen v1: 无 BLOCKER（HIGH 级 warning 可接受——4 条 canonical edge
        缺 supporting assertion 为已知聚合差集，非阻塞）。"""
        report = engine.validate()
        assert report.rules_total == 6
        assert len(report.blockers) == 0  # BLOCKER = 0 即可 release
        assert report.passed >= 5  # 至少 5/6 规则完全通过

    def test_gate_check_pass(self, engine):
        gate = engine.gate_check()
        assert gate["can_release"] is True
        assert gate["can_materialize"] is True
        assert gate["n_blockers"] == 0

    def test_report_structure(self, engine):
        report = engine.validate()
        d = report.to_dict()
        assert "validation_run_id" in d
        assert "snapshot_id" in d
        assert "rules_total" in d
        assert "blockers" in d and "warnings" in d


class TestRuleRegistry:
    def test_six_rules_registered(self, engine):
        ids = {r.rule_id for r in engine.rules}
        assert ids == {
            "RULE_ASSERTION_EVIDENCE_REQUIRED",
            "RULE_MATERIALIZATION_ELIGIBLE",
            "RULE_MANUAL_HOLD_ISOLATION",
            "RULE_CANONICAL_EXPLAINABILITY",
            "RULE_PROVENANCE_COMPLETENESS",
            "RULE_SNAPSHOT_BINDING_INTEGRITY"}

    def test_each_rule_has_contract(self, engine):
        for r in engine.rules:
            assert r.rule_id and r.description and r.scope
            assert isinstance(r.severity, Severity)
            assert callable(r.validate)


class TestIntentionalViolations:
    """5 项故意违规测试（在内存副本上，不动源文件）。"""

    def _clone_engine(self, engine, mutate_fn):
        """深拷贝 engine 并对 assertions/manifest 做变异。"""
        import copy
        clone = GraphValidationEngine.__new__(GraphValidationEngine)
        clone.merged_dir = engine.merged_dir
        clone._assertions = engine.assertions.copy().astype(str)  # 全转 str 防 int64 赋空串报错
        clone._edges = engine.edges.copy()
        clone._manifest = copy.deepcopy(engine.manifest)
        clone.rules = engine._build_rules()
        # Re-bind rule methods to clone
        for rule in clone.rules:
            rule.validate = getattr(clone, f"_{rule.rule_id.split('_', 1)[1].lower()}_check",
                                    lambda _: []) if False else rule.validate
        # Actually rebuild rules bound to clone
        clone.rules = clone._build_rules()
        mutate_fn(clone)
        return clone

    def test_violation_1_missing_evidence(self, engine):
        """Test 1: 删除某 assertion evidence → FAIL。"""
        def mutate(clone):
            clone._assertions.loc[0, "evidence_pmid"] = ""
            clone._assertions.loc[0, "evidence_span_norm"] = ""
        clone = self._clone_engine(engine, mutate)
        report = clone.validate()
        assert report.failed >= 1
        assert any("EVIDENCE_REQUIRED" in str(d.get("rule_id", ""))
                   for d in report.details)

    def test_violation_2_hold_in_serving(self, engine):
        """Test 2: manual_hold 进入 eligible 集 → FAIL。"""
        def mutate(clone):
            ac = clone._manifest.get("assertion_counts", {})
            # Simulate: eligible includes hold (count mismatch)
            ac["materialization_eligible_assertion_count"] = (
                ac.get("retained_assertion_count", 100))
        clone = self._clone_engine(engine, mutate)
        report = clone.validate()
        assert report.failed >= 1
        assert any("MATERIALIZATION_ELIGIBLE" in str(d.get("rule_id", ""))
                   for d in report.details)

    def test_violation_3_canonical_no_assertion(self, engine):
        """Test 3: canonical edge 无 supporting assertion → FAIL。"""
        def mutate(clone):
            # 添加一条假的 llm_extracted canonical edge（无 assertion 支持）
            fake = {"subject": "FAKE:1", "predicate": "affects", "object": "FAKE:2",
                    "source_type": "llm_extracted", "evidence_tier": "B",
                    "pmids": "", "years": "", "support_count": "1",
                    "confidence": "0.9", "polarity": "neutral",
                    "last_updated": "2026-01-01", "relation_status": "",
                    "canonical_view": "derived_summary", "source_ref": "",
                    "curator": ""}
            clone._edges = pd.concat([clone._edges, pd.DataFrame([fake])],
                                     ignore_index=True)
        clone = self._clone_engine(engine, mutate)
        report = clone.validate()
        assert report.failed >= 1
        assert any("CANONICAL_EXPLAINABILITY" in str(d.get("rule_id", ""))
                   for d in report.details)

    def test_violation_4_missing_provenance(self, engine):
        """Test 4: 删除 provenance activity → FAIL。"""
        def mutate(clone):
            # 清空第一条 assertion 的 provenance
            clone._assertions.loc[0, "provenance"] = "{}"
        clone = self._clone_engine(engine, mutate)
        report = clone.validate()
        assert report.failed >= 1
        assert any("PROVENANCE_COMPLETENESS" in str(d.get("rule_id", ""))
                   for d in report.details)

    def test_violation_5_no_snapshot_binding(self, engine):
        """Test 5 变体: 删除 snapshot_id → FAIL。"""
        def mutate(clone):
            clone._manifest.pop("snapshot_id", None)
        clone = self._clone_engine(engine, mutate)
        report = clone.validate()
        assert report.failed >= 1
        assert any("SNAPSHOT_BINDING" in str(d.get("rule_id", ""))
                   for d in report.details)

    def test_violation_blocks_materialization(self, engine):
        """BLOCKER 级违规后 gate_check 返回 can_materialize=False。"""
        def mutate(clone):
            # 制造 BLOCKER 级违规：清空 provenance
            clone._assertions.loc[:, "provenance"] = "{}"
        clone = self._clone_engine(engine, mutate)
        gate = clone.gate_check()
        assert gate["can_materialize"] is False
        assert gate["n_blockers"] > 0


class TestDataIntegrity:
    """P3 铁律：执行后 frozen 数据不变。"""

    def test_source_files_unchanged(self, engine, frozen_hash):
        """所有验证操作后 hash 不变。"""
        engine.validate()
        engine.gate_check()
        current = "sha256:" + hashlib.sha256(
            (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
        assert current == frozen_hash

    def test_p1_p2_still_work(self, engine):
        """P1 SDK + P2 PROV 不受 P3 影响。"""
        from mra.kg.capability_sdk import KGCapabilitySDK
        from mra.kg.prov_standard import ProvenanceGraphBuilder
        sdk = KGCapabilitySDK(KG_MERGED)
        out = sdk.kg_assertions("NCBITaxon:239935")
        assert out["result"]["n_assertions"] > 0
        pb = ProvenanceGraphBuilder(KG_MERGED)
        trace = pb.build_materialization_trace()
        assert trace["trace_id"] == "TraceC_Materialization"
