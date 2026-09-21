#!/usr/bin/env python3
"""图分析：单菌跨域多跳扇出查询 + 图指标/社区检测（networkx，可复现）。

用法:
  python3 graph_analysis.py query --microbe "Faecalibacterium prausnitzii" [--hops 3] [--out out.tsv]
  python3 graph_analysis.py metrics [--json data/merged/graph_metrics.json]

query 输出按 7 类节点分列的 1..k 跳邻居，含路径谓词链与边级证据
（tier/pmids/confidence）；metrics 输出度中心性 Top 与 Louvain 社区。
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
NODES = ROOT / "data" / "merged" / "merged_nodes.tsv"
EDGES = ROOT / "data" / "merged" / "merged_edges.tsv"


def load_graph():
    import pandas as pd
    nodes = pd.read_csv(NODES, sep="\t").fillna("")
    edges = pd.read_csv(EDGES, sep="\t").fillna("")
    g = nx.MultiDiGraph()
    meta = {r["id"]: r for r in nodes.to_dict("records")}
    for r in edges.to_dict("records"):
        g.add_edge(r["subject"], r["object"], predicate=r["predicate"],
                   tier=r.get("evidence_tier", ""), source=r.get("source_type", ""),
                   pmids=r.get("pmids", ""), conf=r.get("confidence", ""))
    return g, meta, edges


def resolve(name_or_id, meta):
    q = str(name_or_id).strip()
    if q in meta:
        return q
    ql = q.lower()
    for nid, r in meta.items():
        if str(r.get("name", "")).lower() == ql:
            return nid
    for nid, r in meta.items():
        if ql in str(r.get("name", "")).lower() or ql in str(r.get("aliases", "")).lower():
            return nid
    return None


def query(args):
    g, meta, _ = load_graph()
    nid = resolve(args.microbe, meta)
    if not nid:
        raise SystemExit(f"未找到实体: {args.microbe}")
    und = nx.Graph(g)  # 跨域扇出走无向图
    # BFS 记录最短跳数与一条路径
    dist = {nid: 0}
    prev = {}
    frontier = [nid]
    for d in range(1, args.hops + 1):
        nxt = []
        for u in frontier:
            for v in und.neighbors(u):
                if v not in dist:
                    dist[v] = d
                    prev[v] = u
                    nxt.append(v)
        frontier = nxt
    rows = []
    for v, d in sorted(dist.items(), key=lambda x: (x[1], x[0])):
        if v == nid:
            continue
        # 回溯一条路径并取每跳谓词
        path, cur = [], v
        while cur != nid and cur in prev:
            u = prev[cur]
            preds = set()
            if g.has_edge(u, cur):
                preds |= {e["predicate"] for e in g[u][cur].values()}
            if g.has_edge(cur, u):
                preds |= {e["predicate"] for e in g[cur][u].values()}
            path.append(f"{meta[u]['name']} -[{('|'.join(sorted(preds))) or '?'}]-> {meta[cur]['name']}")
            cur = u
        path.reverse()
        direct = None
        if g.has_edge(nid, v):
            direct = next(iter(g[nid][v].values()))
        elif g.has_edge(v, nid):
            direct = next(iter(g[v][nid].values()))
        rows.append({
            "hop": d, "id": v, "name": meta[v]["name"], "category": meta[v]["category"],
            "path": " | ".join(path),
            "predicate": direct["predicate"] if direct else "",
            "tier": direct["tier"] if direct else "",
            "pmids": direct["pmids"] if direct else "",
            "confidence": direct["conf"] if direct else "",
        })
    print(f"[query] {meta[nid]['name']} ({nid}) 1-{args.hops} 跳扇出: {len(rows)} 个跨域邻居")
    print("  按类别:", dict(Counter(r["category"] for r in rows)))
    print("  按跳数:", dict(Counter(r["hop"] for r in rows)))
    hdr = ["hop", "category", "name", "id", "predicate", "tier", "pmids", "confidence", "path"]
    print("\t".join(hdr))
    for r in sorted(rows, key=lambda x: (x["hop"], x["category"], x["name"])):
        print("\t".join(str(r[c]) for c in hdr))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\t".join(hdr) + "\n")
            for r in sorted(rows, key=lambda x: (x["hop"], x["category"], x["name"])):
                f.write("\t".join(str(r[c]) for c in hdr) + "\n")
        print(f"[out] -> {args.out}")


def metrics(args):
    g, meta, _ = load_graph()
    und = nx.Graph(g)
    out = {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
           "by_category": dict(Counter(r["category"] for r in meta.values()))}
    deg = nx.degree_centrality(und)
    top = defaultdict(list)
    for nid, d in sorted(deg.items(), key=lambda x: -x[1]):
        top[meta[nid]["category"]].append((meta[nid]["name"], round(d, 4)))
    out["degree_top10_by_category"] = {c: v[:10] for c, v in top.items()}
    # Louvain 社区（无向加权：support_count 不可用时用 1）
    comms = nx.community.louvain_communities(und, resolution=args.resolution, seed=42)
    comms = sorted(comms, key=len, reverse=True)
    comm_out = []
    for i, c in enumerate(comms[:15], 1):
        cats = Counter(meta[n]["category"] for n in c)
        names = [meta[n]["name"] for n in sorted(c, key=lambda n: -deg[n])][:12]
        comm_out.append({"id": i, "size": len(c), "categories": dict(cats), "top_members": names})
    out["louvain_communities_top15"] = comm_out
    out["community_total"] = len(comms)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[out] -> {args.json}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query")
    q.add_argument("--microbe", required=True)
    q.add_argument("--hops", type=int, default=3)
    q.add_argument("--out")
    m = sub.add_parser("metrics")
    m.add_argument("--json", default=None)
    m.add_argument("--resolution", type=float, default=1.0)
    args = ap.parse_args()
    (query if args.cmd == "query" else metrics)(args)


if __name__ == "__main__":
    main()
