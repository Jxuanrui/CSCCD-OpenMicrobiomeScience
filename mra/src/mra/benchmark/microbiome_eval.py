"""菌群科研评测统一入口（MRA-Bench 的菌群扩展层）。

python -m mra.benchmark.microbiome_eval [cutoff]
零 API：回放检索回归（工具层正确性）+ 时序留出图论基线（发现可预测性下界）。
LLM 条件结果由 temporal_runner 单独运行后人工对照（见 var/eval/）。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from ..eval.datasets import DEFAULT_EVAL_ROOT
from ..eval.temporal_runner import (
    PastGraphIndex,
    build_eval_pairs,
    load_temporal,
    run_heuristic_baselines,
)
from ..kg.graph import KGGraph
from ..kg.snapshot import latest_snapshot


def replay_retrieval_regression(graph: KGGraph, n_positive: int = 200,
                                seed: int = 0) -> dict:
    questions_path = Path(DEFAULT_EVAL_ROOT) / "replay_questions.jsonl"
    if not questions_path.is_file():
        return {"error": f"缺少 {questions_path}，请先 build_replay"}
    questions = [json.loads(line) for line in open(questions_path, encoding="utf-8")]
    positives = [q for q in questions if q["answer"]]
    negatives = [q for q in questions if not q["answer"]]
    rng = random.Random(seed)
    rng.shuffle(positives)
    positives = positives[:n_positive]
    tp = sum(1 for q in positives if graph.edge_evidence(q["subject"], q["object"]))
    tn = sum(1 for q in negatives if not graph.edge_evidence(q["subject"], q["object"]))
    return {"positives_recalled": f"{tp}/{len(positives)}",
            "negatives_correctly_rejected": f"{tn}/{len(negatives)}",
            "accuracy": round((tp + tn) / (len(positives) + len(negatives)), 4)}


def main(argv: list[str] | None = None) -> int:
    cutoff = int(argv[0]) if argv else 2022
    graph = KGGraph(latest_snapshot())
    data = load_temporal(cutoff)
    past_index = PastGraphIndex(data["past_edges"])
    pairs = build_eval_pairs(data["heldout"], graph, past_index, n_pairs=200)
    report = {
        "snapshot_stats": graph.stats(),
        "replay_retrieval_regression": replay_retrieval_regression(graph),
        f"temporal_holdout_c{cutoff}": {
            "heldout_edges": len(data["heldout"]),
            "pairs": {"n": len(pairs),
                      "positives": sum(p["label"] for p in pairs)},
            "heuristics": run_heuristic_baselines(pairs, past_index),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
