"""A 基准测试 + B 分箱命中率（预注册冻结版，评审 2026-11-09 口径）。

A 四臂同题配对（回溯性对照，如实披露：KG 卡含全时段证据，与考卷无时间隔离）：
  臂1 KG 综合分 | 臂2 图谱直边共现 | 臂3 BugSigDB 单库 | 臂4 随机期望
  主指标=配对 MRR（考卷对在各臂得分表中的名次倒数均值）；各臂覆盖率单报。
B 固定等宽五箱 [0,.2)[.2,.4)[.4,.6)[.6,.8)[.8,1]（看结果前冻结）：
  每箱报命中比例+Wilson 95%CI；措辞=分箱命中率（非概率校准）。
"""
import argparse, csv, hashlib, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import sha256_file
from timeslice_eval import wilson_ci
import openpyxl

ROOT = Path(__file__).resolve().parents[2]
UID = re.compile(r"^[DC]\d{6,}$", re.I)
BINS = [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.0001)]  # 冻结


def load_exam(gutmgene_xlsx):
    wb = openpyxl.load_workbook(gutmgene_xlsx, read_only=True)
    lit = {}
    for r in wb["Literature"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None: continue
        y = int(r[3]) if isinstance(r[3], (int, float)) and r[3] else None
        cell = (r[8] or "").strip()
        ms = set() if "*" in cell else {t.strip().upper() for t in re.split(r"[,;]", cell) if UID.match(t.strip())}
        lit[idx] = (y, ms)
    py = defaultdict(set)
    for r in wb["Association"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None or r[3] in (None, "") or idx not in lit: continue
        y, ms = lit[idx]
        if y is None: continue
        t = str(int(float(r[3])))
        for m in ms: py[(t, m)].add(y)
    wb.close()
    return {k for k, ys in py.items() if min(ys) >= 2024}


def load_scores():
    """三臂得分表 + KG syn 分。"""
    cards = list(csv.DictReader(open(
        ROOT / "data/kg/run_v04_20261108_1854/subgraph/hypothesis_cards.tsv",
        encoding="utf-8"), delimiter="\t"))
    syn, cooc = {}, {}
    for c in cards:
        t = c["microbe_id"].split(":", 1)[1]; m = c["metabolite_id"]  # 病侧在链终点？
        # 注：卡片链是 菌→代谢物→基因→通路；病=HTN 固定。菌-病对得分取该菌全部卡 max
        key = t
        s = float(c.get("evidence_syn_score", 0) or 0)
        syn[key] = max(syn.get(key, 0), s)
    edges = list(csv.DictReader(open(
        ROOT / "data/kg/k6_full_20261108_malformedfix/merged_edges.tsv",
        encoding="utf-8"), delimiter="\t"))
    nodes = {n["id"]: n.get("category") for n in csv.DictReader(open(
        ROOT / "data/kg/k6_full_20261108_malformedfix/merged_nodes.tsv",
        encoding="utf-8"), delimiter="\t")}
    mic = {i for i, c in nodes.items() if c in ("Species", "Microbe")}
    dis = {i for i, c in nodes.items() if c == "Disease"}
    edge_n = Counter(); syn_pair = defaultdict(float)
    for e in edges:
        s, o = e["subject"], e["object"]
        if ((s in mic and o in dis) or (o in mic and s in dis)):
            mi = s if s in mic else o; di = o if s in mic else s
            acc = di.split(":", 1)[1]
            if UID.match(acc):
                key = (mi.split(":", 1)[1], acc.upper())
                edge_n[key] += 1
    # syn 需要菌-病级：卡只有菌+固定病。改用卡内 per-taxid max 已记 syn[taxid]
    bs = Counter()
    for r in csv.DictReader(open(
        ROOT / "data/external/bugsigdb_20261108/edges.tsv",
        encoding="utf-8"), delimiter="\t"):
        if r["object"].startswith("LFS:COND:"):
            bs[(r["subject"].split(":", 1)[1], r["object"][8:])] += 1
    return syn, edge_n, bs, cards


def mrr_of(scores: dict, exam: set):
    """考卷对在得分表的名次 → MRR + 覆盖。"""
    if not scores: return {"coverage": 0, "mrr": None, "hits": 0}
    order = sorted(scores, key=lambda k: -scores[k])
    rank = {k: i for i, k in enumerate(order, 1)}
    rr, hit = [], 0
    for q in exam:
        if q in rank:
            rr.append(1.0 / rank[q]); hit += 1
    return {"coverage": round(len([q for q in exam if q in scores]) / len(exam), 6),
            "covered_pairs": len([q for q in exam if q in scores]),
            "hits_top100": sum(1 for q in exam if q in rank and rank[q] <= 100),
            "mrr": round(sum(rr) / len(exam), 6) if exam else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit("非空 fail-closed")
    args.out.mkdir(parents=True)
    import glob
    gt = sorted(glob.glob(str(ROOT / "data/external/gutmdisorder_v3_*")))[-1]
    exam = load_exam(Path(gt) / "gutMDisorder_v3_Literature-based_All.xlsx")
    syn_t, edge_pair, bs_pair, cards = load_scores()
    # 四臂得分表（菌-病对级）
    arm_kg = {p: syn_t.get(p[0], 0.0) for p in edge_pair}          # KG 综合分（对级=菌级 max）
    arm_cooc = dict(edge_pair)                                      # 图谱直边共现
    arm_bs = dict(bs_pair)                                          # BugSigDB 单库
    res_A = {
        "exam_size": len(exam),
        "design": "回溯性对照（KG 卡含全时段证据，无时间隔离——评审披露条款）",
        "arms": {
            "kg_syn": mrr_of(arm_kg, exam),
            "graph_cooccurrence": mrr_of(arm_cooc, exam),
            "bugsigdb_lookup": mrr_of(arm_bs, exam),
            "random_expected_mrr": round(1.0 / max(1, len(arm_cooc)), 9),
        },
        "note": "gutMGene 无菌-病表（菌-基因/代谢物空间），不硬凑第四库臂——如实披露",
    }
    # B 分箱：卡级 syn 分 × L2 strict 池命中（54/534）
    strict_pairs = set()
    for r in csv.DictReader(open(
        ROOT / "data/logs/l2_eval_20261106_1821/l2_strict_pair_detail.tsv",
        encoding="utf-8"), delimiter="\t"):
        if r["in_library"] == "yes":
            strict_pairs.add((r["microbe_taxid"], r["disease_mesh"]))
    # B 分箱（对级）：图谱菌-病对 ∪ L2 strict 池，按 arm_kg 综合分入箱，命中=属 L2 strict 54 对
    hist = {i: [0, 0] for i in range(5)}
    all_pairs = set(edge_pair) | strict_pairs
    for p in all_pairs:
        s = arm_kg.get(p, 0.0)
        for i, (lo, hi) in enumerate(BINS):
            if lo <= s < hi:
                hist[i][0] += 1
                hist[i][1] += 1 if p in strict_pairs else 0
                break
    res_B = {"bins_frozen": "[0,.2)[.2,.4)[.4,.6)[.6,.8)[.8,1]",
             "denominator_note": "对级=图谱菌-病对∪L2 strict 池；命中=L2 strict 54 对",
             "bins": {}}
    for i in range(5):
        n, h = hist[i]
        lo_, hi_ = wilson_ci(h, n) if n else (None, None)
        res_B["bins"][f"[{BINS[i][0]},{BINS[i][1] if i<4 else 1}]"] = {
            "n": n, "hit": h,
            "rate": round(h / n, 6) if n else None,
            "ci95": [lo_, hi_]}
    out = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "A_benchmark": res_A, "B_bin_hitrate": res_B,
           "inputs_sha256": {k: sha256_file(ROOT / v) for k, v in {
               "cards": "data/kg/run_v04_20261108_1854/subgraph/hypothesis_cards.tsv",
               "edges": "data/kg/k6_full_20261108_malformedfix/merged_edges.tsv",
               "bugsigdb": "data/external/bugsigdb_20261108/edges.tsv",
               "l2_detail": "data/logs/l2_eval_20261106_1821/l2_strict_pair_detail.tsv"}.items()},
           "script_sha256": sha256_file(Path(__file__))}
    (args.out / "benchmark.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("A 四臂 MRR:", {k: (v.get("mrr") if isinstance(v, dict) else v) for k, v in res_A["arms"].items()})
    print("A 覆盖:", {k: v.get("coverage") for k, v in res_A["arms"].items() if isinstance(v, dict)})
    print("B 分箱:", {k: (v["n"], v["hit"], v["rate"]) for k, v in res_B["bins"].items()})
    return 0

if __name__ == "__main__":
    sys.exit(main())
