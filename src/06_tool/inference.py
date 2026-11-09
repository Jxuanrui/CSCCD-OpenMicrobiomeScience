#!/usr/bin/env python3
"""D1 测序数据推演（MIMOSA2 式能力聚合，在子图上做）——链级标注表。

规格=data/logs/hypothesize_spec_20261101.md §一（逐字对照；改规格须同步改
代码与测试）。

设计立场（为什么这么做）：
- MIMOSA2 证明"菌群功能谱→代谢物潜能"这条路走得通（快照档在案），但其
  Step3 回归判定=本仓红线差异（H1 双阴已封存，推演≠差异检验）——我们只借
  Step1-2 的"机械潜能计算"叙事位，输出=链级标注表，只标注不打关联分。
- 标注粒度=每链×每队列：①菌在位性（MIMOSA2 贡献分解叙事位）②KO 潜能在位性
  （KEGG 模式同构简化：KO 丰度关联代谢物机器，不乘计量系数不做回归）
  ③代谢物可测性（三队列无代谢组→恒 not_available，如实标注不虚设）。
- 组标签结构性隔离：本模块不打开任何 metadata 文件——"在位性"只问样本池
  整体，不问分组，从代码结构上杜绝 C 类偏离（测试 grep 断言零 metadata）。

红线：子图/主图零改动（只写子图目录内两个新文件）；零 LLM；零拟合
（中位数/检出率/求和均为定义式统计量）。
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subgraph import read_tsv  # noqa: E402  复用 B 阶段 TSV 读取口径

# ============================================================
# 规格锁定常量（spec §一/§〇 逐字；改这里=改规格）
# ============================================================
COHORTS = ["PRJEB13870", "PRJEB21612", "SelfData_222"]  # 字典序锁定（spec §一.2）
OBS_DIR = ROOT / "data/observation/htn_v1"
BRIDGE_TSV = ROOT / "data/logs/name_bridge_20261101/name_bridge_cohorts.tsv"
KO_MAP_TSV = ROOT / ("archive/Version01/data/logs/external_evidence/"
                     "metabolite_ko_map_v2.tsv")
KO_MAP_SHA16 = "46df51184c72ec89"   # INHERITANCE.md 登记值（24 行冻结/57 可测）

# 强制三列常量（tool_architecture_spec §六；k5 引用处披露）
KG_VERSION = "k4_full_20261028_155020+8b6a347d5897f993"  # 主图 merge_id+edges_sha16
K5_GATE_STATUS = "gate_pass=False(disgenet_0/28)"
METABOLITE_MEASURABILITY = "not_available"  # 三队列无代谢组（spec §一.3③）

ANNOTATION_COLS = [
    "chain_id", "cohort",
    # ① 菌在位性
    "microbe_presence", "microbe_match_rank", "microbe_median_abundance",
    "microbe_detection_rate",
    # ② KO 潜能在位性
    "ko_label", "n_ko_mapped", "ko_ids", "ko_detection_rate",
    "ko_median_abundance",
    # ③ 代谢物可测性
    "metabolite_measurability",
    # 强制三列 + 溯源四键（自 hop1 子图边回连，非空 fail-closed）
    "kg_version", "evidence_granularity", "k5_gate_status",
    "source_id", "retrieved_at", "version", "knowledge_layer",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt(x: float) -> str:
    """数值列定宽格式化（spec §一.5：%.10g——同值同串，双跑逐字节相等）。"""
    return format(x, ".10g")


# ============================================================
# 输入门禁（fail-closed，spec §〇）
# ============================================================
def gate_ko_map(path: Path) -> dict[str, list[str]]:
    """门禁⑥：冻结 KO 映射 sha16==INHERITANCE 登记值；通过则返回
    chebi → 排序 KO 列表。这是链上 KO 的唯一仓内法定通道（B2 引擎先例）。"""
    sha16 = sha256_file(path)[:16]
    if sha16 != KO_MAP_SHA16:
        raise SystemExit(f"[红线] KO 映射 sha16={sha16} ≠ 登记 {KO_MAP_SHA16}"
                         "——资产被动过，拒跑")
    m: dict[str, set[str]] = defaultdict(set)
    for r in read_tsv(path):
        for chebi in (r["chebi"] or "").split(";"):
            chebi = chebi.strip()
            if chebi:
                m[chebi] |= {k.strip() for k in (r["kos"] or "").split(";")
                             if k.strip()}
    return {c: sorted(ks) for c, ks in m.items()}


def gate_observation_files(obs_dir: Path, cohorts: list[str]) -> dict:
    """门禁③④：三队列 taxonomy/ko_unstratified 逐文件 sha256 与入库
    manifest 契约相等——测序表必须逐字节就是入库那份，防静默换源。"""
    contract = json.loads((obs_dir / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for src in cohorts:
        src_files = contract["sources"][src]["files"]
        for rel in ("metaphlan4/taxonomy.tsv", "humann3/ko_unstratified.tsv"):
            expect = src_files[rel]["sha256"]
            got = sha256_file(obs_dir / src / rel)
            if got != expect:
                problems.append(f"{src}/{rel}: {got[:16]}…≠{expect[:16]}…")
    if problems:
        raise SystemExit("[红线] 观测层 sha 契约失配：\n  " + "\n  ".join(problems))
    return {"files_checked": len(cohorts) * 2, "mismatches": [],
            "machine_proof": True}


# ============================================================
# 数据装载
# ============================================================
def load_bridge(bridge_tsv: Path) -> dict[tuple[str, str], dict]:
    """菌名桥（R4 法定前置）→ (source, taxid) 桥接行。

    同 taxid 多行时种级（s__）优先于属级（g__），同档按 lineage 字典序取首
    ——spec §一.3① 锁定的确定性取法。
    """
    rows = read_tsv(bridge_tsv)
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for b in rows:
        taxid = (b.get("taxid") or "").strip()
        if taxid:
            by_key[(b["source"], taxid)].append(b)
    picked: dict[tuple[str, str], dict] = {}
    for key, cands in by_key.items():
        # 排序：种级优先（显式优先序，字典序 "g__"<"s__" 恰好相反——代理死前
        # 留下的隐性 bug，主会话修复），再按 lineage 确定性取首
        rank_order = {"s__": 0, "g__": 1}
        cands.sort(key=lambda b: (rank_order.get(b["rank_token"], 9),
                                  b["metaphlan_lineage"]))
        picked[key] = cands[0]
    return picked


def load_taxonomy(obs_dir: Path, cohort: str) -> dict[str, list[float]]:
    """队列种属表 → 特征 lineage → 丰度向量（全样本，含零）。"""
    table: dict[str, list[float]] = {}
    with open(obs_dir / cohort / "metaphlan4/taxonomy.tsv",
              encoding="utf-8", newline="") as f:
        rd = csv.reader(f, delimiter="\t")
        next(rd)  # 表头（样本列名）
        for row in rd:
            if not row or not row[0]:
                continue
            table[row[0]] = [float(v) if v not in ("", "NA") else 0.0
                             for v in row[1:]]
    return table


def load_ko_table(obs_dir: Path, cohort: str) -> tuple[list[str], dict[str, list[float]]]:
    """队列 KO 表 → (样本列名序, KO → 丰度向量)。行键=纯 KO 号
    （UNMAPPED/UNGROUPED 等非 K 号行照读不特判——按键查不到自然不命中）。"""
    with open(obs_dir / cohort / "humann3/ko_unstratified.tsv",
              encoding="utf-8", newline="") as f:
        rd = csv.reader(f, delimiter="\t")
        samples = next(rd)[1:]
        table: dict[str, list[float]] = {}
        for row in rd:
            if not row or not row[0]:
                continue
            table[row[0]] = [float(v) if v not in ("", "NA") else 0.0
                             for v in row[1:]]
    return samples, table


def hop1_edge_index(edges: list[dict]) -> dict[str, dict]:
    """子图边 id → 边行（溯源四键+粒度列的回连源）。"""
    return {e["edge_id"]: e for e in edges}


# ============================================================
# 三项标注（纯函数，独立可测）
# ============================================================
def annotate_microbe(chain_mid: str, cohort: str, bridge: dict,
                     taxonomy: dict[str, list[float]]) -> dict:
    """① 菌在位性（spec §一.3①）：taxid→桥→lineage→丰度向量→
    present/absent/not_bridged + 中位数 + 检出率。"""
    taxid = chain_mid.split(":", 1)[1] if ":" in chain_mid else chain_mid
    brow = bridge.get((cohort, taxid))
    if brow is None:
        # 队列里没这个菌的可桥特征——如实标 not_bridged，不硬凑
        return {"microbe_presence": "not_bridged", "microbe_match_rank": "",
                "microbe_median_abundance": "", "microbe_detection_rate": ""}
    vec = taxonomy.get(brow["metaphlan_lineage"])
    if vec is None:
        # 桥行存在但队列表查无该特征（理论上 sha 门禁后不可能）——fail 披露
        return {"microbe_presence": "not_bridged", "microbe_match_rank": "",
                "microbe_median_abundance": "", "microbe_detection_rate": ""}
    n = len(vec)
    det = sum(1 for v in vec if v > 0)
    return {"microbe_presence": "present" if det > 0 else "absent",
            "microbe_match_rank": ("species" if brow["rank_token"] == "s__"
                                   else "genus"),
            "microbe_median_abundance": fmt(median(vec)),
            "microbe_detection_rate": fmt(det / n)}


def chain_ko_ids(chain_chebi: str, ko_map: dict[str, list[str]]) -> list[str]:
    """链 chebi（`;` 多值）→ 冻结映射 KO 并集（排序锁定=后续求和顺序锁定）。"""
    out: set[str] = set()
    for chebi in (chain_chebi or "").split(";"):
        chebi = chebi.strip()
        if chebi:
            out |= set(ko_map.get(chebi, []))
    return sorted(out)


def annotate_ko(chain_chebi: str, ko_map: dict[str, list[str]],
                ko_table: dict[str, list[float]]) -> dict:
    """② KO 潜能在位性（spec §一.3②）：每样本潜能=命中 KO 丰度按 KO 号
    字典序求和（顺序锁定=浮点确定性）；标签四值枚举。"""
    if not (chain_chebi or "").strip():
        return {"ko_label": "no_chebi", "n_ko_mapped": 0, "ko_ids": "",
                "ko_detection_rate": "", "ko_median_abundance": ""}
    kos = chain_ko_ids(chain_chebi, ko_map)
    if not kos:
        # 有 chebi 但不在 24 行冻结映射内——如实标 unmapped，不外扩映射
        return {"ko_label": "unmapped_chebi", "n_ko_mapped": 0, "ko_ids": "",
                "ko_detection_rate": "", "ko_median_abundance": ""}
    n_samples = len(next(iter(ko_table.values()))) if ko_table else 0
    potential = [0.0] * n_samples
    for k in kos:                      # kos 已排序——求和顺序锁定
        vec = ko_table.get(k)
        if vec is None:
            continue
        for i, v in enumerate(vec):
            potential[i] += v
    det = sum(1 for v in potential if v > 0)
    return {"ko_label": "measurable" if det > 0 else "ko_absent",
            "n_ko_mapped": len(kos), "ko_ids": ";".join(kos),
            "ko_detection_rate": fmt(det / n_samples) if n_samples else "",
            "ko_median_abundance": fmt(median(potential)) if n_samples else ""}


# ============================================================
# 完整性校验（与 interact/deepchain 同法——"只增不改"机器证）
# ============================================================
def verify_subgraph_integrity(sg_dir: Path) -> dict:
    frozen = {}
    for line in (sg_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, _, name = line.partition("  ")
            frozen[name.strip()] = sha
    mismatches = [n for n, s in sorted(frozen.items())
                  if (sg_dir / n).exists() and sha256_file(sg_dir / n) != s]
    return {"files_checked": len(frozen), "mismatches": mismatches,
            "machine_proof": not mismatches}


def write_tsv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ============================================================
# 主流程
# ============================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D1 链级标注（MIMOSA2 式，只标注）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--cohort", default=None,
                    help="队列档案 key（configs/cohorts/）")
    ap.add_argument("--obs-dir", type=Path, default=None,
                    help="观测层目录（缺省=注册表/cohort 档案）")
    ap.add_argument("--bridge-tsv", type=Path, default=None,
                    help="菌名桥 TSV（缺省=cohort 档案）")
    ap.add_argument("--ko-map", type=Path, default=None,
                    help="代谢物→KO 映射（缺省=注册表 ko_map）")
    args = ap.parse_args(argv)
    import data_source_profile  # noqa: E402  W2.2 数据腿注册表
    args = data_source_profile.resolve(
        args, {"ko_map": ("ko_map", False)}, {"ko_map": str(KO_MAP_TSV)})
    import cohort_profile  # noqa: E402  W2.4 队列档案
    args = cohort_profile.resolve(
        args, {"obs_dir": OBS_DIR, "bridge_tsv": BRIDGE_TSV})
    sg_dir: Path = args.subgraph_dir

    # —— 门禁前置（fail-closed）：完整性 + 观测层 sha 契约 + KO 冻结映射
    integrity_before = verify_subgraph_integrity(sg_dir)
    obs_gate = gate_observation_files(args.obs_dir, COHORTS)
    ko_map = gate_ko_map(args.ko_map)

    # —— 只读输入
    chains = read_tsv(sg_dir / "chains.tsv")
    edges = read_tsv(sg_dir / "merged_edges.tsv")
    hop1 = hop1_edge_index(edges)
    bridge = load_bridge(args.bridge_tsv)
    taxonomy = {c: load_taxonomy(args.obs_dir, c) for c in COHORTS}
    ko_tables = {c: load_ko_table(args.obs_dir, c) for c in COHORTS}

    # —— 逐链×逐队列标注（四键/三列自 hop1 边回连，缺键 fail-closed）
    rows_out = []
    missing_keys: list[str] = []
    for ch in sorted(chains, key=lambda r: r["chain_id"]):
        e = hop1.get(ch["hop1_edge_id"], {})
        four_keys = {
            "source_id": f"{e.get('run_batch', '')}|{ch['hop1_edge_id']}",
            "retrieved_at": e.get("retrieved_at", ""),
            "version": KG_VERSION,
            "knowledge_layer": e.get("knowledge_layer", ""),
        }
        granularity = e.get("evidence_granularity", "")
        if not all(four_keys.values()) or not granularity:
            # 溯源四键/粒度列非空 fail-closed（spec §一.4）——缺键即停
            missing_keys.append(ch["chain_id"])
        for cohort in COHORTS:
            rows_out.append({
                "chain_id": ch["chain_id"], "cohort": cohort,
                **annotate_microbe(ch["microbe_id"], cohort, bridge,
                                   taxonomy[cohort]),
                **annotate_ko(ch["metabolite_chebi"], ko_map,
                              ko_tables[cohort][1]),
                "metabolite_measurability": METABOLITE_MEASURABILITY,
                "kg_version": KG_VERSION,
                "evidence_granularity": granularity,
                "k5_gate_status": K5_GATE_STATUS,
                **four_keys,
            })
    if missing_keys:
        raise SystemExit(f"[红线] 溯源四键/粒度列缺失 {len(missing_keys)} 链"
                         f"（如 {missing_keys[:3]}）——fail-closed 拒落盘")

    write_tsv(sg_dir / "chain_annotations.tsv", rows_out, ANNOTATION_COLS)

    # —— 机器计数入 manifest（数字机器输出制：全部程序化转写）
    def dist(col: str, cohort: str) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for r in rows_out:
            if r["cohort"] == cohort:
                d[r[col]] += 1
        return dict(sorted(d.items()))
    sub_manifest = json.loads((sg_dir / "manifest.json").read_text(
        encoding="utf-8"))
    manifest = {
        "module": "src/06_tool/inference.py",
        "spec": "data/logs/hypothesize_spec_20261101.md#一",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "subgraph_dir": str(sg_dir),
            "subgraph_version": sub_manifest.get("subgraph_version"),
            "cohorts": COHORTS,
            "obs_dir": str(args.obs_dir),
        },
        "inputs_sha256": {
            "chains": sha256_file(sg_dir / "chains.tsv"),
            "bridge": sha256_file(args.bridge_tsv),
            "ko_map": sha256_file(args.ko_map),
            **{f"taxonomy_{c}": sha256_file(args.obs_dir / c
                                            / "metaphlan4/taxonomy.tsv")
               for c in COHORTS},
            **{f"ko_unstratified_{c}": sha256_file(args.obs_dir / c / "humann3"
                                                   / "ko_unstratified.tsv")
               for c in COHORTS},
        },
        "gates": {
            "ko_map_sha16": {"expect": KO_MAP_SHA16,
                             "got": sha256_file(args.ko_map)[:16],
                             "pass": sha256_file(args.ko_map)[:16]
                             == KO_MAP_SHA16},
            "observation_sha_contract": obs_gate,
        },
        "counts": {
            "n_chains": len(chains),
            "n_rows": len(rows_out),
            "per_cohort": {
                c: {"microbe_presence": dist("microbe_presence", c),
                    "ko_label": dist("ko_label", c)}
                for c in COHORTS},
            "distinct_microbes_in_chains": len({c["microbe_id"]
                                                for c in chains}),
        },
        "red_lines": {
            "zero_differential_testing": "输出零检验列（表头白名单机器可查）",
            "zero_group_labels": "不读取任何 metadata 文件（源码 grep 在测试断言）",
            "zero_llm_zero_fitting": "定义式统计量（median/比率/定序求和）",
        },
        "integrity_before": integrity_before,
        "determinism": {
            "sort_keys": "chain_id,cohort",
            "float_format": ".10g",
            "no_run_time_in_output_rows": True,
        },
    }
    (sg_dir / "inference_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    integrity_after = verify_subgraph_integrity(sg_dir)
    manifest["integrity_after"] = integrity_after
    (sg_dir / "inference_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    c = manifest["counts"]
    print(f"[inference] 标注行 {c['n_rows']}（{c['n_chains']} 链 × "
          f"{len(COHORTS)} 队列）")
    for cohort in COHORTS:
        pc = c["per_cohort"][cohort]
        print(f"  {cohort}: 菌在位 {pc['microbe_presence']} | "
              f"KO {pc['ko_label']}")
    print(f"  观测层 sha 契约 {obs_gate['files_checked']} 件全过 | "
          f"KO 映射门禁过 | 子图完整性 "
          f"{'通过' if integrity_after['machine_proof'] else '失败'}")
    print(f"  产物={sg_dir / 'chain_annotations.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
