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


def side_ev(idx, sid, pred, pmids: str):
    out = []
    for pmid in [p for p in pmids.split(";") if p]:
        r = idx.get((sid, pred, pmid))
        if r:
            out.append(f"[{pmid}] {r.get('evidence') or r.get('sentence', '')[:160]}")
    return " || ".join(out) if out else "（证据句缺失——查 staging）"


def main():
    idx = load_evidence_index()

    # 1) 冲突审阅表：双方证据并排 + 空裁决列
    cf = MERGED / "conflicts.tsv"
    if cf.exists():
        df = pd.read_csv(cf, sep="\t").fillna("")
        rows = []
        for _, c in df.iterrows():
            rows.append({
                "subject": c["subject"], "object": c["object"],
                "side_a": f"{c['predicate_a']} (pmids: {c['pmids_a']})",
                "evidence_a": side_ev(idx, c["subject"], c["predicate_a"], c["pmids_a"]),
                "side_b": f"{c['predicate_b']} (pmids: {c['pmids_b']})",
                "evidence_b": side_ev(idx, c["subject"], c["predicate_b"], c["pmids_b"]),
                "verdict": "",  # 人工：side_a / side_b / both / nei
                "note": ""})
        out = pd.DataFrame(rows)
        out.to_csv(MERGED / "conflicts_review.tsv", sep="\t", index=False)
        n_miss = (out["evidence_a"].str.contains("缺失")).sum() + (out["evidence_b"].str.contains("缺失")).sum()
        print(f"[conflicts] {len(out)} 组冲突审阅表；证据句缺失 {n_miss} 侧")

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
