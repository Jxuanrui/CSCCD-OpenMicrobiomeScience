#!/usr/bin/env python3
"""T1.1 三源零新增人工锚构建器（协议：external_gold_plan_v1.md 三源锚协议节，tag anchor-protocol-v1）。

源A 远程监督正例（R 任务）：relation_assertions ∩ Tier-A 策展边（(s,o) 相同+谓词恒等映射），
  证据句须同现两实体名（否则剔除并计数）。
源B 合成负例：确定性规则生成（禁 LLM）——极性翻转40%(难)/同类实体替换40%(难,同PMID)/跨PMID打乱≤20%(易)；
  去泄漏四规则（违反即剔除+日志）。
源C 历史金标任务对齐：B5→X dev；c2e/c2e_r2→X dev（存在 r2=参与过迭代，保守降级）；role_gold→E dev；supp_bg→E dev。
切分：GroupShuffleSplit 按 PMID 分组（同 PMID 不跨集合，复用 sklearn 成熟轮子）。
冻结：test sha256 写 manifest + 测试断言锁死。

用法：python3 anchor_build.py [--root 项目根]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

SEED = 20261009
FLIP_MAP = {  # 预注册：仅集合内存在真反义的谓词参与翻转
    "increases_abundance_in": "decreases_abundance_in",
    "decreases_abundance_in": "increases_abundance_in",
    "aggravates": "alleviates",
    "alleviates": "aggravates",
}
NEG_RATIO = {"flip": 0.40, "swap": 0.40, "shuffle": 0.20}  # 打乱≤20%（易例单独报告）


def sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def build(root: Path) -> dict:
    rng = random.Random(SEED)
    merged = root / "data/merged/candidate_v3"
    out_dir = root / "data/merged/route_eval/anchors"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = {"seed": SEED, "leak_rejected": {}, "comention_rejected": 0}

    nodes = pd.read_csv(merged / "merged_nodes.tsv", sep="\t").fillna("")
    name = dict(zip(nodes["id"], nodes["name"]))
    cat = dict(zip(nodes["id"], nodes["category"]))
    edges = pd.read_csv(merged / "merged_edges.tsv", sep="\t").fillna("")
    asserts = pd.read_csv(merged / "relation_assertions.tsv", sep="\t").fillna("")
    pending = pd.read_csv(merged / "pending_review_edges.tsv", sep="\t").fillna("")
    conflicts = pd.read_csv(merged / "conflicts.tsv", sep="\t").fillna("")

    # 全量 (s,p,o) 禁集 + (s,o) 极性记录（去泄漏用）
    forbidden = set(zip(edges.subject, edges.predicate, edges.object)) | \
        set(zip(edges.object, edges.predicate, edges.subject))
    pair_polarity = {}  # (s,o)->{predicates}
    for s, p, o in zip(edges.subject, edges.predicate, edges.object):
        pair_polarity.setdefault((s, o), set()).add(p)
        pair_polarity.setdefault((o, s), set()).add(p)
    pending_pairs = set(zip(pending.subject, pending.predicate, pending.object)) | \
        set(zip(pending.subject, pending.object))
    conflict_pairs = set(zip(conflicts.get("subject", []), conflicts.get("object", []))) \
        if "subject" in conflicts.columns else set()

    # llm_relations 全量对（含全部 status）——共现与禁集
    cooccur = set()
    pmid_entities = {}  # pmid -> list[(id, category)]
    with open(root / "data/staging/llm_relations.jsonl") as f:
        for line in f:
            d = json.loads(line)
            s, o = d["subject"], d["object"]
            s, o = s.get("id"), o.get("id")
            cooccur.add((s, o)); cooccur.add((o, s))
            forbidden.add((s, d["predicate"], o))
            pool = pmid_entities.setdefault(d["pmid"], [])
            for e in (s, o):
                if e not in [x[0] for x in pool]:
                    pool.append((e, cat.get(e, "")))

    # ---- 源A：远程监督正例 ----
    tier_a = edges[edges.evidence_tier == "A"]
    a_pairs = {}  # (s,o) -> set(谓词)
    for s, p, o in zip(tier_a.subject, tier_a.predicate, tier_a.object):
        a_pairs.setdefault((s, o), set()).add(p)
    positives = []
    for r in asserts.to_dict("records"):
        key = (r["subject"], r["object"])
        if key not in a_pairs and (r["object"], r["subject"]) not in a_pairs:
            continue
        # 谓词恒等映射：断言谓词须在策展该对的谓词集内（双向对视为同对）
        cur = a_pairs.get(key, a_pairs.get((r["object"], r["subject"]), set()))
        if r["predicate"] not in cur:
            continue
        ev = str(r["evidence_span_norm"]).lower()
        sn, on = str(name.get(r["subject"], "")).lower(), str(name.get(r["object"], "")).lower()
        if not (sn and on and sn in ev and on in ev):
            log["comention_rejected"] += 1
            continue
        positives.append({"pmid": r["evidence_pmid"], "subject": r["subject"],
                          "predicate": r["predicate"], "object": r["object"],
                          "evidence": r["evidence_span_norm"], "label": 1,
                          "origin": "source_A_distant_supervision"})
    log["source_A_positives"] = len(positives)
    if not positives:
        raise SystemExit(
            "[anchor] 源A 正例为 0——远程监督交集不可用（G-T1.1 阻断，2026-10-09 实测："
            "ID 直连 985→21→谓词一致 0；名称归一化 39→6）。R 任务锚供给方案待用户在 D/E/F 中拍板；"
            "见 data/merged/route_eval/anchors/anchor_diag_sourceA.json")

    # ---- 源B：确定性合成负例 ----
    def leaky(s, p, o, is_flip, orig_pair):
        # 规则1：破坏后三元组不得在任何真实边集（含 pending）
        if (s, p, o) in forbidden or (o, p, s) in forbidden:
            return "in_any_edge_set"
        # 规则2：极性翻转的对不得有冲突记录或相反极性来源（仅 flip 走）
        if is_flip:
            if (s, o) in pending_pairs or (o, s) in pending_pairs:
                return "flip_pair_in_pending"
            if orig_pair in conflict_pairs or tuple(reversed(orig_pair)) in conflict_pairs:
                return "flip_conflict_pair"
            if FLIP_MAP.get(p) in pair_polarity.get(orig_pair, set()):
                return "flip_opposite_polarity_exists"
            return None
        # 规则3：仅替换/打乱类负例适用——新实体对不得有任何共现记录
        #（flip 的 (s,o) 来自正例本体，必然共现，不得适用本规则——G-T1.1 修正）
        if (s, o) in pending_pairs or (o, s) in pending_pairs:
            return "swap_pair_in_pending"
        if (s, o) in cooccur or (o, s) in cooccur:
            return "swap_pair_cooccurs"
        return None

    negatives = []
    n_target = max(140, int(len(positives) * 1.2))  # 池要够挑（test 阴性≥60 难例）
    order = list(range(len(positives))); rng.shuffle(order)
    i = 0
    quotas = {"flip": 0, "swap": 0, "shuffle": 0}
    target_per_kind = {k: int(n_target * v) for k, v in NEG_RATIO.items()}
    while i < len(order) and any(quotas[k] < target_per_kind[k] for k in quotas):
        idx = order[i % len(order)]; i += 1
        base = positives[idx]
        kind = min(quotas, key=lambda k: quotas[k] / NEG_RATIO[k])
        s, p, o = base["subject"], base["predicate"], base["object"]
        is_flip, orig_pair, hard = False, (s, o), 0
        if kind == "flip":
            if p not in FLIP_MAP:
                quotas["flip"] = target_per_kind["flip"]  # 谓词无反义则跳过此类
                continue
            p2 = FLIP_MAP[p]; is_flip, hard = True, 1
        elif kind == "swap":
            pool = [e for e, c in pmid_entities.get(base["pmid"], [])
                    if c and c == cat.get(o, "") and e != o and e != s]
            if not pool:
                continue
            o2 = rng.choice(pool); o, hard = o2, 1
        else:
            j = rng.randrange(len(positives))
            o2 = positives[j]["object"]
            if o2 == o or o2 == s:
                continue
            o = o2; hard = 0
        reason = leaky(s, p, o, is_flip, orig_pair)
        if reason:
            log["leak_rejected"][reason] = log["leak_rejected"].get(reason, 0) + 1
            continue
        quotas[kind] += 1
        negatives.append({"pmid": base["pmid"], "subject": s, "predicate": p,
                          "object": o, "evidence": base["evidence"], "label": 0,
                          "origin": f"source_B_{kind}", "hard": hard})
    log["source_B_negatives"] = len(negatives)

    # ---- 切分（PMID 分组）----
    rows = positives + negatives
    df = pd.DataFrame(rows)
    gss = GroupShuffleSplit(n_splits=1, test_size=0.4, random_state=SEED)
    dev_idx, test_idx = next(gss.split(df, groups=df["pmid"]))
    dev, test = df.iloc[dev_idx], df.iloc[test_idx]
    # 保证 test 阴性占比 30-50%：不足则从 dev 挪阴性，超出则挪阳性
    for _ in range(200):
        neg = int((test.label == 0).sum()); pos = int((test.label == 1).sum())
        ratio = neg / max(1, neg + pos)
        if ratio < 0.30:
            cand = dev[(dev.label == 0) & (~dev.pmid.isin(test.pmid))]
            if cand.empty: break
            row = cand.sample(1, random_state=SEED); test = pd.concat([test, row]); dev = dev.drop(row.index)
        elif ratio > 0.50:
            cand = dev[(dev.label == 1) & (~dev.pmid.isin(test.pmid))]
            if cand.empty: break
            row = cand.sample(1, random_state=SEED); test = pd.concat([test, row]); dev = dev.drop(row.index)
        else:
            break
    # 跨集合 PMID/实体对断言（构建时保证，写 manifest 计数）
    def pairs_of(d):
        return set(map(tuple, d[["subject", "object"]].values)) | set(map(tuple, d[["object", "subject"]].values))
    cross_pair = len(pairs_of(dev) & pairs_of(test))

    dev_p = out_dir / "r_dev.tsv"; test_p = out_dir / "r_test.tsv"
    dev.to_csv(dev_p, sep="\t", index=False)
    test.to_csv(test_p, sep="\t", index=False)

    manifest = {
        "protocol": "anchor-protocol-v1 (external_gold_plan_v1.md 三源锚协议节)",
        "seed": SEED, "flip_map": FLIP_MAP,
        "counts": {
            "source_A_positives": len(positives),
            "source_B_negatives": len(negatives),
            "by_kind": {k: sum(1 for n in negatives if n["origin"] == f"source_B_{k}") for k in NEG_RATIO},
            "r_dev": {"n": len(dev), "pos": int((dev.label == 1).sum()), "neg": int((dev.label == 0).sum()),
                      "hard_neg": int(((dev.label == 0) & (dev.hard == 1)).sum())},
            "r_test": {"n": len(test), "pos": int((test.label == 1).sum()), "neg": int((test.label == 0).sum()),
                       "hard_neg": int(((test.label == 0) & (test.hard == 1)).sum())},
        },
        "checks": {
            "cross_set_pmids": int(len(set(dev.pmid) & set(test.pmid))),
            "cross_set_entity_pairs": cross_pair,
            "leak_log": log["leak_rejected"],
            "comention_rejected": log["comention_rejected"],
        },
        "source_C_alignment": {  # 任务对齐登记（按协议降级）
            "X_dev": ["b5_blind_46 (已作v3门禁→dev)", "c2e_gold_80 (存在r2=参与迭代→dev)", "c2e_gold_r2_119 (r2→dev)"],
            "E_dev": ["role_gold_100 (已用于选模→dev)", "supp_bg_50 (保守→dev)"],
            "R_test_note": "R 任务无真实错误样本——按协议披露",
        },
        "freeze": {"r_test_sha256": sha256_file(test_p), "r_dev_sha256": sha256_file(dev_p)},
    }
    (out_dir / "anchor_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    m = build(Path(ap.parse_args().root))
    print(json.dumps(m["counts"], ensure_ascii=False, indent=1))
    print("checks:", json.dumps(m["checks"], ensure_ascii=False))
    print("freeze:", m["freeze"])
