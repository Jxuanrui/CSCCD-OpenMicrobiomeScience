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


def build_context_edges(full_graph: KGGraph, cutoff_year: int,
                        heldout: list[dict]) -> list:
    """评测上下文边：严格过去(年份≤cutoff) ∪ 安全无日期边。

    无日期策展边是知识先验的一部分，但必须剔除与 held-out 同对(无序)的边，
    封死直接泄漏；更深的语义重叠属于'先验知识'定义本身，在报告中披露。
    """
    heldout_pairs = {_pair_key(q["subject"], q["object"]) for q in heldout if q["answer"]}
    context = []
    for edge in full_graph.edges:
        first = edge.earliest_year
        if first is not None:
            if first <= cutoff_year:
                context.append(edge)
        else:
            if _pair_key(edge.subject, edge.object) not in heldout_pairs:
                context.append(edge)
    return context


def mechanism_digest(full_graph: KGGraph, context_edges: list, node_id: str,
                     target_id: str | None = None, max_lines: int = 10) -> str:
    """机制桥摘要：node 产出的代谢物 ×（可选）目标疾病已知菌的共享代谢物桥。

    图中真实存在的 3 路径结构：m --produces--> metab <--produces-- m' --abundance--> D。
    仅使用上下文边；产物清单与共享桥分开陈述，供裁判自行权衡。
    """
    products: dict[str, set[str]] = {}
    abundance: dict[str, list[tuple[str, str]]] = {}
    for edge in context_edges:
        if edge.predicate == "produces":
            products.setdefault(edge.subject, set()).add(edge.object)
        elif edge.predicate in ("increases_abundance_in", "decreases_abundance_in"):
            sign = "↑" if edge.predicate.startswith("increases") else "↓"
            abundance.setdefault(edge.object, []).append((edge.subject, sign))

    def _name(nid: str) -> str:
        node = full_graph.nodes.get(nid)
        return node.name if node else nid

    my_products = products.get(node_id, set())
    lines = [f"已知产出代谢物：{', '.join(_name(p) for p in sorted(my_products)[:8]) or '（无）'}"]
    if target_id and my_products:
        bridges = []
        for microbe, sign in abundance.get(target_id, [])[:12]:
            shared = my_products & products.get(microbe, set())
            if shared:
                bridges.append(f"{_name(microbe)}({sign}于该病, 共享{len(shared)}个代谢物)")
        if bridges:
            lines.append("机制桥（该病已知菌与主语菌共享代谢物）：" + "; ".join(bridges[:max_lines]))
    return "\n".join(lines)


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


def _degree_bucket(node_id: str, index: PastGraphIndex) -> int:
    """度桶（log2）内匹配负例：排除"热门节点天然高分/低分"的混杂。"""
    return int(math.log2(index.deg.get(node_id, 0) + 1))


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
    by_bucket: dict[tuple[str, int], list[str]] = {}
    for category, nodes in by_category.items():
        for node_id in nodes:
            by_bucket.setdefault((category, _degree_bucket(node_id, past_index)), []).append(node_id)

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
        bucket_pool = by_bucket.get((obj_node.category, _degree_bucket(q["object"], past_index)),
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


def build_known_pairs(
    past_edges: list[tuple[str, str]],
    full_graph: KGGraph,
    past_index: PastGraphIndex,
    n_pairs: int = 50,
    negatives_per_positive: int = 4,
    seed: int = 42,
) -> list[dict]:
    """PoT 对照臂：从过去边采样"已知为真"的对（模型应当能验证）。

    与未来臂同构（同负例规则、同度桶匹配）。auc_known 与 auc_future 之差即
    "预见缺口"：known 高而 future 低 = 真预见困难；两者同低 = 上下文贫困。
    """
    rng = random.Random(seed + 1)
    usable = [(s, o) for s, o in past_edges
              if past_index.neighbors(s) and past_index.deg.get(o, 0) > 0
              and full_graph.nodes.get(o) is not None]
    rng.shuffle(usable)
    by_category: dict[str, list[str]] = {}
    by_bucket: dict[tuple[str, int], list[str]] = {}
    for node in full_graph.nodes.values():
        if node.category:
            by_category.setdefault(node.category, []).append(node.id)
            by_bucket.setdefault((node.category, _degree_bucket(node.id, past_index)), []).append(node.id)
    pairs: list[dict] = []
    for s, o in usable[:n_pairs]:
        pairs.append({"subject": s, "object": o, "label": 1, "predicate": "known"})
        obj_node = full_graph.nodes[o]
        pool = by_bucket.get((obj_node.category, _degree_bucket(o, past_index))) \
            or by_category.get(obj_node.category, [])
        made, attempts = 0, 0
        while made < negatives_per_positive and attempts < 60:
            attempts += 1
            neg = rng.choice(pool) if pool else None
            if neg is None or neg in (s, o) or past_index.deg.get(neg, 0) == 0:
                continue
            if _pair_key(s, neg) in past_index.edge_pairs or full_graph.edge_evidence(s, neg):
                continue
            pairs.append({"subject": s, "object": neg, "label": 0, "predicate": "known"})
            made += 1
    return pairs


def earliest_year_from_pair_years(years: list[int] | None, cutoff: int) -> int:
    """held-out 对的证据最早年份（无年份归 cutoff+1）。"""
    if years:
        return min(years)
    return cutoff + 1


def stratified_auc(scores: list[float], labels: list[int], years: list[int],
                   split_year: int) -> dict:
    """HINDSIGHT 式年份分层：近期未来 vs 更远未来的 AUC 衰减。"""
    near = [(s, l) for s, l, y in zip(scores, labels, years) if y <= split_year]
    far = [(s, l) for s, l, y in zip(scores, labels, years) if y > split_year]
    out = {}
    for name, group in (("near_future", near), ("far_future", far)):
        if group and any(l for _, l in group) and not all(l for _, l in group):
            out[name] = round(auc([s for s, _ in group], [l for _, l in group]), 4)
            out[name + "_n_pos"] = sum(l for _, l in group)
    return out


def run_heuristic_baselines(pairs: list[dict], past_index: PastGraphIndex) -> dict:
    out: dict[str, dict] = {}
    for method in ("three_path", "preferential_attachment", "adamic_adar"):
        scores = [past_index.score(p["subject"], p["object"], method) for p in pairs]
        labels = [p["label"] for p in pairs]
        out[method] = {"auc": round(auc(scores, labels), 4), "n": len(pairs)}
    return out


def _neighborhood_digest(full_graph: KGGraph, index: PastGraphIndex,
                         node_id: str, edge_attrs: dict | None = None,
                         max_edges: int = 40) -> str:
    """节点在给定索引中的 1 跳邻域摘要（谓词/证据等级仅取自上下文边，防泄漏）。"""
    lines = []
    for other in sorted(index.neighbors(node_id))[:max_edges]:
        node = full_graph.nodes.get(other)
        attrs = (edge_attrs or {}).get(_pair_key(node_id, other))
        pred = attrs["predicate"] if attrs else "?"
        tier = attrs["tier"] if attrs else "?"
        lines.append(f"{pred}({tier}) {node.name if node else other}")
    return "; ".join(lines) if lines else "（上下文中无邻接）"


def bootstrap_auc_ci(scores: list[float], labels: list[int],
                     n_boot: int = 1000, seed: int = 0) -> tuple[float, float]:
    import random as _random

    rng = _random.Random(seed)
    n = len(scores)
    if n == 0 or not any(labels) or all(labels):
        return (float("nan"), float("nan"))
    aucs = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        s = [scores[i] for i in idx]
        l = [labels[i] for i in idx]
        if any(l) and not all(l):
            aucs.append(auc(s, l))
    if not aucs:
        return (float("nan"), float("nan"))
    aucs.sort()
    return round(aucs[int(0.025 * len(aucs))], 4), round(aucs[int(0.975 * len(aucs))], 4)


def llm_scores(
    pairs: list[dict],
    full_graph: KGGraph,
    index: PastGraphIndex,
    model_name: str | None = None,
    max_calls: int = 60,
    context_mode: str = "flat",
    context_edges: list | None = None,
    edge_attrs: dict | None = None,
    seed: int = 42,
) -> list[float]:
    """LLM 条件打分。context_mode：
    - flat：仅 1 跳邻域摘要；
    - mechanism：邻域 + 代谢物介导机制链（来自上下文边）；
    - shuffled：邻域(真实) + 机制链换随机供体菌（消融：内容 vs 结构）。
    只依据上下文知识，解析失败记中性 0.5，预算硬顶 max_calls。
    """
    import random as _random
    import uuid

    from ..model_runtime import Message, ModelRef, ModelRequest
    from ..model_runtime.ark import ArkRuntime
    from ..research.planner import CAPABILITIES_PATH, PRICING_PATH

    runtime = ArkRuntime(capabilities_path=CAPABILITIES_PATH, pricing_path=PRICING_PATH)
    model_ref = ModelRef(provider="ark", model=model_name or "doubao-seed-2.0-lite",
                         version="unverified", endpoint="ark-coding")
    rng = _random.Random(seed)
    donors = []
    if context_mode == "shuffled" and context_edges:
        seen = set()
        for e in context_edges:
            if e.predicate in ("produces", "consumes") and e.subject not in seen:
                seen.add(e.subject)
                donors.append(e.subject)

    def _context_block(subject: str, target: str) -> str:
        flat = _neighborhood_digest(full_graph, index, subject, edge_attrs)
        if context_mode == "flat" or context_edges is None:
            return f"邻域知识：{flat}"
        if context_mode == "mechanism":
            bridge = mechanism_digest(full_graph, context_edges, subject, target)
            return f"邻域知识：{flat}\n机制信息：{bridge}"
        donor = rng.choice(donors) if donors else subject
        bridge = mechanism_digest(full_graph, context_edges, donor, target)
        return f"邻域知识：{flat}\n机制信息：{bridge}"

    system = (
        "你是菌群知识评测裁判。只依据给定的'已知知识'评估 微生物与目标实体 存在"
        "直接生物学关联的可能性，输出 JSON：{\"score\": 0到1的小数, \"reason\": \"一句话\"}。"
        "不得使用已知知识之外的参数知识猜测；信息不足给 0.5 附近。只输出 JSON。"
    )
    scores: list[float] = []
    for pair in pairs[:max_calls]:
        microbe = full_graph.nodes.get(pair["subject"])
        obj = full_graph.nodes.get(pair["object"])
        if microbe is None or obj is None:
            scores.append(0.5)
            continue
        user = (
            f"微生物：{microbe.name}\n{_context_block(pair['subject'], pair['object'])}\n"
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
    parser.add_argument("--context", choices=("flat", "mechanism", "shuffled"), default="flat",
                        help="LLM 条件上下文模式（mechanism/shuffled 需扩展上下文=严格过去∪安全无日期）")
    parser.add_argument("--strict-context", action="store_true",
                        help="LLM 条件也只用严格过去图（默认扩展上下文用于非 flat 模式）")
    parser.add_argument("--out", default=None, help="报告 JSON 追加写入路径")
    parser.add_argument("--control", action="store_true",
                        help="PoT 对照臂：已知过去对（可验证性）与未来对对照 + 年份分层")
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
    if args.control:
        known = build_known_pairs(data["past_edges"], full_graph, past_index,
                                  n_pairs=max(30, args.llm // 2 or 30))
        report["pot_control"] = {
            "known_pairs": {"n": len(known), "positives": sum(p["label"] for p in known)},
            "heuristics_known": run_heuristic_baselines(known, past_index),
            "protocol": "PoT/HINDSIGHT-aligned: auc_known vs auc_future = 预见缺口",
        }
        years_map = {_pair_key(q["subject"], q["object"]):
                     earliest_year_from_pair_years(q.get("evidence", {}).get("years"), args.cutoff)
                     for q in data["heldout"] if q["answer"]}
        report["pot_control"]["heldout_year_hist"] = {}
        for y in sorted(set(years_map.values())):
            report["pot_control"]["heldout_year_hist"][y] = sum(
                1 for v in years_map.values() if v == y)
    if args.llm > 0:
        context_edges = None
        edge_attrs = None
        index = past_index
        if not args.strict_context and args.context != "flat":
            context_edges = build_context_edges(full_graph, args.cutoff, data["heldout"])
            edge_attrs = {_pair_key(e.subject, e.object): {"predicate": e.predicate,
                                                           "tier": e.evidence_tier}
                          for e in context_edges}
            index = PastGraphIndex([(e.subject, e.object) for e in context_edges])
        subset = []
        for p in pairs:  # 每个正例配其首个负例，保持 1:1 平衡直到用满额度
            if p["label"] == 1 and len(subset) + 2 <= args.llm:
                subset.append(p)
                neg = next((q for q in pairs if q["label"] == 0 and q["subject"] == p["subject"]), None)
                if neg:
                    subset.append(neg)
        scores = llm_scores(subset, full_graph, index, args.model, max_calls=args.llm,
                            context_mode=args.context, context_edges=context_edges,
                            edge_attrs=edge_attrs)
        labels = [p["label"] for p in subset[:len(scores)]]
        lo, hi = bootstrap_auc_ci(scores, labels)
        report["llm"] = {"auc": round(auc(scores, labels), 4),
                         "auc_ci95": [lo, hi], "n": len(labels),
                         "model": args.model or "doubao-seed-2.0-lite",
                         "context": args.context,
                         "context_edges": len(context_edges) if context_edges else "strict-past"}
        years = [years_map.get(_pair_key(p["subject"], p["object"]), args.cutoff + 1)
                 for p in subset[:len(scores)]] if args.control else None
        if years:
            report["llm"]["stratified"] = stratified_auc(scores, labels, years,
                                                         split_year=args.cutoff + 1)
        if args.control:
            known = build_known_pairs(data["past_edges"], full_graph, past_index,
                                      n_pairs=max(10, args.llm // 4))
            known_subset = [p for p in known][: max(10, args.llm // 2)]
            k_scores = llm_scores(known_subset, full_graph, index, args.model,
                                  max_calls=max(10, args.llm // 2),
                                  context_mode=args.context, context_edges=context_edges,
                                  edge_attrs=edge_attrs)
            k_labels = [p["label"] for p in known_subset[:len(k_scores)]]
            report["pot_control"]["llm_known_auc"] = round(auc(k_scores, k_labels), 4)
            report["pot_control"]["foresight_gap"] = round(
                report["pot_control"]["llm_known_auc"] - report["llm"]["auc"], 4)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        out_path = Path(args.out)
        existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
        existing[f"{report.get('llm', {}).get('model', 'heur')}:{args.context}:{args.llm}"] = report
        out_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
