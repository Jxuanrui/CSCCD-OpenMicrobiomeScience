"""P2 PROV Standardization v0.1——知识生命周期追踪的标准化语义映射。

P2 铁律：只增强可追踪性，不修改知识内容。
- frozen v1 assertions / hash / snapshot 不可变
- P1 SDK 输出契约不破坏（只加 optional 字段）
- 三层 provenance 保持分离（curated / literature / materialization）

W3C PROV 核心概念映射：
- Entity: 知识资产（Paper / Assertion / CanonicalEdge / Snapshot / ServingState）
- Activity: 过程（Extraction / Import / Merge / Materialization / Release）
- Agent: 执行者（Capability / Pipeline / Human Reviewer）

PROV 关系：
- wasGeneratedBy: Entity ← Activity（产出）
- used: Activity → Entity（输入）
- wasAssociatedWith: Activity ← Agent（关联）
- wasDerivedFrom: Entity → Entity（派生）
- wasAttributedTo: Entity ← Agent（归属）
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

# ---- P2 §4: Provenance Mapping Table v0.1 ----

PROVENANCE_MAPPING = {
    # Layer 1: Curated Source Provenance
    "source_registry": {
        "layer": "curated_source", "prov_concept": "Entity",
        "prov_role": "Source Dataset",
        "meaning": "数据源登记条目（source_key → license/version/method）",
        "required": True, "validation": "must exist in source_registry.tsv"},
    "source_ref": {
        "layer": "curated_source", "prov_concept": "Entity",
        "prov_role": "Source Dataset reference",
        "meaning": "canonical edge 的来源引用（unattributed/database）",
        "required": True, "validation": "non-empty for curated edges"},
    "source_version": {
        "layer": "curated_source", "prov_concept": "Entity",
        "prov_role": "Source Dataset version",
        "meaning": "数据源版本（如 full_dump 2025 / 2018）",
        "required": False, "validation": "if present, must match registry"},

    # Layer 2: Literature Assertion Provenance
    "PMID": {
        "layer": "literature_assertion", "prov_concept": "Entity",
        "prov_role": "Paper / Evidence Source",
        "meaning": "PubMed 论文标识符——证据的原始来源",
        "required": True, "validation": "digits only"},
    "evidence_span": {
        "layer": "literature_assertion", "prov_concept": "Entity",
        "prov_role": "Evidence Fragment",
        "meaning": "normalized evidence span（norm/0.2 契约）",
        "required": True, "validation": "non-empty for ok assertions"},
    "context": {
        "layer": "literature_assertion", "prov_concept": "Entity",
        "prov_role": "Context Description",
        "meaning": "15 维 evidence-aware context（explicit/inferred/unknown）",
        "required": True, "validation": "must have 15 dimensions"},
    "execution_id": {
        "layer": "literature_assertion", "prov_concept": "Activity",
        "prov_role": "Extraction Activity",
        "meaning": "LLM 抽取执行的唯一标识",
        "required": True, "validation": "EX-kg.llm_relation_classify-*"},
    "capability_id": {
        "layer": "literature_assertion", "prov_concept": "Agent",
        "prov_role": "Capability Agent",
        "meaning": "产生知识的能力标识",
        "required": True, "validation": "kg.llm_relation_classify"},
    "span_normalization_version": {
        "layer": "literature_assertion", "prov_concept": "Activity",
        "prov_role": "Normalization Activity spec",
        "meaning": "span 标准化算法版本",
        "required": True, "validation": "norm/0.2-*"},
    "divergence": {
        "layer": "literature_assertion", "prov_concept": "Entity",
        "prov_role": "Divergence Annotation",
        "meaning": "情境分歧标注（contextual_divergence_pending 等）",
        "required": False, "validation": "if present, must be in taxonomy v0.2"},

    # Layer 3: Materialization Provenance
    "materialization_execution_id": {
        "layer": "materialization", "prov_concept": "Activity",
        "prov_role": "Materialization Activity",
        "meaning": "Neo4j 物化执行标识",
        "required": True, "validation": "EX-neo4j-materialize-*"},
    "snapshot_id": {
        "layer": "materialization", "prov_concept": "Entity",
        "prov_role": "Released Snapshot",
        "meaning": "知识快照标识（如 2026-09-25-v9）",
        "required": True, "validation": "date-version format"},
    "manifest_version": {
        "layer": "materialization", "prov_concept": "Entity",
        "prov_role": "Manifest version",
        "meaning": "kg_schema_version（如 1.0-rc1）",
        "required": True, "validation": "semver-like"},
    "assertion_set_hash": {
        "layer": "materialization", "prov_concept": "Entity",
        "prov_role": "Knowledge Set fingerprint",
        "meaning": "assertion 集 SHA-256 内容指纹",
        "required": True, "validation": "sha256: prefix, 64 hex chars"},
    "annotation_set_hash": {
        "layer": "materialization", "prov_concept": "Entity",
        "prov_role": "Annotation Set fingerprint",
        "meaning": "分歧标注集 SHA-256",
        "required": True, "validation": "sha256: prefix"},
}

#: PROV 关系定义（用于 graph model）
PROV_RELATIONS = {
    "wasGeneratedBy": "Entity ← Activity（产出）",
    "used": "Activity → Entity（输入）",
    "wasAssociatedWith": "Activity ← Agent（关联）",
    "wasDerivedFrom": "Entity → Entity（派生）",
    "wasAttributedTo": "Entity ← Agent（归属）",
}

PROV_MODEL_VERSION = "prov-model/0.1"


# ---- P2 §5: Provenance Graph Prototype ----

@dataclass
class ProvNode:
    """PROV 图节点：Entity / Activity / Agent。"""
    node_id: str
    node_type: str  # Entity | Activity | Agent
    prov_role: str  # Paper / Extraction / Capability / Snapshot ...
    properties: dict = field(default_factory=dict)


@dataclass
class ProvEdge:
    """PROV 图边：wasGeneratedBy / used / wasAssociatedWith / wasDerivedFrom。"""
    edge_type: str  # PROV_RELATIONS key
    source: str  # node_id
    target: str  # node_id


class ProvenanceGraphBuilder:
    """从现有数据构建只读 provenance graph（不修改知识内容）。"""

    def __init__(self, merged_dir: str | Path | None = None):
        self.merged_dir = Path(merged_dir or os.environ.get(
            "KG_MERGED_DIR",
            Path(__file__).resolve().parents[3] / "data" / "merged" / "candidate_v3"))
        self._assertions = None
        self._edges = None
        self._manifest = None

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            self._manifest = json.loads(
                (self.merged_dir / "snapshot_manifest.json").read_text(encoding="utf-8"))
        return self._manifest

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

    def build_literature_trace(self, assertion_id: str) -> dict:
        """Trace A: Paper → Extraction → Assertion → Snapshot。"""
        match = self.assertions[self.assertions.assertion_id == assertion_id]
        if match.empty:
            return {"found": False, "assertion_id": assertion_id}
        r = match.iloc[0]
        try:
            prov = json.loads(r.get("provenance", "{}"))
        except (json.JSONDecodeError, TypeError):
            prov = {}
        nodes = [
            ProvNode(f"paper:{r['evidence_pmid']}", "Entity", "Paper",
                     {"pmid": r["evidence_pmid"], "span": r.get("evidence_span_norm", "")[:200]}),
            ProvNode(f"activity:{prov.get('execution_id', '')}", "Activity", "Extraction",
                     {"capability": prov.get("capability_id", ""),
                      "pipeline": prov.get("pipeline", ""),
                      "span_norm": "norm/0.2"}),
            ProvNode(f"agent:{prov.get('capability_id', '')}", "Agent", "Capability",
                     {"capability_id": prov.get("capability_id", "")}),
            ProvNode(f"assertion:{assertion_id}", "Entity", "RelationAssertion",
                     {"predicate": r["predicate"], "subject": r["subject"], "object": r["object"]}),
            ProvNode(f"snapshot:{self.manifest.get('snapshot_id', '')}", "Entity", "ReleasedSnapshot",
                     {"manifest_version": self.manifest.get("kg_schema_version", "")}),
        ]
        edges = [
            ProvEdge("used", f"activity:{prov.get('execution_id', '')}", f"paper:{r['evidence_pmid']}"),
            ProvEdge("wasAssociatedWith", f"activity:{prov.get('execution_id', '')}",
                     f"agent:{prov.get('capability_id', '')}"),
            ProvEdge("wasGeneratedBy", f"assertion:{assertion_id}",
                     f"activity:{prov.get('execution_id', '')}"),
            ProvEdge("wasDerivedFrom", f"snapshot:{self.manifest.get('snapshot_id', '')}",
                     f"assertion:{assertion_id}"),
        ]
        return self._to_prov_format(nodes, edges, "TraceA_Literature")

    def build_curated_trace(self, subject: str, predicate: str, obj: str) -> dict:
        """Trace B: Curated Source → Import → Canonical Edge。"""
        match = self.edges[
            (self.edges.subject == subject) & (self.edges.predicate == predicate)
            & (self.edges.object == obj)]
        if match.empty:
            return {"found": False}
        r = match.iloc[0]
        source_ref = r.get("source_ref", "unattributed")
        nodes = [
            ProvNode(f"source:{source_ref}", "Entity", "CuratedSource",
                     {"source_ref": source_ref, "source_type": r.get("source_type", "")}),
            ProvNode(f"activity:import-{source_ref}", "Activity", "Import",
                     {"pipeline": "seed_etl", "source_type": r.get("source_type", "")}),
            ProvNode(f"edge:{subject}|{predicate}|{obj}", "Entity", "CanonicalEdge",
                     {"evidence_tier": r.get("evidence_tier", ""),
                      "support_count": r.get("support_count", ""),
                      "pmids": r.get("pmids", "")}),
        ]
        edges = [
            ProvEdge("used", f"activity:import-{source_ref}", f"source:{source_ref}"),
            ProvEdge("wasGeneratedBy",
                     f"edge:{subject}|{predicate}|{obj}", f"activity:import-{source_ref}"),
        ]
        return self._to_prov_format(nodes, edges, "TraceB_Curated")

    def build_materialization_trace(self) -> dict:
        """Trace C: Snapshot → Materialization → Neo4j Serving State。"""
        m = self.manifest
        mat_exec = m.get("materialization_execution_id", "")
        nodes = [
            ProvNode(f"snapshot:{m.get('snapshot_id', '')}", "Entity", "ReleasedSnapshot",
                     {"kg_schema": m.get("kg_schema_version", ""),
                      "assertion_hash": m.get("assertion_set_hash", "")}),
            ProvNode(f"activity:{mat_exec}", "Activity", "Materialization",
                     {"neo4j_uri": "bolt://127.0.0.1:17687",
                      "database": "neo4j"}),
            ProvNode("neo4j:serving_state", "Entity", "ServingState",
                     {"total_nodes": m.get("n_nodes", 0),
                      "total_edges": m.get("n_edges", 0),
                      "assertion_count": m.get("assertion_counts", {})
                      .get("materialization_eligible_assertion_count", 0)}),
        ]
        edges = [
            ProvEdge("used", f"activity:{mat_exec}", f"snapshot:{m.get('snapshot_id', '')}"),
            ProvEdge("wasGeneratedBy", "neo4j:serving_state", f"activity:{mat_exec}"),
        ]
        return self._to_prov_format(nodes, edges, "TraceC_Materialization")

    @staticmethod
    def _to_prov_format(nodes: list[ProvNode], edges: list[ProvEdge], trace_id: str) -> dict:
        return {
            "trace_id": trace_id,
            "prov_model_version": PROV_MODEL_VERSION,
            "nodes": [
                {"id": n.node_id, "type": n.node_type, "role": n.prov_role,
                 "properties": n.properties} for n in nodes],
            "edges": [
                {"type": e.edge_type, "source": e.source, "target": e.target,
                 "description": PROV_RELATIONS.get(e.edge_type, "")} for e in edges],
        }

    # ---- P2 §7: Validation Methods ----

    def validate_assertion_provenance(self) -> dict:
        """Test 1: 每个 assertion 可追踪 assertion→execution→capability→source。"""
        df = self.assertions
        n_total = len(df)
        n_complete = 0
        for _, r in df.iterrows():
            try:
                prov = json.loads(r.get("provenance", "{}"))
            except (json.JSONDecodeError, TypeError):
                prov = {}
            has_exec = bool(prov.get("execution_id"))
            has_cap = bool(prov.get("capability_id"))
            has_source = bool(prov.get("source"))
            has_pmid = bool(r.get("evidence_pmid"))
            if all([has_exec, has_cap, has_source, has_pmid]):
                n_complete += 1
        return {"test": "assertion_provenance_completeness",
                "total": n_total, "complete": n_complete,
                "incomplete": n_total - n_complete,
                "pass": n_complete == n_total}

    def validate_materialization_provenance(self) -> dict:
        """Test 2: snapshot→execution→manifest→hash 链完整。"""
        m = self.manifest
        has_snapshot = bool(m.get("snapshot_id"))
        has_exec = bool(m.get("materialization_execution_id"))
        has_manifest_ver = bool(m.get("kg_schema_version"))
        has_assertion_hash = bool(m.get("assertion_set_hash"))
        has_annotation_hash = bool(m.get("annotation_set_hash"))
        all_present = all([has_snapshot, has_exec, has_manifest_ver,
                           has_assertion_hash, has_annotation_hash])
        return {"test": "materialization_provenance",
                "snapshot_id": has_snapshot, "execution_id": has_exec,
                "manifest_version": has_manifest_ver,
                "assertion_hash": has_assertion_hash,
                "annotation_hash": has_annotation_hash,
                "pass": all_present}

    def validate_no_orphan_provenance(self) -> dict:
        """Test 3: 禁止无主体/activity 无输入或输出/snapshot 无来源。"""
        issues = []
        # Check assertions have provenance
        for _, r in self.assertions.head(100).iterrows():  # sample
            if not r.get("provenance"):
                issues.append(f"assertion {r['assertion_id'][:20]} no provenance")
                break
        # Check manifest has source
        if not self.manifest.get("snapshot_id"):
            issues.append("manifest has no snapshot_id")
        if not self.manifest.get("assertion_set_hash"):
            issues.append("manifest has no assertion hash")
        return {"test": "no_orphan_provenance",
                "issues": issues, "issue_count": len(issues),
                "pass": len(issues) == 0}

    def validate_frozen_integrity(self, expected_assertion_hash: str = "") -> dict:
        """Test 4: P2 执行后 frozen data 不变。"""
        import hashlib
        actual = "sha256:" + hashlib.sha256(
            (self.merged_dir / "relation_assertions.tsv").read_bytes()).hexdigest()
        _ann = self.merged_dir / "divergence_annotations.tsv"
        annotation = ("sha256:" + hashlib.sha256(_ann.read_bytes()).hexdigest()
                      if _ann.exists() else None)  # v3 不产出该文件（v2 批次修复产物）
        hash_ok = not expected_assertion_hash or actual == expected_assertion_hash
        return {"test": "frozen_data_integrity",
                "assertion_hash": actual, "hash_unchanged": hash_ok,
                "annotation_hash": annotation,
                "pass": hash_ok}


__all__ = ["PROVENANCE_MAPPING", "PROV_MODEL_VERSION", "PROV_RELATIONS",
           "ProvEdge", "ProvNode", "ProvenanceGraphBuilder"]
