#!/usr/bin/env python3
"""C2e 预注册抽样执行器（G4 E5 整改：抽样代码入库；G4b：top30 排除+预注册 sha 机器绑定）。

预注册：data/merged/route_eval/c2e_preregistration.json（须独立 commit 先于本脚本产物）。
用法：python3 c2e_sample.py [主项目根]（位置参数，默认脚本仓库根）
"""
from __future__ import annotations
import csv, hashlib, json, random, subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def main(root: str):
    RE = Path(root)/"data/merged/route_eval"; C2 = Path(root)/"data/merged/candidate_v2"
    pre = json.loads((RE/"c2e_preregistration.json").read_text())
    seed = pre["seed"]; n_ctx = pre["context_layer"]["sample_n"]; n_food = pre["food_layer"]["sample_n"]
    rows = list(csv.DictReader(open(C2/"relation_assertions.tsv"), delimiter="\t"))
    old_ids = {r["assertion_id"] for r in csv.DictReader(open(C2/"context_precision_sample.tsv"), delimiter="\t")}
    nodes = {r["id"]: r for r in csv.DictReader(open(C2/"merged_nodes.tsv"), delimiter="\t")}
    food_ids = {i for i, r in nodes.items() if r["category"] == "Food"}
    rng = random.Random(seed)
    by_dim = defaultdict(list)
    for r in rows:
        try: c = json.loads(r.get("context", "{}"))
        except Exception: continue
        if not isinstance(c, dict): continue
        for dim, spec in c.items():
            if isinstance(spec, dict) and spec.get("status") == "explicit" and r["assertion_id"] not in old_ids:
                by_dim[dim].append((r["assertion_id"], dim, str(spec.get("value","")), (r.get("evidence_span_norm","") or "")[:110]))
    ctx_sample = []
    for dim in sorted(by_dim):
        pool = list(by_dim[dim]); rng.shuffle(pool); ctx_sample += pool[:8]
    if len(ctx_sample) > n_ctx:
        by_layer = defaultdict(list)
        for it in ctx_sample: by_layer[it[1]].append(it)
        ctx_sample = []
        while len(ctx_sample) < n_ctx:
            for d in sorted(by_layer):
                if by_layer[d] and len(ctx_sample) < n_ctx: ctx_sample.append(by_layer[d].pop(0))
    top30 = {r["object_name"].strip().lower() for r in csv.DictReader(open(Path(root)/"data/staging/food_sampling3_top30.tsv"), delimiter="\t")}
    food_pool = [r for r in rows if r["subject"] in food_ids or r["object"] in food_ids]
    food_excluded = [r for r in food_pool if any(
        (nodes.get(r["object"], {}).get("name", "") or "").strip().lower() in top30 or
        (nodes.get(r["subject"], {}).get("name", "") or "").strip().lower() in top30
        for _ in [0])]
    food_pool = [r for r in food_pool if r not in food_excluded]
    rng.shuffle(food_pool)
    food_sample = [(r["assertion_id"], "food_pair",
                    f'{nodes.get(r["subject"],{}).get("name",r["subject"])} | {r["predicate"]} | {nodes.get(r["object"],{}).get("name",r["object"])}',
                    (r.get("evidence_span_norm","") or "")[:110]) for r in food_pool[:n_food]]
    out = RE/"c2e_blind_sample.tsv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["assertion_id","dimension","extracted_value","evidence_head","layer","verdict(yes/no)","note"])
        for a,d,v,ev in ctx_sample: w.writerow([a,d,v,ev,"context","",""])
        for a,d,v,ev in food_sample: w.writerow([a,d,v,ev,"food","","菌×食物断言是否成立（含谓词方向）"])
    sha = "sha256:"+hashlib.sha256(out.read_bytes()).hexdigest()
    inter = len({a for a,_,_,_ in ctx_sample} & old_ids)
    pre_sha = "sha256:" + hashlib.sha256((RE/"c2e_preregistration.json").read_bytes()).hexdigest()
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=root).stdout.strip()
    meta = {"file": out.name, "preregistration_commit": head, "preregistration_sha256_runtime": pre_sha,
            "preregistration_binding": "抽样器运行时实算预注册 sha（机器绑定，G4b 风险预警3）",
            "food_top30_intersection": len({a for a,_,_,_ in food_sample} & {r["assertion_id"] for r in food_excluded}),
            "seed": seed,
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "freeze_tag": "c2c-freeze-v2-20261001（fe0c059，早于本抽样）",
            "context_n": len(ctx_sample), "food_n": len(food_sample),
            "exclusion_check": {"old_50_id_intersection": inter},
            "context_dim_distribution": dict(Counter(d for _,d,_,_ in ctx_sample)),
            "label_key": "(assertion_id, dimension)——逐行 verdict yes/no",
            "output_sha256": sha, "sampler": "src/08_route_eval/c2e_sample.py（入库）"}
    (RE/"c2e_blind_sample.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: meta[k] for k in ["context_n","food_n","exclusion_check","output_sha256","seed"]}, ensure_ascii=False))

if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv)>1 else str(ROOT))
