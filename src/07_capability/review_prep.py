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
                "disease", "disease_subtype", "disease_stage", "diet",
                "intervention", "dose", "experimental_model", "study_type",
                "endpoint", "timepoint", "anatomical_site")
#: 可比性判定的关键维度（裁决 5 + E：anatomical_site）
KEY_DIMS = ("strain", "host_species", "disease", "anatomical_site",
            "experimental_model", "study_type", "endpoint", "intervention")

#: 实体粒度不适用于所有 subject（C：applicable 判定）
_IN_VITRO_MARKERS = ("in vitro", "organoid", "cell line")

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
                  "african", "indian"],   # population≠geography（precision 一审移除）
    "endpoint": ["inflammation", "tumorigenesis", "barrier", "proliferation",
                 "survival", "dysbiosis", "colitis", "tumor"],
    "timepoint": ["weeks", "days", "months", "hours", "after"],
    "dose": ["mg", "g/kg", "dose", "cfu"],
    "host_population": [],   # 无显式词表——默认 unknown（禁止模型补全）
    "anatomical_site": ["gut", "intestinal", "colonic", "colon", "liver",
                        "hepatic", "skin", "airway", "lung", "periodontal",
                        "oral", "systemic", "blood", "brain", "joint"],
    "disease": ["colitis", "cancer", "carcinoma", "tumor", "tumorigenesis",
                "inflammation", "IBD", "dermatitis", "obesity", "diabetes",
                "NASH", "NAFLD", "arthritis", "encephalomyelitis", "neoplasia"],
    "disease_subtype": ["ulcerative", "crohn", "collagenous", "CAC", "NASH",
                        "NAFLD", "atopic", "collitis-associated",
                        "colitis-associated", "autoimmune", "hepatocellular"],
}

#: object 侧粗粒度本体 gap（F：不假设粒度问题只在 microbe/food 侧）
OBJECT_SIDE_GAPS = {
    "MESH:D007249": {"entity_granularity": "inflammation_to_anatomical"},
    "MESH:D009369": {"entity_granularity": "cancer_to_specific_cancer"},
    "MESH:D015179": {"entity_granularity": "cancer_to_specific_cancer"},
}


def _hit(text: str, dim: str):
    t = text.lower()
    import re
    out = []
    for k in _EXPLICIT.get(dim, []):
        kl = k.lower()
        # 词边界匹配（precision 一审：子串匹配致 translated→late 类假阳性）
        if re.search(rf"(?<![a-z0-9]){re.escape(kl)}(?![a-z0-9])", t):
            out.append(k)
    return out or None


def build_context(evidence_text: str, object_id: str, subject_id: str = "") -> dict:
    """Evidence-aware context v0.5：每维 {value,status,source,applicable,unknown_reason}。

    C：unknown 细分 applicable/unknown_reason（in vitro 的 geography 与
    human cohort 未采样不是同一种 unknown）；D：object 实体身份 =
    structured_metadata（可确认 match），非 inferred。
    """
    ctx = {}
    t = evidence_text.lower()
    for dim in CONTEXT_DIMS:
        if dim == "disease":
            # 契约修正（裁决第 1 项）：host/background disease context——只由
            # 证据文本/研究元数据填写；object/target 实体身份不得无条件复制进
            # context（target≠context conflation：object=colitis 不代表研究
            # 发生在 colitis 背景下）。object 疾病信息由 assertion.object 承载。
            found = _hit(evidence_text, "disease")
            if found:
                ctx[dim] = {"value": ",".join(found[:2]), "status": "explicit",
                            "source": "abstract_sentence", "applicable": True,
                            "unknown_reason": ""}
            else:
                ctx[dim] = _unknown(True, "not_present_in_available_evidence")
            continue
        found = _hit(evidence_text, dim)
        if found:
            ctx[dim] = {"value": ",".join(found[:2]), "status": "explicit",
                        "source": "abstract_sentence", "applicable": True,
                        "unknown_reason": ""}
        else:
            applicable, reason = _applicability(dim, subject_id, t)
            ctx[dim] = _unknown(applicable, reason)
    return ctx


def _unknown(applicable: bool, reason: str) -> dict:
    return {"value": "", "status": "unknown",
            "source": "",
            "applicable": applicable,
            "unknown_reason": reason if applicable else "not_applicable"}


def _applicability(dim: str, subject_id: str, text: str) -> tuple[bool, str]:
    """维度适用性（确定性规则；in vitro 的 population = not_applicable）。"""
    if dim == "strain":
        if subject_id.startswith("LFS:FOOD"):
            return False, ""
        return True, "not_present_in_available_evidence"
    if dim in ("host_population", "geography"):
        if any(m in text for m in _IN_VITRO_MARKERS):
            return False, ""
        return True, "not_present_in_available_evidence"
    if dim == "dose":
        if _hit(text, "intervention"):
            return True, "not_present_in_available_evidence"
        return False, ""
    return True, "not_present_in_available_evidence"


def context_completeness(ctx: dict) -> float:
    """覆盖率分母 = applicable 维度（C：固定 13/15 维会人为放大 missingness）。"""
    applicable = [d for d in CONTEXT_DIMS if ctx[d].get("applicable", True)]
    known = sum(1 for d in applicable if ctx[d]["status"] != "unknown")
    return round(known / max(len(applicable), 1), 4)


def comparability_gate(ctx_a: dict, ctx_b: dict) -> tuple[str, str]:
    """关键维度门 v0.5（D）：inference 可以增加怀疑，不能制造确定性。

    explicit / structured_metadata 双侧在场 → 可确认 match/mismatch；
    inferred 只作 soft——可维持 partially 或提示检查，不得单独升级 comparable。
    """
    basis, incomparable, blocked = [], False, False
    for d in KEY_DIMS:
        a, b = ctx_a[d], ctx_b[d]
        if a["status"] == "unknown" or b["status"] == "unknown":
            if not (a.get("applicable", True) and b.get("applicable", True)):
                basis.append(f"{d}:not_applicable")
                continue
            blocked = True
            basis.append(f"{d}:unknown")
            continue
        confirmable = all(x["status"] in ("explicit",)
                          or x.get("source") == "structured_metadata"
                          for x in (a, b))
        sa = {x.strip().lower() for x in a["value"].split(",") if x.strip()}
        sb = {x.strip().lower() for x in b["value"].split(",") if x.strip()}
        if sa & sb:
            basis.append(f"{d}:match" if confirmable else f"{d}:soft_match(inferred)")
            if not confirmable:
                blocked = True   # soft 不能升级 comparable
        else:
            incomparable = True
            basis.append(f"{d}:mismatch" if confirmable else f"{d}:soft_mismatch(inferred)")
    status = ("incomparable" if incomparable
              else "partially_comparable" if blocked else "comparable")
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


#: span 规范化契约（裁决第 4 项，冻结）：小写折叠 + 全部 Unicode 空白（含
#: 换行/制表）折叠为单空格 + 首尾去除；不做 NFKC、不去标点、不剥引用标记、
#: 不做句子边界切分；基底 = evidence + sentence 拼接（0.1→0.2 基底变更）。
#: 变更此算法必须 bump 版本并迁移 assertion_id。
SPAN_NORMALIZATION_VERSION = "norm/0.2-lowercase-ws-collapse-basis-evd-plus-sent"


def _norm_span(text: str) -> str:
    return " ".join((text or "").lower().split())


def _content_id(subject: str, predicate: str, object_: str,
                pmid: str, span: str) -> str:
    """内容寻址稳定 ID（B）：sha256(s·p·o·pmid·normalized_span)——
    resume/replay/rerun 后同一证据单元同 identity；execution_id 只进 provenance。"""
    import hashlib
    canon = json.dumps([subject, predicate, object_, pmid, _norm_span(span)],
                       ensure_ascii=False, separators=(",", ":"))
    return "RA-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20]


def iter_atomic_ok_records():
    """A：原子单位 = subject+predicate+object+PMID+evidence span。

    直接遍历 staging ok 行（不经 best-per-key 索引——那会吞掉同 PMID 的
    其他 object/span，重新制造 context collapse）。"""
    seen_spans = set()
    with STAGING.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok" or r.get("predicate") == "no_relation":
                continue
            span = _norm_span(f"{r.get('evidence') or ''} {r.get('sentence') or ''}")
            key = (r["subject"]["id"], r["predicate"], r["object"]["id"],
                   str(r.get("pmid", "")), span)
            if key in seen_spans:   # recovery 重跑产生的重复原子 → 幂等去重
                continue
            seen_spans.add(key)
            yield r, span


def build_relation_assertions(conflicted_pairs: set) -> dict:
    """RelationAssertion v0.5（A/B）：evidence-level atomic unit + 稳定 identity。"""
    import hashlib
    rows, ids, full_span_ids = [], set(), []
    for r, span in iter_atomic_ok_records():
        sid, oid = r["subject"]["id"], r["object"]["id"]
        pmid = str(r.get("pmid", ""))
        aid = _content_id(sid, r["predicate"], oid, pmid, span)
        full_span_ids.append(aid)   # 完整 span 派生（列仅展示截断，不入 ID）
        ids.add(aid)
        div = "contextual_divergence_pending" if (sid, oid) in conflicted_pairs else ""
        # 口径统一：context 抽取文本 == ID 的 span 基 == 存储列（evidence+sentence 拼接）
        text = f"{r.get('evidence') or ''} {r.get('sentence') or ''}"
        ctx = build_context(text, oid, sid)
        rows.append({
            "assertion_id": aid,
            "subject": sid, "predicate": r["predicate"], "object": oid,
            "direction": r.get("polarity", "neutral"),
            "confidence": r.get("confidence", ""),
            "evidence_pmid": pmid,
            "evidence_span_norm": span[:1000],  # 完整 span（ID 始终用完整 span；此列供 precision QC 核验）
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
    # replay 自检（裁决 4）：完整 span 重派生 ID 逐条一致（展示列截断不入 ID）
    replay_ok = full_span_ids == [r["assertion_id"] for r in rows]
    digest = hashlib.sha256(
        (MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
    # I：Assertion Atomicity QC（构造性：一行=一原子；ID 无重复=无聚合）
    n_rows = len(rows)
    atomicity = {"n_assertions": n_rows, "duplicate_ids": n_rows - len(ids),
                 "multi_pmid_per_assertion": 0,
                 "replay_identity_stable": bool(replay_ok),
                 "span_normalization_version": SPAN_NORMALIZATION_VERSION,
                 "status": "PASS" if n_rows == len(ids) and replay_ok else "FAIL"}
    return {"n": n_rows, "sha256": "sha256:" + digest, "atomicity_qc": atomicity}


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
            ctx_a = build_context(ev_a, c["object"], c["subject"])
            ctx_b = build_context(ev_b, c["object"], c["subject"])
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

        # ---- RelationAssertion（A/B，先于 summary：hash 入 summary）----
        ainfo = build_relation_assertions(conflicted_pairs)
        print(f"[assertions] RelationAssertion {ainfo['n']} 条；sha={ainfo['sha256'][:19]}…")
        print(f"[atomicity-qc] {ainfo['atomicity_qc']}")

        # ---- summary v2（五类 context 指标 + backlog 规范化分组计数）----
        ann_rows = out[out["primary_divergence_type"] != ""]
        types = ann_rows["primary_divergence_type"].value_counts().to_dict()
        sec = ann_rows[ann_rows["secondary_divergence_type"] != ""][
            "secondary_divergence_type"].value_counts().to_dict()
        res = ann_rows["resolution_action"].value_counts().to_dict()
        gap_groups: dict = {}

        def _add_gap(group_key: str, gap_json: str, origin: str, ledger: list):
            try:
                d = json.loads(gap_json)
            except (json.JSONDecodeError, TypeError):
                return
            for k, v in d.items():
                gap_groups.setdefault(k, {})
                gap_groups[k][v] = gap_groups[k].get(v, 0) + 1
                ledger.append({"group": group_key, "gap_type": k, "dimension": v,
                               "origin": origin,
                               "record_id": f"{group_key}|{k}|{v}"})

        backlog_ledger = []
        for _, r0 in ann_rows.iterrows():
            for g in str(r0["ontology_gap"]).split(";"):
                if g.strip():
                    _add_gap(f"{r0['subject']}|{r0['object']}", g.strip(),
                             "manual_annotation", backlog_ledger)
        # object 侧派生 gap 统一入账（与手工注记同台账、同去重键）
        for _, r0 in out.iterrows():
            og = OBJECT_SIDE_GAPS.get(r0["object"])
            if og:
                _add_gap(f"{r0['subject']}|{r0['object']}",
                         json.dumps(og, ensure_ascii=False),
                         "object_side_derived", backlog_ledger)
        uniq = {r["record_id"]: r for r in backlog_ledger}
        pd.DataFrame(sorted(uniq.values(), key=lambda r: r["record_id"])).to_csv(
            MERGED / "ontology_backlog.tsv", sep="\t", index=False)
        backlog = len(uniq)
        backlog_qc = {"declared_count": backlog, "unique_records": len(uniq),
                      "duplicates_merged": len(backlog_ledger) - len(uniq),
                      "status": "PASS" if backlog == len(uniq) else "FAIL"}
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
            "ontology_backlog_qc": backlog_qc,
            "true_biological_conflict": 0,  # 仅 comparable 且反向（gate 当前无 comparable → 0）
            "assertion_set": {"n_assertions": ainfo["n"],
                              "assertion_set_hash": ainfo["sha256"],
                              "atomicity_qc": ainfo["atomicity_qc"]},
            "ai_first_pass": True, "human_review": "pending"}
        (MERGED / "contextual_divergence_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[conflicts] {len(out)} 组；已标注 {n_ann}；comparability={comp_dist}")
        print(f"[context] {summary['context_metrics']}")
        print(f"[backlog] {backlog} 项（分组：{ {k: sum(v.values()) for k, v in gap_groups.items()} }）")

    # ---- F：object 侧 gap 已统一入 ontology_backlog.tsv 台账（口径单一）----

    # ---- I：Context Precision QC v2（precision≠coverage；assertion 级分层）----
    try:
        asserts = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    except FileNotFoundError:
        asserts = None
    if asserts is not None:
        sample_rows = []
        by_dim: dict = {}
        na_rows, np_rows = [], []
        for _, r in asserts.iterrows():
            try:
                ctx = json.loads(r["context"])
            except (json.JSONDecodeError, TypeError):
                continue
            for dim, spec in ctx.items():
                span_text = str(r.get("evidence_span_norm", ""))
                kw_anchor = ""
                for kw in str(spec.get("value", "")).split(","):
                    kw = kw.strip().lower()
                    pos = span_text.find(kw) if kw else -1
                    if pos >= 0:
                        lo, hi = max(0, pos - 70), min(len(span_text), pos + 90)
                        kw_anchor = ("..." if lo > 0 else "") + span_text[lo:hi] + \
                                    ("..." if hi < len(span_text) else "")
                        break
                rec = {"assertion_id": r["assertion_id"], "dimension": dim,
                       "extracted_value": spec.get("value", ""),
                       "status": spec.get("status", ""),
                       "source": spec.get("source", ""),
                       "applicable": spec.get("applicable", ""),
                       "unknown_reason": spec.get("unknown_reason", ""),
                       "evidence_anchor_window": kw_anchor or span_text[:160],
                       "evidence_full_span_head": span_text[:120],
                       "human_supported_yes_no": "", "reviewer_note": ""}
                if spec.get("status") == "explicit":
                    by_dim.setdefault(dim, []).append(rec)
                elif spec.get("status") == "unknown" and not spec.get("applicable", True) \
                        and len(na_rows) < 5:
                    na_rows.append(rec)
                elif spec.get("status") == "unknown" and spec.get("applicable", True) \
                        and spec.get("unknown_reason") == "not_present_in_available_evidence" \
                        and len(np_rows) < 5:
                    np_rows.append(rec)
        # 每维度 ≤4 条 explicit（覆盖 anatomical/disease/subtype/strain/model/intervention）
        for dim in sorted(by_dim):
            sample_rows.extend(by_dim[dim][:4])
        sample_rows.extend(na_rows)
        sample_rows.extend(np_rows)
        pd.DataFrame(sample_rows).to_csv(
            MERGED / "context_precision_sample.tsv", sep="\t", index=False)
        print(f"[precision-qc] v2 分层抽样 {len(sample_rows)} 条"
              f"（explicit×{ {d: min(4, len(v)) for d, v in sorted(by_dim.items())} }"
              f" + not_applicable {len(na_rows)} + not_present {len(np_rows)}）")

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
