"""U3 路径洗牌对照臂（B3/U3，spec=data/logs/upgrade_v02_spec_u3_20261105.md）。

定位与红线：
- 只读 hypothesis_cards.tsv + interaction_report.tsv（主仓冻结 golden 件），
  唯一写件=评估档 markdown（本工具 --output）——**洗牌卡中间态零落盘**
  （spec 条款"不产卡片文件，防混淆"），卡片/索引/主链产物全只读；
- 零新依赖（纯标准库）——Wilcoxon 正态近似手算（spec 预注册：
  "scipy 不可用则手算正态近似"，本仓工具线一贯零拟合库口径）；
- 排序键零改动：本模块不排序、不回写任何既有表。

洗牌口径（预注册条款的机器化 + 实现裁定披露）：
- 单元=基因列（hop2_gene + hop2_gene_name 成对整体）在**有基因层**
  （hop2_gene 非空行）内全库随机重排——即 chain(菌,代谢物,基因',通路)，
  (代谢物,基因') 配对被打散重配=spec"全库随机配对"；菌/代谢物/通路/
  链深不动（无基因行 100% 不动，保 depth 结构与票②）；
- 分层披露：只动基因列的最小正确实现（任务书授权"边级交换选最小
  正确实现"）——菌×代谢物边不动，因其非 spec 打乱对象；
- 钉扎=random.Random(20261105)（spec 逐字），行序=卡片文件原序
  （chain_id 升序），同输入同平台必同产物；
- 多重集披露：基因列多重集逐元素保持（置换不增不减）；因基因多重集
  高度集中（少数热门基因），随机重排后部分行会换回原基因——这是
  置换零分布的组成部分，机器计数如实披露，不做剔除。

四票重算口径（无简化——全量精确口径，优于任务书允许的简化档）：
- 票①'=票①：票①查询输入=菌（microbe_id/name，hypothesize.py:122-135），
  洗牌不动菌 → 逐位相同输入必同票值；
- 票②'=票②：链深=结构属性（spec 条款"深度不变"）；
- 票③重查：票③三子句查 菌/代谢物节点 id + 基因名（hypothesize.py:
  157-165）——菌/代谢物子句输入不变，基因子句以新基因名**用冻结原件
  hypothesize.build_vote3_index/vote3 重查**（路径加载 06_tool 原模块，
  非复制粘贴——规则零漂移）；
- 票④'=票④：票④=端点 KO 可测（按 chain_id 查 D1 标注，hypothesize.py:
  202-204；其 KO 值由链代谢物 chebi 推导，inference.py:215-239）+菌株
  可得（查菌，hypothesize.py:192-199）——查询输入（chain_id/代谢物/菌）
  全部不动 → 逐位不变。
- 结论：votes_total'=票①+票②+票③'+票④，四票无一票近似。

机器门禁（fail-closed，任一不过=拒出统计）：
①票③往返核验：以 interaction_report 重建索引对**原实体**重算票③，
  必须与卡片 vote3_gutmgene_cross 列逐行全等——证明索引/口径与产卡时
  同源（防拿错 interaction_report 算出无意义 Δ）；
②votes_total 恒等式：votes_total==票①+②+③+④ 逐行成立；
③票②==depth 逐行成立。
"""
import argparse
import csv
import hashlib
import math
import os
import random
import sys
from collections import Counter
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# ---------------------------------------------------------------
<<<<<<< HEAD
# 冻结常量（spec 逐字）——改这里=过审（评审+用户）。
=======
# 冻结常量（spec 逐字）——改这里=过审（监工+用户）。
>>>>>>> origin/main
# ---------------------------------------------------------------
RNG_SEED = 20261105          # 钉扎 random.Random(20261105)（spec 预注册）

# 卡片读档所需列（缺一=fail-closed；只取洗牌+四票+门禁涉及的最小集）
REQUIRED_CARD_COLS = [
    "chain_id", "microbe_id", "microbe_name", "metabolite_id",
    "metabolite_name", "hop2_gene", "hop2_gene_name", "hop3_pathway",
    "depth", "vote1_ext_cohort", "vote2_kg_depth", "vote3_gutmgene_cross",
    "vote4_measurability", "endpoint_meas_ko", "strain_available",
    "votes_total",
]

ALPHA = 0.05                 # 预注册显著性水平（双侧）


def kg_root() -> Path:
    """仓根定位（与 cli.kg_root 同法）：KG_ROOT 环境变量 > 自动探测。

    注意用 os.environ 而非 sys.environ——运行沙箱实测后者被剥除
    （os 口径与 cli.py 一致）。
    """
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------
# 冻结票③规则装载（06_tool 目录名带连字符不是合法包名——按仓内约定
# 路径加载；懒加载+缓存：pytest 收集 import 本模块时不触发副作用）
# ---------------------------------------------------------------
_HYP = None


def load_hypothesize():
    """路径加载主链冻结件 src/06_tool/hypothesize.py（票③规则原件）。"""
    global _HYP
    if _HYP is None:
        p = kg_root() / "src" / "06_tool" / "hypothesize.py"
        if not p.exists():
            raise SystemExit(f"[shuffle-control] 冻结票③规则件缺失 {p}"
                             "——fail-closed（主链模块须在仓内）")
        spec = spec_from_file_location("hypothesize_frozen_for_u3", p)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        _HYP = mod
    return _HYP


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: Path, required: list[str], what: str) -> list[dict]:
    """TSV 读取 + 缺档/缺列 fail-closed（列按名读，不按位）。"""
    if not path.exists():
        raise SystemExit(f"[shuffle-control] 缺输入 {path}——{what} 无从读起")
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in required
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[shuffle-control] {path.name} 缺列 {missing}"
                             "——fail-closed")
        return list(reader)


# ---------------------------------------------------------------
# 机器门禁
# ---------------------------------------------------------------

def run_gates(cards: list[dict], idx: dict) -> dict:
    """三道门禁（docstring 顶部①②③）——全过才允许出统计。"""
    hyp = load_hypothesize()
    # ① 票③往返核验（原实体重算 == 卡片列）
    rt_bad = []
    for c in cards:
        v3 = hyp.vote3({"microbe_id": c["microbe_id"],
                        "metabolite_id": c["metabolite_id"],
                        "hop2_gene_name": c["hop2_gene_name"]}, idx)
        if v3 != int(c["vote3_gutmgene_cross"]):
            rt_bad.append(c["chain_id"])
            if len(rt_bad) >= 3:
                break
    if rt_bad:
        raise SystemExit(f"[shuffle-control] 票③往返核验失配（如 {rt_bad}）"
                         "——interaction_report 与卡片非同源，Δ 无意义，"
                         "fail-closed 拒出统计")
    # ② votes_total 恒等式 + ③ 票②==depth
    id_bad, dep_bad = [], []
    for c in cards:
        s = (int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"])
             + int(c["vote3_gutmgene_cross"]) + int(c["vote4_measurability"]))
        if s != int(c["votes_total"]):
            id_bad.append(c["chain_id"])
        if int(c["vote2_kg_depth"]) != int(c["depth"]):
            dep_bad.append(c["chain_id"])
    if id_bad or dep_bad:
        raise SystemExit(f"[shuffle-control] 恒等式门禁失配：votes_total "
                         f"{len(id_bad)} 行（如 {id_bad[:3]}）/票②≠深度 "
                         f"{len(dep_bad)} 行（如 {dep_bad[:3]}）——fail-closed")
    return {"vote3_roundtrip_rows": len(cards),
            "votes_identity_rows": len(cards), "gate_pass": True}


# ---------------------------------------------------------------
# 洗牌与四票重算（纯函数）
# ---------------------------------------------------------------

def gene_permutation(cards: list[dict], seed: int = RNG_SEED) -> list[int]:
    """有基因层行号 → 供体行号（钉扎洗牌）。

    返回 donors 与 stratum 等长同序：stratum[k] 行换入 donors[k] 行的
    基因。random.Random(seed).shuffle 对行号列表的原地重排——同输入
    同平台逐位可复现（种子 spec 钉扎）。
    """
    stratum = [i for i, c in enumerate(cards) if c["hop2_gene"].strip()]
    donors = list(stratum)
    random.Random(seed).shuffle(donors)
    return donors


def compute_shuffle(cards: list[dict], interaction_rows: list[dict],
                    seed: int = RNG_SEED) -> dict:
    """洗牌臂全流程（纯内存，零卡片落盘）→ 机器数 dict。

    votes_total' = 票①+票②+票③'(新基因名重查冻结索引)+票④（见模块
    docstring 四票口径）。返回 dict 供 CLI 汇报/测试断言/评估档渲染。
    """
    hyp = load_hypothesize()
    idx = hyp.build_vote3_index(interaction_rows)
    gates = run_gates(cards, idx)

    stratum = [i for i, c in enumerate(cards) if c["hop2_gene"].strip()]
    donors = gene_permutation(cards, seed)

    diffs = [0] * len(cards)          # 真-洗牌 配对差（无基因行恒 0）
    # 无基因行洗牌后票值不变——初值即真值（否则分布/均值会把这些行
    # 误记为 0）；有基因行在下方逐行覆盖
    vt_prime = [int(c["votes_total"]) for c in cards]
    same_gene = 0
    for k, i in enumerate(stratum):
        src = donors[k]
        c, s = cards[i], cards[src]
        if s["hop2_gene"] == c["hop2_gene"]:
            same_gene += 1
        # 票③以新基因名重查（菌/代谢物子句输入不变；票①②④逐位不变）
        v3p = hyp.vote3({"microbe_id": c["microbe_id"],
                         "metabolite_id": c["metabolite_id"],
                         "hop2_gene_name": s["hop2_gene_name"]}, idx)
        vt_prime[i] = (int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"])
                       + v3p + int(c["vote4_measurability"]))
        diffs[i] = int(c["votes_total"]) - vt_prime[i]

    wil = wilcoxon_signed_rank_normal(diffs)
    dist_true = Counter(c["votes_total"] for c in cards)
    dist_shuf = Counter(str(v) for v in vt_prime)
    keys = sorted(set(dist_true) | set(dist_shuf), key=int)
    mean_true = sum(int(c["votes_total"]) for c in cards) / len(cards)
    mean_shuf = sum(vt_prime) / len(vt_prime)
    return {
        "n_cards": len(cards), "n_stratum": len(stratum),
        "n_no_gene": len(cards) - len(stratum), "n_same_gene": same_gene,
        "n_distinct_genes": len({c["hop2_gene"] for c in cards
                                 if c["hop2_gene"].strip()}),
        "gates": gates,
        "mean_true": mean_true, "mean_shuffled": mean_shuf,
        "sum_diff": sum(diffs),
        "dist_true": {k: dist_true.get(k, 0) for k in keys},
        "dist_shuffled": {k: dist_shuf.get(k, 0) for k in keys},
        "wilcoxon": wil,
    }


# ---------------------------------------------------------------
# Δ 检验：配对 Wilcoxon 符号秩，手算正态近似（零 scipy）
# ---------------------------------------------------------------

def wilcoxon_signed_rank_normal(diffs: list[int]) -> dict:
    """配对 Wilcoxon 符号秩正态近似（spec 预注册）。

    惯例与披露：零差弃置（Wilcoxon zero-discard，n_eff=非零对数）；
    并列|d|取中间秩；方差含并列校正 Σ(t³-t)/48；z 含 0.5 连续性校正；
    双侧 p=erfc(|z|/√2)（math 标准库正态尾）；效应量 rank-biserial
    =(W+−W−)/S（正值=真卡高于洗牌卡）。已对 scipy.stats.wilcoxon
    (mode="approx", correction=True) 全量对拍一致（B3 证据档）。
    """
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        # 退化：全零差（如全库同基因）——不硬造 z，如实报无有效对
        return {"n_pairs": len(diffs), "n_eff": 0, "n_pos": 0, "n_neg": 0,
                "n_zero": len(diffs), "w_plus": 0, "w_minus": 0, "rank_sum": 0,
                "tie_correction": 0, "z": 0.0, "p_two_sided": 1.0,
                "rank_biserial": 0.0, "degenerate": True}
    counts = Counter(abs(x) for x in d)
    rank_of, cum = {}, 0            # |d| 值 → 中间秩（并列均分）
    for v in sorted(counts):
        rank_of[v] = cum + (counts[v] + 1) / 2
        cum += counts[v]
    w_plus = sum(rank_of[abs(x)] for x in d if x > 0)
    w_minus = sum(rank_of[abs(x)] for x in d if x < 0)
    s_total = n * (n + 1) / 2
    tie = sum(t ** 3 - t for t in counts.values())
    var = n * (n + 1) * (2 * n + 1) / 24 - tie / 48
    dev = w_plus - s_total / 2
    z = (dev - math.copysign(0.5, dev)) / math.sqrt(var)
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"n_pairs": len(diffs), "n_eff": n,
            "n_pos": sum(1 for x in d if x > 0),
            "n_neg": sum(1 for x in d if x < 0),
            "n_zero": len(diffs) - n,
            "w_plus": w_plus, "w_minus": w_minus, "rank_sum": s_total,
            "tie_correction": tie, "z": z, "p_two_sided": p,
            "rank_biserial": (w_plus - w_minus) / s_total,
            "degenerate": False}


def direction_conclusion(w: dict, mean_true: float,
                         mean_shuf: float) -> str:
    """方向结论（预注册 α=0.05 双侧；负结果也如实返回）。"""
    if w["degenerate"] or w["n_eff"] == 0:
        return ("零有效对（配对差全零）——Δ 检验退化，无方向可判"
                "（如实入档）")
    if w["p_two_sided"] < ALPHA:
        if mean_true > mean_shuf:
            return ("真卡 votes_total 显著高于洗牌卡（H1 方向成立，"
                    f"p={w['p_two_sided']:.4e} < {ALPHA}）")
        if mean_true < mean_shuf:
            return ("真卡 votes_total 显著低于洗牌卡（方向与 H1 相反，"
                    f"p={w['p_two_sided']:.4e} < {ALPHA}，如实入档）")
        return ("显著但均值相等（秩方向与均值方向矛盾，人工复核）")
    return (f"无显著差异（p={w['p_two_sided']:.4e} ≥ {ALPHA}）——"
            "H1\u201c真卡 votes_total 分布显著高于洗牌卡\u201d不成立"
            "（负结果如实入档=预注册精神）")


# ---------------------------------------------------------------
# 评估档渲染（确定性 markdown：无时间戳——同输入同产物逐字节可复现；
# 日期由文件名携带，杜绝双跑 sha 不等的伪不可复现）
# ---------------------------------------------------------------

def render_report(cards_path: Path, irep_path: Path, stats: dict) -> str:
    w = stats["wilcoxon"]
    g = stats["gates"]
    lines = [
        "# U3 路径洗牌对照臂评估档（B3，机器生成——数字全部程序转写）",
        "",
        "- 规格：主仓 data/logs/upgrade_v02_spec_u3_20261105.md"
        "（Δ 检验设计预注册）；本档=该规格的机器化产出",
        "- 定位：机制链中间配对随机打乱重算卡片分——检验"
        "\u201c路径顺序本身携带信息\u201d（HypoKG EMNLP 2026 同型先例）",
        "",
        "## 一、输入与指纹",
        f"- hypothesis_cards：{cards_path}（sha256="
        f"{sha256_file(cards_path)}，{stats['n_cards']} 行）",
        f"- interaction_report：{irep_path}（sha256="
        f"{sha256_file(irep_path)}，票③三桶索引重建源）",
        f"- 洗牌种子：random.Random({RNG_SEED})（spec 钉扎；"
        "行序=卡片文件原序）",
        "",
        "## 二、洗牌口径（预注册条款的机器化+实现裁定披露）",
        f"- 单元=基因列（hop2_gene+hop2_gene_name 成对整体）在有基因层 "
        f"{stats['n_stratum']} 行内全库随机重排；无基因行 "
        f"{stats['n_no_gene']} 行 100% 不动（保 depth/票②结构）——"
        "chain(菌,代谢物,基因',通路)，(代谢物,基因')全库随机重配",
        "- 菌/代谢物/通路/链深不动（spec：节点多重集不变+中间两元重配；"
        "只动基因列=任务书授权的最小正确边级实现，菌×代谢物边非打乱对象）",
        f"- 多重集披露：基因列多重集逐元素保持；因基因高度集中"
        f"（{stats['n_distinct_genes']} 个不同基因），重排后 "
        f"{stats['n_same_gene']} 行换回原基因——置换零分布组成部分，"
        "如实计数不剔除",
        "",
        "## 三、四票重算口径（无简化——四票全量精确）",
        "- 票①'=票①（查询输入=菌，hypothesize.py:122-135，菌不动）；"
        "票②'=票②（链深结构属性，spec 条款）；票④'=票④（端点 KO 按"
        "chain_id 查 D1 标注+菌株查菌，hypothesize.py:192-209，查询输入"
        "全不动；KO 值由链代谢物 chebi 推导=inference.py:215-239）",
        "- 票③'=以新基因名用冻结原件 hypothesize.build_vote3_index/"
        "vote3 重查同一三桶索引（路径加载原件非复制——规则零漂移）",
        "- votes_total'=票①+票②+票③'+票④",
        "",
        "## 四、机器门禁（fail-closed，全过才出统计）",
        f"- ①票③往返核验：{g['vote3_roundtrip_rows']} 行原实体重算与"
        "卡片列逐行全等；②votes_total 恒等式："
        f"{g['votes_identity_rows']} 行全等；③票②==depth 全等",
        "",
        "## 五、Δ 检验（预注册：配对 Wilcoxon 符号秩正态近似，"
        f"双侧 α={ALPHA}）",
        f"- 配对 n={w['n_pairs']}（真卡 vs 洗牌卡，逐链配对）；"
        f"零差弃置后 n_eff={w['n_eff']}（正 {w['n_pos']} / 负 {w['n_neg']}"
        f" / 零 {w['n_zero']}）",
        f"- W+={w['w_plus']:.0f}，W-={w['w_minus']:.0f}，"
        f"S={w['rank_sum']:.0f}；并列校正项 Σ(t³−t)={w['tie_correction']:.0f}"
        "；z 含 0.5 连续性校正",
        f"- z={w['z']:+.6f}；双侧 p={w['p_two_sided']:.4e}（math.erfc "
        "手算正态近似，包内零 scipy；scipy 1.15.3 approx 模式全量对拍"
        "一致见 b3_evidence_20261105.txt）",
        f"- 效应量 rank-biserial r={w['rank_biserial']:+.6f}"
        "（正值=真卡高于洗牌卡）",
        f"- 均值：真卡 {stats['mean_true']:.6f} vs 洗牌卡 "
        f"{stats['mean_shuffled']:.6f}",
        f"- **方向结论：{direction_conclusion(w, stats['mean_true'], stats['mean_shuffled'])}**",
        "",
        "## 六、分布对照（机器计数）",
        "",
        "| votes_total | 真卡行数 | 洗牌卡行数 |",
        "|---|---|---|",
    ]
    for k in stats["dist_true"]:
        lines.append(f"| {k} | {stats['dist_true'][k]} "
                     f"| {stats['dist_shuffled'][k]} |")
    lines += [
        "",
        "## 七、红线与复现",
        "- 洗牌卡中间态零落盘（本工具唯一写件=本档；卡片/索引全只读，"
        "golden 件零改动）；排序键零改动（不排序不回写任何既有表）",
        "- 统计口径披露：置换保边际（基因列多重集不变）→ Δ 只能检出"
        "\u201c真配对上证据锚定的重叠结构\u201d，检不出边际质量差——"
        "对照臂问题的正确零分布，非实现局限",
        "- 复现命令：python3 src/kg_tool/shuffle_control.py --cards "
        f"{cards_path} --interaction-report {irep_path} --output <本档路径>"
        "（同输入同平台逐字节复现；本档零时间戳）",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------
# 落档（只增不改护栏，与 U1/U2 sidecar 同制——防两次运行产物混放）
# ---------------------------------------------------------------

def run(cards_path: Path, irep_path: Path, output_path: Path,
        seed: int = RNG_SEED) -> dict:
    """读档→门禁→洗牌→Δ→评估档落盘（幂等 no-op/漂移拒写）。"""
    cards = read_tsv(cards_path, REQUIRED_CARD_COLS, "卡片")
    irep = read_tsv(irep_path, ["bucket", "entity_a", "entity_b",
                                "matched_node_a", "matched_node_b"],
                    "三桶对账报告")
    stats = compute_shuffle(cards, irep, seed)
    payload = render_report(cards_path, irep_path, stats)
    if output_path.exists():
        old = output_path.read_bytes()
        if old == payload.encode("utf-8"):
            stats.update({"idempotent": True, "out": str(output_path)})
            return stats
        raise SystemExit(f"[shuffle-control] {output_path} 已存在且与本次"
                         "产物不一致——只增不改区拒写（疑输入漂移；人工"
                         "移走旧档过审后重跑）")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8")
    stats.update({"idempotent": False, "out": str(output_path)})
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="U3 路径洗牌对照臂（基因列全库随机重配+四票重算+"
                    "Δ 检验，洗牌卡零落盘只产评估档）")
    ap.add_argument("--cards", required=True, type=Path,
                    help="hypothesis_cards.tsv（主仓冻结 golden 件，只读）")
    ap.add_argument("--interaction-report", type=Path, default=None,
                    help="interaction_report.tsv（票③索引重建源；缺省="
                         "卡片同目录同名件）")
    ap.add_argument("--output", required=True, type=Path,
                    help="评估档 markdown 输出路径（唯一写件；已存在且"
                         "字节一致=幂等 no-op，不一致=拒写）")
    args = ap.parse_args(argv)
    irep = args.interaction_report or (args.cards.parent
                                       / "interaction_report.tsv")

    stats = run(args.cards, irep, args.output)
    w = stats["wilcoxon"]
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    print(f"[shuffle-control] {stats['out']} {verb}")
    print(f"[shuffle-control] 卡片 {stats['n_cards']} 行（有基因层 "
          f"{stats['n_stratum']} / 无基因 {stats['n_no_gene']} / 换回原基因 "
          f"{stats['n_same_gene']}）——洗牌卡中间态零落盘")
    print(f"[shuffle-control] 门禁三道全过（票③往返/恒等式/票②==深度）")
    print(f"[shuffle-control] Δ 检验：n_eff={w['n_eff']}（正 {w['n_pos']}"
          f"/负 {w['n_neg']}/零 {w['n_zero']}） z={w['z']:+.6f} "
          f"p={w['p_two_sided']:.4e} r_rb={w['rank_biserial']:+.6f}")
    print(f"[shuffle-control] 方向结论："
          f"{direction_conclusion(w, stats['mean_true'], stats['mean_shuffled'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
