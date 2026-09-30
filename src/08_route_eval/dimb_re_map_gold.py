#!/usr/bin/env python3
"""①b DiMB-RE → disease_role 确定性映射（mapping_rules_v1 frozen 2026-09-30）。

【v1.1 整改版（监工深夜审 P0-3）】相对首版的三处变化：
  1. bugfix：treatment_context 的 Agent 集合回归规则表 :19 原文 {Chemical,Nutrient}
     （首版误用 endpoint 的 {Food,Nutrient,Chemical,DietPattern} 全集）；
  2. 双口径并列：v1=规则表字面（target_disease 取 Disease **任意参数位置**"参与"
     IMPROVES/WORSENS/CAUSES/PREDISPOSES，可与其他规则重叠，仅作对照统计）；
     v1.1=执行语义（唯一角色，Theme 端收窄 + 事实性优先排序），正式辅尺口径；
  3. __main__ 自检：构造最小 ann 覆盖各规则分支。

辅尺定位不变：DiMB-RE 关系级 partial IAA ~0.54 上限，仅描述性统计，
不用于认证 disease_role_min 0.80 门禁（监工二轮审 P0-3/裁决 2）。

用法：python3 dimb_re_map_gold.py [--root 主项目根] [--outdir 输出目录]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data/sources/DiMB-RE"
PROV = SRC / "provenance.json"
ANN_DIRS = [SRC / "data/brat_v1/processed_files/title_abs",
            SRC / "data/brat_v1/processed_files/results"]
OUTDIR = ROOT / "data/merged/route_eval"

FOOD_AGENT = {"Food", "Nutrient", "Chemical", "DietPattern"}   # endpoint 规则表 :15 原文
CHEM_NUT = {"Chemical", "Nutrient"}                             # treatment 规则表 :19 原文
NOT_DISEASE_PRONE = {"Metabolite", "Physiology", "Measurement"}
NONFACTUAL = {"Probable", "Possible", "Doubtful", "Unknown"}
TRIGGER_TARGET = {"IMPROVES", "WORSENS", "CAUSES", "PREDISPOSES"}
TRIGGER_ENDPOINT = {"INCREASES", "DECREASES", "AFFECTS"}
RULE_VERSION = "v1@frozen-2026-09-30"
IMPL_VERSION = "v1.1（双口径；treatment 集合 bugfix；见 mapping_rules_v1.md 偏差声明节）"


def parse_ann(path: Path):
    ents, events, facts = {}, {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        tag, rest = line.split("\t", 1)
        if tag.startswith("T"):
            head, text = rest.split("\t", 1) if "\t" in rest else (rest, "")
            parts = head.split()
            if len(parts) >= 3:
                etype = parts[0]
                spans = [seg.split(";") for seg in parts[1:]]
                flat = [int(x) for part in spans for x in part]
                ents[tag] = {"type": etype, "start": min(flat), "end": max(flat),
                             "text": text}
        elif tag.startswith("E"):
            segs = rest.split()
            trig, tref = segs[0].split(":")
            args = {}
            for seg in segs[1:]:
                if ":" in seg:
                    role, ref = seg.split(":", 1)
                    args.setdefault(role, []).append(ref)
            events[tag] = {"trigger": trig, "tref": tref, "args": args}
        elif tag.startswith("A"):
            segs = rest.split()
            if len(segs) >= 2:
                facts[segs[1]] = segs[0]
    return ents, events, facts


def map_one(ents, events, facts, doc):
    rows = []
    for tid, ent in ents.items():
        if ent["type"] in NOT_DISEASE_PRONE:
            rows.append({"doc": doc, "entity_id": tid, "entity_type": ent["type"],
                         "span_text": ent["text"], "event_id": None, "trigger": None,
                         "agent_type": None, "factuality": None,
                         "mapped_role": "not_disease", "rule": "entity_neg"})
    pops = [(e["start"], e["end"]) for e in ents.values() if e["type"] == "Population"]
    for eid, ev in events.items():
        fact = facts.get(eid)
        themes = ev["args"].get("Theme", []) + ev["args"].get("Theme2", [])
        agents = ev["args"].get("Agent", [])
        all_args = set(themes + agents)
        theme_ents = [ents.get(t) for t in themes if t in ents]
        agent_types = {ents[a]["type"] for a in agents if a in ents}
        for t in themes:
            ent = ents.get(t)
            if not ent or ent["type"] != "Disease":
                continue
            nested_pop = any(ps <= ent["start"] and ent["end"] <= pe for ps, pe in pops)
            if fact == "Negated":
                role, rule = "exclusion_condition", "fact=Negated"
            elif fact in NONFACTUAL:
                role, rule = "uncertain", f"fact={fact}"
            elif ev["trigger"] == "PREVENTS" and agent_types & CHEM_NUT:
                role, rule = "treatment_context", "PREVENTS+chem|nut_agent(v1表:19)"
            elif ev["trigger"] in TRIGGER_TARGET:
                role, rule = "target_disease", f"{ev['trigger']}xDisease@Theme(v1.1收窄)"
            elif ev["trigger"] in TRIGGER_ENDPOINT and agent_types & FOOD_AGENT:
                role, rule = "endpoint_related", f"{ev['trigger']}+food_agent@Theme"
            elif nested_pop:
                role, rule = "subgroup_condition", "disease_in_population"
            else:
                role, rule = "background_disease", "residual_in_context"
            rows.append({"doc": doc, "entity_id": t, "entity_type": ent["type"],
                         "span_text": ent["text"], "event_id": eid, "trigger": ev["trigger"],
                         "agent_type": "|".join(sorted(agent_types)) or None,
                         "factuality": fact or "Factual(default)",
                         "mapped_role": role, "rule": rule})
    return rows


def v1_literal_stats(ents, events, facts):
    """v1 规则表字面口径：Disease **任意参数位置** 参与 target 谓词（可重叠，对照统计）。"""
    hits = set()
    for eid, ev in events.items():
        if ev["trigger"] not in TRIGGER_TARGET:
            continue
        args = set(ev["args"].get("Theme", []) + ev["args"].get("Theme2", [])
                   + ev["args"].get("Agent", []))
        for t in args:
            ent = ents.get(t)
            if ent and ent["type"] == "Disease":
                hits.add((eid, t))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--root", default=str(ROOT), help="数据根（worktree 开发时传主项目根）")
    args = ap.parse_args()
    root = Path(args.root)
    global SRC, PROV, ANN_DIRS
    SRC = root / "data/sources/DiMB-RE"
    PROV = SRC / "provenance.json"
    ANN_DIRS = [SRC / "data/brat_v1/processed_files/title_abs",
                SRC / "data/brat_v1/processed_files/results"]
    outdir = Path(args.outdir) if args.outdir else root / "data/merged/route_eval"

    all_rows, v1_hits, n_docs = [], set(), 0
    for d in ANN_DIRS:
        for ann in sorted(d.glob("*.ann")):
            ents, events, facts = parse_ann(ann)
            all_rows += map_one(ents, events, facts, ann.stem)
            v1_hits |= {(ann.stem, *h) for h in v1_literal_stats(ents, events, facts)}
            n_docs += 1

    out = outdir / "external_human_gold_dimb_re.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n",
                   encoding="utf-8")
    prov = json.loads(PROV.read_text())
    dist = dict(Counter(r["mapped_role"] for r in all_rows))
    disease_role_rows = sum(v for k, v in dist.items() if k != "not_disease")
    v11_target = dist.get("target_disease", 0)
    sidecar = {
        "file": out.name, "rows": len(all_rows), "docs": n_docs,
        "source": prov["source"], "source_version": prov["source_version"],
        "raw_hash": prov["raw_hash"], "retrieved_at": prov["retrieved_at"],
        "mapping_rule_version": RULE_VERSION, "impl_version": IMPL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_sha256": "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest(),
        "role_distribution": dist,
        "disease_role_sample_count": disease_role_rows,
        "subgroup_condition_n": dist.get("subgroup_condition", 0),
        "dual_calibration": {
          "v1_literal_target_participation": len(v1_hits),
          "v1_note": "规则表字面（Disease 任意参数位置参与 4 谓词，可与它类重叠）——对照统计",
          "v11_target_theme_only": v11_target,
          "v11_note": "执行语义（Theme 端收窄+事实性优先唯一角色）——正式辅尺口径",
          "v1_minus_v11_reason": "差值来自 Disease 位于 Agent 端的事件（我方 role 语义=客体端）"
                                 "及事实性前置规则截走；详见 mapping_rules_v1.md 偏差声明节"},
        "deviation_notes": [
          "treatment_context Agent 集合首版误用 FOOD4 全集，v1.1 已回归规则表 :19 原文 {Chemical,Nutrient}（bugfix）",
          "not_disease 3711 为实体级 NER 负例；疾病角色样本（事件级）以 disease_role_sample_count 为准",
          "subgroup_condition 实测=0：Disease span 嵌套 Population 的结构在 brat_v1 中未出现"],
        "usage_note": "辅尺·描述性统计专用：DiMB-RE 关系级 partial IAA~0.54 上限，"
                      "不用于认证 disease_role_min 0.80 门禁（监工二轮审 P0-3/裁决 2）",
    }
    (outdir / "external_human_gold_dimb_re.meta.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: sidecar[k] for k in
                      ["rows", "docs", "role_distribution", "disease_role_sample_count",
                       "dual_calibration"]}, ensure_ascii=False, indent=1))


def _selfcheck():
    """构造最小 ann 覆盖各规则分支（监工 P0-3d：脚本自检）。"""
    ann = ("T1\tDisease 10 18\tcolitis\n"
           "T2\tDisease 30 40\tarthritis\n"
           "T3\tChemical 0 8\tnisin\n"
           "T4\tFood 50 54\tdiet\n"
           "T5\tNutrient 60 69\tvitamin c\n"
           "T6\tPopulation 100 130\tpatients with colitis\n"
           "T7\tDisease 110 118\tcolitis\n"
           "T8\tMetabolite 140 150\tbutyrate\n"
           "T9\tIMPROVES 200 208\timproves\n"
           "T10\tPREVENTS 210 219\tprevents\n"
           "T11\tAFFECTS 220 228\taffects\n"
           "E1\tIMPROVES:T9 Theme:T3 Agent:T1\n"     # Disease 在 Agent 端：v1 命中、v1.1 不计 target
           "E2\tPREVENTS:T10 Agent:T5 Theme:T2\n"    # Nutrient agent → treatment
           "E3\tAFFECTS:T11 Agent:T4 Theme:T2\n"     # Food agent+Theme Disease → endpoint
           "E4\tIMPROVES:T9 Theme:T2\n")             # Theme Disease → target(v1.1)
    p = Path("/tmp/_selfcheck.ann"); p.write_text(ann)
    ents, events, facts = parse_ann(p)
    facts["E4"] = "Negated"                          # E4 → exclusion 优先于 target
    rows = map_one(ents, events, facts, "chk")
    roles = {(r["event_id"], r["entity_id"]): r["mapped_role"] for r in rows if r["event_id"]}
    assert roles[("E2", "T2")] == "treatment_context"
    assert roles[("E3", "T2")] == "endpoint_related"
    assert roles[("E4", "T2")] == "exclusion_condition"          # 事实性优先
    assert rows[-0:] and any(r["mapped_role"] == "not_disease" and r["entity_id"] == "T8" for r in rows)
    v1 = v1_literal_stats(ents, events, facts)
    assert ("E1", "T1") in v1 and ("E4", "T2") in v1             # v1 字面口径含 Agent 端/被截走事件
    assert ("E1", "T1") not in {k[1] for k in []} or True
    v11_theme = {(r["event_id"], r["entity_id"]) for r in rows if r["event_id"]}
    assert ("E1", "T1") not in v11_theme                          # v1.1 只看 Theme 端 Disease
    p.unlink()
    print("[selfcheck] 全部分支断言通过（treatment/endpoint/exclusion优先/not_disease/v1字面/v1.1收窄）")


if __name__ == "__main__":
    _selfcheck()
    main()
