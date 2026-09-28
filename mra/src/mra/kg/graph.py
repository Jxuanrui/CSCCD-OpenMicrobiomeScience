"""进程内 KG 图服务：加载快照 TSV 为纯内存结构（零第三方依赖），供 agent 检索。

与 schema/microbiome_kg.linkml.yaml 对齐的约定：
- pmids/years 字段分隔符同时接受 ';'（llm_extracted）与 '|'（curated）；
- 任何检索结果必须携带证据属性（evidence_tier/pmids/years/confidence），
  调用方禁止把三元组与证据拆开使用（共识：Tier-C/predicted 不进本图）。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

csv.field_size_limit(10**9)

NODES_FILE = "merged_nodes.tsv"
EDGES_FILE = "merged_edges.tsv"


def _split_multi(raw: str) -> list[str]:
    return [x for x in (raw or "").replace("|", ";").split(";") if x.strip()]


@dataclass
class Node:
    id: str
    name: str
    category: str
    aliases: list[str] = field(default_factory=list)
    xrefs: list[str] = field(default_factory=list)
    tax_rank: str = ""


@dataclass
class Edge:
    subject: str
    predicate: str
    object: str
    source_type: str
    evidence_tier: str
    pmids: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    support_count: int = 0
    confidence: float = 0.0
    polarity: str = ""
    last_updated: str = ""

    @property
    def earliest_year(self) -> int | None:
        return min(self.years) if self.years else None

    def evidence(self) -> dict:
        return {
            "source_type": self.source_type,
            "evidence_tier": self.evidence_tier,
            "pmids": self.pmids,
            "years": self.years,
            "support_count": self.support_count,
            "confidence": self.confidence,
        }


def _node_from_row(row: dict) -> Node:
    return Node(
        id=row["id"],
        name=row.get("name", ""),
        category=row.get("category", ""),
        aliases=_split_multi(row.get("aliases", "")),
        xrefs=_split_multi(row.get("xrefs", "")),
        tax_rank=row.get("tax_rank", ""),
    )


def _edge_from_row(row: dict) -> Edge:
    return Edge(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        source_type=row.get("source_type", ""),
        evidence_tier=row.get("evidence_tier", ""),
        pmids=_split_multi(row.get("pmids", "")),
        years=[int(y) for y in _split_multi(row.get("years", "")) if y.strip().isdigit()],
        support_count=int(row.get("support_count") or 0),
        confidence=float(row.get("confidence") or 0.0),
        polarity=row.get("polarity", ""),
        last_updated=row.get("last_updated", ""),
    )


class KGGraph:
    """加载快照目录为内存图；图规模 ~6k 节点 / ~19k 边，邻接表即可毫秒级 k 跳。"""

    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = Path(snapshot_dir)
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._by_pair: dict[tuple[str, str], list[Edge]] = {}
        self._adj_out: dict[str, list[tuple[Edge, str]]] = {}
        self._adj_in: dict[str, list[tuple[Edge, str]]] = {}
        self._name_index: dict[str, str] = {}  # 小写 name/alias -> node_id（首个命中）
        self._load()

    def _load(self) -> None:
        with open(self.snapshot_dir / NODES_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                node = _node_from_row(row)
                self.nodes[node.id] = node
        with open(self.snapshot_dir / EDGES_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                edge = _edge_from_row(row)
                self.edges.append(edge)
                self._by_pair.setdefault((edge.subject, edge.object), []).append(edge)
                self._adj_out.setdefault(edge.subject, []).append((edge, edge.object))
                self._adj_in.setdefault(edge.object, []).append((edge, edge.subject))
        for node in self.nodes.values():
            for key in (node.name, *node.aliases):
                if key:
                    self._name_index.setdefault(key.strip().lower(), node.id)

    # ---------- 实体解析 ----------
    def resolve(self, term: str) -> list[Node]:
        """按 精确 ID → 精确 name/alias（不区分大小写）→ 名称前缀 兜底解析。"""
        term = term.strip()
        if not term:
            return []
        if term in self.nodes:
            return [self.nodes[term]]
        exact = self._name_index.get(term.lower())
        if exact:
            return [self.nodes[exact]]
        prefix = term.lower()
        return [
            self.nodes[nid]
            for key, nid in self._name_index.items()
            if key.startswith(prefix)
        ][:20]

    # ---------- k 跳检索 ----------
    def neighbors(
        self,
        node_id: str,
        hops: int = 1,
        categories: set[str] | None = None,
        predicates: set[str] | None = None,
    ) -> dict:
        """无向 BFS k 跳扇出；categories 只过滤返回的邻居，遍历可穿过不匹配节点
        （例：查菌 2 跳内的疾病必须途经代谢物）。返回邻居、路径边（含证据）与类别统计。"""
        if hops < 1:
            raise ValueError("hops 必须 >= 1")
        visited = {node_id}
        frontier = {node_id}
        result: dict[str, dict] = {}
        for hop in range(1, hops + 1):
            nxt: set[str] = set()
            for cur in frontier:
                for edge, other in (*self._adj_out.get(cur, ()), *self._adj_in.get(cur, ())):
                    if predicates and edge.predicate not in predicates:
                        continue
                    node = self.nodes.get(other)
                    if node is None:
                        continue
                    if categories is None or node.category in categories:
                        entry = result.setdefault(
                            other,
                            {"node": {"id": node.id, "name": node.name, "category": node.category},
                             "min_hops": hop, "via": []},
                        )
                        entry["via"].append(
                            {"predicate": edge.predicate, "direction": "out" if edge.subject == cur else "in",
                             "from": edge.subject, "to": edge.object, "evidence": edge.evidence()}
                        )
                        entry["min_hops"] = min(entry["min_hops"], hop)
                    if other not in visited:
                        nxt.add(other)
            visited |= nxt
            frontier = nxt
        counts: dict[str, int] = {}
        for entry in result.values():
            counts[entry["node"]["category"]] = counts.get(entry["node"]["category"], 0) + 1
        return {"center": node_id, "hops": hops, "neighbors": result,
                "counts_by_category": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}

    # ---------- 证据查询 ----------
    def edge_evidence(self, subject_id: str, object_id: str, unordered: bool = True) -> list[Edge]:
        hits = list(self._by_pair.get((subject_id, object_id), []))
        if unordered and not hits:
            hits = list(self._by_pair.get((object_id, subject_id), []))
        return hits

    def stats(self) -> dict:
        by_tier: dict[str, int] = {}
        by_pred: dict[str, int] = {}
        for e in self.edges:
            by_tier[e.evidence_tier] = by_tier.get(e.evidence_tier, 0) + 1
            by_pred[e.predicate] = by_pred.get(e.predicate, 0) + 1
        by_cat: dict[str, int] = {}
        for n in self.nodes.values():
            by_cat[n.category] = by_cat.get(n.category, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "nodes_by_category": by_cat, "edges_by_tier": by_tier,
                "edges_by_predicate": dict(sorted(by_pred.items(), key=lambda kv: -kv[1]))}
