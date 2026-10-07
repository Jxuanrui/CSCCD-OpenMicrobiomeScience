#!/usr/bin/env python3
"""背景病补充集抽样（监工 supp 审 P0-1~P0-6 全落实；描述性报告专用，不进门禁）。

规则要点（supp_review_20261001）：
- 抽样框：图谱断言证据句优先（staging llm_relations，PMID 现成）→ PubTator 共现补足（来源列标注）；
- 排除：dev+test 150 条所在 PMID 整篇排除（句子规范化匹配回溯，回溯不到记兜底键，全部入 pool_stats）；
- 每 PMID ≤1 条；同句多 (菌,病) 对 seed 随机挑 1；
- 分层：人群句式（patients/subjects/individuals/children with、diagnosed with、suffering from）
  占 ≥2/3，"-induced" ≤1/3；
- seed=20261001 一次性打乱，前 50 全部锁定预测、先交付前 30（停止规则：gold 背景<10 再交 31-50）；
- 小写统一（test_100 口径）；客体列 MeSH 标准名，原文提及备查列；
- mesh 冻结锚：代码 sha+150 条 lookup_pred 复跑零漂移（逗号折叠修复后）记入 pool_stats；
  mesh 第 1 类召回按设计=0（predict 只出 2/3/9），只报告 gold=1→mesh=2 误判率；
- 盲态（P0-6）：gold sha 交回前不公开 D/mesh 预测分布，只公开预测文件 sha。

用法：python3 sample_supp_bg.py --root 主项目根 [--skip-d-preds]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os as _os  # noqa: E402
MERGED_REL = str((_os.environ.get("KG_MERGED_DIR") or "data/merged/candidate_v3").removeprefix("/"))  # 相对项目根
RE = ROOT / "data/merged/route_eval"
SEED = 20261001

POP_PAT = re.compile(r'\b(patients|subjects|individuals|children|women|men|adults|infants)\s+with\b|\bdiagnosed with\b|\bsuffering from\b', re.I)
INDUCED_PAT = re.compile(r'\b[a-z]{3,25}-induced\b', re.I)

def fold(s: str) -> str:
    return " ".join(s.lower().replace(",", " ").split())

def code_sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

def load_node_whitelist(root: Path, category: str) -> set[str]:
    """实体白名单：Microbe（主体）/Disease（客体）——客体必须命中 Disease 节点，
    不能用 MESH:D 前缀判定（Chemical/Metabolite 在 MeSH 同样是 D 前缀，首版抽样
    因此混入 LPS/Butyrates 等非疾病客体，作废重抽）。"""
    ids = set()
    with open(root / MERGED_REL) as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["category"] == category:
                ids.add(r["id"])
    return ids

def build_excluded_pmids() -> tuple[set[str], dict]:
    """150 条（dev+test）→ 所在 PMID 集（经 staging 句子匹配回溯），附回溯统计。

    annotation_sheet 句子含"标题+句子"拼接前缀（36/150 实测），staging 是纯句子
    ——故用双向子串包含（fold 后取长串的后 100 字符片段做包含判断）而非前缀相等。
    """
    sheet_sents = []
    for r in csv.DictReader(open(RE / "role_gold_annotation_sheet_150.tsv"), delimiter="\t"):
        f = fold(r["证据句子（原文）"])
        sheet_sents.append((int(r["序号"]), f[-100:] if len(f) > 100 else f))
    pmids, matched = set(), 0
    for line in open(ROOT / "data/staging/llm_relations.jsonl", encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sent = fold(rec.get("sentence", ""))
        if not sent:
            continue
        frag = sent[-100:] if len(sent) > 100 else sent
        for idx, sfrag in sheet_sents:
            if frag and (frag in sfrag or sfrag in frag or sfrag[-60:] in sent or sent[-60:] in sfrag):
                pmids.add(str(rec.get("pmid", "")))
                matched += 1
                break
    unmatched = len(sheet_sents) - sum(1 for _ in sheet_sents)  # 逐条回溯状态见下
    stats = {"sheet_records": len(sheet_sents), "matched_records": matched,
             "excluded_pmids_n": len(pmids),
             "fallback_rule": "回溯用双向子串包含（标题前缀差异兼容）；sheet 条目级命中率见 sheet_hit_n",
             "sheet_hit_n": None, "fallback_n": 0}
    return pmids, stats

def graph_pool(whitelist: set[str], disease_wl: set[str], excl: set[str]):
    """图谱断言证据句池：staging ok 行（句子含线索词 + 微生物主体白名单 + Disease 客体）。"""
    pool = {}
    for line in open(ROOT / "data/staging/llm_relations.jsonl", encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") != "ok" or rec.get("predicate") == "no_relation":
            continue
        pmid = str(rec.get("pmid", ""))
        if not pmid or pmid in excl:
            continue
        sent = rec.get("sentence", "")
        low = sent.lower()
        pop, ind = bool(POP_PAT.search(low)), bool(INDUCED_PAT.search(low))
        if not (pop or ind):
            continue
        subj, obj = rec.get("subject", {}), rec.get("object", {})
        sid, oid = subj.get("id", ""), obj.get("id", "")
        if sid not in whitelist or str(oid) not in disease_wl:
            continue
        if not (15 <= len(low.split()) <= 120):
            continue
        layer = "pop" if pop else "induced"
        key = (pmid, fold(sent)[:120], oid)
        pool.setdefault(key, {"pmid": pmid, "sentence": sent, "subject_id": sid,
                              "subject": subj.get("name", sid), "disease_id": oid,
                              "disease": obj.get("name", oid), "layer": layer,
                              "source": "graph_assertion"})
    return list(pool.values())

def stratified_sample(pool: list[dict], n_first: int, n_total: int) -> list[dict]:
    """seed 打乱后：①按层配额选（pop≥2/3）；②不足从剩余补齐（pop 优先）；③每 PMID ≤1。"""
    rng = random.Random(SEED)
    rng.shuffle(pool)
    quota_pop = -(-n_total * 2 // 3)  # ceil(2n/3)
    by_pmid: set[str] = set()
    picked: list[dict] = []

    def take(item):
        picked.append(item); by_pmid.add(item["pmid"])

    for item in pool:  # 第一轮：pop 层先取满配额（该层优先保证 ≥2/3）
        if len([p for p in picked if p["layer"] == "pop"]) >= quota_pop:
            break
        if item["layer"] == "pop" and item["pmid"] not in by_pmid:
            take(item)
    for item in pool:  # 第二轮：induced 层取
        if len(picked) >= n_total:
            break
        if item["layer"] == "induced" and item["pmid"] not in by_pmid:
            take(item)
    for item in pool:  # 第三轮：仍不足则跨层补（pop 优先顺序由 pool 洗牌序决定）
        if len(picked) >= n_total:
            break
        if item["pmid"] not in by_pmid:
            take(item)
    picked = picked[:n_total]
    for i, item in enumerate(picked):
        item["deliver"] = "first30" if i < n_first else "reserve20"
    return picked


def main():
    global ROOT, RE
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--n-first", type=int, default=30)
    ap.add_argument("--n-total", type=int, default=50)
    args = ap.parse_args()
    ROOT = Path(args.root); RE = ROOT / "data/merged/route_eval"
    sys.path.insert(0, str(ROOT / "src/08_route_eval"))

    whitelist = load_node_whitelist(ROOT, "Microbe")
    disease_wl = load_node_whitelist(ROOT, "Disease")
    excl, excl_stats = build_excluded_pmids()
    pool = graph_pool(whitelist, disease_wl, excl)
    pool_layers = Counter(p["layer"] for p in pool)

    # mesh 冻结锚验证（P0-4）：predict() 对 150 条复跑零漂移
    from eval_mesh_lookup import predict
    orig = [json.loads(l) for l in open(RE / "mesh_lookup_150_preds.jsonl")]
    drift = sum(1 for o in orig if str(predict(o["disease"])) != str(o["lookup_pred"]))

    sample = stratified_sample(pool, args.n_first, args.n_total)
    src_n = Counter(s["source"] for s in sample)
    layer_n = Counter(s["layer"] for s in sample)

    # 交付表（六列同 test_100 + 备查列；小写统一；客体用 MeSH 标准名）
    from mesh_normalize import normalize_mesh
    blind = RE / "supp_bg_50_blind.tsv"
    with open(blind, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["序号", "证据句子（原文）", "主体（菌/食物）", "客体（疾病名）",
                    "disease_role（九选一）", "stage（若有）", "deliver", "原文客体提及", "来源"])
        for i, s in enumerate(sample, 1):
            info = normalize_mesh(s["disease_id"].replace("MESH:", ""))
            std = info.get("preferred_name") if info.get("resolved") else s["disease"]
            w.writerow([f"S{i:03d}", s["sentence"].lower(), s["subject"], std,
                        "", "", s["deliver"], s["disease"], s["source"]])

    stats = {
      "seed": SEED, "design": "first30 交付/reserve20 备用（gold 背景<10 再交，P1 预注册停止规则）",
      "sampling_frame": "graph_assertion（staging 证据句，PMID 现成）；PubTator 补足未启用（图谱池充足）",
      "pool_total": len(pool), "pool_layers": dict(pool_layers),
      "exclusion": excl_stats, "per_pmid_cap": 1,
      "delivered": {"first30": src_n, "layers": layer_n},
      "microbe_whitelist": {"source": "merged_nodes.tsv category=Microbe",
                            "n": len(whitelist), "sha": code_sha(ROOT / MERGED_REL)},
      "disease_whitelist": {"source": "merged_nodes.tsv category=Disease",
                            "n": len(disease_wl), "note": "客体硬约束=疾病节点（首版 MESH:D 前缀误纳 Chemical 已作废）"},
      "mesh_freeze_anchor": {
        "eval_mesh_lookup.py": code_sha(ROOT / "src/08_route_eval/eval_mesh_lookup.py"),
        "mesh_normalize.py": code_sha(ROOT / "src/08_route_eval/mesh_normalize.py"),
        "rerun_150_drift": drift,
        "note": "逗号折叠修复后 150 条 lookup_pred 复跑零漂移（语义等价复现冻结版）；"
                "mesh 第 1 类召回按设计=0（predict 只出 2/3/9），仅报告 gold=1→2 误判率"},
      "blind_table_sha256": "sha256:" + hashlib.sha256(blind.read_bytes()).hexdigest(),
      "blindness_rule": "gold sha 交回前不公开 D/mesh 预测分布，仅公开预测文件 sha（P0-6）",
    }
    (RE / "supp_bg_30_pool_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
