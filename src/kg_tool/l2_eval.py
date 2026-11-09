"""L2 分层对齐评估器（strict 主数字 + 扩展层 + 部分分计 + 诊断 0a/0b）。

为什么这样设计（评审 2026-11-06 分层体系，五红线）：
- **strict 层是永久主数字**：只用"菌 taxid 直接同 + 疾病 MeSH 直接同"的对，
  不掺任何桥接；扩展层（crosswalk 祖先桥）有自己的分母，**只增不替换**；
- **分母只许按预声明规则单方向扩展**：距离档 1/2/3 **全档披露**，禁看增量挑步数；
- **child_of 与 dist≥4 一律不进分母**（crosswalk 层已机械过滤）；
- **方向**：本库 `Alteration` 是"菌在病中增多/减少"，与我们的因果谓词不是同一轴，
  只能做**有限的**方向可比（aggravates↔increase / alleviates↔decrease），
  且必须单列、不得混进精确率主数字；
- 所有百分比机器算（`round(x,6)`），文档不得手抄。

诊断：
- 0a 实体构成：用 MeSH 树号把我方疾病节点分"真疾病 / 症状体征(C23) / 无树号"；
- 0b 语料域重叠：我方边 pmid 池 vs 库 Literature pmid 池（区分"库没读"与"读了判不对"）。
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import MESH_UID_RE, canon_mesh, library_sets, sha256_file
from build_crosswalk import ancestors_from_tree_numbers

ROOT = Path(__file__).resolve().parents[2]

# 方向对应（有限可比）：我们谓词 → 库 Alteration 期望值
DIRECTION_MAP = {"aggravates": "increase", "alleviates": "decrease"}
POLAR_PREDICATES = tuple(DIRECTION_MAP)

# MeSH 症状体征分支（诊断 0a 的机械判据）
SYMPTOM_TREE_PREFIX = "C23"


def load_our_pairs(kg_dir: Path):
    """我方菌-疾病对 → { (taxid, mesh): {"preds":set, "pmids":set} }。"""
    nodes = {n["id"]: n for n in csv.DictReader(
        open(kg_dir / "merged_nodes.tsv", encoding="utf-8"), delimiter="\t")}
    mic = {i for i, n in nodes.items()
           if n.get("category") in ("Species", "Microbe")}
    dis = {i for i, n in nodes.items() if n.get("category") == "Disease"}
    out = {}
    for e in csv.DictReader(open(kg_dir / "merged_edges.tsv", encoding="utf-8"),
                            delimiter="\t"):
        s, o = e["subject"], e["object"]
        if not ((s in mic and o in dis) or (o in mic and s in dis)):
            continue
        mi = s if s in mic else o
        di = o if s in mic else s
        acc = canon_mesh(di)
        if acc is None:
            continue
        key = (mi.split(":", 1)[1], acc)
        rec = out.setdefault(key, {"preds": set(), "pmids": set()})
        rec["preds"].add(e.get("predicate", ""))
        for p in (e.get("pmids") or "").split(";"):
            if p.strip().isdigit():
                rec["pmids"].add(p.strip())
    return out, dis, nodes


def load_library(path: Path):
    """库 → {(taxid, mesh): set(alterations)}, 全库 pmid 集, 全库对集。"""
    import openpyxl
    UID = re.compile(r"^[DC]\d{6,}$", re.I)
    wb = openpyxl.load_workbook(path, read_only=True)
    lit = {}
    pmids = set()
    for r in wb["Literature"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None:
            continue
        cell = (r[8] or "").strip()
        if "*" in cell:          # 保守口径：含 * 整格弃用
            meshes = set()
        else:
            meshes = {t.strip().upper() for t in re.split(r"[,;]", cell)
                      if UID.match(t.strip())}
        pmid = str(r[1]).strip() if r[1] else ""
        lit[idx] = (meshes, pmid)
        if pmid.isdigit():
            pmids.add(pmid)
    pairs = defaultdict(set)
    pair_pmids = defaultdict(set)
    for r in wb["Association"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None or r[3] in (None, ""):
            continue
        tax = str(int(float(r[3])))
        alt = (r[9] or "").strip().lower()
        if idx not in lit:
            continue
        meshes, pmid = lit[idx]
        for m in meshes:
            pairs[(tax, m)].add(alt)
            if pmid:
                pair_pmids[(tax, m)].add(pmid)
    wb.close()
    return dict(pairs), dict(pair_pmids), pmids


def load_crosswalk_maps(path: Path):
    """crosswalk → (菌侧映射, 关系分布)。只取可进评估的 relation。"""
    germ = {}
    kinds = Counter()
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["src_system"] != "NCBI:taxid":
                continue
            if r["relation"] not in ("exact", "synonym", "parent_of"):
                continue
            germ[r["src_id"]] = r["dst_id"]
            kinds[r["relation"]] += 1
    return germ, kinds


def disease_class(mesh: str, tn: dict):
    """诊断 0a 分类：症状体征(C23) / 其他疾病分支 / 无树号。"""
    tns = tn.get(mesh) or []
    if not tns:
        return "no_tree_number"
    if any(t.startswith(SYMPTOM_TREE_PREFIX) for t in tns):
        return "signs_symptoms_C23"
    return "disease_other"


def eval_layer(pool_keys, our, lib_pairs, lib_pair_pmids, lib_pmids, label,
               lib_tax_any_disease=(), lib_mesh_any_microbe=(),
               matched_override=None, lib_key_of=None):
    """对给定对池算：库内存在率 / 方向一致性 / 部分分计 / pmid 重叠。"""
    n = len(pool_keys)
    matched = ([k for k in pool_keys if k in matched_override]
               if matched_override is not None
               else [k for k in pool_keys if k in lib_pairs])
    # 方向可比：我方该对只有一个极性谓词（多谓词=我方自相矛盾，单列）
    comparable, consistent, inconsistent, our_conflict = [], [], [], []
    for k in matched:
        preds = our[k]["preds"] & set(POLAR_PREDICATES)
        if len(preds) != 1:
            if len(preds) > 1:
                our_conflict.append(k)
            continue
        pred = next(iter(preds))
        want = DIRECTION_MAP[pred]
        lk = (lib_key_of or {}).get(k, k)
        alts = {a for a in lib_pairs.get(lk, set()) if a}
        comparable.append(k)
        if want in alts:
            consistent.append(k)
        elif alts and want not in alts:
            inconsistent.append(k)
    # 0b：配对级 pmid 重叠（我方该对的 pmid ∩ 库该对的 pmid）
    pair_pmid_overlap = sum(
        1 for k in matched
        if our[k]["pmids"] & lib_pair_pmids.get(
            (lib_key_of or {}).get(k, k), set()))
    # 部分分计：池内三量（分母=层内全池，不是 matched）
    taxids = {k[0] for k in pool_keys}
    meshes = {k[1] for k in pool_keys}
    return {
        "label": label,
        "n_pairs": n,
        "library_has_pair": len(matched),
        "library_has_pair_rate": round(len(matched) / n, 6) if n else None,
        "library_has_pair_ci95": wilson_ci(len(matched), n),
        "direction": {
            "comparable_pairs": len(comparable),
            "consistent": len(consistent),
            "inconsistent": len(inconsistent),
            "consistency_rate": (round(len(consistent) / len(comparable), 6)
                                 if comparable else None),
            "consistency_rate_ci95": wilson_ci(len(consistent), len(comparable)),
            "our_self_conflict_pairs": len(our_conflict),
            "note": "库 Alteration=菌在病中增多/减少，与因果谓词非同轴；仅有限可比",
        },
        "partial_credit": {
            "microbe_side_present_any_disease": sum(
                1 for t, _ in pool_keys if t in lib_tax_any_disease),
            "disease_side_present_any_microbe": sum(
                1 for _, m in pool_keys if m in lib_mesh_any_microbe),
            "direction_consistent": len(consistent),
            "denominator": n,
            "definition": "分母=层内全池；三分量各报一致率，用于定位'错在哪一侧'",
            "microbe_side_rate": round(
                sum(1 for t, _ in pool_keys if t in lib_tax_any_disease) / n, 6)
            if n else None,
            "disease_side_rate": round(
                sum(1 for _, m in pool_keys if m in lib_mesh_any_microbe) / n, 6)
            if n else None,
        },
        "pmid": {
            "library_pool_size": len(lib_pmids),
            "pairs_with_shared_pmid": pair_pmid_overlap,
            "pairs_with_shared_pmid_rate": (round(pair_pmid_overlap / len(matched), 6)
                                            if matched else None),
        },
        "_keys": matched,
    }


def wilson_ci(k: int, n: int, z: float = 1.96):
    """Wilson 95% 置信区间（stdlib 手算，零新依赖）——审稿人必问，n 小尤其要。"""
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (round((c - h) / d, 6), round((c + h) / d, 6))


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.2f}%"


def _write_report(out: Path, res: dict) -> None:
    """报告由本函数从 res 生成——**任何数字都不手抄**（防文档与机器档分叉）。"""
    L = res["layers"]
    s = L["L2_strict"]
    lines = [
        "# L2 分层对齐评估报告（自动生成，数字全部来自 l2_eval.json）", "",
        f"- 生成时刻（UTC）：{res['generated_at_utc']}",
        f"- 输入指纹：crosswalk `{res['inputs']['crosswalk_tsv_sha256'][:16]}…`"
        f"｜MeSH 树号 `{res['inputs']['mesh_tree_numbers_sha256'][:16]}…`"
        f"｜脚本 `{res['inputs']['script_sha256'][:16]}…`",
        f"- 我方菌-疾病对总数：{res['pool_total_pairs']}", "",
        "## 一、四层结果（分母独立，不合并）", "",
        "| 层 | 可比对 | 库内存在 | 比率 | 说明 |", "|---|---|---|---|---|",
    ]
    desc = {
        "L2_strict": "**主数字**：菌 taxid 直连 + 疾病 MeSH 直连",
        "L2_ext_bidir_le1": "扩展：菌侧≤1步(恒) + 疾病侧≤1步（带限定语）",
        "L2_ext_bidir_le2": "扩展：菌侧≤1步(恒) + 疾病侧≤2步（设计上限；带限定语）",
        "L2_ext_bidir_le3": "扩展：菌侧≤1步(恒) + 疾病侧≤3步（敏感性，非主）",
    }
    for k in ("L2_strict", "L2_ext_bidir_le1", "L2_ext_bidir_le2",
              "L2_ext_bidir_le3"):
        v = L[k]
        lines.append(f"| {k} | {v['n_pairs']} | {v['library_has_pair']} | "
                     f"{_pct(v['library_has_pair_rate'])} | {desc[k]} |")
    d = s["direction"]
    lines += ["", "## 二、方向一致性（单列，不混入主数字）", "",
              f"- 可比对（我方为该对唯一极性谓词的命中对）：{d['comparable_pairs']}",
              f"- 一致：{d['consistent']}；不一致：{d['inconsistent']}；"
              f"一致率：**{_pct(d['consistency_rate'])}**",
              f"- 我方自相矛盾对（**命中对中**，同对既 aggravates 又 alleviates）："
              f"{d['our_self_conflict_pairs']}；全池冲突见 "
              f"`l2_conflict_pairs.tsv`（机器输出）",
              "- 口径：库 `Alteration`=菌在病中增多/减少，与因果谓词**非同轴**，"
              "仅按 aggravates↔increase / alleviates↔decrease 做有限可比。", "",
              "## 三、strict 池分层", "",
              "### 3.1 按我方谓词", "", "| 谓词 | 对数 | 库内存在 | 比率 |",
              "|---|---|---|---|"]
    for k, v in res["strict_strata"]["by_our_predicate"].items():
        lines.append(f"| {k} | {v['n']} | {v['matched']} | {_pct(v['rate'])} |")
    lines += ["", "### 3.2 按疾病类别（MeSH 树号）", "",
              "| 类别 | 对数 | 库内存在 | 比率 |", "|---|---|---|---|"]
    for k, v in res["strict_strata"]["by_disease_class"].items():
        lines.append(f"| {k} | {v['n']} | {v['matched']} | {_pct(v['rate'])} |")
    a = res["diagnostic_0a_entity_composition"]
    b = res["diagnostic_0b_pmid_overlap"]
    lines += ["", "## 四、诊断", "",
              f"- **0a 实体构成（在池对）**：{a['pairs_in_play_classes']}",
              f"  - 限制：{a['coverage_limitation']}",
              f"- **0b 语料域重叠**：我方 PMID {b['our_pmid_pool']}｜库 "
              f"{b['library_pmid_pool']}｜交集 {b['intersection']}"
              f"（占我方 {_pct(b['overlap_rate_vs_our'])}）",
              "  - 含义：两库读的文献域几乎不重叠 → 主数字是**下界**，"
              "不能解读为'其余边都错'。", "",
              "## 五、不能声称什么", "",
              f"1. 不能把 {_pct(s['library_has_pair_rate'])} 写成'图谱准确率'"
              f"——它是**覆盖域受限的银标准精确率下界**（95%CI "
              f"{_pct(s['library_has_pair_ci95'][0])}–{_pct(s['library_has_pair_ci95'][1])}）；",
              "2. 不能把任何扩展层数字写成'覆盖率达 X%'（分母是桥接后可比对池）；",
              f"3. 不能由 {_pct(d['consistency_rate'])} 方向一致率推断因果方向正确率"
              f"（库的轴不同；95%CI {_pct(d['consistency_rate_ci95'][0])}–"
              f"{_pct(d['consistency_rate_ci95'][1])}）；",
              "4. 不能外推到 4,183 个疾病节点的全体构成（树号只查了在池 386 个）。", "",
              "## 六、纪律留痕", ""]
    lines += [f"{i+1}. {x}" for i, x in enumerate(res["discipline"])]
    (out / "l2_eval_report.md").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L2 分层对齐评估（strict 主数字）")
    ap.add_argument("--kg-dir", type=Path,
                    default=ROOT / "data/kg/k4_full_20261028_155020")
    ap.add_argument("--crosswalk", type=Path, default=ROOT / "bridge/l2_crosswalk.tsv")
    ap.add_argument("--mesh-snapshot", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[l2-eval] {args.out} 非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    import glob
    gt = sorted(glob.glob(str(ROOT / "data/external/gutmdisorder_v3_*")))[-1]
    xlsx = f"{gt}/gutMDisorder_v3_Literature-based_All.xlsx"

    our, dis_nodes, nodes = load_our_pairs(args.kg_dir)
    lib_pairs, lib_pair_pmids, lib_pmids = load_library(Path(xlsx))
    # **分母口径必须与 crosswalk 预注册一致**（否则就是事后改分母）：
    # 菌侧全集取 Association 的 taxid；疾病侧全集取 Literature 层的合法 MeSH
    # （保守口径：含 '*' 格整格弃用）。注意二者与"实际存在关联对"不同——
    # 有 59 个病在 Literature 出现但无任何 Association 行，这些对进分母且判"库无此对"。
    lib_tax_d, lib_mesh_full_d, lib_mesh_d, _gt, _cells = library_sets()
    tn = json.loads((args.mesh_snapshot / "tree_numbers.json").read_text(
        encoding="utf-8"))
    anc_map = ancestors_from_tree_numbers(tn)
    lib_tax = lib_tax_d
    lib_mesh = lib_mesh_d

    # ---------- 对池分层（与 crosswalk 同规则，独立重算） ----------
    def dist_of(mesh):
        if mesh in lib_mesh:
            return 0
        anc = {a: d for a, d in anc_map.get(mesh, {}).items() if a in lib_mesh}
        return min(anc.values()) if anc else None

    keys = list(our)

    def in_tier(k, cap: int) -> bool:
        """显式判 None——**禁止**写 `dist or 99`：dist=0（直连）会被 or 当假值
        变成 99，把最严的一档整个漏掉（本项目 2026-11-06 已因此出过哨兵事故）。"""
        if k[0] not in lib_tax:
            return False
        d = dist_of(k[1])
        return d is not None and d <= cap

    strict = [k for k in keys if in_tier(k, 0)]
    tier1 = [k for k in keys if in_tier(k, 1)]
    tier2 = [k for k in keys if in_tier(k, 2)]
    tier3 = [k for k in keys if in_tier(k, 3)]

    # ---------- 扩展层：双向候选（菌侧 crosswalk 一次上卷 + 疾病侧最近祖先） ----------
    germ_map, germ_kinds = load_crosswalk_maps(args.crosswalk)

    def best_mesh(m, cap):
        if m in lib_mesh:
            return m, 0
        anc = {a: d for a, d in anc_map.get(m, {}).items()
               if a in lib_mesh and d <= cap}
        if not anc:
            return None, None
        a, d = min(anc.items(), key=lambda kv: (kv[1], kv[0]))
        return a, d

    def build_tier(cap):
        pool, matched, keymap = [], [], {}
        for k in keys:
            t, m = k
            t2 = t if t in lib_tax else germ_map.get(t)
            if t2 is None:
                continue
            m2, d = best_mesh(m, cap)
            if m2 is None:
                continue
            pool.append(k)
            keymap[k] = (t2, m2)
            if (t2, m2) in lib_pairs:
                matched.append(k)
        return pool, set(matched), keymap

    layers = {
        "L2_strict": eval_layer(strict, our, lib_pairs, lib_pair_pmids,
                                lib_pmids, "strict(直连,主数字)",
                                lib_tax, lib_mesh),
    }
    for cap, name in ((1, "扩展 菌侧≤1步(恒) + 疾病侧≤1步"),
                      (2, "扩展 菌侧≤1步(恒) + 疾病侧≤2步(设计上限)"),
                      (3, "扩展 菌侧≤1步(恒) + 疾病侧≤3步(敏感性,非主)")):
        pool, mset, kmap = build_tier(cap)
        v = eval_layer(pool, our, lib_pairs, lib_pair_pmids, lib_pmids, name,
                       lib_tax, lib_mesh, matched_override=mset,
                       lib_key_of=kmap)
        v["microbe_side_bridge"] = dict(germ_kinds)
        layers[f"L2_ext_bidir_le{cap}"] = v
    for v in layers.values():
        v.pop("_keys", None)

    # ---------- 诊断 0a：实体构成 ----------
    # 只对"在池且查过树号的病"分类（树号快照只覆盖 760 UID）；全库 4,183 节点
    # 未逐一查树号，故不给全库构成——**披露限制，不猜**。
    cls_in_play = Counter(disease_class(m, tn) for _, m in keys)
    cls_strict = Counter(disease_class(m, tn) for _, m in strict)
    cls_lib = Counter(disease_class(m, tn) for m in lib_mesh)

    # ---------- 诊断 0b：语料域重叠（全库级） ----------
    our_pmids = set()
    for rec in our.values():
        our_pmids |= rec["pmids"]
    overlap = our_pmids & lib_pmids

    # ---------- 明细表（strict 池逐对，供人工抽查/论文附表） ----------
    detail = args.out / "l2_strict_pair_detail.tsv"
    with open(detail, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["microbe_taxid", "disease_mesh", "our_predicates",
                    "our_pmids", "library_alterations", "library_pmid_overlap",
                    "in_library"])
        for k in sorted(strict):
            preds = ",".join(sorted(our[k]["preds"]))
            alts = ",".join(sorted(a for a in lib_pairs.get(k, set()) if a))
            shared = our[k]["pmids"] & lib_pair_pmids.get(k, set())
            w.writerow([k[0], k[1], preds, ",".join(sorted(our[k]["pmids"])),
                        alts, ",".join(sorted(shared)), "yes" if k in lib_pairs else "no"])

    # ---------- 全池自相矛盾清单（评审 P0-1②） ----------
    conflict_rows = []
    for k in sorted(keys):
        preds = our[k]["preds"] & set(POLAR_PREDICATES)
        if len(preds) < 2:
            continue
        t2 = k[0] if k[0] in lib_tax else germ_map.get(k[0])
        m2, dd = best_mesh(k[1], 3)
        if t2 is not None and m2 is not None and (t2, m2) in lib_pairs:
            status = "bridged_hit"
        elif k in lib_pairs:
            status = "strict_hit"
        elif t2 is not None and m2 is not None:
            status = "bridged_miss"
        else:
            status = "not_comparable"
        conflict_rows.append([k[0], k[1], ",".join(sorted(preds)),
                              disease_class(k[1], tn), status])
    with open(args.out / "l2_conflict_pairs.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["microbe_taxid", "disease_mesh", "our_polar_predicates",
                    "disease_class", "library_status"])
        w.writerows(conflict_rows)

    # ---------- strict 池分层（按谓词 / 按病类） ----------
    def strat(key_fn):
        out = {}
        for name in sorted({key_fn(k) for k in strict}):
            sub = [k for k in strict if key_fn(k) == name]
            hit = sum(1 for k in sub if k in lib_pairs)
            out[name] = {"n": len(sub), "matched": hit,
                         "rate": round(hit / len(sub), 6) if sub else None}
        return out

    strict_by_predicate = strat(
        lambda k: "+".join(sorted(our[k]["preds"])))
    strict_by_disease_class = strat(lambda k: disease_class(k[1], tn))

    res = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "kg_dir": str(args.kg_dir.relative_to(ROOT)),
            "gutmdisorder": Path(gt).name,
            "crosswalk_tsv_sha256": sha256_file(args.crosswalk),
            "mesh_tree_numbers_sha256": sha256_file(
                args.mesh_snapshot / "tree_numbers.json"),
            "script_sha256": sha256_file(Path(__file__)),
        },
        "pool_total_pairs": len(keys),
        "layers": layers,
        "self_conflict_full_pool": {
            "n_pairs": len(conflict_rows),
            "by_status": dict(Counter(r[4] for r in conflict_rows)),
            "sidecar": "l2_conflict_pairs.tsv",
            "note": "全池（1545）自相矛盾对；strict 命中对中的冲突数见 layers.direction",
        },
        "strict_strata": {
            "by_our_predicate": strict_by_predicate,
            "by_disease_class": strict_by_disease_class,
        },
        "diagnostic_0a_entity_composition": {
            "pairs_in_play_classes": dict(cls_in_play),
            "coverage_limitation": "分类仅覆盖在池疾病的 MeSH（760 UID 快照内的 386 个）；"
                                   "全库 4,183 个 Disease 节点未逐一查树号，不给全库构成",
            "pairs_in_play": dict(cls_in_play),
            "strict_pool": dict(cls_strict),
            "library_meshes": dict(cls_lib),
            "definition": "C23 分支=病理状态/症状体征；其余 C 分支=疾病类；无树号=补充概念",
        },
        "diagnostic_0b_pmid_overlap": {
            "our_pmid_pool": len(our_pmids),
            "library_pmid_pool": len(lib_pmids),
            "intersection": len(overlap),
            "overlap_rate_vs_our": round(len(overlap) / len(our_pmids), 6)
            if our_pmids else None,
            "note": "重叠低=两库读的文献域不同；不直接等于质量问题",
        },
        "discipline": [
            "strict 层分母与数字永久保留，扩展层只增不替换",
            "child_of 与 dist>=4 不进任何分母",
            "方向一致性单列，不混入精确率主数字",
            "所有百分比机器算（6 位）",
        ],
    }
    (args.out / "l2_eval.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    _write_report(args.out, res)
    # 目录内 SHA256SUMS（评审 P0-1③：不靠 CHANGELOG 截断引用）
    import hashlib
    sumlines = []
    for f in sorted(args.out.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            sumlines.append(f"{h}  {f.name}")
    (args.out / "SHA256SUMS").write_text("\n".join(sumlines) + "\n",
                                         encoding="utf-8")

    s = layers["L2_strict"]
    print(f"[l2-eval] {args.out}")
    print(f"  strict: {s['library_has_pair']}/{s['n_pairs']}"
          f" = {s['library_has_pair_rate']:.2%}")
    for k in ("L2_ext_bidir_le1", "L2_ext_bidir_le2", "L2_ext_bidir_le3"):
        v = layers[k]
        print(f"  {k}: {v['library_has_pair']}/{v['n_pairs']}"
              f" = {v['library_has_pair_rate']:.2%}")
    print(f"  方向(可比 {s['direction']['comparable_pairs']}): "
          f"一致 {s['direction']['consistent']} / "
          f"一致率 {s['direction']['consistency_rate']}")
    print(f"  0a 在池对构成: {dict(cls_in_play)}")
    print(f"  全池自相矛盾: {res['self_conflict_full_pool']['n_pairs']}"
          f" {res['self_conflict_full_pool']['by_status']}")
    print(f"  0b pmid 重叠: {len(overlap)}/{len(our_pmids)}"
          f" = {len(overlap) / len(our_pmids):.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
