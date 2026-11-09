<<<<<<< HEAD
"""序3：反事实证据贡献（规格 v04_seq3_spec_20261107.md v1.2，评审 2026-11-08 批准执行）。
=======
"""序3：反事实证据贡献（规格 v04_seq3_spec_20261107.md v1.2，监工 2026-11-08 批准执行）。
>>>>>>> origin/main

做法（最忠实实现）：每次拔边 = 在隔离副本里删掉该边后**整跑一遍 D 阶段产卡管线**
（子进程，与基线完全同参数）→ 读回全量卡集 → 用 hypothesize.rank_key 整体重排 →
取反事实 TOP-10 → 记录目标卡位移。完整卡集统一重算=天然满足（无选择性重算）。

工具化：本模块即 `kg explain --counterfactual` 的内核（CLI 薄转发待挂）。
"""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/06_tool"))
from bridge_probe import sha256_file

SRC_SUB = ROOT / "data/kg/subgraph_htn_20261101_120403_t2merge"
CHAIN_EDGE_COLS = ["hop1_edge_id", "hop2_edge_id", "hop3_edge_id"]
EDGE_FILE = "merged_edges.tsv"
ORIG = json.loads((SRC_SUB / "hypothesis_manifest.json").read_text(
    encoding="utf-8"))["params"]
PY = str(ROOT / ".venv/bin/python")


def read_tsv(p: Path):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def run_pipeline(sg: Path):
    """整跑 D 阶段（同基线参数）→ 全量卡集列表。"""
    r = subprocess.run(
        [PY, "src/06_tool/hypothesize.py", "--subgraph-dir", str(sg),
         "--ext102", str(ROOT / ORIG["ext102"]),
         "--gutmgene-dir", ORIG["gutmgene_dir"]],
        cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[cf] 管线失败: {r.stderr[-400:]}")
    return read_tsv(sg / "hypothesis_cards.tsv")


def rank_of(cards, chain_id):
    from hypothesize import rank_key
    ordered = sorted(cards, key=rank_key)
    for i, c in enumerate(ordered, 1):
        if c["chain_id"] == chain_id:
            return i
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序3 反事实证据贡献")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    for d in (args.work, args.out):
        if d.exists() and any(d.iterdir()):
            raise SystemExit(f"[cf] {d} 非空（fail-closed）")
    args.work.mkdir(parents=True)
    args.out.mkdir(parents=True)

    from hypothesize import rank_key
    # 0) 工作副本 + 基线（对账器已证=冻结件逐字节同，此处复算取基线排名）
    sg = args.work / "sub"
    shutil.copytree(SRC_SUB, sg)
    for f in ("hypothesis_cards.tsv", "top_hypotheses.tsv",
              "hypothesis_cards_null_control.tsv", "hypothesis_manifest.json"):
        (sg / f).unlink(missing_ok=True)
    t0 = time.time()
    base_cards = run_pipeline(sg)
    base_ordered = sorted(base_cards, key=rank_key)
    base_top = [c["chain_id"] for c in base_ordered[:args.top_n]]
    base_rank = {c["chain_id"]: i for i, c in enumerate(base_ordered, 1)}
    print(f"[cf] 基线 {len(base_cards)} 卡 TOP{args.top_n} 就绪（{time.time()-t0:.0f}s）")

    # 1) 边→卡归属（机械展开：链的三跳边 id）
    chains = read_tsv(SRC_SUB / "chains.tsv")
    edges_of = {}
    for ch in chains:
        if ch["chain_id"] in set(base_top):
            edges_of[ch["chain_id"]] = [ch[c] for c in CHAIN_EDGE_COLS
                                        if ch.get(c)]

    # 2) 逐边拔除（整管线重跑）
    rows = []
    counter = 0
    for cid in base_top:
        for eid in edges_of.get(cid, []):
            counter += 1
            # 副本：从干净基线副本拷贝后删边（避免累积污染）
            cf_dir = args.work / f"cf_{counter}"
            shutil.copytree(sg, cf_dir)
            ep = cf_dir / EDGE_FILE
            lines = ep.read_text(encoding="utf-8").splitlines(keepends=True)
            kept = [lines[0]] + [l for l in lines[1:]
                                 if f"\t{eid}\t" not in l]
            if len(kept) == len(lines):
                rows.append([cid, eid, "EDGE_NOT_FOUND", "", "", ""])
                shutil.rmtree(cf_dir)
                continue
            ep.write_text("".join(kept), encoding="utf-8")
            # 反事实语义：边没了 → 引用该边的链断裂（同步删除）——否则
            # 管线 fail-closed（链缺溯源列）会正确拒跑；删链正是"该证据
            # 不存在则该机制链不存在"的忠实表达（规格 §二 共享边条款）
            cp = cf_dir / "chains.tsv"
            cl = cp.read_text(encoding="utf-8").splitlines(keepends=True)
            keptc = [cl[0]] + [l for l in cl[1:] if f"\t{eid}\t" not in l]
            cp.write_text("".join(keptc), encoding="utf-8")
            for f in ("hypothesis_cards.tsv", "top_hypotheses.tsv",
                      "hypothesis_cards_null_control.tsv",
                      "hypothesis_manifest.json"):
                (cf_dir / f).unlink(missing_ok=True)
            cards = run_pipeline(cf_dir)
            new_rank = rank_of(cards, cid)
            n_top_after = min(args.top_n, len(cards))
            disp = ("OUT" if new_rank is None or new_rank > n_top_after
                    else new_rank - base_rank[cid])
            rows.append([cid, eid, base_rank[cid],
                         new_rank if new_rank is not None else "OUT",
                         disp, len(cards)])
            shutil.rmtree(cf_dir)
            print(f"  {cid[:14]}… −{eid[:8]} → "
                  f"{base_rank[cid]}→{new_rank if new_rank else 'OUT'}")

    # 3) 产物
    with open(args.out / "counterfactual_edges.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["chain_id", "removed_edge_id", "baseline_rank",
                    "counterfactual_rank", "rank_shift", "n_cards_after"])
        w.writerows(rows)
    # 汇总（每卡：最大位移/位移中位/OUT 计数）
    import statistics
    summ = []
    for cid in base_top:
        rs = [r for r in rows if r[0] == cid]
        shifts = [r[4] for r in rs if isinstance(r[4], int)]
        outs = sum(1 for r in rs if r[4] == "OUT")
        summ.append([cid, base_rank[cid], len(rs), outs,
                     max(shifts) if shifts else 0,
                     statistics.median(shifts) if shifts else 0])
    with open(args.out / "counterfactual_summary.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["chain_id", "baseline_rank", "n_edges_tested",
                    "n_out_of_top", "max_shift", "median_shift"])
        w.writerows(summ)
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": "v04_seq3_spec_20261107.md (v1.2)",
        "top_n": args.top_n,
        "method": "每拔一边=隔离副本整跑 D 阶段管线+全量卡集 rank_key 重排",
        "n_cards_baseline": len(base_cards),
        "n_counterfactual_runs": counter,
        "inputs_sha256": {
            "chains": sha256_file(SRC_SUB / "chains.tsv"),
            "merged_edges": sha256_file(SRC_SUB / EDGE_FILE),
            "hypothesize_py": sha256_file(ROOT / "src/06_tool/hypothesize.py"),
            "script_sha256": sha256_file(Path(__file__)),
        },
        "baseline_reconcile_ref":
            "data/logs/baseline_reconcile_20261108_1013 (PASS)",
        "discipline": ["禁因果措辞（只说移除后排名变为）",
                       "完整卡集统一重算（子进程整跑，无选择性重算）",
                       "边→卡归属机械展开自 chains.tsv"],
    }
    (args.out / "cf_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}"
             for f in sorted(args.out.iterdir()) if f.name != "SHA256SUMS"]
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    print(f"[cf] 完成：{counter} 次反事实重跑 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
