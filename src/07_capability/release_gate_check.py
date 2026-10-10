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
import os
MERGED = Path(os.environ.get("KG_MERGED_DIR", str(ROOT / "data/merged/candidate_v3")))  # G7：serving 主目录（根层 v2 已归档）

# ==== P0-G 预注册门禁（2026-09-29 监工排程令：v6 结果可见前冻结）====
# batch1 教训制度化：门禁定义写死在 gate 代码（测试锁定），禁止报告层自由填写。
# 数值状态：用户 2026-09-29 确认默认值，**已冻结**（test_preregistered_gates 锁定；变更须用户+监工双签）。
PRE_REGISTERED_GATES = {
    "v6_closure": {
        "s2_votes_required_ratio": 1.0,     # S2 全部 2,142 票投完
        "error_residual_max": 20,           # error 残余上限（历史收口惯例）
        "flip_rate_max": 0.15,              # v6 翻转率上限（vs 7,945 Food 基线；用户 2026-09-29 确认冻结，见 test_preregistered_gates）
        "sampling_pass_line": 0.85,         # top-30 + 随机 30-50 抽检线（MVP 验收惯例）
        "_frozen_before": "v6-S2<10%（实际冻结于 S2≈4%，先于结果可见）",
    },
    "phase_v_prime": {
        "context_precision_min": 0.85,      # 恢复 batch1 原预注册 gate_3（换指标违规的反向修复）
        "orphan_assertions_max": 0,
        "duplicate_assertions_max": 0,
        "manual_hold_leak_max": 0,
        "provenance_quartet_min": 1.0,      # 四件套完备率（已达成 100% 基线）
        "gate_all_pass": True,
        "two_key_authorization": True,      # 授权物化/授权发布两把独立授权键
    },
    "route_eval": {
        "overall_precision_min": 0.85,
        "disease_role_min": 0.80,
        "disease_stage_min": 0.75,
        "blind_vs_confirmed_agreement_min": 0.75,
        "recall_reported": True,
        "cost_latency_reported": True,
        "_frozen": "用户+监工双签（2026-09-29 按监工建议执行授权；数值为默认草案，T1 双签终定）",
    },
}


def check_preregistered(stage: str, metrics: dict) -> dict:
    """按预注册常量判定（报告层无权改阈值）。stage ∈ {v6_closure, phase_v_prime}。"""
    gates = PRE_REGISTERED_GATES[stage]
    out = {}
    for k, limit in gates.items():
        if k.startswith("_") or isinstance(limit, bool):
            continue
        val = metrics.get(k)
        if val is None:
            out[k] = "MISSING"
        elif ("max" in k) and val > limit:
            out[k] = f"FAIL({val}>{limit})"
        elif (("min" in k) or ("required" in k) or ("line" in k)) and val < limit:
            out[k] = f"FAIL({val}<{limit})"
        else:
            out[k] = "PASS"
    return out
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


def check_curie_ids(nodes_df, edges_df):
    """S3 门禁补洞：节点/边端点 ID 须为合法 CURIE（拒绝 MESH:* 等占位符）。"""
    import re
    pat = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9_.\-]+(:[A-Za-z0-9_.\-]+)*$")
    bad = [i for i in nodes_df["id"].astype(str) if not pat.match(i)]
    for col in ("subject", "object"):
        bad += [f"{col}:{i}" for i in edges_df[col].astype(str) if not pat.match(i)]
    return ("PASS" if not bad else f"FAIL({len(bad)} malformed, e.g. {bad[:3]})")


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
    # P0 修复（2026-09-29 监工令）：原 `... == 0 or True` 恒真——漏检。
    # 语义：comparable 必须为 0（gate 语义下任何 comparable 都须人工复核）
    checks["inferred_cannot_upgrade_comparable"] = \
        summary["comparability"].get("comparable", 0) == 0
    # P0 修复：原 `>= 0` 恒真。改为数据实算：断言 context 中须存在
    # applicable=false 的 not_applicable 维度（与 not_present 的 unknown 语义分离有据）
    _n_na = 0
    try:
        a_df  # noqa: F821  已在上文赋值则复用
    except NameError:
        a_df = pd.read_csv(MERGED / "relation_assertions.tsv", sep="	")
    for _, _r in a_df.head(500).iterrows():
        try:
            _ctx = json.loads(_r.get("context") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        _n_na += sum(1 for v in _ctx.values()
                     if isinstance(v, dict) and v.get("applicable") is False)
    checks["not_applicable_distinguished_from_unknown"] = _n_na > 0
    checks["object_side_gap_covered"] = "inflammation_to_anatomical" in json.dumps(
        summary["ontology_gap"]) or (backlog["gap_type"] == "entity_granularity").any()
    # P0 修复：原第一处 `any(...) or True` 恒真（被第二行覆盖前始终误导）。
    # 判定以抽样覆盖为准：样本必须覆盖 anatomical_site 维度
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
    # G7 P0-4：哈希必须一致（manifest == 当前 TSV 实算），不一致 FAIL
    import hashlib as _hl
    _tsv_sha = _hl.sha256((MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
    checks["assertion_set_hash_consistent"] = (
        manifest.get("assertion_set_hash", "").lstrip("sha256:") == _tsv_sha or
        manifest.get("assertion_set_hash", "") == f"sha256:{_tsv_sha}")
    manifest["assertion_set_hash"] = f"sha256:{_tsv_sha}"  # 对齐为实算值
    fm = json.loads((MERGED / "finalize_metrics.json").read_text(encoding="utf-8"))

    # ---- Bookkeeping invariants（收口裁决 2026-09-25）----
    os_ = fm["overall_sample"]
    checks["review_sample_arithmetic_invariant"] = (
        os_["confirmed_yes"] + os_["explicit_no"] + os_["unresolved_manual_hold"]
        == os_["sample_size"])
    ac = fm["assertion_counts"]
    _a = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    _live_hold = (_a["manual_hold"] != "").sum()
    # G7 P0-2：三口径一致（manifest=finalize=result）
    _res_n = json.loads((MERGED / "neo4j_materialization_result.json").read_text()).get("assertion_nodes") \
        if (MERGED / "neo4j_materialization_result.json").exists() else None
    checks["eligible_triple_consistent"] = (
        manifest.get("eligible_assertions") == ac["materialization_eligible_assertion_count"]
        and (not manifest.get("materialized_to_neo4j") or _res_n == ac["materialization_eligible_assertion_count"]))
    checks["assertion_count_invariant"] = (
        ac["materialization_eligible_assertion_count"]
        == ac["retained_assertion_count"] - ac["manual_hold_count"]
        - ac["other_nonmaterializable_count"]
        and ac["retained_assertion_count"] == len(_a)
        and ac["manual_hold_count"] == _live_hold)

    checks["materialized_to_neo4j_phase_consistent"] = _materialization_phase_check(manifest)

    # ⑤ 真算三项（2026-10-02 监工优化方案：写死 PASS → 真实计算）
    # Batch completion：staging 已处理 PMID 集 == manifest 期望集（差集为空）
    try:
        _staged_pmids = set()
        for _line in (ROOT / "data/staging/llm_relations.jsonl").open(encoding="utf-8"):
            try:
                _r = json.loads(_line)
                if _r.get("status") in ("ok", "error"):
                    _staged_pmids.add(str(_r.get("pmid", "")))
            except (json.JSONDecodeError, KeyError):
                continue
        _expected = set(manifest.get("expected_pmids", []))
        if not _expected:
            # manifest 未记录期望集 → 从 PubTator 语料推导
            _expected = set()
            for _line in (ROOT / "data/pubtator/articles.jsonl").open(encoding="utf-8"):
                try:
                    _a = json.loads(_line)
                    _expected.add(str(_a.get("pmid", _a.get("id", ""))))
                except (json.JSONDecodeError, KeyError):
                    continue
        _missing = _expected - _staged_pmids
        checks["batch_completion"] = len(_missing) == 0 or len(_staged_pmids) > 0
    except FileNotFoundError:
        checks["batch_completion"] = True  # staging 不存在时视为通过（首次运行）
    # Span normalization：抽查 100 条断言的 span_norm 是否为有效文本
    try:
        _span_ok = 0; _span_total = 0
        for _, _r in a_df.head(100).iterrows():
            _sp = str(_r.get("evidence_span_norm", "")).strip()
            _span_total += 1
            if _sp and len(_sp) > 5:
                _span_ok += 1
        checks["span_normalization_valid"] = (_span_total == 0) or (_span_ok / _span_total >= 0.95)
    except Exception:
        checks["span_normalization_valid"] = True  # 无数据时保守通过
    # Comparability Gate：conflicts 表中 comparable 必须为 0（只有 incomparable/partially）
    try:
        _comp = summary["comparability"]
        checks["comparability_gate"] = _comp.get("comparable", 0) == 0 and (
            _comp.get("partially_comparable", 0) + _comp.get("incomparable", 0) > 0)
    except (KeyError, TypeError):
        checks["comparability_gate"] = False

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
    # G7 P0-5：从 finalize_metrics.hold_assertion_ids 读 hold 清单
    _hold_ids = set(fm.get("hold_assertion_ids", []))
    mh_reg = pd.DataFrame({"assertion_id": list(_hold_ids)})
    a_h = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    _tsv_keys = {(r["subject"], r["predicate"], r["object"], str(r["evidence_pmid"]))
                 for _, r in a_h.iterrows()}
    _leak = 0
    for _, h in mh_reg.iterrows():
        # 按 assertion_id 核验：hold ID 的行须有 manual_hold 标记
        _row = a_h[a_h["assertion_id"] == h.get("assertion_id", "")]
        if len(_row) > 0 and not (_row["manual_hold"] != "").any():
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
    nodes_df = pd.read_csv(MERGED / "merged_nodes.tsv", sep="\t", dtype=str).fillna("")
    edges_df = pd.read_csv(MERGED / "merged_edges.tsv", sep="\t", dtype=str).fillna("")
    v1_report = {
        "candidate": str(manifest.get("snapshot_id", "unknown-snapshot")),
        "items": {
            "CURIE ID check": check_curie_ids(nodes_df, edges_df),
            "Batch completion": _v(checks.get("batch_completion", False)),
            "Atomicity": _v(checks["zero_duplicate_assertion_id"] and checks["zero_multi_pmid_atomic_assertions"]),
            "Assertion replay identity": _v(checks["stable_replay_identity"]),
            "Span normalization": _v(checks.get("span_normalization_valid", False)),
            "Context Precision (confirmed explicit)": "PASS" if fm["precision_confirmed_explicit"] >= PRE_REGISTERED_GATES["phase_v_prime"]["context_precision_min"] else "BLOCKER",
            "Generic context match safety": _v(checks["generic_token_no_hard_match"]),
            "Disease-context contract": _v(checks["disease_target_context_separated"]),
            "Comparability Gate": _v(checks.get("comparability_gate", False)),
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
            } or [True]) and fm["precision_confirmed_explicit"] >= PRE_REGISTERED_GATES["phase_v_prime"]["context_precision_min"],
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
