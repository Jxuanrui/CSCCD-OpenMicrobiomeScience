"""KG 工具 CLI：snapshot 快照管理 + query 实体解析/k跳扇出 + evidence 边级证据。

用法：
  python -m mra.kg snapshot create [--source DIR] [--id ID]
  python -m mra.kg snapshot list
  python -m mra.kg query --term "Faecalibacterium prausnitzii" --hops 2 [--categories Disease,Metabolite]
  python -m mra.kg evidence --subject NCBITaxon:xxx --object MESH:yyy
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import KGGraph
from .snapshot import DEFAULT_SOURCE, create_snapshot, latest_snapshot, list_snapshots


def _load_graph(path: str | None) -> KGGraph:
    snap = Path(path) if path else latest_snapshot()
    return KGGraph(snap)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KG 快照与检索工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="快照管理")
    snap_sub = p_snap.add_subparsers(dest="snap_command", required=True)
    p_create = snap_sub.add_parser("create", help="从主图谱只读复制新快照")
    p_create.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p_create.add_argument("--id", default=None)
    snap_sub.add_parser("list", help="列出现有快照")

    p_query = sub.add_parser("query", help="实体解析 + k 跳扇出")
    p_query.add_argument("--term", required=True, help="ID / 名称 / 别名")
    p_query.add_argument("--hops", type=int, default=1)
    p_query.add_argument("--categories", default=None, help="逗号分隔类别过滤，如 Disease,Metabolite")
    p_query.add_argument("--snapshot", default=None, help="快照目录，缺省取最新")

    p_evidence = sub.add_parser("evidence", help="查询两点间边及证据")
    p_evidence.add_argument("--subject", required=True)
    p_evidence.add_argument("--object", required=True)
    p_evidence.add_argument("--snapshot", default=None)

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        if args.snap_command == "create":
            dest = create_snapshot(source=args.source, snapshot_id=args.id)
            print(f"快照已创建：{dest}")
        elif args.snap_command == "list":
            for m in list_snapshots():
                files = m["files"]
                n_nodes = files["merged_nodes.tsv"]["data_lines"]
                n_edges = files["merged_edges.tsv"]["data_lines"]
                print(f"{m['snapshot_id']}  nodes={n_nodes}  edges={n_edges}  source={m['source']}")
        return 0

    graph = _load_graph(args.snapshot)
    if args.command == "query":
        hits = graph.resolve(args.term)
        if not hits:
            print(f"未解析到实体：{args.term}", file=sys.stderr)
            return 1
        node = hits[0]
        print(json.dumps({"resolved": [{"id": n.id, "name": n.name, "category": n.category} for n in hits[:5]],
                          **graph.neighbors(node.id, hops=args.hops,
                                            categories=set(args.categories.split(",")) if args.categories else None)},
                         ensure_ascii=False, indent=2))
    elif args.command == "evidence":
        edges = graph.edge_evidence(args.subject, args.object)
        print(json.dumps([{"predicate": e.predicate, "evidence": e.evidence()} for e in edges],
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
