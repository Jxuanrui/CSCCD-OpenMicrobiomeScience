"""桥接子图谱探测（L2 桥接层可行性，只读点数，产机器档）。

为什么需要：提案档里的 +76/84.0%/698 等数字若只活在 Markdown 里，即违
"数字机器输出制"（AGENTS 规则 5）。本脚本把三路桥接（exact/synonym/parent_of）
与疾病侧基数全部变成可复算产物，含**内容锚**（父 taxid+rank 实值）而非仅行号
<<<<<<< HEAD
——NCBI 周更，行号只在钉扎 sha 下可复现（评审 2026-11-06 P0-3④）。
=======
——NCBI 周更，行号只在钉扎 sha 下可复现（监工 2026-11-06 P0-3④）。
>>>>>>> origin/main

只读：不写任何图谱产物，只落 data/logs 探测档。
"""
import argparse
import csv
import re
import glob
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]


def _sha16(src_system, src_id, dst_system, dst_id, relation, evidence) -> str:
    """单边指纹：与规格 §三 逐字一致（六列 + 竖线，UTF-8，不 strip）。"""
    s = f"{src_system}|{src_id}|{dst_system}|{dst_id}|{relation}|{evidence}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _rank_hist(d: dict) -> dict:
    """统计某个步数档里命中祖先的 rank 分布（防"步数"与"分类阶元"错位）。"""
    hist = {}
    for v in d.values():
        rk = v.split("rank=")[-1]
        hist[rk] = hist.get(rk, 0) + 1
    return hist


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ncbi(names_p: Path, nodes_p: Path):
    """NCBI taxonomy：taxid → 名字集；taxid → (parent, rank)。"""
    names = defaultdict(set)
    for line in open(names_p, encoding="utf-8", errors="ignore"):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 4:
            continue
        names[p[0]].add(p[1].lower())
    ranks = {}
    for line in open(nodes_p, encoding="utf-8", errors="ignore"):
        p = [x.strip() for x in line.split("|")]
        if len(p) < 3:
            continue
        ranks[p[0]] = (p[1], p[2])
    return names, ranks


def our_microbe_disease_pairs():
    """我方 菌-疾病 distinct 对（规范化：taxid 数值 + MeSH casefold）。"""
    kg = ROOT / "data/kg/k4_full_20261028_155020"
    nodes = {n["id"]: n for n in csv.DictReader(
        open(kg / "merged_nodes.tsv", encoding="utf-8"), delimiter="\t")}
    mic = {i for i, n in nodes.items() if n.get("category") in ("Species", "Microbe")}
    dis = {i for i, n in nodes.items() if n.get("category") == "Disease"}
    pairs = set()
    malformed = set()
<<<<<<< HEAD
    # 规范化（评审 P0-3①：MESH:/MeSH: 双拼写实证 390+3,793）
=======
    # 规范化（监工 P0-3①：MESH:/MeSH: 双拼写实证 390+3,793）
>>>>>>> origin/main
    for e in csv.DictReader(open(kg / "merged_edges.tsv", encoding="utf-8"),
                            delimiter="\t"):
        s, o = e["subject"], e["object"]
        if (s in mic and o in dis) or (o in mic and s in dis):
            mi = s if s in mic else o
            di = o if s in mic else s
            acc = canon_mesh(di)
            if acc is None:
                malformed.add(di)
                continue
            pairs.add((mi.split(":")[1], acc))
    return pairs, len(mic), len(dis), malformed


MESH_UID_RE = re.compile(r"^[DC]\d{6,}$", re.I)


def canon_mesh(node_id: str):
    r"""Disease 节点 id → 规范 MeSH UID；非规范形态返回 None 并单列缺陷。

    为什么必须白名单化：主图 Disease 里有 4 个 `MESH:OMIM:603663` 这类畸形
    id（MeSH 前缀套 OMIM 号）。若按"首个冒号后"直接切，会全部塌缩成同一个
    串 `omim`，凭空造出一个交点（旧口径 64→65 的差就来自这里）。只有
    ^[DC]\d{6,}$ 才是 MeSH Unique ID（D=Descriptor, C=Supplementary）。
    """
    acc = node_id.split(":", 1)[1] if ":" in node_id else node_id
    return acc.upper() if MESH_UID_RE.match(acc) else None


def mesh_tokens(cell: str) -> set:
    r"""gutMDisorder Condition1ID 格 → 合法 MeSH UID 集。

    为什么不能整格当一个 ID：实测 1,591 个非空格里 773 个是多值
    （`*,D020181,D010372`）或通配（`*` 出现 733 次）。整格比对会把
    "含 MeSH 的格"判为不匹配 → 系统性低估覆盖。按 [,;] 分词后只留
    ^[DC]\d{6,}$ 才是库侧真实病种集。
    """
    out = set()
    for tok in re.split(r"[,;]", cell or ""):
        tok = tok.strip()
        if MESH_UID_RE.match(tok):
            out.add(tok.upper())
    return out


def library_sets():
    """gutMDisorder v3：库侧 taxid 集 + MeSH 集（Condition1ID）。"""
    import openpyxl
    gt = sorted(glob.glob(str(ROOT / "data/external/gutmdisorder_v3_*")))[-1]
    wb = openpyxl.load_workbook(f"{gt}/gutMDisorder_v3_Literature-based_All.xlsx",
                                read_only=True)
    meshes = set()
    meshes_conservative = set()  # 含 "*" 的整格弃用（"*" 语义无数据字典可查）
    n_cells = n_multi = n_star = 0
    for r in wb["Literature"].iter_rows(min_row=2, values_only=True):
        v = (r[8] or "").strip()
        if not v:
            continue
        n_cells += 1
        parts = [x.strip() for x in re.split(r"[,;]", v) if x.strip()]
        if len(parts) > 1:
            n_multi += 1
        n_star += sum(1 for x in parts if x == "*")
        meshes |= mesh_tokens(v)
        if "*" not in v:
            meshes_conservative |= mesh_tokens(v)
    tax = set()
    for r in wb["Association"].iter_rows(min_row=2, values_only=True):
        if r[3] not in (None, ""):
            tax.add(str(int(float(r[3]))))
    wb.close()
    return tax, meshes, meshes_conservative, Path(gt).name, {
        "nonempty_cells": n_cells, "multi_value_cells": n_multi,
        "wildcard_tokens": n_star,
    }


EVAL_RELATIONS = ("exact", "synonym", "parent_of")  # child_of 永不在此列


MAX_ANCESTOR_DIST = 3   # 祖先距离上限（事前定）：≥4 已跳到"疾病"级泛类目，只登记不评估


def load_crosswalk_for_eval(path: Path, max_dist: int = MAX_ANCESTOR_DIST
                            ) -> list[dict]:
    """读 crosswalk 供**评估**用：硬编码丢弃 child_of + 未知 relation 直接报错。

    为什么写死而不是靠调用方自觉：child_of=向下匹配（拿库内更细的证据支撑我方
<<<<<<< HEAD
    更粗的主张），语义上是"沾边算对"，一旦混进评估分母就污染主数字（评审
=======
    更粗的主张），语义上是"沾边算对"，一旦混进评估分母就污染主数字（监工
>>>>>>> origin/main
    P0-3③）。未知 relation 一律 fail-closed——防未来加新类时被默认放进分母。
    """
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rel = r.get("relation", "")
            if rel == "child_of":
                continue
            if rel not in EVAL_RELATIONS:
                raise SystemExit(
                    f"[crosswalk] 未知 relation={rel!r}（评估只认 {EVAL_RELATIONS}）")
            # 祖先距离上限过滤（机械读 dist= 字段；无 dist 的菌侧边不涉此限）
            m = re.search(r"dist=(\d+)", r.get("evidence", ""))
            if m and int(m.group(1)) > max_dist:
                continue
            out.append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="桥接层可行性探测（只读，产机器档）")
    ap.add_argument("--out", type=Path, required=True,
                    help="输出目录（data/logs/bridge_probe_<ts>/）")
    ap.add_argument("--emit-crosswalk", type=Path, default=None,
                    help="同时导出菌侧桥接边草案（crosswalk_germ.tsv）")
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[bridge-probe] {args.out} 已存在且非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    NAMES = ROOT / "data/raw/names.dmp"
    NODES = ROOT / "data/raw/nodes.dmp"
    names, ranks = load_ncbi(NAMES, NODES)
    pairs, n_mic_nodes, n_dis_nodes, malformed = our_microbe_disease_pairs()
    (lib_tax, lib_mesh_full, lib_mesh,
     gt_dir_name, lib_cell_stats) = library_sets()

    our_tax = {t for t, _ in pairs}
    lib_names = set()
    for t in lib_tax:
        lib_names |= names.get(t, set())

    exact, synonym, parent_of, child_of = {}, {}, {}, {}
<<<<<<< HEAD
    # 多步披露（评审 P0-2：1/2/3 步全报，禁看增量挑步数）
=======
    # 多步披露（监工 P0-2：1/2/3 步全报，禁看增量挑步数）
>>>>>>> origin/main
    # 主参数事前定为 **1 步**，依据是本体结构而非增量：
    # ① 我方 312 个菌 taxid 绝大多数是 species，库也以 species(761)/genus(513) 为主；
    # ② 每多跨一级（genus→family→order），主张范围成倍放宽，属"语义降格"，
    #    只能作上限披露，不能作主口径（红线④语义单调降格）；
    # ③ 1 步是"两套 ID 体系粒度差"的最小补偿，再往上就是在替读者做归纳。
    multistep = {1: {}, 2: {}, 3: {}}  # hop -> {taxid: "anc|rank=..."}
    for t in our_tax:
        if t in lib_tax:
            exact[t] = f"exact:{t}"
            continue
        # synonym：任一名与库 taxid 的任一名相同
        hit = names.get(t, set()) & lib_names
        if hit:
            synonym[t] = f"synonym:{sorted(hit)[0]}"
            continue
        # parent_of：1 步（主口径）——父 taxid 在库，带内容锚
        par, rk = ranks.get(t, (None, None))
        if par and par in lib_tax:
            parent_of[t] = f"parent_of:{par}|rank={rk}"
            continue
        # 2/3 步：仅披露增量，不进主口径
        cur, hit_hop = t, None
        for hop in (1, 2, 3):
            cur, _ = ranks.get(cur, (None, None))
            if not cur:
                break
            if cur in lib_tax:
                hit_hop = hop
                multistep[hop][t] = f"hop{hop}:{cur}|rank={ranks.get(cur, ('', ''))[1]}"
                break
<<<<<<< HEAD
        # child_of：仅登记（评审 P0-3③：不进评估判定）
=======
        # child_of：仅登记（监工 P0-3③：不进评估判定）
>>>>>>> origin/main
        kids = [c for c, (p, _) in ranks.items() if p == t and c in lib_tax]
        if kids:
            child_of[t] = f"child_of:{sorted(kids)[0]}"

    res = {
        "inputs_sha256": {
            "names.dmp": sha256_file(NAMES),
            "nodes.dmp": sha256_file(NODES),
            "gutMDisorder_dir": gt_dir_name,
        },
        "counts": {
            "our_microbe_taxids": len(our_tax),
            "our_pairs": len(pairs),
            "our_microbe_nodes": n_mic_nodes,
            "our_disease_nodes": n_dis_nodes,
            "lib_taxids": len(lib_tax),
            "lib_meshes_valid_uid": len(lib_mesh),
            "lib_condition_cells": lib_cell_stats,
        },
        "germ_bridge": {
            "exact": len(exact),
            "synonym": len(synonym),
            "parent_of_1hop_PRIMARY": len(parent_of),
            "child_of_registered_only": len(child_of),
            "multistep_disclosure": {
                "primary_choice_reason": "1 步=两套 ID 粒度差的最小补偿；"
                "每多 1 步主张范围成倍放宽（语义降格），只作上限披露",
                "hop2_family_level_increment": len(multistep[2]),
                "hop3_order_level_increment": len(multistep[3]),
                "hop2_ranks": _rank_hist(multistep[2]),
                "hop3_ranks": _rank_hist(multistep[3]),
                "cum_coverage_if_3hop": (len(exact) + len(synonym)
                                         + len(parent_of) + len(multistep[2])
                                         + len(multistep[3])) / len(our_tax),
            },
        },
        "coverage": {
            "exact_rate": len(exact) / len(our_tax),
            "exact_plus_synonym": len(exact) + len(synonym),
            "exact_synonym_parent": len(exact) + len(synonym) + len(parent_of),
            "exact_synonym_parent_rate": (len(exact) + len(synonym) + len(parent_of))
            / len(our_tax),
        },
        "disease_side": {
            "PRIMARY": "conservative（含 '*' 的格整格弃用）",
            "our_meshes_valid_uid": len({m for _, m in pairs}),
            "lib_meshes_valid_uid": len(lib_mesh),
            "upper_bound_full_tokenization": {
                "lib_meshes_valid_uid": len(lib_mesh_full),
                "direct_intersection": len({m for _, m in pairs}
                                           & lib_mesh_full),
                "pair_pool_both_sides": sum(
                    1 for t, m in pairs if t in lib_tax and m in lib_mesh_full),
            },
            "lib_condition_cells": lib_cell_stats,
            "direct_intersection": len({m for _, m in pairs} & lib_mesh),
            "direct_intersection_rate": len({m for _, m in pairs} & lib_mesh)
            / max(1, len({m for _, m in pairs})),
            "malformed_disease_ids": sorted(malformed),
            "pair_pool": {
                "our_pairs": len(pairs),
                "both_sides_matched": sum(
                    1 for t, m in pairs if t in lib_tax and m in lib_mesh),
                "rate": sum(1 for t, m in pairs
                            if t in lib_tax and m in lib_mesh) / max(1, len(pairs)),
            },
            "note": "疾病侧层级桥需 MeSH desc 树（未接入），本档不给预期数字",
        },
        "relation_semantics": {
            "exact": "src 与 dst 为同一标准实体",
            "synonym": "src 与 dst 的全部名字集有交集（taxid 级）",
            "parent_of": "src 的父节点=dst（内容锚=父 taxid+rank 实值）",
            "child_of": "src 的子节点=dst —— **只登记不进评估判定**"
                        "（向下匹配=拿库内更细证据支撑我方更粗主张，"
<<<<<<< HEAD
                        "类比已否决 B 案，评审 2026-11-06 P0-3③）",
=======
                        "类比已否决 B 案，监工 2026-11-06 P0-3③）",
>>>>>>> origin/main
        },
    }
    (args.out / "bridge_probe.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    # 桥接边草案（菌侧，含证据列）
    if args.emit_crosswalk:
        with open(args.emit_crosswalk, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["src_system", "src_id", "dst_system", "dst_id",
                        "relation", "evidence", "sha16"])
            for rel, d in (("exact", exact), ("synonym", synonym),
                           ("parent_of", parent_of), ("child_of", child_of)):
                for src, ev in sorted(d.items()):
                    w.writerow(["NCBI:taxid", src, "gutMDisorder:taxid",
                                ev.split(":", 1)[1].split("|")[0], rel, ev,
                                _sha16("NCBI:taxid", src,
                                   "gutMDisorder:taxid",
                                   ev.split(":", 1)[1].split("|")[0],
                                   rel, ev)])
    m = res["coverage"]
    print(f"[bridge-probe] {args.out}")
    print(f"  菌侧 {len(our_tax)} taxid：exact {len(exact)}"
          f" | synonym +{len(synonym)} | parent_of +{len(parent_of)}"
          f" | child_of(仅登记) {len(child_of)}")
    print(f"  exact {m['exact_rate']:.1%} → exact+syn+parent"
          f" {m['exact_synonym_parent_rate']:.1%}")
    d = res["disease_side"]
    # 多步披露回显
    print(f"  多步披露：1 步 +{len(parent_of)} | 2 步 +{len(multistep[2])}"
          f" {res['germ_bridge']['multistep_disclosure']['hop2_ranks']}"
          f" | 3 步 +{len(multistep[3])}"
          f" {res['germ_bridge']['multistep_disclosure']['hop3_ranks']}")
    print(f"  疾病侧：我方 {d['our_meshes_valid_uid']} MeSH vs 库"
          f" {d['lib_meshes_valid_uid']} MeSH | 直接交"
          f" {d['direct_intersection']} ({d['direct_intersection_rate']:.1%})")
    print(f"  可评估边池：{d['pair_pool']['both_sides_matched']}"
          f"/{d['pair_pool']['our_pairs']}"
          f" = {d['pair_pool']['rate']:.2%} | 畸形病 id {len(d['malformed_disease_ids'])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
