"""P4 Scale Readiness 测试——cost model / policy / validation scale / gate 完整性。"""
from __future__ import annotations

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

from mra.kg.scale_readiness import (
    CANONICAL_EXPLAINABILITY_POLICY, EXPANSION_GATE,
    build_cost_model, generate_scale_readiness_report,
    validation_scale_test, SCALE_FACTOR)


class TestCostModel:
    def test_baseline_metrics(self):
        cost = build_cost_model(KG_MERGED)
        b = cost["baseline"]
        assert b["papers"] == 56_629
        # 基线从数据实读（P0-4 修复：4500 硬编码→动态读取）
        import csv as _csv
        from pathlib import Path as _P
        _tsv = KG_MERGED / 'relation_assertions.tsv'
        _n = sum(1 for _ in open(_tsv)) - 1
        assert b['assertions'] == _n, f"基线 {_n} != 实际 {b['assertions']}（来源: relation_assertions.tsv）"
        assert b["acceptance_rate"] > 0.05  # >5% acceptance
        assert b["api_calls_per_accepted_assertion"] > 0

    def test_projection_130k(self):
        cost = build_cost_model(KG_MERGED)
        p = cost["projection_130k"]
        assert p["papers"] == 130_000
        assert p["assertions"] > 8_000  # >8k projected
        assert p["runtime_hours"] < 200  # <200h

    def test_scale_factor(self):
        assert SCALE_FACTOR > 2.0 and SCALE_FACTOR < 3.0


class TestExplainabilityPolicy:
    def test_policy_decision(self):
        assert CANONICAL_EXPLAINABILITY_POLICY["decision"] == "OPTION_B"

    def test_policy_has_rules(self):
        assert len(CANONICAL_EXPLAINABILITY_POLICY["rules"]) >= 3

    def test_policy_addresses_current_gap(self):
        assert "4/500" in CANONICAL_EXPLAINABILITY_POLICY["rationale"]


class TestValidationScale:
    def test_10x_scaling(self):
        result = validation_scale_test(KG_MERGED, multiplier=10)
        assert result["linear_scaling"] is True
        assert result["130k_projection_runtime_s"] < 5  # <5s for full validation


class TestExpansionGate:
    def test_seven_steps(self):
        assert len(EXPANSION_GATE["steps"]) == 7

    def test_rollback_strategy(self):
        assert "immutable" in EXPANSION_GATE["rollback"]["strategy"].lower()

    def test_batch_strategy(self):
        batches = EXPANSION_GATE["batch_strategy"]["recommended_batches"]
        assert sum(batches) >= 130_000


class TestReadinessReport:
    def test_full_report_generatable(self):
        report = generate_scale_readiness_report(KG_MERGED)
        assert report["overall_ready"] is True
        checklist = report["readiness_checklist"]
        assert all(checklist.values())

    def test_frozen_unchanged(self):
        """Scale readiness 只读。"""
        import hashlib
        h = "sha256:" + hashlib.sha256(
            (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
        generate_scale_readiness_report(KG_MERGED)
        h2 = "sha256:" + hashlib.sha256(
            (KG_MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
        assert h == h2
