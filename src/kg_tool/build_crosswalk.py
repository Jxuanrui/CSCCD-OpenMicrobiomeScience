<<<<<<< HEAD
"""crosswalk 资产包生成器（L2 第 2 层，评审批准后的正式产物）。
=======
"""crosswalk 资产包生成器（L2 第 2 层，监工批准后的正式产物）。
>>>>>>> origin/main

产出（新路径 bridge/，2026-11-06 用户 T2 批准）：
  bridge/l2_crosswalk.tsv      —— 逐行带证据的跨体系对照表
  bridge/crosswalk_manifest.json —— 行数/四 relation 计数/输入 sha/脚本 sha/时刻

设计纪律（全部来自 spec `bridge_crosswalk_spec_20261106.md`）：
- relation 读法 src→dst；parent_of = "src 的父是 dst"，即 **dst 更泛化**；
  评估只允许"库侧更泛化"这一向（parent_of），**child_of 只登记不进判定**；
- evidence 写内容锚（taxid/rank/MeSH 树号实值），**绝不写行号**；
- sha16 = sha256(六个字段竖线相连)[:16]；
- 疾病侧层级桥用 NLM 官方 SPARQL 快照的树号（非全量 dump，限制入 manifest）；
- 保守口径：库侧含 `*` 的 Condition1ID 整格弃用（主值）。

只读：除 bridge/ 外不写任何数据；不碰主图与子图。
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import (MESH_UID_RE, _sha16, canon_mesh, library_sets,
                          load_ncbi, mesh_tokens, our_microbe_disease_pairs,
                          sha256_file)

ROOT = Path(__file__).resolve().parents[2]


def ancestors_from_tree_numbers(tn_by_uid: dict) -> dict:
    """uid → {祖先uid: 最小层级差}（只在给定全集内解析，够用且省内存）。"""
    tn2uid = defaultdict(list)
    for uid, tns in tn_by_uid.items():
        for tn in tns:
            tn2uid[tn].append(uid)
    out = {}
    for uid, tns in tn_by_uid.items():
        anc = {}
        for tn in tns:
            parts = tn.split(".")
            for k in range(1, len(parts)):
                prefix = ".".join(parts[:k])
                dist = len(parts) - k
                for other in tn2uid.get(prefix, ()):
                    if other == uid:
                        continue
                    if other not in anc or dist < anc[other]:
                        anc[other] = dist
        out[uid] = anc
    return out


def descendants_of(uid: str, anc_map: dict) -> dict:
    """反查：谁是 uid 的后代（用于 child_of 只登记）。"""
    out = {}
    for other, anc in anc_map.items():
        if uid in anc and other != uid:
            out[other] = anc[uid]
    return out


def _pool_tier(pairs, lib_tax, dist_map, cap):
    """按"疾病侧祖先距离上限"数可评估对（菌侧仍要求 taxid 在库）。

    **哨兵教训**：初版写 `dist_map.get(m, 99) <= cap`，当 cap=99 时"没有桥"的病
    （默认 99）也会通过 → 疾病侧过滤空转，第三档数字退化成"仅菌侧匹配数"。
<<<<<<< HEAD
    评审 2026-11-06 P0-A 抓出。改为**显式要求 m 在 dist_map 里**（即有桥）。
=======
    监工 2026-11-06 P0-A 抓出。改为**显式要求 m 在 dist_map 里**（即有桥）。
>>>>>>> origin/main
    """
    return sum(1 for t, m in pairs
               if t in lib_tax and m in dist_map and dist_map[m] <= cap)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 crosswalk 资产包（bridge/）")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "bridge")
    ap.add_argument("--mesh-snapshot", type=Path, required=True,
                    help="mesh_sparql_* 目录（含 tree_numbers.json）")
    ap.add_argument("--names", type=Path, default=ROOT / "data/raw/names.dmp")
    ap.add_argument("--nodes", type=Path, default=ROOT / "data/raw/nodes.dmp")
    args = ap.parse_args(argv)

    # ---------- 菌侧 ----------
    names, ranks = load_ncbi(args.names, args.nodes)
    pairs, _, _, malformed = our_microbe_disease_pairs()
    lib_tax, lib_mesh_full, lib_mesh, gt_dir, cell_stats = library_sets()
    our_tax = sorted({t for t, _ in pairs})
    lib_names = set()
    for t in lib_tax:
        lib_names |= names.get(t, set())

    germ_rows = []          # (src_id, dst_id, relation, evidence)
    germ_stat = Counter()
    for t in our_tax:
        if t in lib_tax:
            germ_rows.append((t, t, "exact", f"exact:{t}"))
            germ_stat["exact"] += 1
            continue
        hit = names.get(t, set()) & lib_names
        if hit:
            # 反查命中名对应的库 taxid（名字级证据，不写行号）
            dst = next(x for x in sorted(lib_tax)
                       if names.get(x, set()) & hit)
            germ_rows.append((t, dst, "synonym", f"synonym:name={sorted(hit)[0]}"))
            germ_stat["synonym"] += 1
            continue
        par, rk = ranks.get(t, (None, None))
        if par and par in lib_tax:
            germ_rows.append((t, par, "parent_of", f"parent_of:{par}|rank={rk}"))
            germ_stat["parent_of"] += 1
            continue
        kids = [c for c, (p, _) in ranks.items() if p == t and c in lib_tax]
        if kids:
            k = sorted(kids)[0]
            germ_rows.append((t, k, "child_of",
                              f"child_of:{k}|rank={ranks.get(k, ('', ''))[1]}"))
            germ_stat["child_of"] += 1

    # ---------- 疾病侧 ----------
    tn = json.loads((args.mesh_snapshot / "tree_numbers.json").read_text(
        encoding="utf-8"))
    anc_map = ancestors_from_tree_numbers(tn)
    our_mesh = sorted({m for _, m in pairs})
    dis_rows = []
    dis_stat = Counter()
    dist_hist = Counter()
    no_tree = []
    for m in our_mesh:
        if m in lib_mesh:
            dis_rows.append((m, m, "exact", f"exact:{m}"))
            dis_stat["exact"] += 1
            continue
        if m not in tn:
            no_tree.append(m)
            continue
        anc = {a: d for a, d in anc_map.get(m, {}).items() if a in lib_mesh}
        if anc:
            a, d = min(anc.items(), key=lambda kv: (kv[1], kv[0]))
            dis_rows.append((m, a, "parent_of",
                             f"parent_of:{a}|dist={d}|tree={tn[m][0]}"))
            dis_stat["parent_of"] += 1
            dist_hist[d] += 1
            continue
        # 库侧更细：只登记，永不进评估
        desc = {x: d for x, d in descendants_of(m, anc_map).items()
                if x in lib_mesh}
        if desc:
            x, d = min(desc.items(), key=lambda kv: (kv[1], kv[0]))
            dis_rows.append((m, x, "child_of",
                             f"child_of:{x}|dist={d}|REGISTERED_ONLY"))
            dis_stat["child_of"] += 1

    # ---------- 写资产 ----------
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_tsv = args.out_dir / "l2_crosswalk.tsv"
    with open(out_tsv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["src_system", "src_id", "dst_system", "dst_id",
                    "relation", "evidence", "sha16"])
        for src, dst, rel, ev in germ_rows:
            w.writerow(["NCBI:taxid", src, "gutMDisorder:taxid", dst, rel, ev,
                        _sha16("NCBI:taxid", src, "gutMDisorder:taxid", dst,
                               rel, ev)])
        for src, dst, rel, ev in dis_rows:
            w.writerow(["MeSH:UID", src, "gutMDisorder:MeSH", dst, rel, ev,
                        _sha16("MeSH:UID", src, "gutMDisorder:MeSH", dst,
                               rel, ev)])

    dist_map = {}      # our mesh -> 允许桥的祖先距离（exact 记 0）
    for m in our_mesh:
        if m in lib_mesh:
            dist_map[m] = 0
            continue
        anc = {a: d for a, d in anc_map.get(m, {}).items() if a in lib_mesh}
        if anc:
            dist_map[m] = min(anc.values())

    # ---------- 疾病侧边池（只算 parent_of/exact，child_of 不计） ----------
    eval_mesh = {r[0]: r for r in dis_rows if r[2] in ("exact", "parent_of")}
    pool_all = len(pairs)
    pool_germ = sum(1 for t, _ in pairs if t in lib_tax)
    pool_dis = sum(1 for _, m in pairs if m in eval_mesh)

    manifest = {
        "generated_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "spec": "bridge_crosswalk_spec_20261106.md",
        "rows": {"total": len(germ_rows) + len(dis_rows),
                 "germ": dict(germ_stat), "disease": dict(dis_stat)},
        "evaluable_pairs": {
            "our_pairs": pool_all,
            "germ_side_matched": pool_germ,
            "disease_side_matched": pool_dis,
            "note": "child_of 不计入（只登记）；疾病侧按祖先距离分层",
            "by_tier": {
                "strict_exact": sum(1 for t, m in pairs
                                    if t in lib_tax and m in lib_mesh),
                "with_parent_dist_le3": _pool_tier(pairs, lib_tax, dist_map, 3),
                "with_parent_any_dist": _pool_tier(pairs, lib_tax, dist_map, 99),
            },
            "by_tier_pct": {
                k: round(v / pool_all, 6) for k, v in {
                    "strict_exact": sum(1 for t, m in pairs
                                        if t in lib_tax and m in lib_mesh),
                    "with_parent_dist_le3": _pool_tier(pairs, lib_tax,
                                                       dist_map, 3),
                    "with_parent_any_dist": _pool_tier(pairs, lib_tax,
                                                       dist_map, 99),
                }.items()
            },
            "definitions": {
                "strict_exact": "菌侧 taxid 在库 且 疾病 MeSH 直接相同",
                "with_parent_dist_le3": "疾病侧允许祖先桥（库更泛化）距离≤3",
                "with_parent_any_dist": "含距离≥4 的极泛祖先（仅披露，不作主值）",
            },
        },
        "disease_distance_hist": dict(sorted(dist_hist.items())),
        "disease_no_tree_number": {
            "count": len(no_tree), "uids": no_tree,
            "note": "补充概念(C码)多数无 treeNumber，无法层级桥；不得当成'库里没有'",
        },
        "inputs_sha256": {
            "names.dmp": sha256_file(args.names),
            "nodes.dmp": sha256_file(args.nodes),
            "gutMDisorder_dir": gt_dir,
            "mesh_tree_numbers.json": sha256_file(
                args.mesh_snapshot / "tree_numbers.json"),
            "mesh_sparql_manifest.json": sha256_file(
                args.mesh_snapshot / "mesh_sparql_manifest.json"),
            "l2_crosswalk.tsv": sha256_file(out_tsv),
        },
        "script_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "MeSH 树号来自官方 SPARQL 快照（760 UID 全集），非全量 dump",
            "库侧保守口径：含 '*' 的 Condition1ID 整格弃用",
            "菌侧 parent_of 只取直接父节点；疾病侧 parent_of 距离已分层记录",
            "跨体系桥（UMLS/OXO）本期不做",
        ],
    }
    (args.out_dir / "crosswalk_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[crosswalk] {out_tsv}  {len(germ_rows) + len(dis_rows)} 行")
    print(f"  菌侧 {dict(germ_stat)}")
    print(f"  疾病侧 {dict(dis_stat)}  距离分布 {dict(sorted(dist_hist.items()))}"
          f"  无树号 {len(no_tree)}")
    bt = manifest["evaluable_pairs"]["by_tier"]
    print(f"  可评估边池：strict {bt['strict_exact']}/{pool_all}"
          f" | +祖先≤3 {bt['with_parent_dist_le3']}/{pool_all}"
          f" | 任意距离 {bt['with_parent_any_dist']}/{pool_all}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
