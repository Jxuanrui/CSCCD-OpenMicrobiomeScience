"""历史回放与时序留出两类考题生成器（第三类 LitQA2 子集见 litqa2.py）。

1. replay（历史发现回放）：Tier A/B 边为正例；负例 = 保持主语与谓词、换同类别
   干扰宾语且图中不存在的三元组；确定性采样（seed 固定）保证可复现。
2. temporal（时序留出，原创评测）：以边的最早支持年份为知识出现时间，把图切成
   "过去图"（cutoff 前已知）与"held-out 答案集"（cutoff 后才出现），考 agent 在
   过去图上能否预见后续发现。
所有考题携带证据出处；无年份的边进 temporal 时计入 undated 并排除。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from ..kg.graph import KGGraph

DEFAULT_EVAL_ROOT = Path(__file__).resolve().parents[3] / "var" / "eval"


def _edge_question(subject: str, predicate: str, obj: str, answer: bool, evidence: dict | None) -> dict:
    return {
        "type": "edge_veracity",
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "answer": answer,
        "evidence": evidence or {},
    }


def replay_questions(g: KGGraph, *, n_negative: int = 100, seed: int = 42) -> list[dict]:
    """正例 = 全部 Tier A/B 边；负例 = 确定性采样的不存在三元组（n_negative 条或与正例等量取小）。"""
    rng = random.Random(seed)
    positives = [e for e in g.edges if e.evidence_tier in ("A", "B")]
    questions = [_edge_question(e.subject, e.predicate, e.object, True, e.evidence()) for e in positives]

    by_category: dict[str, list[str]] = {}
    for node in g.nodes.values():
        if node.category:
            by_category.setdefault(node.category, []).append(node.id)

    made, attempts = 0, 0
    max_attempts = max(len(positives) * 20, 1000)
    while made < min(n_negative, len(positives)) and attempts < max_attempts:
        attempts += 1
        base = rng.choice(positives)
        obj_node = g.nodes.get(base.object)
        if obj_node is None or obj_node.category not in by_category:
            continue
        distractor = rng.choice(by_category[obj_node.category])
        if distractor in (base.subject, base.object):
            continue
        if g.edge_evidence(base.subject, distractor):
            continue
        questions.append(_edge_question(base.subject, base.predicate, distractor, False, None))
        made += 1
    return questions


def temporal_split(g: KGGraph, cutoff_year: int) -> dict:
    """按最早支持年份切图；返回过去边/held-out 边/无年份边（id 列表级）。"""
    past, heldout, undated = [], [], []
    for e in g.edges:
        first = e.earliest_year
        if first is None:
            undated.append(e)
        elif first <= cutoff_year:
            past.append(e)
        else:
            heldout.append(e)
    return {"cutoff_year": cutoff_year, "past": past, "heldout": heldout, "undated": undated}


def build_replay(g: KGGraph, out_path: Path | None = None, **kwargs) -> Path:
    out_path = Path(out_path) if out_path else DEFAULT_EVAL_ROOT / "replay_questions.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    questions = replay_questions(g, **kwargs)
    with open(out_path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    return out_path


def build_temporal(g: KGGraph, cutoff_year: int, out_dir: Path | None = None) -> dict:
    """输出 held-out 考题 JSONL + 过去图边 TSV + 统计；过去图节点 TSV 由调用方连同快照一并使用。"""
    out_dir = Path(out_dir) if out_dir else DEFAULT_EVAL_ROOT / f"temporal_{cutoff_year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    split = temporal_split(g, cutoff_year)

    with open(out_dir / "heldout_questions.jsonl", "w", encoding="utf-8") as f:
        for e in split["heldout"]:
            f.write(json.dumps(_edge_question(e.subject, e.predicate, e.object, True, e.evidence()),
                               ensure_ascii=False) + "\n")

    header = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
              "support_count\tconfidence\tpolarity\tlast_updated\n")
    with open(out_dir / "past_edges.tsv", "w", encoding="utf-8") as f:
        f.write(header)
        for e in split["past"]:
            f.write("\t".join([
                e.subject, e.predicate, e.object, e.source_type, e.evidence_tier,
                ";".join(e.pmids), ";".join(str(y) for y in e.years),
                str(e.support_count), str(e.confidence), e.polarity, e.last_updated,
            ]) + "\n")

    stats = {
        "cutoff_year": cutoff_year,
        "past_edges": len(split["past"]),
        "heldout_edges": len(split["heldout"]),
        "undated_edges": len(split["undated"]),
        "heldout_by_tier": {},
    }
    for e in split["heldout"]:
        stats["heldout_by_tier"][e.evidence_tier] = stats["heldout_by_tier"].get(e.evidence_tier, 0) + 1
    (out_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out_dir": out_dir, **stats}
