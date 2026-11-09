"""序1：时间切片发现验证（规格 v04_seq1_spec_20261107.md，全部阈值先冻结）。

一句话：把图谱"拨回 2023 年"（只用证据年 ≤2023 的边），拿 gutMDisorder
2024 年后才收录的 1,976 对当考卷，看图谱当时是否已经把答案排出来了。

冻结条款（运行前已锁定，运行中零决策）：
- 主 cutoff=2023（唯一）；考卷=库文献年 ≥2024 的 (taxid,mesh) 对；
- 第 1 层 strict=考卷对直连出现在 KG@2023（唯一主数字）；
- 第 2 层 crosswalk 桥（次级，带限定语）：菌侧 ≤1 步恒 + 疾病祖先 ≤3，
  child_of 与 dist≥4 机械排除；两层禁合并；
- 止损：可比考卷 <100 → INSUFFICIENT_EXAM；命中 0/1-4/≥5 三档解读模板；
- 防污染双断言：命中对全部证据年 ≤2023；命中对证据 PMID 与库侧 PMID 交集单列披露。
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import MESH_UID_RE, sha256_file
from build_crosswalk import ancestors_from_tree_numbers

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = 2023                      # 冻结：唯一主 cutoff
EXAM_MIN_YEAR = 2024               # 冻结：考卷=文献年 ≥2024
STOP_LOSS_EXAM = 100               # 冻结：考卷下限
WILSON_Z = 1.96


def wilson_ci(k, n, z=WILSON_Z):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (round((c - h) / d, 6), round((c + h) / d, 6))


def load_graph_pairs(kg_dir: Path, year_snapshot: Path, cutoff: int):
    """→ (kg_at_pairs, full_pairs, edge_year_resolved, evidence_by_pair)。"""
    cat = {}
    for n in csv.DictReader(open(kg_dir / "merged_nodes.tsv", encoding="utf-8"),
                            delimiter="\t"):
        cat[n["id"]] = n.get("category")
    years = {}
    for r in csv.DictReader(open(year_snapshot, encoding="utf-8"),
                            delimiter="\t"):
        if r["year"].isdigit():
            years[r["pmid"]] = int(r["year"])
    kg_at, full = set(), set()
    ev = defaultdict(lambda: {"pmids": set(), "max_year": None})
    for e in csv.DictReader(open(kg_dir / "merged_edges.tsv", encoding="utf-8"),
                            delimiter="\t"):
        s, o = e["subject"], e["object"]
        if not (((cat.get(s) in ("Species", "Microbe")) and cat.get(o) == "Disease")
                or ((cat.get(o) in ("Species", "Microbe")) and cat.get(s) == "Disease")):
            continue
        mi = s if cat.get(s) in ("Species", "Microbe") else o
        di = o if cat.get(s) in ("Species", "Microbe") else s
        acc = di.split(":", 1)[1] if ":" in di else di
        if not MESH_UID_RE.match(acc):
            continue
        key = (mi.split(":", 1)[1], acc.upper())
        full.add(key)
        ps = {p.strip() for p in (e.get("pmids") or "").split(";")
              if p.strip().isdigit()}
        ys = [years[p] for p in ps if p in years]
        rec = ev[key]
        rec["pmids"] |= ps
        if ys:
            my = max(ys)
            rec["max_year"] = my if rec["max_year"] is None else max(
                rec["max_year"], my)
            if my <= cutoff:
                kg_at.add(key)
    return kg_at, full, years, {k: dict(v) for k, v in ev.items()}


def load_exam(gutmdisorder_xlsx: Path):
    """考卷：库文献年 ≥2024 的 (taxid, mesh) 对 + 对级 PMID 集。"""
    import openpyxl
    wb = openpyxl.load_workbook(gutmdisorder_xlsx, read_only=True)
    lit = {}
    for r in wb["Literature"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None:
            continue
        y = int(r[3]) if isinstance(r[3], (int, float)) and r[3] else None
        cell = (r[8] or "").strip()
        meshes = set() if "*" in cell else {
            t.strip().upper() for t in re.split(r"[,;]", cell)
            if MESH_UID_RE.match(t.strip())}
        pmid = str(r[1]).strip() if r[1] else ""
        lit[idx] = (y, meshes, pmid)
    # 冻结口径：对级取**最早**文献年，考卷=最早年 ≥2024 的对
    # （勘误自纠：初版只收 ≥2024 的记录年，会把"老对新都有记录"的对误进考卷，
    #   分母 2085≠预注册 1976——属口径 bug，已按规格 §一改回）
    pair_years = defaultdict(set)
    pair_pmids = defaultdict(set)
    for r in wb["Association"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None or r[3] in (None, "") or idx not in lit:
            continue
        y, meshes, pmid = lit[idx]
        if y is None:
            continue
        t = str(int(float(r[3])))
        for m in meshes:
            pair_years[(t, m)].add(y)
            if pmid.isdigit():
                pair_pmids[(t, m)].add(pmid)
    wb.close()
    exam = {k for k, ys in pair_years.items() if min(ys) >= EXAM_MIN_YEAR}
    return exam, {k: v for k, v in pair_pmids.items() if k in exam}


def load_crosswalk_germ_and_mesh(cw: Path, tree_numbers: Path, lib_mesh: set):
    germ = {}
    for r in csv.DictReader(open(cw, encoding="utf-8", newline=""),
                            delimiter="\t"):
        if (r["src_system"] == "NCBI:taxid"
                and r["relation"] in ("exact", "synonym", "parent_of")):
            germ[r["src_id"]] = r["dst_id"]
    tn = json.loads(tree_numbers.read_text(encoding="utf-8"))
    anc = ancestors_from_tree_numbers(tn)
    return germ, anc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序1 时间切片验证（cutoff=2023 冻结）")
    ap.add_argument("--kg-dir", type=Path,
                    default=ROOT / "data/kg/k5_full_20261107_1006")
    ap.add_argument("--year-snapshot", type=Path, required=True)
    ap.add_argument("--crosswalk", type=Path, default=ROOT / "bridge/l2_crosswalk.tsv")
    ap.add_argument("--mesh-tree", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[timeslice] {args.out} 非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    import glob
    gt = sorted(glob.glob(str(ROOT / "data/external/gutmdisorder_v3_*")))[-1]

    kg_at, full, years, ev = load_graph_pairs(args.kg_dir, args.year_snapshot,
                                              CUTOFF)
    exam, exam_pmids = load_exam(Path(gt) /
                                 "gutMDisorder_v3_Literature-based_All.xlsx")
    germ, anc = load_crosswalk_germ_and_mesh(args.crosswalk, args.mesh_tree,
                                             set())

    strict_hits, bridge_hits, full_only = [], [], []
    for (t, m) in sorted(exam):
        if (t, m) in kg_at:
            strict_hits.append((t, m, "strict"))
            continue
        t2 = t if (t, m) in kg_at else germ.get(t)
        cands = {}
        if t2 is not None:
            for a, dd in anc.get(m, {}).items():
                if dd <= 3 and (t2, a) in kg_at:
                    cands[a] = dd
        if cands:
            m2, d = min(cands.items(), key=lambda kv: (kv[1], kv[0]))
            bridge_hits.append((t, m, t2, m2, d))
            continue
        if (t, m) in full:
            full_only.append((t, m))

    n_exam = len(exam)
    n_strict, n_bridge = len(strict_hits), len(bridge_hits)
    # 防污染断言①：命中对全部证据年 ≤ cutoff（KG@2023 按构造，机器复核）
    viol = []
    for t, m, _ in strict_hits:
        my = ev[(t, m)]["max_year"]
        if my is None or my > CUTOFF:
            viol.append((t, m, my))
    assert not viol, f"污染断言失败: {viol[:5]}"
    # 防污染②：命中对证据 PMID 与库侧该对 PMID 交集（单列披露，不断言为零）
    pmid_overlap = []
    for t, m, _ in strict_hits:
        ov = ev[(t, m)]["pmids"] & exam_pmids.get((t, m), set())
        if ov:
            pmid_overlap.append((t, m, sorted(ov)))

    if n_exam < STOP_LOSS_EXAM:
        verdict = "INSUFFICIENT_EXAM"
    elif n_strict >= 5:
        verdict = "POSITIVE_CLAIMABLE"
    elif n_strict >= 1:
        verdict = "CASE_ONLY"
    else:
        verdict = "NEGATIVE_PREWRITTEN"

    lo, hi = wilson_ci(n_strict, n_exam)
    res = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_spec": "v04_seq1_spec_20261107.md",
        "cutoff": CUTOFF, "exam_min_year": EXAM_MIN_YEAR,
        "inputs_sha256": {
            "merged_edges": sha256_file(args.kg_dir / "merged_edges.tsv"),
            "year_snapshot": sha256_file(args.year_snapshot),
            "crosswalk": sha256_file(args.crosswalk),
            "mesh_tree_numbers": sha256_file(args.mesh_tree),
            "script_sha256": sha256_file(Path(__file__)),
        },
        "kg_at_pairs": len(kg_at), "full_graph_pairs": len(full),
        "n_exam": n_exam,
        "strict_hits": n_strict,
        "strict_rate": round(n_strict / n_exam, 6) if n_exam else None,
        "strict_ci95": [lo, hi],
        "bridge_hits_secondary": n_bridge,
        "full_graph_hits_control": len(exam & full),
        "verdict": verdict,
        "verdict_rule": "≥5=POSITIVE / 1-4=CASE_ONLY / 0=NEGATIVE / <100考卷=INSUFFICIENT",
        "contamination": {
            "assert_all_hit_evidence_le_cutoff": "PASS" if not viol else "FAIL",
            "hit_evidence_pmid_overlap_with_library": len(pmid_overlap),
        },
        "discipline": [
            "cutoff=2023 唯一主值；2024+ 证据边全部排除在 KG@2023 外",
            "strict 唯一主数字；bridge 次级带限定语；两层数字禁合并",
            "全图命中列只作透明对照，不作预测力主张",
        ],
    }
    (args.out / "timeslice_eval.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(args.out / "timeslice_hits.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["layer", "microbe_taxid", "disease_mesh",
                    "bridge_taxid", "bridge_mesh", "bridge_dist",
                    "our_max_evidence_year", "shared_pmids_with_library"])
        for t, m, _ in strict_hits:
            w.writerow(["strict", t, m, "", "", "",
                        ev[(t, m)]["max_year"],
                        ";".join(next((o for tt, mm, o in pmid_overlap
                                       if tt == t and mm == m), []))])
        for t, m, t2, m2, d in bridge_hits:
            w.writerow(["bridge_secondary", t, m, t2, m2, d,
                        "", ""])
    # SHA256SUMS
    lines = []
    for f in sorted(args.out.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    print(f"[timeslice] {args.out}")
    print(f"  KG@2023 对 {len(kg_at)}｜全图对 {len(full)}｜考卷 {n_exam}")
    print(f"  strict 命中 {n_strict}（{res['strict_rate']:.2%}，CI {lo}-{hi}）"
          f" → verdict={verdict}")
    print(f"  bridge(次级) {n_bridge}｜全图对照 {res['full_graph_hits_control']}｜"
          f"PMID 交集命中 {len(pmid_overlap)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
