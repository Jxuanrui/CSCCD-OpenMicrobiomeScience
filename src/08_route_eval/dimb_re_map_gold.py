#!/usr/bin/env python3
"""①b DiMB-RE → disease_role 确定性映射（mapping_rules_v1 frozen 2026-09-30）。

把 DiMB-RE BRAT 事件标注按冻结规则映射为我方 disease_role 九类样本，
作为 external_human_gold 辅尺（监工 2026-09-30 二轮审 P0-3：关系级仅描述性使用，
DiMB-RE 关系级 partial IAA ~0.54 为一致性上限，不用于认证 0.80 门禁）。

规则优先级（规则表未排序，按事实性优先实现，全确定性无 LLM）：
  exclusion(Negated) > uncertain(非事实四值) > treatment(PREVENTS) >
  target(IMPROVES/WORSENS/CAUSES/PREDISPOSES×Disease) >
  endpoint(INCREASES/DECREASES/AFFECTS×Disease 且 Agent∈食物域) >
  subgroup(Disease span 嵌套 Population) > background(Population 嵌套残余) > not_disease(实体级负例)
注：factuality 属性只标非事实关系，无标注 = 默认 Factual（DiMB-RE 论文约定，规则表已披露）。

用法：python3 dimb_re_map_gold.py [--outdir data/merged/route_eval]
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

FOOD_AGENT = {"Food", "Nutrient", "Chemical", "DietPattern"}
NOT_DISEASE_PRONE = {"Metabolite", "Physiology", "Measurement"}
NONFACTUAL = {"Probable", "Possible", "Doubtful", "Unknown"}
TRIGGER_TARGET = {"IMPROVES", "WORSENS", "CAUSES", "PREDISPOSES"}
TRIGGER_ENDPOINT = {"INCREASES", "DECREASES", "AFFECTS"}
RULE_VERSION = "v1@frozen-2026-09-30"


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
                # BRAT 分片 offset（"439;446 452;458"）：start=首段起点 end=末段终点
                spans = [seg.split(";") for seg in parts[1:-1] + [parts[-1]]]
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
                facts[segs[1]] = segs[0]  # event_id -> factuality 值
    return ents, events, facts


def map_one(ents, events, facts, doc):
    rows = []
    # 实体级负例（NER 维度，规则表：可认证）
    for tid, ent in ents.items():
        if ent["type"] in NOT_DISEASE_PRONE:
            rows.append({"doc": doc, "entity_id": tid, "entity_type": ent["type"],
                         "span_text": ent["text"], "event_id": None, "trigger": None,
                         "agent_type": None, "factuality": None,
                         "mapped_role": "not_disease", "rule": "entity_neg"})
    pops = [(e["start"], e["end"]) for e in ents.values() if e["type"] == "Population"]
    for eid, ev in events.items():
        fact = facts.get(eid)  # None = 默认 Factual
        themes = ev["args"].get("Theme", []) + ev["args"].get("Theme2", [])
        agents = ev["args"].get("Agent", [])
        theme_ents = [ents.get(t) for t in themes if t in ents]
        agent_ents = [ents.get(t) for t in agents if t in ents]
        agent_types = {a["type"] for a in agent_ents if a}
        for t in themes:
            ent = ents.get(t)
            if not ent or ent["type"] != "Disease":
                continue  # 本尺只映射疾病角色样本
            nested_pop = any(ps <= ent["start"] and ent["end"] <= pe for ps, pe in pops)
            if fact == "Negated":
                role, rule = "exclusion_condition", "fact=Negated"
            elif fact in NONFACTUAL:
                role, rule = "uncertain", f"fact={fact}"
            elif ev["trigger"] == "PREVENTS" and agent_types & FOOD_AGENT:
                role, rule = "treatment_context", "PREVENTS+food_agent"
            elif ev["trigger"] in TRIGGER_TARGET:
                role, rule = "target_disease", f"{ev['trigger']}xDisease"
            elif ev["trigger"] in TRIGGER_ENDPOINT and agent_types & FOOD_AGENT:
                role, rule = "endpoint_related", f"{ev['trigger']}+food_agent"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=None, help="默认 <root>/data/merged/route_eval")
    ap.add_argument("--root", default=str(ROOT), help="数据根（worktree 开发时传主项目根）")
    args = ap.parse_args()
    root = Path(args.root)
    global SRC, PROV, ANN_DIRS
    SRC = root / "data/sources/DiMB-RE"
    PROV = SRC / "provenance.json"
    ANN_DIRS = [SRC / "data/brat_v1/processed_files/title_abs",
                SRC / "data/brat_v1/processed_files/results"]
    outdir = Path(args.outdir) if args.outdir else root / "data/merged/route_eval"

    all_rows, n_docs = [], 0
    for d in ANN_DIRS:
        for ann in sorted(d.glob("*.ann")):
            ents, events, facts = parse_ann(ann)
            all_rows += map_one(ents, events, facts, ann.stem)
            n_docs += 1

    out = outdir / "external_human_gold_dimb_re.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n",
                   encoding="utf-8")
    prov = json.loads(PROV.read_text())
    sidecar = {
        "file": out.name, "rows": len(all_rows), "docs": n_docs,
        "source": prov["source"], "source_version": prov["source_version"],
        "raw_hash": prov["raw_hash"], "retrieved_at": prov["retrieved_at"],
        "mapping_rule_version": RULE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_sha256": "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest(),
        "usage_note": "辅尺·描述性统计专用：DiMB-RE 关系级 partial IAA~0.54 上限，"
                      "不用于认证 disease_role_min 0.80 门禁（监工二轮审 P0-3/裁决 2）",
        "role_distribution": dict(Counter(r["mapped_role"] for r in all_rows)),
    }
    (outdir / "external_human_gold_dimb_re.meta.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(sidecar, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
