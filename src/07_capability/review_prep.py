#!/usr/bin/env python3
"""抽检准备（裁决 P0）：把 merge 产物变成人类可裁决的审阅表。

三件产出（data/merged/）：
- conflicts_review.tsv   冲突双方证据句并排（48 组左右）——人工判 left/right/both/nei
- pending_review_sample  分层抽样：Tier-B top30（support×confidence）+ 冲突边 + Tier-C 随机20
- high_degree_report.tsv 高扇出主体 top20（hub 风险抽检口径）

证据句从 staging ok 行按 (pmid, subject_id, predicate) 回捞——审阅者不需要
回原文库即可裁决。
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "data/staging/llm_relations.jsonl"
MERGED = ROOT / "data/merged"
random.seed(20260925)


def load_evidence_index():
    """(subject_id, predicate, pmid) -> 最佳证据记录（confidence 最高者）。"""
    idx = {}
    with STAGING.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok" or r.get("predicate") == "no_relation":
                continue
            key = (r["subject"]["id"], r["predicate"], str(r.get("pmid", "")))
            if key not in idx or float(r.get("confidence", 0)) > float(idx[key].get("confidence", 0)):
                idx[key] = r
    return idx


_CTX_PATTERNS = [
    ("model", ["mice", "mouse", "murine", "in vitro", "organoid", "CAC", "DSS",
               "AOM", "EAE", "model", "cell line", "HCT-116", "HT-29"]),
    ("host", ["patients", "human", "cohort", "children", "adults", "elderly",
              "RCT", "randomized", "volunteers"]),
    ("strain", ["MMX", "MRE 600", "ETBF", "NTBF", "pks+", "engineered", "strain",
                "derived from", "genotypes", "K-12", "Nissle"]),
    ("mechanism", ["LPS", "toxin", "membrane vesicles", "supernatant", "tRNA",
                   "metabolite", "SCFA", "indole"]),
]


def _ctx_hints(ev_text: str) -> str:
    """从证据句提取情境提示（首过粗粒度，人工复核列）。"""
    t = ev_text.lower()
    hits = []
    for dim, kws in _CTX_PATTERNS:
        found = [k for k in kws if k.lower() in t]
        if found:
            hits.append(f"{dim}:{','.join(found[:3])}")
    return "; ".join(hits) if hits else ""


def side_ev(idx, sid, pred, pmids: str):
    out = []
    for pmid in [p for p in pmids.split(";") if p]:
        r = idx.get((sid, pred, pmid))
        if r:
            out.append(f"[{pmid}] {r.get('evidence') or r.get('sentence', '')[:160]}")
    return " || ".join(out) if out else "（证据句缺失——查 staging）"


def main():
    idx = load_evidence_index()

    # 1) 情境分歧审阅表（v2 语义：conflict = knowledge context divergence signal）
    #    裁决 2026-09-25：conflict ≠ error——目标不是消除矛盾，而是解释
    #    "该关系在什么条件下成立"。divergence_type 六类 + resolution_action 五路径；
    #    既有标注存 divergence_annotations.tsv（键=subject|object），重跑不丢。
    cf = MERGED / "conflicts.tsv"
    ann_path = MERGED / "divergence_annotations.tsv"
    ann = {}
    if ann_path.exists():
        _a = pd.read_csv(ann_path, sep="\t").fillna("")
        for _, r in _a.iterrows():
            ann[f"{r['subject']}|{r['object']}"] = r.to_dict()
    if cf.exists():
        df = pd.read_csv(cf, sep="\t").fillna("")
        rows = []
        for _, c in df.iterrows():
            ev_a = side_ev(idx, c["subject"], c["predicate_a"], c["pmids_a"])
            ev_b = side_ev(idx, c["subject"], c["predicate_b"], c["pmids_b"])
            key = f"{c['subject']}|{c['object']}"
            a = ann.get(key, {})
            rows.append({
                "subject": c["subject"], "object": c["object"],
                "side_a": f"{c['predicate_a']} (pmids: {c['pmids_a']})",
                "evidence_a": ev_a,
                "side_b": f"{c['predicate_b']} (pmids: {c['pmids_b']})",
                "evidence_b": ev_b,
                "context_a": a.get("context_a", _ctx_hints(ev_a)),
                "context_b": a.get("context_b", _ctx_hints(ev_b)),
                "primary_divergence_type": a.get("primary_divergence_type", ""),
                "secondary_divergence_type": a.get("secondary_divergence_type", ""),
                "context_match_status": a.get("context_match_status", ""),
                "ontology_gap": a.get("ontology_gap", ""),
                "resolution_action": a.get("resolution_action", ""),
                "note": a.get("note", ""),
                "annotated_by": a.get("annotated_by", "")})
        out = pd.DataFrame(rows)
        out.to_csv(MERGED / "conflicts_review.tsv", sep="\t", index=False)
        n_ann = (out["primary_divergence_type"] != "").sum()
        n_miss = (out["evidence_a"].str.contains("缺失")).sum() + (out["evidence_b"].str.contains("缺失")).sum()
        print(f"[conflicts] {len(out)} 组情境分歧审阅表；已标注 {n_ann}；证据句缺失 {n_miss} 侧")
        # Contextual resolution metrics（裁决 v0.1）+ summary JSON（snapshot 发布依据）
        ann_rows = out[out["primary_divergence_type"] != ""]
        types = ann_rows["primary_divergence_type"].value_counts().to_dict()
        sec = ann_rows[ann_rows["secondary_divergence_type"] != ""]["secondary_divergence_type"].value_counts().to_dict()
        res = ann_rows["resolution_action"].value_counts().to_dict()
        match = ann_rows["context_match_status"].value_counts().to_dict()
        gaps = {}
        for g in ann_rows["ontology_gap"]:
            if g:
                gaps[g] = gaps.get(g, 0) + 1
        summary = {
            "phase": "contextual_divergence_review_v0.1",
            "total_divergence": len(out),
            "classified": int(n_ann),
            "classification_completeness": round(n_ann / max(len(out), 1), 4),
            "types": {str(k): int(v) for k, v in types.items()},
            "secondary_types": {str(k): int(v) for k, v in sec.items()},
            "multi_label_count": int((ann_rows["secondary_divergence_type"] != "").sum()),
            "context_match": {str(k): int(v) for k, v in match.items()},
            "resolution": {str(k): int(v) for k, v in res.items()},
            "resolution_path_completeness": round(
                (ann_rows["resolution_action"] != "").sum() / max(n_ann, 1), 4),
            "ontology_gap": gaps,
            "ontology_refinement_backlog": int(sum(gaps.values())),
            "true_biological_conflict": int(types.get("true_biological_conflict", 0)),
            "ai_first_pass": True,
            "human_review": "pending"}
        (MERGED / "contextual_divergence_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[summary] types={summary['types']}")
        print(f"[summary] multi_label={summary['multi_label_count']} "
              f"match={summary['context_match']} backlog={summary['ontology_refinement_backlog']}")

    # 2) pending 分层抽样
    pr = MERGED / "pending_review_edges.tsv"
    if pr.exists():
        df = pd.read_csv(pr, sep="\t").fillna("")
        top_b = df[df.evidence_tier == "B"].sort_values(
            ["support_count", "confidence"], ascending=False).head(30)
        conflicted = df[df.get("conflict_state", False) == True] if "conflict_state" in df else df.iloc[0:0]  # noqa: E712
        tier_c = df[df.evidence_tier == "C"]
        rand_c = tier_c.sample(n=min(20, len(tier_c)), random_state=25)
        sample = pd.concat([top_b, conflicted, rand_c]).drop_duplicates(
            subset=["subject", "predicate", "object"])
        sample["evidence"] = [
            side_ev(idx, r.subject, r.predicate, r.pmids)
            for r in sample.itertuples()]
        sample["verdict"] = ""
        sample["sampled_at"] = date.today().isoformat()
        sample.to_csv(MERGED / "pending_review_sample.tsv", sep="\t", index=False)
        print(f"[pending] 抽样 {len(sample)} 条（B-top {len(top_b)} / 冲突 {len(conflicted)} / C-随机 {len(rand_c)}）")

    # 3) 高扇出主体
    me = MERGED / "merged_edges.tsv"
    if me.exists():
        df = pd.read_csv(me, sep="\t")
        deg = df.groupby("subject").size().sort_values(ascending=False).head(20)
        rep = pd.DataFrame({"subject": deg.index, "out_degree": deg.values})
        rep["predicates"] = [", ".join(sorted(df[df.subject == s].predicate.unique())[:6])
                             for s in deg.index]
        rep.to_csv(MERGED / "high_degree_report.tsv", sep="\t", index=False)
        print(f"[degree] top20 扇出：最高 {deg.iloc[0]}（{deg.index[0]}）")


if __name__ == "__main__":
    main()
