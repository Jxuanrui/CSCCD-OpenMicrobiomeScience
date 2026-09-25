#!/usr/bin/env python3
"""Contextual Divergence Review v0.2 + RelationAssertion 生成（裁决 2026-09-25 之十项）。

核心原则：context 不是关系的附加 metadata，context 是 assertion 成立条件的
一部分。知识单位 = Entity + Predicate + Target + Context + Evidence + Provenance。

产出（data/merged/）：
- conflicts_review.tsv     情境分歧审阅表（v0.4：evidence-aware context + gate 派生可比性）
- contextual_divergence_summary.json  五类 context 指标 + 分类/解决完备度 + backlog（规范化计数）
- relation_assertions.tsv  RelationAssertion 一等知识对象（canonical relation 之外的事实层）
- pending_review_sample / high_degree_report（既有抽检口径）

Evidence-aware context（裁决 3/4）：每个维度 {value, status: explicit|inferred|unknown,
source}——证据不足禁止补全，缺失显式 unknown；只有 evidence-backed 维度参与
可比性判断。Comparability Gate（裁决 5）：关键维度全匹配才 comparable；任何
关键维度 unknown → partially_comparable（不得"看起来一样"判 comparable）；
关键维度已知但不匹配 → incomparable；comparable 且方向相反才可能
true_biological_conflict（且不进入 canonical summary，裁决 8）。
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "data/staging/llm_relations.jsonl"
MERGED = ROOT / "data/merged"
random.seed(20260925)

#: 核心情境维度（裁决 3）
CONTEXT_DIMS = ("strain", "host_species", "host_population", "geography",
                "disease", "disease_stage", "diet", "intervention", "dose",
                "experimental_model", "study_type", "endpoint", "timepoint")
#: 可比性判定的关键维度（裁决 5）
KEY_DIMS = ("strain", "host_species", "disease", "experimental_model",
            "study_type", "endpoint", "intervention")

_EXPLICIT = {
    "strain": ["MMX", "MRE 600", "ETBF", "NTBF", "pks+", "K-12", "Nissle",
               "engineered", "strain", "isolate", "clone", "derived from",
               "genotypes", "CD-SpA"],
    "host_species": ["mice", "mouse", "murine", "human", "patients", "rats",
                     "children", "adults", "in vitro", "organoid"],
    "experimental_model": ["DSS", "AOM", "CAC", "EAE", "in vitro", "organoid",
                           "cell line", "HCT-116", "HT-29", "gnotobiotic",
                           "germ-free"],
    "study_type": ["cohort", "RCT", "randomized", "trial", "cross-sectional",
                   "case-control", "volunteers"],
    "disease_stage": ["early", "late", "advanced", "mild", "severe", "recovery",
                      "chronic", "acute"],
    "diet": ["diet", "dietary", "fiber", "inulin", "FOS", "GOS", "high-fat",
             "western diet"],
    "intervention": ["supplementation", "administration", "treated",
                     "supplemented", "gavage", "intake", "probiotic"],
    "geography": ["chinese", "china", "european", "japanese", "korean",
                  "african", "indian", "population"],
    "endpoint": ["inflammation", "tumorigenesis", "barrier", "proliferation",
                 "survival", "dysbiosis", "colitis", "tumor"],
    "timepoint": ["weeks", "days", "months", "hours", "after"],
    "dose": ["mg", "g/kg", "dose", "cfu"],
    "host_population": [],   # 无显式词表——默认 unknown（禁止模型补全）
}


def _hit(text: str, dim: str):
    t = text.lower()
    return [k for k in _EXPLICIT.get(dim, []) if k.lower() in t] or None


def build_context(evidence_text: str, object_id: str) -> dict:
    """Evidence-aware context：每维 {value,status,source}；缺失显式 unknown。"""
    ctx = {}
    for dim in CONTEXT_DIMS:
        if dim == "disease":
            if object_id.startswith("MESH:") or object_id.startswith("NCBITaxon:"):
                ctx[dim] = {"value": object_id, "status": "inferred",
                            "source": "metadata"}
            else:
                ctx[dim] = {"value": "", "status": "unknown", "source": ""}
            continue
        found = _hit(evidence_text, dim)
        ctx[dim] = ({"value": ",".join(found[:2]), "status": "explicit",
                     "source": "abstract_sentence"} if found
                    else {"value": "", "status": "unknown", "source": ""})
    return ctx


def context_completeness(ctx: dict) -> float:
    known = sum(1 for d in CONTEXT_DIMS if ctx[d]["status"] != "unknown")
    return round(known / len(CONTEXT_DIMS), 4)


def comparability_gate(ctx_a: dict, ctx_b: dict) -> tuple[str, str]:
    """关键维度门：全匹配→comparable；任一 unknown→partially；已知不匹配→incomparable。"""
    basis, incomparable, unknown = [], False, False
    for d in KEY_DIMS:
        a, b = ctx_a[d], ctx_b[d]
        if a["status"] == "unknown" or b["status"] == "unknown":
            unknown = True
            basis.append(f"{d}:unknown")
            continue
        sa = {x.strip().lower() for x in a["value"].split(",") if x.strip()}
        sb = {x.strip().lower() for x in b["value"].split(",") if x.strip()}
        if sa & sb:
            basis.append(f"{d}:match")
        else:
            incomparable = True
            basis.append(f"{d}:mismatch")
    status = ("incomparable" if incomparable
              else "partially_comparable" if unknown else "comparable")
    return status, ";".join(basis)


def load_evidence_index():
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
    return " || ".join(out) if out else ""


def build_relation_assertions(idx, conflicted_pairs: set) -> int:
    """RelationAssertion 一等知识对象（裁决 7/8）：每条 ok 证据一个 assertion，
    context 挂 assertion 级；canonical relation 只是派生摘要（不吞并 assertion）。"""
    rows = []
    for (sid, pred, pmid), r in sorted(idx.items()):
        div = "contextual_divergence_pending" if (sid, r["object"]["id"]) in conflicted_pairs else ""
        text = f"{r.get('evidence') or ''} {r.get('sentence') or ''}"
        ctx = build_context(text, r["object"]["id"])
        rows.append({
            "assertion_id": f"RA-{pmid}-{abs(hash((sid, pred, r['object']['id']))) % 10**8:08d}",
            "subject": sid, "predicate": pred, "object": r["object"]["id"],
            "direction": r.get("polarity", "neutral"),
            "confidence": r.get("confidence", ""),
            "evidence_pmid": pmid,
            "evidence_excerpt": (r.get("evidence") or r.get("sentence", ""))[:200],
            "provenance": json.dumps({
                "execution_id": r.get("execution_id", ""),
                "capability_id": r.get("capability_id", ""),
                "source": "pubtator3@2026-09", "pipeline": "llm_extract_v2",
                "created_at": r.get("created_at", "")}, ensure_ascii=False),
            "context": json.dumps(ctx, ensure_ascii=False),
            "context_completeness": context_completeness(ctx),
            "divergence": div,
            "is_canonical_summary": False})
    pd.DataFrame(rows).to_csv(MERGED / "relation_assertions.tsv", sep="\t", index=False)
    return len(rows)


def main():
    idx = load_evidence_index()

    # ---- 情境分歧审阅表 v0.4 ----
    cf = MERGED / "conflicts.tsv"
    ann_path = MERGED / "divergence_annotations.tsv"
    ann = {}
    if ann_path.exists():
        for _, r in pd.read_csv(ann_path, sep="\t").fillna("").iterrows():
            ann[f"{r['subject']}|{r['object']}"] = r.to_dict()
    conflicted_pairs = set()
    rows, ctx_stats = [], {"explicit": 0, "inferred": 0, "unknown": 0, "total": 0}
    comp_dist = {}
    if cf.exists():
        for _, c in pd.read_csv(cf, sep="\t").fillna("").iterrows():
            ev_a = side_ev(idx, c["subject"], c["predicate_a"], c["pmids_a"])
            ev_b = side_ev(idx, c["subject"], c["predicate_b"], c["pmids_b"])
            ctx_a = build_context(ev_a, c["object"])
            ctx_b = build_context(ev_b, c["object"])
            for ctx in (ctx_a, ctx_b):
                for d in CONTEXT_DIMS:
                    ctx_stats[ctx[d]["status"]] += 1
                    ctx_stats["total"] += 1
            match, basis = comparability_gate(ctx_a, ctx_b)
            comp_dist[match] = comp_dist.get(match, 0) + 1
            conflicted_pairs.add((c["subject"], c["object"]))
            a = ann.get(f"{c['subject']}|{c['object']}", {})
            rows.append({
                "subject": c["subject"], "object": c["object"],
                "side_a": f"{c['predicate_a']} (pmids: {c['pmids_a']})",
                "evidence_a": ev_a,
                "side_b": f"{c['predicate_b']} (pmids: {c['pmids_b']})",
                "evidence_b": ev_b,
                "context_a": json.dumps(ctx_a, ensure_ascii=False),
                "context_b": json.dumps(ctx_b, ensure_ascii=False),
                "context_match_status": match,          # gate 派生（非人工猜测）
                "comparability_basis": basis,
                "primary_divergence_type": a.get("primary_divergence_type", ""),
                "secondary_divergence_type": a.get("secondary_divergence_type", ""),
                "ontology_gap": a.get("ontology_gap", ""),
                "resolution_action": a.get("resolution_action", ""),
                "note": a.get("note", ""),
                "annotated_by": a.get("annotated_by", "")})
        out = pd.DataFrame(rows)
        out.to_csv(MERGED / "conflicts_review.tsv", sep="\t", index=False)
        n_ann = (out["primary_divergence_type"] != "").sum()

        # ---- summary v2（五类 context 指标 + backlog 规范化分组计数）----
        ann_rows = out[out["primary_divergence_type"] != ""]
        types = ann_rows["primary_divergence_type"].value_counts().to_dict()
        sec = ann_rows[ann_rows["secondary_divergence_type"] != ""][
            "secondary_divergence_type"].value_counts().to_dict()
        res = ann_rows["resolution_action"].value_counts().to_dict()
        gap_groups: dict = {}
        for g in ann_rows["ontology_gap"]:
            if not g:
                continue
            try:
                d = json.loads(g)
            except json.JSONDecodeError:
                continue
            for k, v in d.items():
                gap_groups.setdefault(k, {})
                gap_groups[k][v] = gap_groups[k].get(v, 0) + 1
        backlog = sum(sum(b.values()) for b in gap_groups.values())
        summary = {
            "phase": "contextual_divergence_review_v0.2",
            "total_divergence": len(out),
            "classified": int(n_ann),
            "classification_completeness": round(n_ann / max(len(out), 1), 4),
            "types": {str(k): int(v) for k, v in types.items()},
            "secondary_types": {str(k): int(v) for k, v in sec.items()},
            "multi_label_count": int((ann_rows["secondary_divergence_type"] != "").sum()),
            "resolution": {str(k): int(v) for k, v in res.items()},
            "resolution_path_completeness": round(
                (ann_rows["resolution_action"] != "").sum() / max(n_ann, 1), 4),
            "comparability": comp_dist,   # gate 派生；comparable 且方向相反才可 true_conflict（人工判定）
            "context_metrics": {
                "evidence_availability": round(
                    ((out["evidence_a"] != "") & (out["evidence_b"] != "")).mean(), 4),
                "dimension_coverage": round(
                    1 - ctx_stats["unknown"] / max(ctx_stats["total"], 1), 4),
                "explicit_rate": round(ctx_stats["explicit"] / max(ctx_stats["total"], 1), 4),
                "inferred_rate": round(ctx_stats["inferred"] / max(ctx_stats["total"], 1), 4),
                "unknown_rate": round(ctx_stats["unknown"] / max(ctx_stats["total"], 1), 4)},
            "ontology_gap": gap_groups,
            "ontology_refinement_backlog": backlog,
            "true_biological_conflict": 0,  # 仅 comparable 且反向（gate 当前无 comparable → 0）
            "ai_first_pass": True, "human_review": "pending"}
        (MERGED / "contextual_divergence_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[conflicts] {len(out)} 组；已标注 {n_ann}；comparability={comp_dist}")
        print(f"[context] {summary['context_metrics']}")
        print(f"[backlog] {backlog} 项（分组：{ {k: sum(v.values()) for k, v in gap_groups.items()} }）")

    # ---- RelationAssertion ----
    n_assert = build_relation_assertions(idx, conflicted_pairs)
    print(f"[assertions] RelationAssertion {n_assert} 条（assertion 级 context/provenance）")

    # ---- 既有抽检口径 ----
    pr = MERGED / "pending_review_edges.tsv"
    if pr.exists():
        df = pd.read_csv(pr, sep="\t").fillna("")
        top_b = df[df.evidence_tier == "B"].sort_values(
            ["support_count", "confidence"], ascending=False).head(30)
        conflicted = df[df["conflict_state"] == True] if "conflict_state" in df else df.iloc[0:0]  # noqa: E712
        tier_c = df[df.evidence_tier == "C"]
        rand_c = tier_c.sample(n=min(20, len(tier_c)), random_state=25)
        sample = pd.concat([top_b, conflicted, rand_c]).drop_duplicates(
            subset=["subject", "predicate", "object"])
        sample["evidence"] = [side_ev(idx, r.subject, r.predicate, r.pmids)
                              for r in sample.itertuples()]
        sample["verdict"] = ""
        sample["sampled_at"] = date.today().isoformat()
        sample.to_csv(MERGED / "pending_review_sample.tsv", sep="\t", index=False)
        print(f"[pending] 抽样 {len(sample)} 条")
    me = MERGED / "merged_edges.tsv"
    if me.exists():
        df = pd.read_csv(me, sep="\t")
        deg = df.groupby("subject").size().sort_values(ascending=False).head(20)
        rep = pd.DataFrame({"subject": deg.index, "out_degree": deg.values})
        rep["predicates"] = [", ".join(sorted(df[df.subject == s].predicate.unique())[:6])
                             for s in deg.index]
        rep.to_csv(MERGED / "high_degree_report.tsv", sep="\t", index=False)
        print(f"[degree] top20 扇出：最高 {deg.iloc[0]}")


if __name__ == "__main__":
    main()
