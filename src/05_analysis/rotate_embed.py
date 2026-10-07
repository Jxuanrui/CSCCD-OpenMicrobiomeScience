#!/usr/bin/env python3
"""C1: 自包含 RotatE 链接预测（纯 PyTorch，零 PyKEEN 依赖）.

替代 link_prediction.py 的 PyKEEN 版本——避免 torch 1.13 + pykeen + pystow 版本冲突。
输入 candidate_v3 的 TSV，训练 RotatE 嵌入，导出 Top-N 预测边 + hits@10/MRR 报告。

用法：
  python3 rotate_embed.py [--epochs 200] [--dim 128] [--topn 200] [--outdir DIR]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "data/merged/candidate_v3"

# ===== RotatE 模型 =====

class RotatE(nn.Module):
    """RotatE: Knowledge Graph Embedding by Relational Rotation in Complex Space.

    实体 -> 2*dim 实向量（视为 dim 维复数）；关系 -> dim 相位角。
    score(h, r, t) = -|| h ∘ e^{iθ_r} - t ||
    """

    def __init__(self, n_entities: int, n_relations: int, dim: int = 128, gamma: float = 12.0):
        super().__init__()
        self.dim = dim
        self.gamma = gamma
        self.ent = nn.Embedding(n_entities, dim * 2)  # 实部+虚部
        self.rel = nn.Embedding(n_relations, dim)       # 相位角
        nn.init.xavier_uniform_(self.ent.weight)
        nn.init.uniform_(self.rel.weight, -math.pi, math.pi)

    def forward(self, h_idx, r_idx, t_idx):
        h = self.ent(h_idx)          # (B, 2*dim)
        r = self.rel(r_idx)          # (B, dim)
        t = self.ent(t_idx)          # (B, 2*dim)

        h_re, h_im = h[:, :self.dim], h[:, self.dim:]
        t_re, t_im = t[:, :self.dim], t[:, self.dim:]

        cos_r, sin_r = torch.cos(r), torch.sin(r)
        # 复数乘法: (h_re + i*h_im) * (cos_r + i*sin_r)
        rot_re = h_re * cos_r - h_im * sin_r
        rot_im = h_re * sin_r + h_im * cos_r

        dist = (rot_re - t_re) ** 2 + (rot_im - t_im) ** 2
        score = self.gamma - torch.sqrt(dist.sum(dim=1) + 1e-9)
        return score

    def loss(self, pos_score, neg_score):
        """Softmax loss（RotatE 原论文）."""
        return -(F.logsigmoid(pos_score).mean() + F.logsigmoid(-neg_score).mean())


# ===== 数据加载 =====

def load_triples(tsv_path: Path) -> list[tuple[int, int, int]]:
    """从 merged_edges.tsv 加载三元组并编码为整数索引."""
    edges = []
    with open(tsv_path) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            edges.append((r["subject"], r["predicate"], r["object"]))

    entities = sorted({e[0] for e in edges} | {e[2] for e in edges})
    relations = sorted({e[1] for e in edges})
    ent2id = {e: i for i, e in enumerate(entities)}
    rel2id = {r: i for i, r in enumerate(relations)}

    triples = [(ent2id[s], rel2id[p], ent2id[o]) for s, p, o in edges]
    return triples, entities, relations, ent2id, rel2id


def negative_sampling(triples, n_entities, n_neg=1):
    """简单均匀负采样."""
    neg = []
    for h, r, t in triples:
        for _ in range(n_neg):
            neg_t = random.randint(0, n_entities - 1)
            while neg_t == t:
                neg_t = random.randint(0, n_entities - 1)
            neg.append((h, r, neg_t))
    return neg


# ===== 评估 =====

def evaluate(model, triples, n_entities, batch_size=1024, k=10):
    """Hits@K 和 MRR（filtered 设置简化为 raw）."""
    model.eval()
    hits, ranks = 0, []
    with torch.no_grad():
        for i in range(0, len(triples), batch_size):
            batch = triples[i:i + batch_size]
            h = torch.tensor([b[0] for b in batch])
            r = torch.tensor([b[1] for b in batch])
            t = torch.tensor([b[2] for b in batch])

            # 对每个三元组，评分所有尾实体
            all_scores = []
            for j in range(n_entities):
                t_all = torch.full((len(batch),), j, dtype=torch.long)
                s = model(h, r, t_all)
                all_scores.append(s)

            # (n_entities, batch_size) -> 按 batch 取排名
            scores = torch.stack(all_scores, dim=1)  # (batch, n_entities)
            for bi in range(len(batch)):
                true_score = scores[bi, t[bi]]
                rank = (scores[bi] > true_score).sum().item() + 1
                ranks.append(rank)
                if rank <= k:
                    hits += 1

    return hits / len(triples), sum(1 / r for r in ranks) / len(ranks)


# ===== 主流程 =====

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--topn", type=int, default=200)
    ap.add_argument("--outdir", default=str(CANDIDATE))
    args = ap.parse_args()

    random.seed(42)
    torch.manual_seed(42)

    # 加载数据
    triples, entities, relations, ent2id, rel2id = load_triples(CANDIDATE / "merged_edges.tsv")
    n_ent, n_rel = len(entities), len(relations)
    print(f"[data] {len(triples)} triples | {n_ent} entities | {n_rel} relations")

    # 划分训练/测试（90/10）
    random.shuffle(triples)
    split = int(len(triples) * 0.9)
    train, test = triples[:split], triples[split:]
    print(f"[split] train={len(train)} test={len(test)}")

    # 模型
    model = RotatE(n_ent, n_rel, args.dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    # 训练
    print(f"[train] epochs={args.epochs} dim={args.dim}")
    for epoch in range(args.epochs):
        model.train()
        optimizer.zero_grad()

        # 正/负样本
        batch = random.sample(train, min(512, len(train)))
        pos_h = torch.tensor([b[0] for b in batch])
        pos_r = torch.tensor([b[1] for b in batch])
        pos_t = torch.tensor([b[2] for b in batch])
        neg = negative_sampling(batch, n_ent, n_neg=1)
        neg_h = torch.tensor([b[0] for b in neg])
        neg_r = torch.tensor([b[1] for b in neg])
        neg_t = torch.tensor([b[2] for b in neg])

        pos_score = model(pos_h, pos_r, pos_t)
        neg_score = model(neg_h, neg_r, neg_t)
        loss = model.loss(pos_score, neg_score)
        loss.backward()
        optimizer.step()

        if (epoch + 1) % 50 == 0:
            print(f"  epoch {epoch+1}/{args.epochs} loss={loss.item():.4f}", flush=True)

    # 评估
    print("[eval] computing hits@10 and MRR...")
    h10, mrr = evaluate(model, test[:200], n_ent, k=10)  # 测试集限制 200 条（速度）
    print(f"[result] hits@10={h10:.3f} MRR={mrr:.3f}")

    # 导出 Top-N 预测边
    print(f"[export] top-{args.topn} predictions...")
    model.eval()
    predictions = []
    with torch.no_grad():
        # 取高频关系
        rel_counts = Counter(t[1] for t in triples)
        top_rels = [r for r, _ in rel_counts.most_common(5)]

        for rel_id in top_rels[:3]:  # 前 3 个关系
            r_tensor = torch.tensor([rel_id])
            for h_id in random.sample(range(n_ent), min(50, n_ent)):
                h_tensor = torch.tensor([h_id])
                scores = []
                for t_id in range(n_ent):
                    if t_id == h_id:
                        continue
                    s = model(h_tensor, r_tensor, torch.tensor([t_id])).item()
                    scores.append((s, t_id))
                scores.sort(reverse=True)
                for score, t_id in scores[:5]:  # 每个头实体取 top-5
                    predictions.append({
                        "subject": entities[h_id],
                        "predicate": relations[rel_id],
                        "object": entities[t_id],
                        "score": round(score, 4),
                        "evidence": "predicted",
                    })
                    if len(predictions) >= args.topn:
                        break
                if len(predictions) >= args.topn:
                    break
            if len(predictions) >= args.topn:
                break

    # 落盘
    outdir = Path(args.outdir)
    pred_path = outdir / "rotate_predictions.tsv"
    with open(pred_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["subject", "predicate", "object", "score", "evidence"], delimiter="\t")
        w.writeheader()
        w.writerows(predictions)

    report = {
        "model": "RotatE (self-contained PyTorch)",
        "dim": args.dim, "epochs": args.epochs,
        "n_triples": len(triples), "n_entities": n_ent, "n_relations": n_rel,
        "test_size": min(200, len(test)),
        "hits_at_10": round(h10, 4), "mrr": round(mrr, 4),
        "predictions_exported": len(predictions),
        "input_sha256": "sha256:" + hashlib.sha256((CANDIDATE / "merged_edges.tsv").read_bytes()).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (outdir / "rotate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))

    print(f"[done] predictions -> {pred_path}")
    print(f"[done] report -> {outdir / 'rotate_report.json'}")
    print(f"[note] 预测边 evidence=predicted，永不进入 serving（三关制管控）")


if __name__ == "__main__":
    main()
