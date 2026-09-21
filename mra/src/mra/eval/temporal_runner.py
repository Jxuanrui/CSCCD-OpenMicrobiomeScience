"""时序留出评测（原创设计）：agent 能否在"过去图"上预见 cutoff 后的发现。

两层打分：
1. 图论基线（零 API）：共同邻居 / Adamic-Adar / Jaccard —— 对 held-out 边与
   安全负例（past 与当前全图均无边）配对打分，输出 AUC 与 Hit@1/@5；
2. LLM 条件（预算受控）：给 planner 模型"过去图"中微生物的邻域摘要，让其给
   (microbe, object) 关联合理性打 0-1 分，同口径算 AUC。

评测公平性规则：负例必须与正例同 object 类别；负例在 past 图与当前全图均无边
（防止把"其实也已发现"的边当负例）。
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from ..kg.graph import KGGraph
from .datasets import DEFAULT_EVAL_ROOT


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def load_temporal(cutoff_year: int, eval_root: Path = DEFAULT_EVAL_ROOT) -> dict:
    out_dir = Path(eval_root) / f"temporal_{cutoff_year}"
    if not (out_dir / "heldout_questions.jsonl").is_file():
        raise FileNotFoundError(f"缺少评测产物，请先 build_temporal(cutoff={cutoff_year})")
    heldout = [json.loads(line) for line in
               open(out_dir / "heldout_questions.jsonl", encoding="utf-8")]
    past_edges = []
    with open(out_dir / "past_edges.tsv", encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            row = dict(zip(header, line.rstrip("\n").split("\t")))
            past_edges.append((row["subject"], row["object"]))
    return {"heldout": heldout, "past_edges": past_edges, "out_dir": out_dir}


class PastGraphIndex:
    """过去图的邻接与评分索引（小图纯内存，节点的全邻居集 + 程度）。"""

    def __init__(self, past_edges: list[tuple[str, str]]):
        self.adj: dict[str, set[str]] = {}
        self.deg: dict[str, int] = {}
        self.edge_pairs: set[tuple[str, str]] = set()
        for a, b in past_edges:
            self.adj.setdefault(a, set()).add(b)
            self.adj.setdefault(b, set()).add(a)
            self.edge_pairs.add(_pair_key(a, b))
        for node, nbrs in self.adj.items():
            self.deg[node] = len(nbrs)

    def neighbors(self, node: str) -> set[str]:
        return self.adj.get(node, set())

    def common_neighbors(self, a: str, b: str) -> set[str]:
        return self.neighbors(a) & self.neighbors(b)

    def score(self, a: str, b: str, method: str) -> float:
        if method == "preferential_attachment":
            # 二部图基础基线：度乘积（热门节点更可能连边）
            return float(self.deg.get(a, 0) * self.deg.get(b, 0))
        if method == "three_path":
            # 二部图核心基线：a-b'-a'-b 的 3 路径计数（共现介导的关联强度）
            total = 0
            for mid in self.neighbors(a):
                total += len(self.neighbors(mid) & self.neighbors(b))
            return float(total)
        cn = self.common_neighbors(a, b)
        if method == "common_neighbors":
            return float(len(cn))
        union = self.neighbors(a) | self.neighbors(b)
        if method == "jaccard":
            return len(cn) / len(union) if union else 0.0
        if method == "adamic_adar":
            return sum(1.0 / math.log(self.deg.get(n, 2)) for n in cn if self.deg.get(n, 1) > 1)
        raise KeyError(f"未知打分方法 {method}")


def build_eval_pairs(
    heldout: list[dict],
    full_graph: KGGraph,
    past_index: PastGraphIndex,
    n_pairs: int = 200,
    negatives_per_positive: int = 4,
    seed: int = 42,
) -> list[dict]:
    """正例 = held-out 边（subject 必须在 past 图中有上下文）；负例 = 同类别安全非边。"""
    rng = random.Random(seed)
    by_category: dict[str, list[str]] = {}
    for node in full_graph.nodes.values():
        if node.category:
            by_category.setdefault(node.category, []).append(node.id)
    # 度桶（log2）内匹配负例：排除"热门节点天然高分/低分"的混杂
    def _bucket(node_id: str) -> int:
        return int(math.log2(past_index.deg.get(node_id, 0) + 1))
    by_bucket: dict[tuple[str, int], list[str]] = {}
    for category, nodes in by_category.items():
        for node_id in nodes:
            by_bucket.setdefault((category, _bucket(node_id)), []).append(node_id)

    usable = [q for q in heldout
              if q["answer"] and past_index.neighbors(q["subject"])]
    rng.shuffle(usable)
    pairs: list[dict] = []
    for q in usable[:n_pairs]:
        obj_node = full_graph.nodes.get(q["object"])
        if obj_node is None or obj_node.category not in by_category:
            continue
        pairs.append({"subject": q["subject"], "object": q["object"],
                      "label": 1, "predicate": q["predicate"]})
        made, attempts = 0, 0
        bucket_pool = by_bucket.get((obj_node.category, _bucket(q["object"])),
                                    by_category.get(obj_node.category, []))
        while made < negatives_per_positive and attempts < 60:
            attempts += 1
            neg = rng.choice(bucket_pool)
            if neg in (q["subject"], q["object"]):
                continue
            if past_index.deg.get(neg, 0) == 0:
                continue  # 孤立点作负例对 LLM 条件不公平（无任何可判信息）
            if _pair_key(q["subject"], neg) in past_index.edge_pairs or \
                    full_graph.edge_evidence(q["subject"], neg):
                continue  # 过去或当前全图已有边：不是安全负例
            pairs.append({"subject": q["subject"], "object": neg,
                          "label": 0, "predicate": q["predicate"]})
            made += 1
    return pairs


def auc(scores: list[float], labels: list[int]) -> float:
    """Rank-based AUC（Mann-Whitney），并列取 0.5。"""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    pos = sum(1 for l in labels if l == 1)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    rank_sum_pos = sum(r for r, l in zip(ranks, labels) if l == 1)
    return (rank_sum_pos - pos * (pos + 1) / 2) / (pos * neg)


def run_heuristic_baselines(pairs: list[dict], past_index: PastGraphIndex) -> dict:
    out: dict[str, dict] = {}
    for method in ("three_path", "preferential_attachment", "adamic_adar"):
        scores = [past_index.score(p["subject"], p["object"], method) for p in pairs]
        labels = [p["label"] for p in pairs]
        out[method] = {"auc": round(auc(scores, labels), 4), "n": len(pairs)}
    return out


def _neighborhood_digest(full_graph: KGGraph, past_index: PastGraphIndex,
                         node_id: str, max_edges: int = 40) -> str:
    """节点在"过去图"中的 1 跳邻域摘要（带谓词与证据等级）。"""
    lines = []
    for other in sorted(past_index.neighbors(node_id))[:max_edges]:
        node = full_graph.nodes.get(other)
        edges = full_graph.edge_evidence(node_id, other)
        pred = edges[0].predicate if edges else "?"
        tier = edges[0].evidence_tier if edges else "?"
        lines.append(f"{pred}({tier}) {node.name if node else other}")
    return "; ".join(lines) if lines else "（过去图中无邻接）"


def llm_scores(
    pairs: list[dict],
    full_graph: KGGraph,
    past_index: PastGraphIndex,
    model_name: str | None = None,
    max_calls: int = 60,
) -> list[float]:
    """LLM 条件：只依据过去图邻域给关联合理性打分（0-1），预算硬顶 max_calls。"""
    import uuid

    from ..model_runtime import Message, ModelRef, ModelRequest
    from ..model_runtime.ark import ArkRuntime
    from ..research.planner import CAPABILITIES_PATH, PRICING_PATH

    runtime = ArkRuntime(capabilities_path=CAPABILITIES_PATH, pricing_path=PRICING_PATH)
    model_ref = ModelRef(provider="ark", model=model_name or "doubao-seed-2.0-lite",
                         version="unverified", endpoint="ark-coding")
    system = (
        "你是菌群知识评测裁判。只依据给定的'过去知识'评估 微生物与目标实体 存在"
        "直接生物学关联的可能性，输出 JSON：{\"score\": 0到1的小数, \"reason\": \"一句话\"}。"
        "不得使用过去知识之外的参数知识猜测；信息不足给 0.5 附近。只输出 JSON。"
    )
    scores: list[float] = []
    for pair in pairs[:max_calls]:
        microbe = full_graph.nodes.get(pair["subject"])
        obj = full_graph.nodes.get(pair["object"])
        if microbe is None or obj is None:
            scores.append(0.5)
            continue
        user = (
            f"微生物：{microbe.name}\n其过去知识邻域：{_neighborhood_digest(full_graph, past_index, pair['subject'])}\n"
            f"目标实体：{obj.name}（{obj.category}）\n"
            f"问题：二者存在直接生物学关联（如丰度变化/产生/调控）的可能性？"
        )
        response = runtime.complete(ModelRequest(
            request_id=uuid.uuid4().hex, model=model_ref,
            messages=(Message(role="system", content=system),
                      Message(role="user", content=user))))
        try:
            parsed = json.loads(response.content[response.content.find("{"):
                                                response.content.rfind("}") + 1])
            scores.append(max(0.0, min(1.0, float(parsed["score"]))))
        except Exception:  # noqa: BLE001 —— 解析失败记 0.5（中性，不崩评测）
            scores.append(0.5)
    return scores


def main(argv: list[str] | None = None) -> int:
    from ..kg.snapshot import latest_snapshot
    parser = argparse.ArgumentParser(description="时序留出评测（预见 cutoff 后的发现）")
    parser.add_argument("cutoff", nargs="?", type=int, default=2022)
    parser.add_argument("--n-pairs", type=int, default=200)
    parser.add_argument("--llm", type=int, default=0, help="LLM 条件调用数上限（0=跳过）")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    data = load_temporal(args.cutoff)
    full_graph = KGGraph(latest_snapshot())
    past_index = PastGraphIndex(data["past_edges"])
    pairs = build_eval_pairs(data["heldout"], full_graph, past_index, n_pairs=args.n_pairs)
    n_pos = sum(p["label"] for p in pairs)
    report = {
        "cutoff": args.cutoff,
        "heldout_edges_total": len(data["heldout"]),
        "pairs": {"n": len(pairs), "positives": n_pos, "negatives": len(pairs) - n_pos},
        "heuristics": run_heuristic_baselines(pairs, past_index),
    }
    if args.llm > 0:
        subset = []
        for p in pairs:  # 每个正例配其首个负例，保持 1:1 平衡直到用满额度
            if p["label"] == 1 and len(subset) + 2 <= args.llm:
                subset.append(p)
                neg = next((q for q in pairs if q["label"] == 0 and q["subject"] == p["subject"]), None)
                if neg:
                    subset.append(neg)
        scores = llm_scores(subset, full_graph, past_index, args.model, max_calls=args.llm)
        labels = [p["label"] for p in subset[:len(scores)]]
        report["llm"] = {"auc": round(auc(scores, labels), 4), "n": len(labels),
                         "model": args.model or "doubao-seed-2.0-lite"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
