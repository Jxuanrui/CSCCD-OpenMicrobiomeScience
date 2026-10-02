#!/usr/bin/env python3
"""PyKEEN (RotatE) 链接预测：在主图 Tier A/B 边上训练，输出未观察的
Microbe→Disease 关联预测排序（标记 predicted，不并入主图）。

用法: python3 link_prediction.py [--epochs 200] [--dim 128] [--topn 200]
输出: data/merged/link_predictions.tsv
"""
import argparse
import random
from pathlib import Path

import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[2]
NODES = ROOT / "data" / "merged" / "merged_nodes.tsv"
EDGES = ROOT / "data" / "merged" / "merged_edges.tsv"
OUT_TPL = ROOT / "data" / "merged" / "link_predictions_{model}.tsv"
TARGET_RELATIONS = ["increases_abundance_in", "decreases_abundance_in"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--topn", type=int, default=200)
    ap.add_argument("--per-microbe", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--model", default="RotatE", choices=["RotatE", "TransE", "ComplEx", "DistMult"])
    args = ap.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(8)  # 服务器 GPU 驱动不匹配，用 CPU

    nodes = pd.read_csv(NODES, sep="\t").fillna("")
    edges = pd.read_csv(EDGES, sep="\t").fillna("")
    meta = {r["id"]: r for r in nodes.to_dict("records")}
    triples = edges[["subject", "predicate", "object"]].rename(
        columns={"subject": "head", "object": "tail"})
    print(f"[data] triples={len(triples)}", flush=True)

    from pykeen.pipeline import pipeline
    from pykeen.predict import predict_target
    from pykeen.triples import TriplesFactory
    # PyKEEN 1.11 需要 TriplesFactory（DataFrame 不被 pipeline 接受）
    tf_all = TriplesFactory.from_labeled_triples(
        triples[["head", "predicate", "tail"]].values.astype(str))
    train_tf, test_tf = tf_all.split([0.9, 0.1], random_state=args.seed)
    print(f"[data] train={train_tf.num_triples} test={test_tf.num_triples}", flush=True)
    result = pipeline(
        training=train_tf, testing=test_tf, model=args.model,
        model_kwargs={"embedding_dim": args.dim},
        training_kwargs={"num_epochs": args.epochs, "batch_size": 512, "use_tqdm": False},
        evaluation_kwargs={"batch_size": 512},
        random_seed=args.seed, device="cpu",
    )
    flat = result.metric_results.to_flat_dict()
    print(f"[eval] H@10={flat.get('hits_at_10.both', flat.get('both.realistic.hits_at_10', '?'))} "
          f"MRR={flat.get('mean_reciprocal_rank.both', flat.get('both.realistic.mean_reciprocal_rank', '?'))}", flush=True)

    model, tf = result.model, result.training
    ent_ids = set(tf.entity_to_id.keys())
    rels = set(tf.relation_to_id.keys())
    microbes = [i for i, r in meta.items() if r["category"] == "Microbe" and i in ent_ids]
    diseases = {i for i, r in meta.items() if r["category"] == "Disease" and i in ent_ids}
    known = set(zip(edges["subject"], edges["predicate"], edges["object"]))
    rows = []
    for rel in [r for r in TARGET_RELATIONS if r in rels]:
        for i, mid in enumerate(microbes, 1):
            known_tails = {o for (s, p, o) in known if s == mid and p == rel}
            try:
                pred = predict_target(model=model, head=mid, relation=rel, triples_factory=tf)
                df = pred.df  # PyKEEN 1.11 返回 TargetPredictions，需 .df 转 DataFrame
            except Exception as exc:
                print(f"  [warn] {mid} {rel}: {exc}", flush=True)
                continue
            df = df[df["tail_label"].isin(diseases) & ~df["tail_label"].isin(known_tails)]
            for _, r in df.nlargest(args.per_microbe, "score").iterrows():
                tail = r["tail_label"]
                rows.append({"subject": mid, "subject_name": meta[mid]["name"],
                             "predicate": rel, "object": tail,
                             "object_name": meta.get(tail, {}).get("name", tail),
                             "score": round(float(r["score"]), 4)})
            if i % 200 == 0:
                print(f"  {rel}: {i}/{len(microbes)} 菌完成", flush=True)
    out = pd.DataFrame(rows).sort_values("score", ascending=False).head(args.topn)
    OUT = Path(str(OUT_TPL).format(model=args.model))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep="\t", index=False)
    print(f"[out] {len(out)} 条预测（evidence=predicted，不入主图） -> {OUT}", flush=True)
    print(out.head(10).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
