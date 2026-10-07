"""P1 Capability SDK——AI Agent 安全消费 context-aware KG 知识的三个核心能力。

P1 铁律（裁决 2026-09-25）：
- **只增加消费能力，不修改知识资产**——frozen v1 assertion/hash/snapshot 不可变
- 三层 provenance 保持分离（curated source / literature assertion / materialization）
- canonical query 与 evidence query 分离——kg.neighbors() 不返回 evidence 节点
- snapshot 绑定：每次查询可追踪 snapshot_id / manifest / schema / execution

三个 Capability：
1. kg.assertions(entity, predicate?, target?, context_filter?) → evidence assertions for entity
2. kg.evidence(assertion_id) → 完整证据记录（来源、PMID、span、context、review）
3. kg.explain_relation(entity, predicate, target) → canonical edge 的可解释性输出

设计：本模块直接读 TSV 文件（relation_assertions.tsv + merged_edges.tsv），不依赖
Neo4j 连接——consumer 侧与 serving 层解耦，TSV 是 source of truth 的 snapshot 产物。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

# ---- Capability Contract ----

CAPABILITY_REGISTRY = {
    "kg.assertions": {
        "capability_id": "kg.assertions",
        "version": "0.1",
        "description": "查询实体对应的 evidence-level assertions（含 context/provenance）",
        "input_schema": {
            "entity": "str（entity ID 或可解析名称）",
            "predicate": "str?（可选过滤）",
            "target": "str?（可选过滤）",
            "context_filter": "dict?（可选：{dim: value} 过滤 context 维度）",
        },
        "output_schema": {
            "assertion_id": "str（内容寻址 SHA-256）",
            "subject": "str", "predicate": "str", "object": "str",
            "direction": "str", "confidence": "str",
            "evidence_pmid": "str", "evidence_span": "str",
            "context": "dict（15 维 evidence-aware）",
            "context_completeness": "float",
            "provenance": "dict（execution/capability/source/pipeline）",
            "divergence": "str（若属分歧组）",
            "manual_hold": "str（若被隔离）",
            "schema_version": "str",
        },
        "required_permissions": ["READ_ONLY"],
        "snapshot_dependency": "relation_assertions.tsv",
    },
    "kg.evidence": {
        "capability_id": "kg.evidence",
        "version": "0.1",
        "description": "根据 assertion_id 获取完整证据（回答'这条关系来自哪里'）",
        "input_schema": {"assertion_id": "str"},
        "output_schema": {
            "assertion_id": "str", "subject": "str", "predicate": "str", "object": "str",
            "evidence_pmid": "str", "evidence_span": "str",
            "context": "dict", "provenance": "dict",
            "review_status": "str（ok/dropped/hold）",
            "source": "str（pubtator3@2026-09）",
            "generation_metadata": "dict（execution/capability/schema/span_norm_version）",
        },
        "required_permissions": ["READ_ONLY"],
        "snapshot_dependency": "relation_assertions.tsv",
    },
    "kg.explain_relation": {
        "capability_id": "kg.explain_relation",
        "version": "0.1",
        "description": "解释 canonical relation：支持论文、实验条件、context 限制、divergence",
        "input_schema": {
            "entity": "str", "predicate": "str", "target": "str",
        },
        "output_schema": {
            "canonical_relation": "dict（subject/predicate/object/evidence_tier/support_count）",
            "supporting_assertions": "list[dict]",
            "context_summary": "dict（各维度分布）",
            "divergence": "dict|None（若存在分歧）",
            "provenance": "dict（curated/literature/materialization 三层）",
        },
        "required_permissions": ["READ_ONLY"],
        "snapshot_dependency": "merged_edges.tsv + relation_assertions.tsv",
    },
}

SDK_VERSION = "capability-sdk/0.1"


@dataclass
class SnapshotBinding:
    """P1 每次查询必须绑定的 snapshot 元数据。"""
    snapshot_id: str = ""
    manifest_version: str = ""
    schema_version: str = ""
    materialization_execution_id: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("snapshot_id", "manifest_version", "schema_version",
                 "materialization_execution_id")}


class KGCapabilitySDK:
    """KG 消费能力 SDK——只读，绑定 snapshot。"""

    def __init__(self, merged_dir: str | Path | None = None):
        self.merged_dir = Path(merged_dir or os.environ.get(
            "KG_MERGED_DIR",
            Path(__file__).resolve().parents[4] / "data" / "merged" / "candidate_v3"))
        self._assertions: pd.DataFrame | None = None
        self._edges: pd.DataFrame | None = None
        self._nodes: pd.DataFrame | None = None
        self._snapshot = self._load_snapshot_binding()

    def _load_snapshot_binding(self) -> SnapshotBinding:
        manifest_path = self.merged_dir / "snapshot_manifest.json"
        if not manifest_path.exists():
            return SnapshotBinding()
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            return SnapshotBinding(
                snapshot_id=m.get("snapshot_id", ""),
                manifest_version=m.get("kg_schema_version", ""),
                schema_version=m.get("assertion_schema_version", ""),
                materialization_execution_id=m.get("materialization_execution_id", ""))
        except (json.JSONDecodeError, OSError):
            return SnapshotBinding()

    @property
    def assertions(self) -> pd.DataFrame:
        if self._assertions is None:
            self._assertions = pd.read_csv(
                self.merged_dir / "relation_assertions.tsv", sep="\t").fillna("")
        return self._assertions

    @property
    def edges(self) -> pd.DataFrame:
        if self._edges is None:
            self._edges = pd.read_csv(
                self.merged_dir / "merged_edges.tsv", sep="\t").fillna("")
        return self._edges

    @property
    def nodes(self) -> pd.DataFrame:
        if self._nodes is None:
            self._nodes = pd.read_csv(
                self.merged_dir / "merged_nodes.tsv", sep="\t").fillna("")
        return self._nodes

    def _resolve_entity(self, entity: str) -> str:
        """按 ID → name → prefix 解析实体。"""
        if entity in set(self.assertions.subject) or entity in set(self.assertions.object):
            return entity
        match = self.nodes[self.nodes.name.str.lower() == entity.lower()]
        if not match.empty:
            return match.iloc[0]["id"]
        match = self.nodes[self.nodes.id.str.contains(entity, case=False, na=False)]
        if not match.empty:
            return match.iloc[0]["id"]
        return entity

    def _output_envelope(self, capability_id: str, result: Any) -> dict:
        """统一输出信封：capability + snapshot 绑定 + 结果。"""
        return {
            "capability": {
                "capability_id": capability_id,
                "sdk_version": SDK_VERSION,
                "permissions": ["READ_ONLY"],
            },
            "snapshot_binding": self._snapshot.to_dict(),
            "result": result,
        }

    # ---- Capability 1: kg.assertions() ----

    def kg_assertions(self, entity: str, predicate: str = "",
                      target: str = "", context_filter: dict | None = None) -> dict:
        """查询实体对应的 evidence assertions（含 context/provenance）。"""
        eid = self._resolve_entity(entity)
        df = self.assertions
        mask = (df.subject == eid) | (df.object == eid)
        if predicate:
            mask &= df.predicate == predicate
        if target:
            tid = self._resolve_entity(target)
            mask &= df.object == tid
        rows = []
        for _, r in df[mask].iterrows():
            try:
                ctx = json.loads(r.get("context", "{}"))
            except (json.JSONDecodeError, TypeError):
                ctx = {}
            if context_filter:
                skip = False
                for dim, val in context_filter.items():
                    if ctx.get(dim, {}).get("value", "") != val:
                        skip = True
                        break
                if skip:
                    continue
            rows.append({
                "assertion_id": r["assertion_id"],
                "subject": r["subject"], "predicate": r["predicate"],
                "object": r["object"], "direction": r.get("direction", ""),
                "confidence": r.get("confidence", ""),
                "evidence_pmid": r["evidence_pmid"],
                "evidence_span": r.get("evidence_span_norm", "")[:300],
                "context": ctx,
                "context_completeness": float(r.get("context_completeness", 0)),
                "provenance": json.loads(r.get("provenance", "{}")
                                         ) if r.get("provenance") else {},
                "divergence": r.get("divergence", ""),
                "manual_hold": r.get("manual_hold", ""),
            })
        return self._output_envelope("kg.assertions", {
            "entity": eid, "n_assertions": len(rows), "assertions": rows})

    # ---- Capability 2: kg.evidence() ----

    def kg_evidence(self, assertion_id: str) -> dict:
        """根据 assertion_id 获取完整证据。"""
        df = self.assertions
        match = df[df.assertion_id == assertion_id]
        if match.empty:
            return self._output_envelope("kg.evidence", {
                "found": False, "assertion_id": assertion_id})
        r = match.iloc[0]
        try:
            ctx = json.loads(r.get("context", "{}"))
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        try:
            prov = json.loads(r.get("provenance", "{}"))
        except (json.JSONDecodeError, TypeError):
            prov = {}
        review = "manual_hold" if r.get("manual_hold") else "ok"
        if r.get("divergence"):
            review = f"divergence_pending"
        result = {
            "found": True, "assertion_id": assertion_id,
            "subject": r["subject"], "predicate": r["predicate"],
            "object": r["object"],
            "evidence_pmid": r["evidence_pmid"],
            "evidence_span": r.get("evidence_span_norm", ""),
            "context": ctx,
            "provenance": prov,
            "review_status": review,
            "source": prov.get("source", ""),
            "generation_metadata": {
                "execution_id": prov.get("execution_id", ""),
                "capability_id": prov.get("capability_id", ""),
                "pipeline": prov.get("pipeline", ""),
                "span_normalization_version": "norm/0.2",
            }}
        return self._output_envelope("kg.evidence", result)

    # ---- Capability 3: kg.explain_relation() ----

    def kg_explain_relation(self, entity: str, predicate: str, target: str) -> dict:
        """解释 canonical relation：支持论文、条件、divergence。"""
        sid = self._resolve_entity(entity)
        tid = self._resolve_entity(target)

        # 1) Find canonical edge
        ce = self.edges[
            (self.edges.subject == sid) & (self.edges.predicate == predicate)
            & (self.edges.object == tid)]
        canonical = None
        if not ce.empty:
            r = ce.iloc[0]
            canonical = {
                "subject": sid, "predicate": predicate, "object": tid,
                "evidence_tier": r.get("evidence_tier", ""),
                "support_count": int(r.get("support_count", 0) or 0),
                "pmids": r.get("pmids", ""),
                "relation_status": r.get("relation_status", ""),
                "source_type": r.get("source_type", ""),
            }

        # 2) Find supporting assertions
        sa = self.assertions[
            (self.assertions.subject == sid) & (self.assertions.predicate == predicate)
            & (self.assertions.object == tid)]
        supporting = []
        for _, r in sa.iterrows():
            try:
                ctx = json.loads(r.get("context", "{}"))
            except (json.JSONDecodeError, TypeError):
                ctx = {}
            supporting.append({
                "assertion_id": r["assertion_id"],
                "PMID": r["evidence_pmid"],
                "evidence_span": r.get("evidence_span_norm", "")[:200],
                "context": ctx,
                "model": ctx.get("experimental_model", {}).get("value", ""),
                "population": ctx.get("host_population", {}).get("value", ""),
                "confidence": r.get("confidence", ""),
            })

        # 3) Context summary across assertions
        ctx_summary: dict = {}
        for s in supporting:
            for dim, spec in s["context"].items():
                if isinstance(spec, dict) and spec.get("status") == "explicit":
                    ctx_summary.setdefault(dim, set()).add(spec.get("value", ""))
        ctx_summary = {k: sorted(v) for k, v in ctx_summary.items()}

        # 4) Divergence check
        divergence = None
        opp = self.edges[
            (self.edges.subject == sid) & (self.edges.object == tid)
            & (self.edges.predicate != predicate)]
        if not opp.empty:
            div_types = self._get_divergence_type(sid, tid)
            divergence = {
                "detected": True,
                "opposing_predicates": opp.predicate.tolist(),
                "divergence_type": div_types,
                "note": "context-dependent relation——不同条件下方向不同",
            }

        # 5) Provenance (three layers)
        provenance = {
            "curated_source": canonical.get("source_type", "") if canonical else "",
            "literature_assertions": {
                "n_supporting": len(supporting),
                "pmids": [s["PMID"] for s in supporting],
                "extraction_execution": supporting[0]["context"].get("_exec", "")
                if supporting else "",
            },
            "materialization": self._snapshot.to_dict(),
        }

        return self._output_envelope("kg.explain_relation", {
            "canonical_relation": canonical,
            "supporting_assertions": supporting,
            "context_summary": ctx_summary,
            "divergence": divergence,
            "provenance": provenance,
        })

    def _get_divergence_type(self, sid: str, tid: str) -> list[str]:
        """查分歧类型。"""
        ann_path = self.merged_dir / "divergence_annotations.tsv"
        if not ann_path.exists():
            return []
        ann = pd.read_csv(ann_path, sep="\t").fillna("")
        match = ann[(ann.subject == sid) & (ann.object == tid)]
        if match.empty:
            return []
        r = match.iloc[0]
        types = [r.get("primary_divergence_type", "")]
        if r.get("secondary_divergence_type"):
            types.append(r["secondary_divergence_type"])
        return [t for t in types if t]

    # ---- Registry ----

    def list_capabilities(self) -> dict:
        return self._output_envelope("registry", {
            "sdk_version": SDK_VERSION,
            "capabilities": CAPABILITY_REGISTRY})


__all__ = ["CAPABILITY_REGISTRY", "KGCapabilitySDK", "SDK_VERSION", "SnapshotBinding"]
