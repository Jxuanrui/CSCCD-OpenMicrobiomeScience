#!/usr/bin/env python3
"""P0-1 构造性回归测试（2026-09-29 监工令）：断言引用实体闭包 → 物化 orphan=0 的上游保证。

覆盖：Tier-C 缺失实体补建 / 幂等 / no_relation 与非 ok 行排除 / 字段回退 / 损坏行容忍。
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_qc import close_entity_closure  # noqa: E402


def _row(status="ok", predicate="alleviates", pmid="12345",
         sid="NCBITaxon:853", sname="F. prausnitzii", scat="Microbe",
         oid="MESH:D003092", oname="Colitis", ocat="Disease"):
    return {"status": status, "predicate": predicate, "pmid": pmid,
            "subject": {"id": sid, "name": sname, "category": scat},
            "object": {"id": oid, "name": oname, "category": ocat}}


def _nodes(ids):
    return pd.DataFrame([{"id": i, "name": i, "category": "Disease",
                          "aliases": "", "xrefs": "", "tax_rank": ""} for i in ids])


def _stage(tmp_path, rows, corrupt=False):
    p = tmp_path / "llm_relations.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    if corrupt:
        lines.insert(1, '{"broken json …')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_tier_c_missing_entity_added(tmp_path):
    # Tier-C（单篇）断言引用的实体不在节点表 → 必须补建，category 继承
    stage = _stage(tmp_path, [_row()])
    nodes = _nodes(["MESH:D003092"])  # 只有 object，subject 缺失
    out = close_entity_closure(nodes, stage)
    assert "NCBITaxon:853" in set(out["id"])
    row = out[out["id"] == "NCBITaxon:853"].iloc[0]
    assert row["category"] == "Microbe" and row["name"] == "F. prausnitzii"


def test_idempotent_second_run_no_growth(tmp_path):
    stage = _stage(tmp_path, [_row()])
    nodes = _nodes(["MESH:D003092"])
    once = close_entity_closure(nodes, stage)
    twice = close_entity_closure(once, stage)
    assert len(once) == len(twice) == 2


def test_no_relation_and_non_ok_excluded(tmp_path):
    stage = _stage(tmp_path, [
        _row(status="ok", predicate="no_relation", sid="NCBITaxon:X1", oid="MESH:X2"),
        _row(status="error", sid="NCBITaxon:X3", oid="MESH:X4"),
    ])
    nodes = _nodes(["MESH:D003092"])
    out = close_entity_closure(nodes, stage)
    assert set(out["id"]) == {"MESH:D003092"}


def test_field_fallbacks(tmp_path):
    stage = _stage(tmp_path, [_row(sid="NCBITaxon:999", sname=None, scat=None)])
    nodes = _nodes(["MESH:D003092"])
    out = close_entity_closure(nodes, stage)
    row = out[out["id"] == "NCBITaxon:999"].iloc[0]
    assert row["name"] == "NCBITaxon:999" and row["category"] == "literature_only"


def test_corrupt_line_skipped(tmp_path):
    stage = _stage(tmp_path, [_row()], corrupt=True)
    nodes = _nodes(["MESH:D003092"])
    out = close_entity_closure(nodes, stage)  # 不得抛异常
    assert "NCBITaxon:853" in set(out["id"])


def test_missing_stage_file_returns_nodes(tmp_path):
    out = close_entity_closure(_nodes(["MESH:D003092"]), tmp_path / "absent.jsonl")
    assert len(out) == 1
