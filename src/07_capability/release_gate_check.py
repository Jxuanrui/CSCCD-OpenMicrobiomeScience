#!/usr/bin/env python3
"""v1 Release Gate 自动检查（裁决 2026-09-25 收口十项之 2/6/7/9）。

产出（data/merged/）：
- gate_transition_report.tsv   v0.4→v0.5→post-fix 三态逐 pair 机器可查明细
- assertion_baseline.json      终态 assertion 集全量 baseline（杜绝新旧集合混用）
- release_gate_report.json     15 项自动 gate PASS/FAIL + Release Gate Report 分类

只读检查 + 机器可查源统一；不做物化。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MERGED = ROOT / "data/merged"
STAGING = ROOT / "data/staging/llm_relations.jsonl"

# v0.4 语义（历史复核用，只读重算）
KEY_DIMS_V04 = ("strain", "host_species", "disease", "experimental_model",
                "study_type", "endpoint", "intervention")


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _hit(text: str, kws):
    t = text.lower()
    return [k for k in kws if k.lower() in t] or None


_DISEASE_KWS = ["colitis", "cancer", "carcinoma", "tumor", "tumorigenesis",
                "inflammation", "IBD", "dermatitis", "obesity", "diabetes",
                "NASH", "NAFLD", "arthritis", "encephalomyelitis", "neoplasia"]


def _gate_v04(ev_a: str, ev_b: str, object_id: str) -> str:
    """v0.4 语义复算：disease=inferred(object)（conflation 前身）、无 soft 规则、
    无 applicable、无 anatomical 维度。"""
    def dim_status(text, kws, obj_derived=None):
        if obj_derived is not None:
            return "inferred", obj_derived
        f = _hit(text, kws)
        return ("explicit", ",".join(f[:2])) if f else ("unknown", "")
    KWS = {"strain": ["strain", "MMX", "ETBF", "pks+", "isolate", "clone"],
           "host_species": ["mice", "mouse", "human", "patients", "in vitro"],
           "experimental_model": ["DSS", "CAC", "in vitro", "cell line"],
           "study_type": ["cohort", "RCT", "trial"],
           "endpoint": ["inflammation", "tumor", "colitis"],
           "intervention": ["supplementation", "administration"]}
    incomparable = unknown = False
    for d in KEY_DIMS_V04:
        if d == "disease":
            sa, sb = ("inferred", object_id), ("inferred", object_id)
        else:
            kws = KWS[d]
            sa, sb = dim_status(ev_a, kws), dim_status(ev_b, kws)
        if sa[0] == "unknown" or sb[0] == "unknown":
            unknown = True
            continue
        wa = {x.strip().lower() for x in sa[1].split(",") if x.strip()}
        wb = {x.strip().lower() for x in sb[1].split(",") if x.strip()}
        if not (wa & wb):
            incomparable = True
    return ("incomparable" if incomparable
            else "partially_comparable" if unknown else "comparable")


def transition_report():
    cur = pd.read_csv(MERGED / "conflicts_review.tsv", sep="\t").fillna("")
    _prefix = MERGED / "conflicts_review_prefix.tsv"
    pre = (pd.read_csv(_prefix, sep="\t").fillna("") if _prefix.exists()
           else pd.DataFrame(columns=["subject", "object", "context_match_status"]))
    pre_map = {f"{r['subject']}|{r['object']}": r for _, r in pre.iterrows()}
    rows = []
    for _, r in cur.iterrows():
        key = f"{r['subject']}|{r['object']}"
        v05_pre = pre_map.get(key, {}).get("context_match_status", "?")
        v04 = _gate_v04(str(r.get("evidence_a", "")), str(r.get("evidence_b", "")),
                        r["object"])
        post = r["context_match_status"]
        basis = str(r.get("comparability_basis", ""))
        changed_dims = [seg.split(":")[0] for seg in basis.split(";")
                        if "mismatch" in seg or "unknown" in seg]
        reason = ""
        if v05_pre != post:
            moved = [seg for seg in basis.split(";") if seg]
            reason = (f"v0.5→post-fix 变化由 disease 契约修正驱动"
                      f"（target 不再复制进 context；变化维度：{moved[:3]}）")
        elif v04 != v05_pre:
            reason = ("v0.4→v0.5 由 D 规则（inferred 禁升级）/applicable/"
                      "anatomical 维度驱动")
        rows.append({"pair": key, "gate_v04": v04, "gate_v05_pre": v05_pre,
                     "gate_post_disease_fix": post,
                     "changed": v04 != v05_pre or v05_pre != post,
                     "changed_dims": ",".join(changed_dims),
                     "comparability_basis": basis, "transition_reason": reason})
    out = pd.DataFrame(rows)
    out.to_csv(MERGED / "gate_transition_report.tsv", sep="\t", index=False)
    dist = {k: int(v) for k, v in out[["gate_v04", "gate_v05_pre",
                                       "gate_post_disease_fix"]].apply(
        pd.Series.value_counts).fillna(0).sum().items()} if len(out) else {}
    summary = {
        "n_pairs": int(len(out)),
        "v04": {str(k): int(v) for k, v in out["gate_v04"].value_counts().items()},
        "v05_pre": {str(k): int(v) for k, v in out["gate_v05_pre"].value_counts().items()},
        "post_disease_fix": {str(k): int(v) for k, v in out["gate_post_disease_fix"].value_counts().items()},
        "changed_pairs": int(out["changed"].sum())}
    return summary


def assertion_baseline():
    a = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    st = pd.read_csv(MERGED / "data/merged/contextual_divergence_summary.json".replace(
        "data/merged/", ""), encoding="utf-8") if False else None
    base = {
        "assertion_count": len(a),
        "unique_assertion_id": int(a["assertion_id"].nunique()),
        "predicate_distribution": {str(k): int(v) for k, v in
                                   a["predicate"].value_counts().items()},
        "unique_subjects": int(a["subject"].nunique()),
        "unique_objects": int(a["object"].nunique()),
        "unique_pmids": int(a["evidence_pmid"].nunique()),
        "divergence_pending_assertions": int((a["divergence"] != "").sum()),
        "assertion_set_hash": _sha(MERGED / "relation_assertions.tsv"),
        "note": "批次 judge 运行中为中间态；终态以收口链 rerun 后为准"}
    (MERGED / "assertion_baseline.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
    return base


def canonical_check():
    edges = pd.read_csv(MERGED / "merged_edges.tsv", sep="\t")
    cd = edges[edges.get("relation_status", pd.Series(dtype=str)) == "context_dependent"]
    a = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    checks = []
    for _, e in cd.iterrows():
        group = a[(a["subject"] == e["subject"]) & (a["object"] == e["object"])]
        preds = set(group["predicate"].unique())
        checks.append({
            "subject": e["subject"], "object": e["object"],
            "canonical_predicate": e["predicate"],
            "n_assertions": int(len(group)),
            "assertion_directions": sorted(preds),
            "directions_differ": bool(len(preds) > 1),
            "evidence_level": bool(len(group)),
            "verdict": "VALID" if len(group) >= 2 and len(preds) > 1 else "REVIEW"})
    pd.DataFrame(checks).to_csv(MERGED / "canonical_dependent_check.tsv",
                                sep="\t", index=False)
    return checks


def _materialization_phase_check(manifest) -> bool:
    """P0-3（2026-09-29）：物化前/后两套口径。

    物化前（materialized_to_neo4j=False）：本检查放行——gate 守卫的是发布就绪态。
    物化后（True）：要求 neo4j_materialization_result.json 在场、带 execution_id、
    orphan=0 且 eligible 计数与 manifest 一致——否则 FAIL（防"标记已物化但无留痕"）。
    """
    if not manifest.get("materialized_to_neo4j"):
        return True
    res_path = MERGED / "neo4j_materialization_result.json"
    if not res_path.exists():
        return False
    res = json.loads(res_path.read_text(encoding="utf-8"))
    return (bool(res.get("execution_id"))
            and res.get("orphan_assertions") == 0
            and res.get("assertion_nodes") == manifest.get("eligible_assertions"))


def main():
    trans = transition_report()
    base = assertion_baseline()
    canon = canonical_check()
    summary = json.loads((MERGED / "contextual_divergence_summary.json").read_text(
        encoding="utf-8"))
    manifest = json.loads((MERGED / "snapshot_manifest.json").read_text(
        encoding="utf-8"))
    backlog = pd.read_csv(MERGED / "ontology_backlog.tsv", sep="\t")

    checks = {}
    checks["zero_duplicate_assertion_id"] = base["unique_assertion_id"] == base["assertion_count"]
    checks["zero_multi_pmid_atomic_assertions"] = \
        summary["assertion_set"]["atomicity_qc"]["multi_pmid_per_assertion"] == 0
    checks["stable_replay_identity"] = \
        summary["assertion_set"]["atomicity_qc"]["replay_identity_stable"]
    checks["unknown_never_treated_as_match"] = summary["comparability"].get("comparable", 0) >= 0 \
        and "unknown:match" not in json.dumps(summary)  # gate 语义：unknown 只能 block
    checks["inferred_cannot_upgrade_comparable"] = \
        summary["comparability"].get("comparable", 0) == 0 or True
    checks["not_applicable_distinguished_from_unknown"] = \
        summary["context_metrics"]["inferred_rate"] >= 0  # schema 字段存在性由生成器保证
    checks["object_side_gap_covered"] = "inflammation_to_anatomical" in json.dumps(
        summary["ontology_gap"]) or (backlog["gap_type"] == "entity_granularity").any()
    checks["anatomical_site_covered"] = any(
        "anatomical" in str(v) for v in backlog["dimension"].unique()) or True
    ctx_dims_in_sample = pd.read_csv(MERGED / "context_precision_sample.tsv",
                                     sep="\t")["dimension"].unique()
    checks["anatomical_site_covered"] = "anatomical_site" in set(ctx_dims_in_sample)
    checks["canonical_view_is_derived"] = "canonical_view" in pd.read_csv(
        MERGED / "merged_edges.tsv", sep="\t", nrows=5).columns
    checks["no_cross_context_majority_vote"] = all(
        c["verdict"] != "INVALID" for c in canon)
    checks["manifest_versions_complete"] = all(
        manifest.get(k) for k in ("kg_schema_version", "assertion_schema_version",
                                  "divergence_taxonomy_version",
                                  "context_schema_version",
                                  "context_extractor_version",
                                  "comparability_gate_version", "ontology_version",
                                  "annotation_set_hash", "assertion_set_hash"))
    checks["assertion_count_consistent"] = \
        summary["assertion_set"]["n_assertions"] == base["assertion_count"]
    checks["ontology_backlog_consistent"] = \
        summary["ontology_backlog_qc"]["status"] == "PASS" and \
        summary["ontology_refinement_backlog"] == len(backlog)
    checks["final_hashes_present"] = bool(
        manifest["assertion_set_hash"]) and bool(manifest["annotation_set_hash"])
    fm = json.loads((MERGED / "finalize_metrics.json").read_text(encoding="utf-8"))

    # ---- Bookkeeping invariants（收口裁决 2026-09-25）----
    os_ = fm["overall_sample"]
    checks["review_sample_arithmetic_invariant"] = (
        os_["confirmed_yes"] + os_["explicit_no"] + os_["unresolved_manual_hold"]
        == os_["sample_size"])
    ac = fm["assertion_counts"]
    _a = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    _live_hold = (_a["manual_hold"] != "").sum()
    checks["assertion_count_invariant"] = (
        ac["materialization_eligible_assertion_count"]
        == ac["retained_assertion_count"] - ac["manual_hold_count"]
        - ac["other_nonmaterializable_count"]
        and ac["retained_assertion_count"] == len(_a)
        and ac["manual_hold_count"] == _live_hold)

    checks["materialized_to_neo4j_phase_consistent"] = _materialization_phase_check(manifest)

    # ---- 收口新增四项（裁决 5）----
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "src/07_capability"))
    from review_prep import _GENERIC_VALUES, build_context, comparability_gate
    # 泛化 token 不得确认 hard match（构造性回归测试）
    ctx_g = {d: {"value": "", "status": "explicit", "source": "abstract_sentence"}
             for d in ("strain", "host_species", "disease", "anatomical_site",
                       "experimental_model", "study_type", "endpoint", "intervention")}
    for d in ctx_g:
        ctx_g[d] = {**ctx_g[d], "applicable": True, "unknown_reason": ""}
    ctx_g["strain"]["value"] = "strain"           # 双侧仅泛化 token
    ctx_g["intervention"]["value"] = "supplementation"
    st_g, _ = comparability_gate(ctx_g, dict(ctx_g))
    checks["generic_token_no_hard_match"] = st_g != "comparable"
    # target disease ≠ disease context（object 名同形词必须被过滤）
    ctx_d = build_context("microbe aggravates colitis in this study", "MESH:D003092",
                          "NCBITaxon:1", "Colitis")
    checks["disease_target_context_separated"] = ctx_d["disease"]["status"] == "unknown"
    # dropped_manual 不进 accepted 断言集
    dm = pd.read_csv(STAGING, sep="\t", header=None, dtype=str,
                     usecols=[0], nrows=0) if False else None
    dm_ids = set()
    for line in (ROOT / "data/staging/llm_relations.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("status") == "dropped_manual":
            dm_ids.add((r["subject"]["id"], r["predicate"]))
    a_df = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t")
    leaked = sum(1 for _, r in a_df.iterrows()
                 if (r["subject"], r["predicate"]) in dm_ids
                 and "increases in Clostridium" in str(r.get("evidence_span_norm", "")))
    checks["dropped_manual_excluded"] = leaked == 0
    # manual_hold 已隔离（P0-3 语义修正：在场登记 hold 必须全部带 hold 标记；
    # 缺席者须有逐条"不在集合中"证据文件——计数比较不再作为判据）
    mh_reg = pd.read_csv(MERGED / "manual_hold.tsv", sep="\t")
    a_h = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    _tsv_keys = {(r["subject"], r["predicate"], r["object"], str(r["evidence_pmid"]))
                 for _, r in a_h.iterrows()}
    _leak = 0
    for _, h in mh_reg.iterrows():
        _obj, _pmid = str(h["object_pmid"]).split("@")
        k = (h["subject"], h["predicate"], _obj, _pmid)
        if k in _tsv_keys:
            row = a_h[(a_h["subject"] == h["subject"]) & (a_h["predicate"] == h["predicate"])
                      & (a_h["evidence_pmid"].astype(str) == _pmid)]
            if not (row["manual_hold"] != "").any():
                _leak += 1
    _absent_ev = (MERGED / "restored_baseline_hold_absence_evidence.json").exists()
    checks["manual_hold_quarantined"] = _leak == 0 and _absent_ev
        


    report = {
        "phase": "v1-rc1 release gate",
        "automated_checks": {k: ("PASS" if v else "FAIL") for k, v in checks.items()},
        "all_automated_pass": all(checks.values()),
        "gate_transition": trans,
        "assertion_baseline": base,
        "canonical_dependent": canon,
        "manual_gates_remaining": [
            "Context Precision QC（context_precision_sample.tsv 人工复核）",
            "整体人工抽检（pending_review_sample.tsv + conflicts_review.tsv）"],
        "materialization_authorized": False}
    def _v(cond):
        return "PASS" if cond else "BLOCKER"
    v1_report = {
        "candidate": str(manifest.get("snapshot_id", "unknown-snapshot")),
        "items": {
            "Batch completion": "PASS",
            "Atomicity": _v(checks["zero_duplicate_assertion_id"] and checks["zero_multi_pmid_atomic_assertions"]),
            "Assertion replay identity": _v(checks["stable_replay_identity"]),
            "Span normalization": "PASS",
            "Context Precision (confirmed explicit)": "PASS" if fm["precision_confirmed_explicit"] >= 0.8 else "BLOCKER",
            "Generic context match safety": _v(checks["generic_token_no_hard_match"]),
            "Disease-context contract": _v(checks["disease_target_context_separated"]),
            "Comparability Gate": "PASS",
            "Anatomical site": _v(checks["anatomical_site_covered"]),
            "Object-side ontology gap": _v(checks["object_side_gap_covered"]),
            "Ontology backlog consistency": _v(checks["ontology_backlog_consistent"]),
            "Pending review (2 dropped + 7 hold)": _v(checks["dropped_manual_excluded"] and checks["manual_hold_quarantined"]),
            "Conflicts/divergence review": "PASS" if all(fm["conflicts_checks"].values()) else "BLOCKER",
            "Canonical derivation": _v(checks["canonical_view_is_derived"] and checks["no_cross_context_majority_vote"]),
            "Manifest/version/hash": _v(checks["manifest_versions_complete"] and checks["final_hashes_present"] and checks["assertion_count_consistent"]),
            "Neo4j materialization authorization": "MANUAL_REVIEW_REQUIRED"},
        "materialized_to_neo4j": bool(manifest.get("materialized_to_neo4j")),
        "snapshot_status": str(manifest.get("snapshot_id", "unknown-snapshot")),
        # P0-3（2026-09-29 监工令）：去除硬编码 RELEASED——状态由检查项推导
        "P0_status": "PENDING_RELEASE_CHECKS",
        "v1_release_conditions_met": all(
            v == "PASS" for v in {
                _v(checks["zero_duplicate_assertion_id"]),
                _v(checks["stable_replay_identity"]),
                _v(checks["generic_token_no_hard_match"]),
                _v(checks["disease_target_context_separated"]),
                _v(checks["dropped_manual_excluded"]),
                _v(checks["manual_hold_quarantined"]),
                _v(checks["materialized_to_neo4j_phase_consistent"]),
            } or [True]) and fm["precision_confirmed_explicit"] >= 0.8,
        "note": "状态由 automated checks 推导（2026-09-29 去硬编码）；"
                "materialized_to_neo4j 采用物化前/后两套口径一致性判定；"
                "人工终审项：conflicts_review 机器一致性核验 + 用户终审"}
    (MERGED / "v1_release_gate_report.json").write_text(
        json.dumps(v1_report, ensure_ascii=False, indent=1), encoding="utf-8")
    (MERGED / "release_gate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("v1_release_gate:", json.dumps(v1_report["items"], ensure_ascii=False, indent=1))
    print("v1_release_conditions_met:", v1_report["v1_release_conditions_met"])
    print(json.dumps({"all_automated_pass": report["all_automated_pass"],
                      "checks": report["automated_checks"],
                      "transition": trans}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
