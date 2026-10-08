#!/usr/bin/env python3
"""Phase U'（P0-I）：MCP 两新工具（kg_get_assertions / kg_get_prov）回归测试。

只测 SDK 包装层与真实快照数据（只读）；不启动 stdio server。
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MERGED = ROOT / "data/merged/candidate_v2"  # 冻结 v2 基线（G1 勘误：provenance 缺 resource_ref 等四件套字段，见 CHANGELOG 勘误段）
pytestmark = pytest.mark.skipif(
    not (MERGED / "relation_assertions.tsv").exists(),
    reason="无恢复基线快照（data/merged）")


def test_get_assertions_wraps_sdk(monkeypatch):
    monkeypatch.setenv("KG_MERGED_DIR", str(MERGED))
    sys.path.insert(0, str(ROOT / "mra/src"))
    from mra.mcp_server import _sdk, kg_get_assertions, kg_get_prov
    out = json.loads(kg_get_assertions("NCBITaxon:853"))  # F. prausnitzii
    assert out["capability"]["capability_id"] == "kg.assertions"
    assert out["snapshot_binding"]["snapshot_id"]
    rows = out["result"]["assertions"]
    assert rows, "恢复基线应含该菌断言"
    r0 = rows[0]
    assert r0["evidence_pmid"] and "context" in r0 and "provenance" in r0


def test_get_prov_roundtrip(monkeypatch):
    monkeypatch.setenv("KG_MERGED_DIR", str(MERGED))
    sys.path.insert(0, str(ROOT / "mra/src"))
    from mra.mcp_server import kg_get_assertions, kg_get_prov
    rows = json.loads(kg_get_assertions("NCBITaxon:853"))["result"]["assertions"]
    aid = rows[0]["assertion_id"]
    out = json.loads(kg_get_prov(aid))
    assert out["result"]["found"] is True
    assert out["result"]["assertion_id"] == aid
    prov = out["result"]["provenance"]
    assert prov.get("execution_id", "").startswith("EX-"), "四件套重建后应带 execution"
    for f in ("resource_ref", "retrieved_at", "source_version", "raw_hash"):
        assert prov.get(f), f"provenance 四件套缺 {f}"


def test_get_prov_missing_id(monkeypatch):
    monkeypatch.setenv("KG_MERGED_DIR", str(MERGED))
    sys.path.insert(0, str(ROOT / "mra/src"))
    from mra.mcp_server import kg_get_prov
    out = json.loads(kg_get_prov("RA-nonexistent000000"))
    assert out["result"]["found"] is False
