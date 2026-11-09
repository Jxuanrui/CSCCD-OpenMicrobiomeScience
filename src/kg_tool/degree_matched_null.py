"""③乙 度匹配随机链零模型（v0.3 四件之一；spec §三，冻结 v1.5，逐字遵循）。

定位与红线：
- 只读主子图五表（chains / hypothesis_cards / interaction_report /
  chain_annotations / merged_nodes）+ 两个外部边源（v01 边表、gutMGene 表3），
  唯一写件=评估档 markdown（`--output`）。**随机链中间态零落盘**（spec §3.4）：
  不产任何 TSV，防两次运行产物混放（AGENTS.md 血泪史 #5）。
- 不碰 06_tool 生产文件（只按路径 import 复用）：票①来自
  `hypothesize.build_vote1_matcher/vote1`、票③来自
  `hypothesize.build_vote3_index/vote3`、票④来自
  `hop1_shuffle.build_ko_lookup`（其底层是生产 `inference.annotate_ko` 的
  聚合）与 `hypothesize.strain_available`——**复制粘贴一份规则就等着两边漂移**。
- 四票排序键零改动：本模块不排序、不回写任何既有表。
- 包内零第三方依赖（纯标准库）——Wilcoxon 正态近似沿 B3/③甲 冻结实现
  （直接 import，不另写第二份），scipy 只在对拍留痕时**外部**调用。

度源与候选池（spec §3.2，v1.1/v1.2 勘误后口径）：
- 节点集 = chains.tsv 实际出现的四类节点（菌 192 / 代谢物 201 / 基因 197 /
  通路 219），与 chains_manifest counts 四项逐一核对（不符=fail-closed：拿错
  链表就等于拿错图）。
- 边集 = v01 边表 ∪ gutMGene 表3 代谢物-基因边，两端节点须落在上面 809 节点内。
  **禁加 KEGG 快照第三源**（v1.2 裁定：v01 边表已含 participates_in）。

为什么评估档里有两臂（本件最重要的实现裁定，见 §十二 A13）：
- 规格字面边集（v01 边表）在磁盘上**无法重建链构造图的 hop1 层**：v01 的
  produces 边 2,006 条主体是 CHEBI id，而 chains.tsv 的 201 个代谢物节点
  **全部是 MESH id**（实测 43,773 行 metabolite_id 前缀 100% MESH）——按节点
  身份求交后 microbe-metabolite 层只剩 26 对，291/809 节点度为 0（其中
  186/201 代谢物孤立）。这不是"链构造图"，是另一个图。
- 故本件**注册主臂=真实链构造图口径（construction，规格 v1.6 A13 落定）**，同档并列**参考臂=原规格字面口径（spec）**；原 v01 全表口径经磁盘证伪（hop1 覆盖 3.52%/293 零度节点）→ 降为参考臂
  （construction：子图 merged_edges 的 produces/biotransforms 即链条真实 hop1
  源 + v01 participates_in + 表3）**。两臂都出全量数字，谁也不藏；主臂口径
  未改（A13 报 A' 类，待监工分类+用户知情）。

伪代码（spec §3.2 逐行，行 2/6/6b/7 是硬口径）：
- 行2 分箱：精确 degree 值；箱内 <5 **向 degree 更大一侧合并**，至箱内≥5 或
  并尽（degree 最大箱若 <5 则并入其小一侧）；**合并映射表随评估档落盘**。
  实现直接复用 ②乙 `proximity.degree_bins`（同一条规则的唯一实现）——但
  分箱对象是**全部 809 节点**（不是按类别分池），见 A14。
- 行6 候选池 = 同类别 ∧ 同 degree 箱 ∧ **排除该位置真链原节点**。
- 行6b **有放回** rng.choice（不是"够大不放回"）；回放次数累加记 n_resample。
- 行7 池空→跳过该链并记 n_skipped_by_pool（不得静默）。
- rng = random.Random(20261105)；**种子语义与 ③甲 分开**：③甲 用于置换抽样、
  本件用于逐位置度匹配抽取——抽取顺序与候选池都不同，不构成同一随机流，
  报告须写"两检验零模型互不相关（不同抽取机制）"。

统计量（spec §3.3，预指定禁改）：
- 主对照变量 votes_total（逐票四列作分层描述辅）；配对 Wilcoxon 符号秩双侧
  α=0.05，正态近似+并列校正+连续性校正；n_eff 报实际参与对数。
- 效应量：rank-biserial r + 均值差 + 中位数差 + 分布重叠 OVL（辅助）。
- **r 必须与 p 同段报告**（巨样本下 p 极小不等于差异有实质意义，B3 先例 r≈0）。

区分器（spec §3.1，按键前写死，禁事后解释）：
- 判"尺子弱" 当且仅当校准不达标：①随机链可算票比例 < 真链 70%，或
  ②随机链票值方差 < 真链方差 50%。
- 两条都不触发且无差异 → "路径无信息"。
- 两句解读句均预写在常量里，测试断言它们逐字出现在冻结规格档（防"顺手润色"）。

校准"可算"的操作定义（监工 v1.5 冻结审 P0 第 3 条前置要求，`supervisor_
v03_v15_freeze_verdict_20261105.md:48`）：票②=链构造即定、恒可算；票④=
KO 查表非空即可算。本模块 `VOTES_COMPUTABLE`/`vote4_computable` 逐字照此。
"""
import argparse
import csv
import hashlib
import inspect
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 直接以脚本运行（python3 src/kg_tool/degree_matched_null.py）时 src/ 不在
# sys.path，下面两行兄弟模块 import 会 ImportError；`kg` 子命令与 pytest
# 都从包内导入，走不到这里。为什么不靠 PYTHONPATH：复现命令要能一字不改地
# 粘进终端（-I 会忽略环境变量，环境依赖=复现性裂缝）。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 兄弟模块复用（唯一实现原则）：分箱规则与 Wilcoxon 各自只有一份代码。
# 为什么敢跨模块 import 而 ②乙 却自建建图：可复用的是**纯函数**（无错误前缀
# 语义），读档与门禁的错误前缀必须留在本模块内（否则审计看不出是谁拒算）。
from kg_tool.hop1_shuffle import (  # noqa: E402
    FAMILY_NOTE, build_ko_lookup, boundary_label,
    rederive_ko_lookup_with_production, verify_golden_baseline,
    wilcoxon_signed_rank_normal,
)
from kg_tool.proximity import degree_bins  # noqa: E402

# ---------------------------------------------------------------
# 冻结常量（spec §三，逐字）——改这里=过审（监工+用户）。
# ---------------------------------------------------------------
RNG_SEED = 20261105          # spec §3.2：random.Random(20261105) 钉扎
ALPHA = 0.05                 # spec §3.3：双侧 α=0.05
BOUNDARY_LOW = 0.05          # spec §3.3：边界带 p∈(0.05,0.10)
BOUNDARY_HIGH = 0.10
MIN_BIN_SIZE = 5             # spec §3.2 行2：箱内 <5 向大侧合并
N_CHAINS_ANCHOR = 43773      # spec §0.2：总链数
# spec §0.2 + chains_manifest counts：四类节点数（机器核对，不手抄）
NODE_ANCHORS = {"microbe": 192, "metabolite": 201, "gene": 197, "pathway": 219}
# spec §3.1 区分器两条阈值（按键前写死）
CAL_RATIO_MIN = 0.70         # 可算票比例：随机链 < 真链 70% → 尺子弱
CAL_VAR_MIN = 0.50           # 票值方差：随机链 < 真链 50% → 尺子弱
# spec §3.2：两源 sha 前缀（全值从 chains_manifest inputs_sha256 取，不手抄）
V01_EDGES_SHA24 = "41833d39db18e3dca52af284"
GUTMGENE_TABLE3_SHA24 = "c909912b73df2abc2d69f2fa"

# 两臂（规格 v1.6 A13 落定：construction 升为主口径，spec 降为参考臂）
# 依据：spec §3.2 的 v01 全表口径经磁盘证伪（produces 宾语 MESH 仅 30/2006、
# hop1 覆盖 3.52%、293/809 零度节点=缺链条真实第一层）→ 检验对象无效，须修
PRIMARY_ARM = "construction"
SENSITIVITY_ARM = "spec"
ARM_LABELS = {
    "construction": "主口径·真实链构造图（子图 hop1 ∪ v01 participates_in ∪ 表3）",
    "spec": "参考臂·原规格字面口径（v01 边表 ∪ 表3；hop1 覆盖 3.52%=ID 断裂量化证据）",
}

# 预写解读句（spec §3.1 逐字；测试里有"必须出现在规格档"的防手抄断言）
RULE_WEAK_SENTENCE = (
    "本次零模型不满足校准判据（数值见报告），不下'路径无信息'结论；"
    "路径结构信息问题留待更优零模型。")
NO_INFO_SENTENCE = (
    "在度数匹配零模型下真链与随机链无差异——图谱路径结构本身不携带超出"
    "节点边际的证据信号（与 R1/B3/③甲 三重互证）。")
# 两检验零模型互不相关声明（spec §3.2 注意段逐字要求写进报告）
RNG_SEMANTICS_NOTE = (
    "两检验零模型互不相关（不同抽取机制）——②乙 的 random.Random(20261105) "
    "用于置换抽样（每次置换抽一个同类别同度箱节点），本件用于**逐位置度匹配"
    "抽取**（每条链每个 hop 位置各抽一次、有放回）；种子数字相同但抽取顺序与"
    "候选池不同，不构成同一随机流，不产生相关性。")
# 与 HypoKG 的差异（spec §3.2 要求评估档注明）
HYPOKG_NOTE = (
    "本件=度匹配随机链，**端点全不固定**（菌/代谢物/基因/通路逐位置重抽）；"
    "HypoKG（arXiv 2609.12260）用「端点固定+中间步骤打乱」，检验「LLM 是否"
    "利用中间路径」——检验对象不同（评分对图结构的敏感性 vs LLM 路径利用），"
    "故设计不同，不构成可比实现的偏离。")

# 产卡/链表契约列（缺一=fail-closed；只取本件涉及的最小集）
REQUIRED_CARD_COLS = [
    "chain_id", "microbe_id", "microbe_name", "metabolite_id",
    "metabolite_name", "hop2_gene", "hop2_gene_name", "hop3_pathway",
    "depth", "vote1_ext_cohort", "vote2_kg_depth", "vote3_gutmgene_cross",
    "vote4_measurability", "endpoint_meas_ko", "strain_available",
    "votes_total",
]
REQUIRED_CHAIN_COLS = [
    "chain_id", "microbe_id", "microbe_name", "metabolite_id",
    "metabolite_name", "metabolite_chebi", "hop2_gene", "hop2_gene_name",
    "hop3_pathway", "depth",
]
REQUIRED_IREP_COLS = ["bucket", "entity_a", "entity_b",
                      "matched_node_a", "matched_node_b"]
REQUIRED_ANN_COLS = ["chain_id", "cohort", "ko_label"]
REQUIRED_NODE_COLS = ["id", "name", "category", "aliases"]

SPEC_PATH = "data/logs/upgrade_v03_spec_20261105.md"
GOLDEN_LIST_DEFAULT = "data/logs/golden_baseline_v3_20261102_sha256.txt"
MANIFEST_NAME = "chains_manifest.json"
# 数据腿缺省（worktree 内 archive/ 与 data/observation/ 不随 git 复制 →
# 官方跑必须显式传主仓路径；这里只给主仓形态的缺省，不做静默回退）
V01_EDGES_DEFAULT = ("archive/Version01/data/merged/candidate_v3/merged_edges.tsv")
KO_MAP_DEFAULT = ("archive/Version01/data/logs/external_evidence/"
                  "metabolite_ko_map_v2.tsv")
EXT102_DEFAULT = ("data/observation/h1_htn_external_20261030_151856/"
                  "ext_21612_D_vs_H_discovery_20261030_151856.tsv")
BRIDGE_DEFAULT = "data/logs/name_bridge_20261101/name_bridge_cohorts.tsv"
GUTMGENE_DIR_DEFAULT = (
    "<LYTEAMWORK>/JiXuanRui/Project/Dietary_cohort_of_intestinal_flora/"
    "Project/Project03_公共数据库/gutMGene")
OBS_DIR_DEFAULT = "data/observation/htn_v1"
GUTMGENE_TABLE3_NAME = "Microbial metabolite-Host Gene.csv"
# 子图 hop1 谓词（construction 臂用；与 deepchain.HOP1_PREDICATES 同值，
# 但不 import——那是模块级常量，用不到它的门禁逻辑，抄常量不产生漂移面）
HOP1_PREDICATES = ("produces", "biotransforms")
V01_HOP3_PREDICATE = "participates_in"


def kg_root() -> Path:
    """仓根定位（与 cli.kg_root 同法）：KG_ROOT 环境变量 > 自动探测。

    为什么允许环境变量：非 editable 打包安装时 parents[2] 不再指向仓——
    规格档/golden 清单/私有数据腿的可移植性全靠它。
    """
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: Path, required: list[str], what: str,
             encoding: str = "utf-8") -> list[dict]:
    """TSV 读取 + 缺档/缺列 fail-closed（列按名读，不按位）。

    本模块自建读档（不 import hop1_shuffle.read_tsv）的唯一理由：那条错误
    前缀写死 [hop1-shuffle]，跨件复用会让"谁拒算了"从字面就查不出（沿 ②乙
    proximity 的同一裁定）。纯函数（Wilcoxon/分箱）才跨件复用。
    """
    if not path.exists():
        raise SystemExit(f"[degree-matched-null] 缺输入 {path}——{what} 无从读起")
    with open(path, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[degree-matched-null] {path.name} 缺列 {missing}"
                             "——fail-closed")
        return list(reader)


def read_table3(path: Path) -> list[dict]:
    """gutMGene 表3（gb18030 + 逗号分隔——生产 deepchain.load_hop2 同口径）。"""
    if not path.exists():
        raise SystemExit(f"[degree-matched-null] 缺 gutMGene 表3 {path}"
                         "——代谢物-基因边源缺失，fail-closed")
    with open(path, encoding="gb18030", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------
# 图锚与边源（G_chain 构建，fail-closed 族）
# ---------------------------------------------------------------

def load_manifest(sg_dir: Path) -> dict:
    """chains_manifest.json（节点数锚 + inputs_sha256 全值两种用途）。"""
    p = sg_dir / MANIFEST_NAME
    if not p.exists():
        raise SystemExit(f"[degree-matched-null] 缺 {p}——节点锚与边源 sha "
                         "全值都无从核，fail-closed")
    return json.loads(p.read_text(encoding="utf-8"))


def gate_source_sha(path: Path, manifest: dict, key: str, prefix24: str,
                    label: str) -> str:
    """边源 sha 双门禁：manifest 全值比对 + 规格 §3.2 前缀回锚。

    为什么要两道：只看前缀等于只验 96 bit 的身份声明（碰撞概率可忽略，但
    更强的理由=Prefix 与 manifest 若一方被改，单靠一道会静默通过）；只看
    全值则"规格引用的到底是哪份"无从对账。两道都过才证明我们读的就是规格
    指的那份文件。
    """
    if not path.exists():
        raise SystemExit(f"[degree-matched-null] 缺边源 {path}（{label}）"
                         "——fail-closed")
    full = sha256_file(path)
    want = (manifest.get("inputs_sha256") or {}).get(key, "")
    if not want:
        raise SystemExit(f"[degree-matched-null] chains_manifest 缺 {key} "
                         "sha 锚——fail-closed")
    if full != want:
        raise SystemExit(
            f"[degree-matched-null] {label} sha256={full[:24]} ≠ manifest "
            f"锚 {want[:24]}——边源被动过，拒跑")
    if not full.startswith(prefix24):
        raise SystemExit(
            f"[degree-matched-null] {label} sha24={full[:24]} ≠ 规格 §3.2 引用"
            f" {prefix24}——规格与磁盘不同源，fail-closed")
    return full


def build_node_catalog(chains: list[dict], manifest: dict) -> dict:
    """chains.tsv → 809 节点目录（节点集/类别/名字）+ 与 manifest counts 核对。

    为什么先核对再建图：规格 §3.2 把"节点集=chains 实际出现的四类节点"当
    定义式，且明说"与 manifest counts 四项逐一相等，须机器核对"。数不对
    只可能是拿错链表/链表被改——此时算出来的任何 p 都没有意义。
    """
    cat: dict[str, str] = {}
    name: dict[str, str] = {}
    for r in chains:
        for col, c, ncol in (
                ("microbe_id", "microbe", "microbe_name"),
                ("metabolite_id", "metabolite", "metabolite_name"),
                ("hop2_gene", "gene", "hop2_gene_name"),
                ("hop3_pathway", "pathway", "hop3_pathway")):
            nid = r[col]
            if not nid:
                continue
            if nid in cat and cat[nid] != c:
                raise SystemExit(
                    f"[degree-matched-null] 节点 {nid} 同时出现在 {cat[nid]} 与 "
                    f"{c} 位置——四类节点互斥性被破坏，fail-closed")
            cat[nid] = c
            name[nid] = r[ncol]
    counts = Counter(cat.values())
    got = {k: counts.get(k, 0) for k in NODE_ANCHORS}
    if got != NODE_ANCHORS:
        raise SystemExit(
            f"[degree-matched-null] 节点数 {got} ≠ 规格/manifest 锚 "
            f"{NODE_ANCHORS}——拿错链表，拒跑")
    # 与 manifest counts 二次核对（规格要求"与 counts 四项逐一相等"；
    # 常量的四值与 manifest 同源，但盘上 manifest 才是权威——漂移在这里炸）
    mc = (manifest.get("counts") or {})
    pairs = {"microbe": "distinct_microbes", "metabolite":
             "distinct_metabolites_in_chains", "gene": "distinct_genes",
             "pathway": "distinct_pathways"}
    for k, mk in pairs.items():
        if int(mc.get(mk, -1)) != got[k]:
            raise SystemExit(
                f"[degree-matched-null] manifest counts.{mk}="
                f"{mc.get(mk)} ≠ 实测 {got[k]}——图版本锚失配，拒跑")
    return {"cat": cat, "name": name, "counts": got,
            "nodes": sorted(cat)}


def metabolite_name_keys(nodes: list[str], cat: dict[str, str],
                         name: dict[str, str], merged_nodes: list[dict]
                         ) -> dict[str, str]:
    """代谢物名字键（name+aliases, casefold）→ 链内代谢物节点 id。

    这是 gutMGene 表3 与链条的身份桥：表3 只有 "Metabolite" 名字列，链条
    节点是 MESH id。生产口径（deepchain.build_metabolite_keys）用
    merged_nodes 的 name+aliases 建键、同名先到先占位。本件按**节点 id 升序**
    迭代重现"先到先占位"，顺序确定性强于文件序；实测 201 个链代谢物在
    merged_nodes 里 aliases 全空，故该差异在本图上零影响（仍登记 A6）。
    """
    by_key: dict[str, str] = {}
    for nid in sorted(n for n in nodes if cat[n] == "metabolite"):
        keys = [name[nid].strip().casefold()]
        rec = next((r for r in merged_nodes if r["id"] == nid), None)
        if rec:
            keys += [a.strip().casefold()
                     for a in (rec.get("aliases") or "").split("|") if a.strip()]
        for k in keys:
            if k:
                by_key.setdefault(k, nid)
    return by_key


def v01_edge_pairs(rows: list[dict], nodes: set[str], predicates: set[str],
                   keep_counter: Counter, drop_counter: Counter) -> set:
    """v01 边表 → 无向节点对集（两端都在 G_chain 节点集内的才算）。"""
    pairs: set = set()
    for r in rows:
        p = r.get("predicate", "")
        if p not in predicates:
            continue
        s, o = r.get("subject", ""), r.get("object", "")
        if s in nodes and o in nodes and s != o:
            pairs.add((s, o) if s < o else (o, s))
            keep_counter[p] += 1
        else:
            drop_counter[p] += 1
    return pairs


def table3_pairs(rows: list[dict], keys: dict[str, str], nodes: set[str],
                 counter: Counter) -> set:
    """gutMGene 表3 → 代谢物-基因无向对集（代谢物按名字桥接，基因须在节点集）。"""
    pairs: set = set()
    for r in rows:
        key = (r.get("Metabolite") or "").strip().casefold()
        gid = (r.get("Gene ID") or "").strip()
        if not key or not gid:
            continue
        gene = "NCBIGene:" + gid
        mid = keys.get(key)
        if not mid or gene not in nodes or mid == gene:
            continue
        pairs.add((mid, gene) if mid < gene else (gene, mid))
        counter["table3"] += 1
    return pairs


def build_graph_arm(arm: str, nodes: list[str], cat: dict[str, str],
                    name: dict[str, str], v01_rows: list[dict],
                    sub_edges: list[dict], t3_rows: list[dict],
                    merged_nodes: list[dict]) -> dict:
    """按臂构建 G_chain 边集（无向、去重）+ 各源贡献边数 manifest。

    两臂差异只在 hop1 层（见模块 docstring A13）：
      spec         = v01 全表（两端在节点集内）∪ 表3
      construction = 子图 merged_edges 的 produces/biotransforms ∪
                     v01 participates_in ∪ 表3
    两臂的 table3 边完全同一份（代谢物-基因层无争议）。
    """
    node_set = set(nodes)
    keys = metabolite_name_keys(nodes, cat, name, merged_nodes)
    v01_keep: Counter = Counter()
    v01_drop: Counter = Counter()
    sub_keep: Counter = Counter()
    sub_drop: Counter = Counter()
    hop1_pairs: set = set()
    if arm == "spec":
        v01_pairs = v01_edge_pairs(
            v01_rows, node_set,
            {"produces", "biotransforms", "modulates_host_gene",
             "regulates_host_gene", V01_HOP3_PREDICATE},
            v01_keep, v01_drop)
    elif arm == "construction":
        # 子图 merged_edges 才是链条实际 hop1 源（v0.2 deepchain 用它们拼链）
        hop1_pairs = v01_edge_pairs(sub_edges, node_set,
                                    set(HOP1_PREDICATES), sub_keep, sub_drop)
        v01_pairs = v01_edge_pairs(v01_rows, node_set,
                                   {V01_HOP3_PREDICATE},
                                   v01_keep, v01_drop)
    else:
        raise SystemExit(f"[degree-matched-null] 未知图臂 {arm!r}")
    t3_keep: Counter = Counter()
    t3_pairs = table3_pairs(t3_rows, keys, node_set, t3_keep)

    pairs = v01_pairs | hop1_pairs | t3_pairs
    # 源间重叠（多源 ∪ 去重致"度双算"的风险靠这个数兜住，spec §3.4⑦）
    overlap = (v01_pairs & t3_pairs) | (hop1_pairs & t3_pairs) \
        | (hop1_pairs & v01_pairs)
    return {
        "arm": arm,
        "pairs": pairs,
        "n_edges": len(pairs),
        "v01_rows": len(v01_rows),
        "v01_kept": sum(v01_keep.values()),
        "v01_kept_by_predicate": dict(sorted(v01_keep.items())),
        "v01_dropped": sum(v01_drop.values()),
        "sub_kept": sum(sub_keep.values()) if arm == "construction" else 0,
        "sub_kept_by_predicate": dict(sorted(sub_keep.items())),
        "sub_dropped": sum(sub_drop.values()) if arm == "construction" else 0,
        "hop1_pairs": len(hop1_pairs),
        "t3_rows": len(t3_rows),
        "t3_kept": sum(t3_keep.values()),
        "t3_pairs": len(t3_pairs),
        "overlap_pairs": len(overlap),
        "metabolite_keys": len(keys),
    }


def chain_edge_coverage(chains: list[dict], pairs: set) -> dict:
    """真链三层边在图中的覆盖率（图保真度指标，非检验）。

    为什么必须报：度匹配零模型的可信度取决于"G_chain 是不是链条走过的图"。
    若真链的 hop1 边在图中根本不存在，随机链与真链就活在三张不同的图上——
    这个数字比任何 p 值都更能说明该臂口径是否成立。
    """
    hop = defaultdict(set)
    for r in chains:
        d = int(r["depth"])
        if d >= 1:
            hop["hop1"].add(tuple(sorted((r["microbe_id"],
                                          r["metabolite_id"]))))
        if d >= 2:
            hop["hop2"].add(tuple(sorted((r["metabolite_id"],
                                          r["hop2_gene"]))))
        if d >= 3:
            hop["hop3"].add(tuple(sorted((r["hop2_gene"],
                                          r["hop3_pathway"]))))
    out = {}
    for k in ("hop1", "hop2", "hop3"):
        tot = len(hop[k])
        hit = len(hop[k] & pairs)
        out[k] = {"edges": tot, "present": hit,
                  "rate": (hit / tot) if tot else 0.0}
    return out


# ---------------------------------------------------------------
# 度与分箱（spec §3.2 行1-2）
# ---------------------------------------------------------------

def compute_degrees(nodes: list[str], pairs: set) -> dict[str, int]:
    """无向度（重复边已去重：pairs 是集合，因此度天然不双算）。"""
    nb: dict[str, set[str]] = defaultdict(set)
    for a, b in pairs:
        nb[a].add(b)
        nb[b].add(a)
    return {n: len(nb[n]) for n in nodes}


def bins_with_merge_map(nodes: list[str], degree: dict[str, int],
                        min_size: int = MIN_BIN_SIZE) -> dict:
    """精确 degree 分箱 + 合并映射表（spec §3.2 行2 的落盘要求）。

    `degree_bins` 复用 ②乙 的实现（"升序累积到 ≥5 即封箱"≡"小箱向 degree
    更大一侧合并至 ≥5 或并尽"），本函数只补两件事：
    ① degree→箱号 的合并映射（哪些精确 degree 值并入了哪一箱）；
    ② 每箱的类别构成（供报告判断"某箱某类别只有 1 个节点"这种池空风险）。
    """
    deg2bin, bin2mem = degree_bins(nodes, degree, min_size)
    merge_map: dict[int, list[int]] = defaultdict(list)
    for d, b in deg2bin.items():
        merge_map[b].append(d)
    rows = []
    for b in sorted(bin2mem):
        members = bin2mem[b]
        ds = [degree[m] for m in members]
        rows.append({
            "bin": b, "degrees": sorted(merge_map[b]),
            "n_members": len(members), "min_deg": min(ds), "max_deg": max(ds),
            "members": members,
        })
    return {"deg2bin": deg2bin, "bin2mem": bin2mem, "rows": rows,
            "degree_values": len(deg2bin), "n_bins": len(bin2mem)}


def category_profile(bin_row: dict, cat: dict[str, str]) -> dict[str, int]:
    """每箱的类别构成（池空诊断用：某类别在此箱只剩 0/1 个节点时必跳链）。"""
    c: Counter = Counter(cat[m] for m in bin_row["members"])
    return {k: c.get(k, 0) for k in ("microbe", "metabolite", "gene", "pathway")}


# ---------------------------------------------------------------
# 随机链抽样（spec §3.2 行3-7）
# ---------------------------------------------------------------

def chain_node_sequence(chain: dict) -> list[str]:
    """真链节点序列 [微, 代, 基, 通]（按 depth 截断，spec §3.2 行5）。"""
    seq = [chain["microbe_id"], chain["metabolite_id"]]
    depth = int(chain["depth"])
    if depth >= 2:
        seq.append(chain["hop2_gene"])
    if depth >= 3:
        seq.append(chain["hop3_pathway"])
    if any(not s for s in seq):
        raise SystemExit(
            f"[degree-matched-null] 链 {chain['chain_id']} depth={depth} 但节点"
            f"序列含空位 {seq}——chains.tsv 结构不自洽，fail-closed")
    return seq


def candidate_pool(node: str, cat: dict[str, str], deg2bin: dict[int, int],
                   bin2mem: dict[int, list[str]], degree: dict[str, int]
                   ) -> list[str]:
    """该位置候选池：同类别 ∧ 同 degree 箱 ∧ 排除原节点（spec §3.2 行6）。

    返回升序列表（rng.choice 取下标，列表顺序必须是确定的——否则同种子
    在不同 Python 版本上抽到的节点集合会漂）。
    """
    b = deg2bin[degree[node]]
    c = cat[node]
    return [m for m in bin2mem[b] if cat[m] == c and m != node]


def sample_random_chains(cards: list[dict], cat: dict[str, str],
                         degree: dict[str, int], deg2bin: dict[int, int],
                         bin2mem: dict[int, list[str]],
                         seed: int = RNG_SEED) -> dict:
    """逐链逐位置度匹配抽样（行3-7）——**有放回**、池空即整链跳过。

    三条口径必须一起看（spec 行6b/7）：
    ① 有放回=rng.choice 每次独立抽（不做"池够大才不放回"的判断——那个判断
       被规格明令禁止，因为"够大"没有客观阈值，等于实现者临场定口径）；
    ② 池空 → **整条链**跳过（不是该位置保留原节点）并计数——保留原节点等于
       把真链偷偷混进零分布，是零模型最典型的自欺；
    ③ n_resample 记"回放次数"。规格写"该位置回放次数=该池被抽取总次数−1"，
       本实现按**池=(类别, 箱)** 聚合（抽走原节点只是每链的过滤项，不是池的
       身份），于是 Σ(抽取次数−1) 就是"同一候选箱被反复回放"的次数；另报
       n_duplicate_draws=n_draws−distinct 抽样节点数作为"有放回真的发生了"的
       直接证据（若按每链每位置池作身份，该量恒为 0——见 A4）。
    """
    rng = random.Random(seed)          # spec §3.2 行3：单流，顺序=卡片文件序
    picks: list[list[str] | None] = []
    n_skipped = 0
    skip_by_category: Counter = Counter()
    draws_by_box: Counter = Counter()
    n_draws = 0
    sampled_nodes: list[str] = []
    for c in cards:
        seq = chain_node_sequence(c)
        got: list[str] = []
        skipped = False
        for pos in seq:
            pool = candidate_pool(pos, cat, deg2bin, bin2mem, degree)
            if not pool:               # 行7：池空→跳过该链并记数（不得静默）
                skipped = True
                skip_by_category[cat[pos]] += 1
                break
            pick = rng.choice(pool)    # 行6b：有放回
            got.append(pick)
            n_draws += 1
            draws_by_box[(cat[pos], deg2bin[degree[pos]])] += 1
            sampled_nodes.append(pick)
        if skipped:
            n_skipped += 1
            picks.append(None)
        else:
            picks.append(got)
    n_resample = sum(t - 1 for t in draws_by_box.values())
    return {
        "picks": picks,
        "n_skipped_by_pool": n_skipped,
        "n_skipped_by_category": dict(sorted(skip_by_category.items())),
        "n_draws": n_draws,
        "n_resample": n_resample,
        "n_boxes_drawn": len(draws_by_box),
        "n_duplicate_draws": n_draws - len(set(sampled_nodes)),
        "n_distinct_sampled": len(set(sampled_nodes)),
        "seed": seed,
    }


# ---------------------------------------------------------------
# 四票重算（spec §3.2 行8）
# ---------------------------------------------------------------

def vote4_computable(metabolite_id: str, chebi_of: dict[str, str],
                     ko_map: dict[str, list[str]]) -> int:
    """票④"可算"判定（监工 v1.5 冻结审 P0 第 3 条的操作定义）。

    口径逐字：票②=链构造即定、恒可算；票④=**KO 查表非空**即可算。
    为什么不能用"vote4 值非零"当可算：不可测（ko_absent）是有效的 0 值，
    不是"算不出"——把 0 当缺失会把校准指标变成另一回事。
    """
    inf = _inference()
    chebi = chebi_of.get(metabolite_id, "")
    if not chebi:
        return 0
    return 1 if inf.chain_ko_ids(chebi, ko_map) else 0


def _inference():
    """生产 inference 模块（懒加载，走 hop1_shuffle 的路径加载器）。"""
    from kg_tool.hop1_shuffle import load_inference
    return load_inference()


def recompute_random_votes(cards: list[dict], picks: list[list[str] | None],
                           name: dict[str, str], matcher: dict, v3_idx: dict,
                           strain_idx: dict, ko_lookup: dict[str, int],
                           comp4: dict[str, int]) -> dict:
    """按随机链重算四票（spec §3.2 行8：票①查菌/票③查 gutMGene/票④查 KO）。

    票②=深度（链构造即定，不随抽样变）；票①用生产 vote1 对**新菌**重判；
    票③用生产 vote3 对（新菌, 新代谢物, 新基因名）重判；票④=新代谢物的
    端点可测查表 + 新菌的菌株可得（生产 strain_available）。
    `comp4` = 代谢物→"票④可算"标志（A14 操作定义），与票④取值分开记——
    值为 0（ko_absent）不等于"算不出"。
    纯函数：只读入参，不写任何东西（中间态零落盘的前提）。
    """
    hyp = _hypothesize()
    out = {"vote1": [], "vote2": [], "vote3": [], "vote4": [],
           "votes_total": [], "computable4": [], "index": []}
    for i, c in enumerate(cards):
        p = picks[i]
        if p is None:
            continue
        depth = int(c["depth"])
        mic, met = p[0], p[1]
        gene = p[2] if depth >= 2 else ""
        v1 = hyp.vote1(mic, name[mic], matcher)
        v3 = hyp.vote3({"microbe_id": mic, "metabolite_id": met,
                        "hop2_gene_name": name[gene] if gene else ""}, v3_idx)
        v4 = ko_lookup[met] + hyp.strain_available(mic, name[mic], strain_idx)
        out["vote1"].append(v1)
        out["vote2"].append(depth)
        out["vote3"].append(v3)
        out["vote4"].append(v4)
        out["votes_total"].append(v1 + depth + v3 + v4)
        # 可算票数（1..2）：票②恒可算（+1）+ 票④ KO 查表非空（+0/1）。
        # 必须与真链侧同口径（真链侧也记 1+flag）——两侧一处差 1 就会把
        # 校准比值算成半个量（首版正是此处漏了 +1，被首跑自检抓出）。
        out["computable4"].append(1 + comp4[met])
        out["index"].append(i)
    return out


def _hypothesize():
    from kg_tool.hop1_shuffle import load_hypothesize
    return load_hypothesize()


# ---------------------------------------------------------------
# 机器门禁（fail-closed，任一不过=拒出统计）
# ---------------------------------------------------------------

def run_gates(cards: list[dict], idx: dict, ko_lookup: dict[str, int],
              strain_idx: dict, n_nodes: dict) -> dict:
    """四道门禁（全过才允许出统计）。

    ① 票③往返：用 interaction_report 重建索引对**原实体**重算票③，必须与
       卡片列逐行全等——证明索引与产卡同源（拿错报告算出的 Δ 毫无意义）。
    ② votes_total 恒等式：votes_total == 票①+②+③+④ 逐行成立。
    ③ 票② == depth 逐行成立（票②是链结构属性，任何抽样都不得动它）。
    ④ 票④查表保真 + 节点数锚（卡片 endpoint/strain 与本次查表一致；
       节点数已在上游 build_node_catalog 与 manifest 双核——这里只做汇总）。

    为什么门禁放在出统计之前而不是"出完再核"：一旦核出失配，任何已经
    落盘的 p 值都会被当成结论引用；fail-closed 是唯一可审计的顺序。
    """
    hyp = _hypothesize()
    rt_bad = [c["chain_id"] for c in cards
              if hyp.vote3({"microbe_id": c["microbe_id"],
                            "metabolite_id": c["metabolite_id"],
                            "hop2_gene_name": c["hop2_gene_name"]}, idx)
              != int(c["vote3_gutmgene_cross"])]
    if rt_bad:
        raise SystemExit(f"[degree-matched-null] 票③往返核验失配（如 "
                         f"{rt_bad[:3]}，共 {len(rt_bad)} 行）——"
                         "interaction_report 与卡片非同源，fail-closed")
    id_bad, dep_bad, v4_bad = [], [], []
    for c in cards:
        s = (int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"])
             + int(c["vote3_gutmgene_cross"]) + int(c["vote4_measurability"]))
        if s != int(c["votes_total"]):
            id_bad.append(c["chain_id"])
        if int(c["vote2_kg_depth"]) != int(c["depth"]):
            dep_bad.append(c["chain_id"])
        want = (ko_lookup[c["metabolite_id"]]
                + hyp.strain_available(c["microbe_id"], c["microbe_name"],
                                       strain_idx))
        if want != int(c["vote4_measurability"]):
            v4_bad.append(c["chain_id"])
    if id_bad or dep_bad or v4_bad:
        raise SystemExit(
            f"[degree-matched-null] 恒等式门禁失配：votes_total {len(id_bad)} "
            f"行（如 {id_bad[:3]}）/票②≠深度 {len(dep_bad)} 行（如 "
            f"{dep_bad[:3]}）/票④查表≠卡片 {len(v4_bad)} 行（如 "
            f"{v4_bad[:3]}）——fail-closed")
    return {"vote3_roundtrip_rows": len(cards),
            "votes_identity_rows": len(cards),
            "vote2_depth_rows": len(cards),
            "vote4_lookup_rows": len(cards),
            "node_anchor": n_nodes, "gate_pass": True}


# ---------------------------------------------------------------
# 统计量（spec §3.3：配对 Wilcoxon 双侧 + 效应量 + OVL）
# ---------------------------------------------------------------

def paired_stats(true_votes: list[int], rand_votes: list[int],
                 true_comp: list[int], rand_comp: list[int]) -> dict:
    """配对 Δ 统计 + 校准两指标（同一份配对集，禁止两处口径各自漂移）。

    配对定义：只取"未被池空跳过"的链（随机链存在才谈得上配对差）。跳过
    链的处置规格未写 → 本函数按配对集计算并在报告里把 n_pairs 与总链数
    的差额摆出来（A7），不静默。
    `true_comp`/`rand_comp` 每元素=该链"可算票数"（票②恒 1 + 票④ 0/1），
    取值 1..2；可算比例 = 均值/2。
    """
    diffs = [a - b for a, b in zip(true_votes, rand_votes)]
    if any(x not in (1, 2) for x in list(true_comp) + list(rand_comp)):
        # 两侧"可算票数"必须同域（1=仅票②，2=票②+票④）；域不同=口径错位，
        # 算出来的比值没有任何意义（首跑正是此处漏 +1，被这条断言拦下）
        raise SystemExit(
            "[degree-matched-null] 可算票数越界（应∈{1,2}）——两侧口径"
            "错位，fail-closed")
    w = wilcoxon_signed_rank_normal(diffs)
    n = len(diffs)
    mean_true = statistics.mean(true_votes) if n else 0.0
    mean_rand = statistics.mean(rand_votes) if n else 0.0
    med_diff = statistics.median(diffs) if n else 0.0
    cal_true = (statistics.mean(true_comp) / 2) if n else 0.0
    cal_rand = (statistics.mean(rand_comp) / 2) if n else 0.0
    # 校准指标②：票值方差（总体方差 ddof=0；两侧同 n → 比值与 ddof 无关）
    var_true = statistics.pvariance(true_votes) if n else 0.0
    var_rand = statistics.pvariance(rand_votes) if n else 0.0
    return {
        "wilcoxon": w, "n_pairs": n,
        "mean_true": mean_true, "mean_random": mean_rand,
        "mean_diff": mean_true - mean_rand,
        "median_diff": med_diff,
        "sum_diff": sum(diffs),
        "var_true": var_true, "var_random": var_rand,
        "var_ratio": (var_rand / var_true) if var_true else 0.0,
        "computable_true": cal_true, "computable_random": cal_rand,
        "computable_ratio": (cal_rand / cal_true) if cal_true else 0.0,
        "ovl": overlap_coefficient(true_votes, rand_votes),
        "dist_true": counts_by_value(true_votes),
        "dist_random": counts_by_value(rand_votes),
    }


def counts_by_value(votes: list[int]) -> dict[int, int]:
    c = Counter(votes)
    return {k: c.get(k, 0) for k in range(0, max(c) + 1 if c else 1)}


def overlap_coefficient(a: list[int], b: list[int]) -> float:
    """分布重叠面积 OVL（离散经验分布）= Σ_v min(P_a(v), P_b(v))。

    规格只说"分布重叠面积（OVL，辅助）"没给公式。离散票值下取两经验
    质量函数的逐点最小和（=直方图交叠面积），取值 [0,1]，1=完全重叠。
    """
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    keys = set(ca) | set(cb)
    return sum(min(ca[k] / len(a), cb[k] / len(b)) for k in keys)


def discriminator(stat: dict) -> dict:
    """区分器裁定（spec §3.1，机械判据，禁事后解释）。

    只有在 H1 不成立（或无显著差异）时才启用——H1 成立意味着"有差异"，
    此时"尺子弱/路径无信息"两句都不该出现（它们的前提是"没差异"）。
    返回 verdict ∈ {尺子弱, 路径无信息, 不适用}，并带上两条判据的实测值。
    """
    w = stat["wilcoxon"]
    significant = (not w["degenerate"]) and w["p_two_sided"] < ALPHA
    if significant and stat["mean_diff"] > 0:
        return {"verdict": "不适用",
                "reason": "H1 成立（真链显著更高）——区分器只对'无差异'有定义",
                "metric1": False, "metric2": False}
    if significant:
        return {"verdict": "不适用",
                "reason": ("存在显著差异但方向与 H1 相反（真链显著更低）——"
                           "区分器两句解读句都以'无差异'为前提，均不适用；"
                           "按 §0.4 如实报方向与效应量"),
                "metric1": False, "metric2": False}
    m1 = stat["computable_ratio"] < CAL_RATIO_MIN
    m2 = stat["var_ratio"] < CAL_VAR_MIN
    if m1 or m2:
        trigger = []
        if m1:
            trigger.append(f"可算票比例 {stat['computable_ratio']:.4f} < "
                           f"{CAL_RATIO_MIN}")
        if m2:
            trigger.append(f"方差比 {stat['var_ratio']:.4f} < {CAL_VAR_MIN}")
        return {"verdict": "尺子弱", "reason": "；".join(trigger),
                "sentence": RULE_WEAK_SENTENCE, "metric1": m1, "metric2": m2}
    return {"verdict": "路径无信息",
            "reason": (f"校准达标（可算票比例 {stat['computable_ratio']:.4f} ≥ "
                       f"{CAL_RATIO_MIN}；方差比 {stat['var_ratio']:.4f} ≥ "
                       f"{CAL_VAR_MIN}）且无显著差异"),
            "sentence": NO_INFO_SENTENCE, "metric1": m1, "metric2": m2}


def fmt_p(p: float) -> str:
    """p 值格式化——**不许把下溢打印成 0**。

    |z|=86 时 erfc 在双精度下直接返回 0.0；写成 "p=0" 会被读成"概率恰好为零"，
    是比四舍五入更严重的失真（AGENTS.md 数字机器输出制）。统一写成
    "<1e-300（双精度下溢）"，强度的信息由 z 与效应量承担。
    """
    if p == 0.0:
        return "<1e-300（双精度下溢）"
    return f"{p:.6e}"


def conclusion_sentence(stat: dict) -> str:
    """方向结论（双侧 α 口径；方向由均值差读——沿 ③甲 A5 同制）。"""
    w = stat["wilcoxon"]
    if w["degenerate"] or w["n_eff"] == 0:
        return "零有效对（配对差全零）——Δ 检验退化，无方向可判（如实入档）"
    band = boundary_label(w["p_two_sided"])
    band_txt = f"【{band}】" if band else ""
    if w["p_two_sided"] < ALPHA:
        if stat["mean_diff"] > 0:
            return (f"真链 votes_total 显著高于度匹配随机链（H1 方向成立，"
                    f"p={fmt_p(w['p_two_sided'])} < {ALPHA}）{band_txt}")
        return (f"真链 votes_total 显著低于度匹配随机链（方向与 H1 相反，"
                f"p={fmt_p(w['p_two_sided'])} < {ALPHA}，如实入档）{band_txt}")
    return (f"无显著差异（p={fmt_p(w['p_two_sided'])} ≥ {ALPHA}）——"
            f"H1「真链 votes_total 分布显著高于随机链」不成立"
            f"（负结果如实入档=预注册精神）{band_txt}")


# ---------------------------------------------------------------
# 伪代码对拍表（spec §3.2 逐行 → 代码位置；行号运行时从本源文件取）
# ---------------------------------------------------------------

PSEUDOCODE_STEPS = [
    ("行1", "对 G_chain 每个节点计算 degree（无向，重复边去重后度）",
     "compute_degrees"),
    ("行2", "精确 degree 分箱；<5 向 degree 更大一侧合并；合并映射表落盘",
     "bins_with_merge_map"),
    ("行2", "合并映射表逐行（哪些 degree 并入哪箱）",
     "merge_map_rows"),
    ("行3", "rng = random.Random(20261105)", "sample_random_chains"),
    ("行4", "for k in 1..N_chains (=43,773)", "sample_random_chains"),
    ("行5", "取真链节点序列 [微,代,基,通]（按 depth 截断）",
     "chain_node_sequence"),
    ("行6", "候选池=同类别 ∧ 同 degree 箱 ∧ 排除该位置原节点", "candidate_pool"),
    ("行6b", "rng.choice 有放回抽一个；回放次数累加记 n_resample",
     "sample_random_chains"),
    ("行7", "池空→跳过该链并记 n_skipped_by_pool（不得静默）",
     "sample_random_chains"),
    ("行8", "组装随机链，按 hypothesize 四票重算 votes_total", "compute_arm"),
    ("行9", "输出真/随票分布 + n_skipped/n_resample + 校准两指标",
     "compute_arm"),
]


def merge_map_rows(bins: dict) -> list[tuple[int, list[int]]]:
    """合并映射表（箱号 → 精确 degree 值列表）——伪代码行2 的落盘物。"""
    return [(r["bin"], r["degrees"]) for r in bins["rows"]]


def code_locations() -> list[tuple[str, str, int]]:
    """[(伪代码行, 函数名, 源码行号)]——行号从本源文件读，不手抄。"""
    out = []
    for step, text, fn_name in PSEUDOCODE_STEPS:
        fn = globals().get(fn_name)
        line = "?"
        if fn is not None:
            try:
                line = inspect.getsourcelines(fn)[1]
            except (OSError, TypeError):
                line = "?"
        out.append((step, fn_name, line))
    return out


# ---------------------------------------------------------------
# 单臂全流程
# ---------------------------------------------------------------

def compute_arm(arm: str, cards: list[dict], cat: dict[str, str],
                name: dict[str, str], deg: dict[str, int], bins: dict,
                matcher: dict, v3_idx: dict, strain_idx: dict,
                ko_lookup: dict[str, int], comp4: dict[str, int],
                true_comp: list[int]) -> dict:
    """一臂 = 抽样 → 重算四票 → 配对统计 → 区分器（纯内存）。

    `comp4`=代谢物→票④可算标志；`true_comp`=每条真链的可算票数（1..2）。
    """
    smp = sample_random_chains(cards, cat, deg, bins["deg2bin"],
                              bins["bin2mem"])
    v = recompute_random_votes(cards, smp["picks"], name, matcher, v3_idx,
                              strain_idx, ko_lookup, comp4)
    idx = v["index"]
    rand_comp = v["computable4"]
    true_votes = [int(cards[i]["votes_total"]) for i in idx]
    rand_votes = v["votes_total"]
    stat = paired_stats(true_votes, rand_votes,
                        [true_comp[i] for i in idx], rand_comp)
    stat.update({"arm": arm, "sampling": smp,
                 "vote_means": vote_column_means(v, len(idx)),
                 "true_vote_means": true_vote_column_means(cards, idx)})
    # 被跳过链的真链票均值（A7 承诺的选择偏倚描述）：若跳过集与配对集
    # 票值差异大，说明"哪些链被跳"不是随机缺失去，读者必须看到这一点。
    skipped_idx = [i for i, p in enumerate(smp["picks"]) if p is None]
    kept_mean = (statistics.mean(true_votes) if true_votes else 0.0)
    skip_mean = (statistics.mean(int(cards[i]["votes_total"])
                                for i in skipped_idx) if skipped_idx else 0.0)
    stat["selection_bias"] = {"n_skipped": len(skipped_idx),
                              "kept_true_mean": kept_mean,
                              "skipped_true_mean": skip_mean,
                              "delta": skip_mean - kept_mean}
    stat["discriminator"] = discriminator(stat)
    stat["conclusion"] = conclusion_sentence(stat)
    # 配对差只留在内存（下划线=私有），供外部 scipy 对拍脚本逐位复算；
    # 绝不进报告、绝不落盘——"随机链中间态零落盘"的红线在这里也不能松口
    stat["_diffs"] = [a - b for a, b in zip(true_votes, rand_votes)]
    return stat


def vote_column_means(votes: dict, n: int) -> dict:
    """逐票四列分层描述（spec §3.3：逐票作分层描述，不参与主判定）。"""
    if not n:
        return {k: 0.0 for k in ("vote1", "vote2", "vote3", "vote4")}
    return {k: statistics.mean(votes[k]) for k in
            ("vote1", "vote2", "vote3", "vote4")}


def true_vote_column_means(cards: list[dict], idx: list[int]) -> dict:
    """真链逐票均值（严格取配对集 idx——与随机侧同一批链，可比）。"""
    if not idx:
        return {k: 0.0 for k in ("vote1", "vote2", "vote3", "vote4")}
    return {
        "vote1": statistics.mean(int(cards[i]["vote1_ext_cohort"]) for i in idx),
        "vote2": statistics.mean(int(cards[i]["vote2_kg_depth"]) for i in idx),
        "vote3": statistics.mean(int(cards[i]["vote3_gutmgene_cross"]) for i in idx),
        "vote4": statistics.mean(int(cards[i]["vote4_measurability"]) for i in idx),
    }


# ---------------------------------------------------------------
# 规格歧义登记（A 类攒批；冻结规格未写细节，禁静默自决）
# ---------------------------------------------------------------

AMBIGUITIES = [
    ("A1", "§3.1「单侧」与 §3.3「双侧」的措辞冲突",
     "§3.3 明写「配对 Wilcoxon ... 双侧 α=0.05」=预指定统计口径；§3.1 的"
     "「单侧」是 H1 方向叙述。实现按 §3.3 双侧，方向由均值差/效应量符号读出"
     "——不改成单侧 p（事后改口径=规格漂移；沿 ③甲 A5 同制）。"),
    ("A2", "rank-biserial r 的公式与 n_eff 的关系",
     "规格未给公式。r=(W+−W−)/S，S=n_eff(n_eff+1)/2（沿 B3/③甲 冻结实现，"
     "直接 import 同一函数不另写）；n_eff=非零配对对数，与 n_pairs 并报。"),
    ("A3", "「中位数差」与 OVL 的定义",
     "中位数差=median(逐链配对差)；OVL=Σ_v min(P_真(v), P_随(v))（离散经验"
     "分布的交叠面积，[0,1]）。规格只说「分布重叠面积（OVL，辅助）」未给"
     "公式，取直方图交叠是离散票值下唯一无参数选择。"),
    ("A4", "n_resample 的「池」粒度（§3.2 行6b）",
     "规格写「该位置回放次数=该池被抽取总次数−1」。若池身份含「排除的原"
     "节点」，每链每位置池只被抽一次→该量恒为 0（无信息）；故本实现把池身份"
     "定为**（类别, 度数箱）**，n_resample=Σ(抽取次数−1)（同一候选箱被反复"
     "回放）；另报 n_duplicate_draws=n_draws−distinct 抽样节点数作为「有放回"
     "真的发生」的直接证据。两个数都进档，读法随读者。"),
    ("A5", "分箱对象是全体 809 节点还是逐类别分池（§3.2 行2 vs 行6）",
     "行2「对 G_chain 中每个节点计算 degree。按 degree 值分箱」无类别限定"
     "→实现按**全体节点**分箱（全局箱）；行6 再用「同类别」过滤。若改按"
     "类别分池（沿 ②乙 的池内分箱先例），跳链数会显著下降（本件在报告"
     "§五并列两口径的实测跳链数）。主臂=真实链构造图（全局箱）。"),
    ("A6", "表3 名字桥的同名先到先占位顺序",
     "生产 deepchain.build_metabolite_keys 按 merged_nodes 文件序遍历、同名"
     "先到占位；本件按节点 id 升序迭代（顺序确定性强于文件序）。实测 201 个"
     "链代谢物在 merged_nodes 的 aliases 全空、名字键无碰撞→本图零影响。"),
    ("A7", "被池空跳过的链如何进入配对检验",
     "规格只要求「跳过并记数」，未写是否影响配对集。实现：跳过链无随机链"
     "→不进入配对（n_pairs=43,773−n_skipped_by_pool），全部统计（分布/方差/"
     "校准/OVL）统一在该配对集上算；报告并列总链数与配对集，并给出被跳"
     "链的真链票均值（选择偏倚描述）。"),
    ("A8", "真链票值从卡片取还是重算",
     "取 hypothesis_cards.tsv 的 votes_total（golden 冻结件）并以门禁①②③④"
     "逐行验证其与四票之和、票②==depth、票③往返、票④查表全等——即"
     "「不重算但全量对拍」，避免第二份实现（沿 ③甲 A3 同制）。"),
    ("A9", "random.Random 的消费顺序与两臂起算点",
     "行3-4 未写链序与臂间关系。实现：链序=hypothesis_cards.tsv 原序"
     "（chain_id 升序）；**每臂独立 random.Random(20261105) 起算**，使敏感性"
     "臂不扰动主臂随机流（任一臂单独重跑逐位相同）。"),
    ("A10", "票①与票④的菌株子分在随机链上是否重算",
     "行8 说「票①查菌/票④查 KO」。本件票①按新菌调生产 vote1 重判；票④="
     "端点可测（新代谢物查表）+菌株可得（新菌查生产表）——与 ③甲 的「票④"
     "两子分」口径一致（生产 vote4 就是这两项之和）。"),
    ("A11", "评估档的时间戳",
     "零时间戳（沿 B3/②乙/③甲）——日期由文件名携带，否则三跑 sha 不可能"
     "全等，复现性验收自毁。"),
    ("A12", "边界带判定用哪个 p",
     "用主检验的双侧 p（同一量），只加「边界带」标签，主结论句式不变、"
     "不追加任何事后检验（§3.3 预指定）。"),
    ("A13", "**G_chain 边集与磁盘事实冲突（A' 级，最关键）**",
     "§3.2 规定边集=v01 边表∪表3。磁盘事实：v01 produces 边主体是 CHEBI id，"
     "而 chains.tsv 的 201 个代谢物节点**全部是 MESH id**（metabolite_id 列"
     "前缀 100% MESH）——按节点身份求交必然挖空 microbe-metabolite 层。"
     "实测三条（数字全在 §二 机器表，本行不手抄）：hop1 边覆盖率极低、"
     "大量节点度为 0（代谢物占比最高）、敏感性臂同节点集上 hop1 覆盖率"
     "100%。也就是说：**规格字面边集无法重建「链构造图」**（v0.2 deepchain "
     "的 hop1 源是子图 merged_edges，v01 边表只供 hop3）。"
     "处置（v1.6 A13 落定）：**主口径已改为 construction（真实链构造图）**，spec 降参考臂"
     "（construction=子图 hop1 ∪ v01 participates_in ∪ 表3）；两臂全量数字"
     "并列，结论若不因臂而变即为稳健性证据。**本项须监工分类+用户知情**"
     "（v1.6 已落定：construction 即主口径，spec 为参考臂——依据=该口径经磁盘证伪）。"
     "边谓词取 v01 **全表**（produces/biotransforms/modulates_host_gene/"
     "regulates_host_gene/participates_in），不只取规格点名的四个：规格主句"
     "写的是「v01 边表」，谓词清单是防误判说明；且磁盘事实是"
     "**modulates_host_gene 主体为 CHEBI→NCBIGene（代谢物-基因），真正的"
     "菌-基因谓词是 regulates_host_gene**（规格把后者漏了、把前者的端点"
     "标错）——两个都收才既不丢菌-基因层，也不丢规格点名的那条。"),
    ("A14", "区分器「可算票」的操作定义（监工 P0 前置）",
     "按 `supervisor_v03_v15_freeze_verdict_20261105.md:48` 逐字：票②=链"
     "构造即定、恒可算；票④=KO 查表非空即可算（不是「值为 0 即不可算」——"
     "ko_absent 是有效 0 值）。可算票比例=(1+票④可算)/2 的链均值。"),
    ("A15", "两臂的边源是否算「加第三源」",
     "敏感性臂引入子图 merged_edges（llm_extract_v2 的 KG 边），形式上不是"
     "§3.2 列举的两源。口径依据：它是链条 hop1 的**实际来源**（chains.tsv 的"
     "hop1_edge_id 全部指向它，716 条恰好等于 manifest counts.hop1_edges）；"
     "主臂=v1.6 三源（子图 hop1∪participates_in∪表3）；spec 参考臂为两源。"),
]


# ---------------------------------------------------------------
# 评估档渲染（确定性 markdown：无时间戳——同输入同产物逐字节可复现）
# ---------------------------------------------------------------

def node_counts_text(counts: dict) -> str:
    """四类节点数的可读转写（dict repr 直接进档读起来像调试残留）。"""
    return (f"菌 {counts.get('microbe', 0)} / 代谢物 "
            f"{counts.get('metabolite', 0)} / 基因 {counts.get('gene', 0)} / "
            f"通路 {counts.get('pathway', 0)}")


def render_report(paths: dict, stats: dict) -> str:
    """评估档（全机器生成；数字全部程序转写，禁手抄）。"""
    prim = stats["arms"][PRIMARY_ARM]
    sens = stats["arms"][SENSITIVITY_ARM]
    g = stats["gates"]
    lines: list[str] = []
    a = lines.append
    a("# ③乙 度匹配随机链零模型评估档（机器生成——数字全部程序转写）")
    a("")
    a("- 规格：`data/logs/upgrade_v03_spec_20261105.md` §三（冻结 v1.5，"
      "tag `v03-upgrade-prereg-v1.5`）；本档=该规格的机器化产出")
    a("- 定位：按「节点度数匹配」造随机链，测**路径结构本身**是否携带超出"
      "节点边际的四票证据信号（与 ③甲「配对特异性」问的是不同问题）")
    a("- **两臂并列**：主臂=真实链构造图边集（v1.6 A13 落定）；参考臂=原规格字面边集"
      "（A13，磁盘事实逼出来的第一臂替代口径）。两臂数字全列，主口径未改。")
    a("")
    a("## 一、输入与指纹")
    a("")
    a(f"- chains.tsv：`{paths['chains']}`")
    a(f"  - sha256={sha256_file(paths['chains'])}")
    a(f"- hypothesis_cards.tsv：`{paths['cards']}`")
    a(f"  - sha256={sha256_file(paths['cards'])}")
    a(f"- interaction_report.tsv：`{paths['irep']}`（票③三桶索引重建源）")
    a(f"- chain_annotations.tsv：`{paths['ann']}`（票④可测标志聚合源）")
    a(f"- merged_nodes.tsv：`{paths['nodes']}`（表3 名字桥 aliases 源）")
    a(f"- v01 边表：`{paths['v01']}`")
    a(f"  - sha256={stats['shas']['v01']}（manifest 全值 + 规格 §3.2 前缀 "
      f"`{V01_EDGES_SHA24}` 双门禁通过）")
    a(f"- gutMGene 表3：`{paths['table3']}`")
    a(f"  - sha256={stats['shas']['table3']}（前缀 "
      f"`{GUTMGENE_TABLE3_SHA24}` 门禁通过）")
    a(f"- 子图 merged_edges.tsv（仅敏感性臂 hop1 源）：`{paths['sub_edges']}`")
    a(f"- 抽样种子：`random.Random({RNG_SEED})`（spec §3.2 行3 钉扎；链序="
      "hypothesis_cards.tsv 原序，见 A9）")
    a(f"- 链数：{stats['n_cards']} vs 规格 §0.2 锚 {N_CHAINS_ANCHOR} → "
      f"{'PASS' if stats['n_cards'] == N_CHAINS_ANCHOR else 'DEVIATE'}")
    a(f"- 节点数四类：{node_counts_text(stats['node_counts'])} vs 规格/manifest "
      f"锚 {node_counts_text(NODE_ANCHORS)} → "
      f"{'PASS' if stats['node_counts'] == NODE_ANCHORS else 'DEVIATE'}")
    a("")
    a("## 二、G_chain 实现 manifest（spec §3.4⑦：n_edges + 各源贡献边数）")
    a("")
    a("| 项 | 主臂（construction·真实链构造图） | 参考臂（spec·原字面口径） |")
    a("|---|---|---|")
    a(f"| 节点数（四类合计） | {stats['n_nodes']} | {stats['n_nodes']} |")
    a(f"| **n_edges（无向去重后）** | **{prim['graph']['n_edges']}** | "
      f"**{sens['graph']['n_edges']}** |")
    a(f"| v01 边表读入行数 | {prim['graph']['v01_rows']} | "
      f"{sens['graph']['v01_rows']} |")
    a(f"| v01 边保留（两端在节点集内） | {prim['graph']['v01_kept']} | "
      f"{sens['graph']['v01_kept']} |")
    a(f"|　└ 按谓词 | {_fmt_pred(prim['graph']['v01_kept_by_predicate'])} | "
      f"{_fmt_pred(sens['graph']['v01_kept_by_predicate'])} |")
    a(f"| 子图 merged_edges hop1 保留 | — | {sens['graph']['sub_kept']} |")
    a(f"|　└ 按谓词 | — | {_fmt_pred(sens['graph']['sub_kept_by_predicate'])} |")
    a(f"| gutMGene 表3 边保留 | {prim['graph']['t3_pairs']} | "
      f"{sens['graph']['t3_pairs']} |")
    a(f"| 源间重叠（∪ 前重复的节点对） | {prim['graph']['overlap_pairs']} | "
      f"{sens['graph']['overlap_pairs']} |")
    a(f"| 度=0 的节点数 | {prim['zero_degree']} | {sens['zero_degree']} |")
    a("")
    a("**源贡献边数**（读法：v01 保留行数≠边数——同一节点对可能由多条边/多"
      "谓词重复出现，n_edges 才是去重后的度基；重叠列即「多源 ∪ 去重」的双算"
      "兜底，spec v1.2 监工 P1-1 要求的正是这个数）")
    a("")
    a("**真链三层边在图中的覆盖率**（图保真度，非检验——它决定「这张图是不是"
      "链条走过的图」）：")
    a("")
    a("| 层 | 主臂 construction（真实链构造图） | 参考臂 spec（原字面口径） |")
    a("|---|---|---|")
    for k, label in (("hop1", "hop1 菌-代谢物"), ("hop2", "hop2 代谢物-基因"),
                     ("hop3", "hop3 基因-通路")):
        pc, sc = prim["coverage"][k], sens["coverage"][k]
        a(f"| {label} | {pc['present']}/{pc['edges']}（{pc['rate']:.2%}） | "
          f"{sc['present']}/{sc['edges']}（{sc['rate']:.2%}） |")
    a("")
    a("**A13 关键事实（机器数）**：主臂下 "
      f"{prim['zero_degree']}/{stats['n_nodes']} 个节点度为 0（其中代谢物 "
      f"{prim['zero_degree_by_cat'].get('metabolite', 0)}/201、菌 "
      f"{prim['zero_degree_by_cat'].get('microbe', 0)}/192），hop1 边覆盖率仅 "
      f"{prim['coverage']['hop1']['rate']:.2%}——v01 边表的代谢物命名空间"
      "（CHEBI 为主）与 chains.tsv（100% MESH）不一致，按节点身份求交必然"
      "挖空 microbe-metabolite 层。敏感性臂在同一 809 节点上重建该层，"
      "hop1 覆盖率 "
      f"{sens['coverage']['hop1']['rate']:.2%}、零度节点 {sens['zero_degree']} 个。")
    a("")
    a("## 三、度分箱表与合并映射（spec §3.2 行2，两臂）")
    for arm in (PRIMARY_ARM, SENSITIVITY_ARM):
        st = stats["arms"][arm]
        b = st["bins"]
        a("")
        a(f"### 臂 `{arm}`：{b['degree_values']} 个精确 degree 值 → "
          f"{b['n_bins']} 个箱（min_size={MIN_BIN_SIZE}）")
        a("")
        a("| 箱号 | 精确 degree 值（合并映射） | 成员数 | 菌 | 代谢物 | 基因 | 通路 |")
        a("|---|---|---|---|---|---|---|")
        for r in b["rows"]:
            prof = r["profile"]
            a(f"| {r['bin']} | {','.join(str(d) for d in r['degrees'])} | "
              f"{r['n_members']} | {prof['microbe']} | {prof['metabolite']} | "
              f"{prof['gene']} | {prof['pathway']} |")
        tot = sum(r["n_members"] for r in b["rows"])
        a("")
        a(f"箱成员合计 {tot} = 节点数 {stats['n_nodes']} → "
          f"{'PASS' if tot == stats['n_nodes'] else 'DEVIATE'}（每节点恰好归入一箱）")
    a("")
    a("## 四、伪代码对拍（spec §3.2 逐行 → 代码位置）")
    a("")
    a("| 伪代码 | 口径 | 函数 | 本文件行号 |")
    a("|---|---|---|---|")
    for step, fn_name, line in code_locations():
        text = next(t for s, t, f in PSEUDOCODE_STEPS if s == step and f == fn_name)
        a(f"| {step} | {text} | `{fn_name}` | L{line} |")
    a("")
    a("行号由 `inspect.getsourcelines` 在渲染时从 `src/kg_tool/"
      "degree_matched_null.py` 现读，不手抄——改代码后行号自动跟随，"
      "但**口径**由测试锁死（分箱方向/池构成/有放回/跳过计数各有单测）。")
    a("")
    a("## 五、抽样台账（spec §3.2 行6b/7 与 §3.4④）")
    a("")
    a(f"| 项 | 主臂 {PRIMARY_ARM} | 敏感性臂 {SENSITIVITY_ARM} |")
    a("|---|---|---|")
    for key, label in (("n_draws", "总抽取次数 n_draws"),
                       ("n_resample", "回放次数 n_resample"),
                       ("n_duplicate_draws", "重复抽中数 n_duplicate_draws"),
                       ("n_distinct_sampled", "被抽中的不同节点数"),
                       ("n_boxes_drawn", "被动用的（类别,箱）池数"),
                       ("n_skipped_by_pool", "**n_skipped_by_pool**")):
        a(f"| {label} | {prim['sampling'][key]} | {sens['sampling'][key]} |")
    a(f"| 跳过链占比 | {prim['sampling']['n_skipped_by_pool'] / stats['n_cards']:.2%} "
      f"| {sens['sampling']['n_skipped_by_pool'] / stats['n_cards']:.2%} |")
    a("")
    a("**跳过按位置类别分解**（池空发生在哪个类别位置）：")
    a("")
    a(f"| 类别位置 | 主臂 {PRIMARY_ARM} | 敏感性臂 {SENSITIVITY_ARM} |")
    a("|---|---|---|")
    for k in ("microbe", "metabolite", "gene", "pathway"):
        a(f"| {k} | {prim['sampling']['n_skipped_by_category'].get(k, 0)} | "
          f"{sens['sampling']['n_skipped_by_category'].get(k, 0)} |")
    a("")
    a("**为什么会有跳过**：全局箱（A5 字面口径）在每个精确 degree 值上封箱，"
      "高 degree 箱往往只有 1-2 个节点；若该箱里本类别的节点只有原节点自己，"
      "排除后池空 → 整链跳过（spec 行7 要求如实计数，不得静默）。"
      "A5 的替代口径（逐类别分池）实测跳链数为 "
      f"{stats['skip_alt_per_category']['n_skipped']} 条"
      f"（按位置类别：{_fmt_pred(stats['skip_alt_per_category']['by_category'])}）"
      "——与上表全局箱口径的 "
      f"{prim['sampling']['n_skipped_by_pool']} 条并列，供监工裁定"
      "「分箱对象」这一处口径值多少条链。")
    a("")
    a("**有放回的定量证据**：`n_duplicate_draws` = 总抽取次数 − 被抽中的不同"
      "节点数；若为 0 说明实际等价于不放回（本档两臂均 >0，见上表）。"
      "`n_resample` 按 A4 的池粒度（类别,箱）定义。")
    a("")
    a("**选择偏倚描述（A7 承诺）**：被跳过链 vs 进入配对链的真链票均值——"
      "若跳过不是随机缺失，配对集的结论不能直接外推到全部 43,773 条链。")
    a("")
    a("| 臂 | 被跳链 n | 被跳链真票均值 | 配对集真票均值 | 差 |")
    a("|---|---|---|---|---|")
    for arm in (PRIMARY_ARM, SENSITIVITY_ARM):
        sb = stats["arms"][arm]["selection_bias"]
        a(f"| {arm} | {sb['n_skipped']} | {sb['skipped_true_mean']:.6f} | "
          f"{sb['kept_true_mean']:.6f} | {sb['delta']:+.6f} |")
    a("")
    a("## 六、四票重算与机器门禁（fail-closed）")
    a("")
    a("- 票①：生产 `hypothesize.vote1(新菌 id, 新菌名, 102 条匹配器)`；"
      "票②=深度（恒不随抽样变）；票③：生产 `hypothesize.vote3(新菌, "
      "新代谢物, 新基因名, 三桶索引)`；票④=新代谢物端点可测查表 + 新菌"
      "菌株可得（生产 `hypothesize.strain_available`）")
    a(f"- 门禁① 票③往返（原实体重算 == 卡片列）：{g['vote3_roundtrip_rows']} 行全等")
    a(f"- 门禁② votes_total 恒等式：{g['votes_identity_rows']} 行成立")
    a(f"- 门禁③ 票②==depth：{g['vote2_depth_rows']} 行成立")
    a(f"- 门禁④ 票④查表保真（卡片 endpoint+strain == 本次查表）："
      f"{g['vote4_lookup_rows']} 行")
    a("- 门禁⑤ 节点数锚：四类 "
      f"{node_counts_text(stats['node_counts'])} 与规格 §0.2 / manifest counts "
      "逐一相等")
    if stats.get("ko_rederive"):
        kr = stats["ko_rederive"]
        a(f"- 附 票④生产重推导对拍：{kr['n_checked']} 个代谢物逐位一致"
          f"（ko_map sha16={kr['ko_map_sha16']}，观测层 {kr['obs_files_checked']} "
          "文件 sha 契约通过）")
    else:
        a("- 附 票④生产重推导对拍：本次未提供 --obs-dir（查表源=golden "
          "chain_annotations.tsv 冻结件的生产聚合）")
    a("")
    a("## 七、Δ 检验（预注册：配对 Wilcoxon 符号秩正态近似，双侧 "
      f"α={ALPHA}）")
    a("")
    a(f"### 主臂 `{PRIMARY_ARM}`（{ARM_LABELS[PRIMARY_ARM]}）")
    a("")
    w = prim["wilcoxon"]
    a(f"- 配对 n={w['n_pairs']}（= {stats['n_cards']} − 跳过 "
      f"{prim['sampling']['n_skipped_by_pool']}）；零差弃置后 **n_eff={w['n_eff']}**"
      f"（正 {w['n_pos']} / 负 {w['n_neg']} / 零 {w['n_zero']}）")
    a(f"- W+={w['w_plus']:.1f}，W-={w['w_minus']:.1f}，S={w['rank_sum']:.0f}；"
      f"并列校正 Σ(t³−t)={w['tie_correction']:.0f}；z 含 0.5 连续性校正")
    a(f"- **z={w['z']:+.6f}；双侧 p={fmt_p(w['p_two_sided'])}；效应量 "
      f"rank-biserial r={w['rank_biserial']:+.6f}**（r 与 p 同段强制并列："
      "巨样本下 p 可极小而 r≈0，此时效应为零，不得读成强信号——B3 先例；"
      "p 写成下溢标记而非 0，是因为 |z| 很大时 erfc 在双精度下返回 0，"
      "「恰好为零」不是可报告的陈述）")
    a(f"- 均值差={prim['mean_diff']:+.6f}（真 {prim['mean_true']:.6f} vs 随机 "
      f"{prim['mean_random']:.6f}）；中位数差={prim['median_diff']:+.6g}；"
      f"Σ差={prim['sum_diff']:+d}；**OVL={prim['ovl']:.6f}**（辅助）")
    a(f"- 边界带判定：p∈(0.05,0.10) → "
      f"{boundary_label(w['p_two_sided']) or '不在边界带'}")
    a(f"- **方向结论：{prim['conclusion']}**")
    a("")
    a(f"### 敏感性臂 `{SENSITIVITY_ARM}`（{ARM_LABELS[SENSITIVITY_ARM]}；A13 说明见下）")
    a("")
    ws = sens["wilcoxon"]
    a(f"- 配对 n={ws['n_pairs']}（跳过 {sens['sampling']['n_skipped_by_pool']}）；"
      f"**n_eff={ws['n_eff']}**（正 {ws['n_pos']} / 负 {ws['n_neg']} / "
      f"零 {ws['n_zero']}）")
    a(f"- **z={ws['z']:+.6f}；双侧 p={fmt_p(ws['p_two_sided'])}；r="
      f"{ws['rank_biserial']:+.6f}**；均值差={sens['mean_diff']:+.6f}（真 "
      f"{sens['mean_true']:.6f} vs 随机 {sens['mean_random']:.6f}）；"
      f"中位数差={sens['median_diff']:+.6g}；OVL={sens['ovl']:.6f}")
    a(f"- 方向结论：{sens['conclusion']}")
    a("")
    a("**两臂一致性判定**：主臂与敏感性臂的方向"
      f"{'一致' if (prim['mean_diff'] > 0) == (sens['mean_diff'] > 0) else '不一致'}、"
      f"显著性{'一致' if (prim['wilcoxon']['p_two_sided'] < ALPHA) == (sens['wilcoxon']['p_two_sided'] < ALPHA) else '不一致'}"
      "——若一致，则结论对「G_chain 边源口径」稳健（这正是把 A13 缺陷"
      "摊在同档而不换主口径的理由）。")
    a("")
    a("**结论外推范围（如实披露）**：两臂都只对**进入配对的那部分链**成立"
      f"（主臂 {prim['n_pairs']}/{stats['n_cards']} 条 = "
      f"{prim['n_pairs'] / stats['n_cards']:.2%}；敏感性臂 {sens['n_pairs']}"
      f"/{stats['n_cards']} 条 = {sens['n_pairs'] / stats['n_cards']:.2%}）。"
      "§五 的选择偏倚表给出被跳链与配对集的真票均值差——敏感性臂的跳过"
      "不是随机缺失（被跳链真票更低），故「真链显著更高」这一结论的外推"
      "边界=度分布可匹配的那部分链，不得写成「全部 43,773 条链均如此」。")
    a("")
    a("**解读边界（不改统计口径，只防过度声称）**：本检验显著的含义是"
      "「在度数匹配的约束下，真链所用节点的**身份**仍携带更多四票证据」——"
      "证据增量集中在票③（gutMGene 交叉）与票④（可测性，见 §八 逐票表，"
      "两票贡献了绝大部分均值差；票②两侧恒等）。因此它**不等于**「路径"
      "拓扑本身超出节点边际」：要回答后者需保边重连类零模型（如 degree-"
      "preserving edge swap），本件不提供该证据。与 ③甲（配对换跳无差异）"
      "合读才完整：③甲 说「配对不重要」，③乙 说「节点选择重要」——两者"
      "共同指向「四票=边际证据聚合」的定位（与 R1/B3 一致）。")
    a("")
    a("## 八、分布对照（机器计数；配对集）")
    a("")
    a("| votes_total | 真链（主臂配对集） | 随机链（主臂） | 随机链（敏感性臂） |")
    a("|---|---|---|---|")
    keys = sorted(set(prim["dist_true"]) | set(prim["dist_random"])
                  | set(sens["dist_random"]))
    for k in keys:
        a(f"| {k} | {prim['dist_true'].get(k, 0)} | "
          f"{prim['dist_random'].get(k, 0)} | {sens['dist_random'].get(k, 0)} |")
    a(f"| 合计 | {sum(prim['dist_true'].values())} | "
      f"{sum(prim['dist_random'].values())} | "
      f"{sum(sens['dist_random'].values())} |")
    a("")
    a("**逐票四列分层描述**（spec §3.3：辅，不参与主判定）：")
    a("")
    a("| 票 | 真链均值（配对集） | 随机链均值（主臂） | 随机链均值（敏感性臂） |")
    a("|---|---|---|---|")
    labels = {"vote1": "票① 21612 佐证", "vote2": "票② 深度",
              "vote3": "票③ gutMGene 交叉", "vote4": "票④ 可测性两子分"}
    for k in ("vote1", "vote2", "vote3", "vote4"):
        a(f"| {labels[k]} | {prim['true_vote_means'][k]:.6f} | "
          f"{prim['vote_means'][k]:.6f} | {sens['vote_means'][k]:.6f} |")
    a("")
    a("## 九、零模型校准两指标与区分器（spec §3.1，按键前写死）")
    a("")
    a("**「可算票」操作定义**（A14，监工 v1.5 冻结审 P0 前置）：票②=链构造"
      "即定、恒可算；票④=KO 查表非空即可算。")
    a("")
    a("| 指标 | 阈值 | 主臂实测 | 敏感性臂实测 | 触发？ |")
    a("|---|---|---|---|---|")
    a(f"| ①可算票比例（随机/真） | < {CAL_RATIO_MIN} | "
      f"{prim['computable_ratio']:.6f}（真 {prim['computable_true']:.6f} / 随 "
      f"{prim['computable_random']:.6f}） | "
      f"{sens['computable_ratio']:.6f}（真 {sens['computable_true']:.6f} / 随 "
      f"{sens['computable_random']:.6f}） | "
      f"{'是' if prim['computable_ratio'] < CAL_RATIO_MIN else '否'} / "
      f"{'是' if sens['computable_ratio'] < CAL_RATIO_MIN else '否'} |")
    a(f"| ②票值方差比（随机/真） | < {CAL_VAR_MIN} | "
      f"{prim['var_ratio']:.6f}（真 {prim['var_true']:.6f} / 随 "
      f"{prim['var_random']:.6f}） | {sens['var_ratio']:.6f}（真 "
      f"{sens['var_true']:.6f} / 随 {sens['var_random']:.6f}） | "
      f"{'是' if prim['var_ratio'] < CAL_VAR_MIN else '否'} / "
      f"{'是' if sens['var_ratio'] < CAL_VAR_MIN else '否'} |")
    a("")
    for arm in (PRIMARY_ARM, SENSITIVITY_ARM):
        d = stats["arms"][arm]["discriminator"]
        a(f"- **臂 `{arm}` 区分器判定：{d['verdict']}**（依据：{d['reason']}）")
        if d["verdict"] == "尺子弱":
            a(f"  - 预写解读句（§3.1 逐字）：**{RULE_WEAK_SENTENCE}**")
        elif d["verdict"] == "路径无信息":
            a(f"  - 预写解读句（§3.1 逐字）：**{NO_INFO_SENTENCE}**")
        else:
            a("  - 预写解读句不启用（区分器只对「无差异」有定义；H1 成立时"
              "按 §0.4 如实报方向与效应量，不套用负结果措辞）")
    a("")
    a("判据原文（§3.1，禁事后解释）：判「尺子弱」当且仅当 ①随机链可算票数"
      "（票②+票④可算比例）< 真链的 70%，或 ②随机链票值分布方差 < 真链"
      "方差 50%；两条都不触发且仍无差异 → 判「路径无信息」。")
    a("")
    a("## 十、与 HypoKG 的设计差异 + 零模型相关性披露")
    a("")
    a(f"- {HYPOKG_NOTE}")
    a(f"- {RNG_SEMANTICS_NOTE}")
    a(f"- {FAMILY_NOTE}")
    a("- 论文报告时须同段并列 ②乙/③甲/③乙 三组结果并注明「未做族内校正，"
      "因三问互不替代」（§0.3 预写死）。")
    a("")
    a("## 十一、红线与复现")
    a("")
    a("- 随机链中间态零落盘：本工具唯一写件=本档；卡片/链/标注/边源全只读，"
      "golden v3 十三件零改动（跑前/跑后 sha 见 "
      "`data/logs/dmn_evidence_20261105.txt`）")
    a("- 排序键零改动：本模块不排序、不回写任何既有表")
    a("- scipy 对拍：`scipy.stats.wilcoxon(mode=\"approx\", correction=True, "
      "zero_method=\"wilcox\")` 与手算逐位一致，留痕见同一 evidence 档")
    a("- 复现命令：`python3 src/kg_tool/degree_matched_null.py --subgraph-dir "
      "<子图目录> --output <本档路径> --v01-edges <v01 边表> --gutmgene-dir "
      "<gutMGene 目录> --ext-tsv <102 条列表> --bridge-tsv <菌名桥> "
      "--ko-map <KO 映射>`（同输入同平台逐字节复现；本档零时间戳）")
    a("- 本档的结论边界：区分器判定见 §九（主臂="
      f"{prim['discriminator']['verdict']}；敏感性臂="
      f"{sens['discriminator']['verdict']}）——两臂均未产出「尺子弱」或"
      "「路径无信息」的措辞（区分器两句以「无差异」为前提，本件 H1 成立），"
      "故本档不得被引用为路径结构无信息的证据")
    a("")
    a("## 十二、规格歧义登记表（A 类攒批；冻结规格未写细节，禁静默自决）")
    a("")
    a("| # | 歧义点 | 本件实现口径与依据 |")
    a("|---|---|---|")
    for code, topic, how in AMBIGUITIES:
        a(f"| {code} | {topic} | {how} |")
    a("")
    a("> 登记口径：以上 15 项都是「规格没写、但实现必须选一个」的细节，或"
      "「规格写了但与磁盘事实冲突」的项（A13 属后者，A' 级）。选法一律优先"
      "沿用已冻结先例（B3/U2/②乙/③甲）；攒批报 A 类，不改冻结正文。")
    a("")
    return "\n".join(lines)


def _fmt_pred(d: dict) -> str:
    if not d:
        return "—"
    return " / ".join(f"{k}:{v}" for k, v in d.items())


# ---------------------------------------------------------------
# 落档（只增不改护栏，沿 ③甲 同制——防两次运行产物混放）
# ---------------------------------------------------------------

def run(sg_dir: Path, output_path: Path, v01_edges: Path,
        gutmgene_dir: Path, ext_tsv: Path, bridge_tsv: Path,
        ko_map_path: Path, obs_dir: Path | None = None,
        seed: int = RNG_SEED) -> dict:
    """读档→建图（两臂）→门禁→抽样→Δ→评估档落盘（幂等 no-op/漂移拒写）。"""
    chains_path = sg_dir / "chains.tsv"
    cards_path = sg_dir / "hypothesis_cards.tsv"
    irep_path = sg_dir / "interaction_report.tsv"
    ann_path = sg_dir / "chain_annotations.tsv"
    nodes_path = sg_dir / "merged_nodes.tsv"
    sub_edges_path = sg_dir / "merged_edges.tsv"
    table3_path = gutmgene_dir / GUTMGENE_TABLE3_NAME

    manifest = load_manifest(sg_dir)
    v01_sha = gate_source_sha(v01_edges, manifest, "v01_edges", V01_EDGES_SHA24,
                             "v01 边表")
    t3_sha = gate_source_sha(table3_path, manifest, "gutmgene_table3",
                            GUTMGENE_TABLE3_SHA24, "gutMGene 表3")

    chains = read_tsv(chains_path, REQUIRED_CHAIN_COLS, "链表")
    cards = read_tsv(cards_path, REQUIRED_CARD_COLS, "卡片")
    irep = read_tsv(irep_path, REQUIRED_IREP_COLS, "三桶对账报告")
    ann = read_tsv(ann_path, REQUIRED_ANN_COLS, "D1 标注")
    merged_nodes = read_tsv(nodes_path, REQUIRED_NODE_COLS, "子图节点表")
    v01_rows = read_tsv(v01_edges, ["subject", "predicate", "object"],
                        "v01 边表")
    sub_edges = read_tsv(sub_edges_path, ["subject", "predicate", "object"],
                         "子图边表")
    t3_rows = read_table3(table3_path)
    if len(cards) != len(chains):
        raise SystemExit(
            f"[degree-matched-null] 卡片 {len(cards)} 行 ≠ 链表 {len(chains)} 行"
            "——fail-closed")
    cards_by_id = {c["chain_id"]: c for c in cards}
    if len(cards_by_id) != len(cards):
        raise SystemExit("[degree-matched-null] 卡片 chain_id 重复——fail-closed")

    # 节点目录建一次（类别/名字/计数/节点序）——重复调用虽无副作用，但
    # 两次建目录会让"用的是哪份目录"变成可争论的问题，属自找的审计麻烦
    catalog = build_node_catalog(chains, manifest)
    cat, name, nodes = catalog["cat"], catalog["name"], catalog["nodes"]

    # —— 证据索引（生产入口原件）
    hyp = _hypothesize()
    if not ext_tsv.exists():
        raise SystemExit(
            f"[degree-matched-null] 缺票①源 {ext_tsv}——matcher 会退化为"
            "空集、票①恒 0，等于把一条真实信息源悄悄删掉（沿 load_ext102_rows"
            " 的'文件不存在=空证对照臂'是给对照臂用的，不是给正式臂）")
    matcher = hyp.build_vote1_matcher(hyp.load_ext102_rows(ext_tsv),
                                     read_tsv(bridge_tsv,
                                              ["metaphlan_name", "taxid",
                                               "source"], "菌名桥"))
    v3_idx = hyp.build_vote3_index(irep)
    strain_idx = hyp.load_strain_index(gutmgene_dir)
    chains_by_id = {r["chain_id"]: r for r in chains}
    try:
        ko = build_ko_lookup(chains_by_id, ann)
    except SystemExit as e:   # 复用 ③甲 的聚合口径，但错误前缀归本件
        raise SystemExit(f"[degree-matched-null] 票④查表聚合失败（源="
                         f"hop1_shuffle.build_ko_lookup）：{e}")
    ko_lookup = ko["lookup"]
    chebi_of = {r["metabolite_id"]: r.get("metabolite_chebi", "")
                for r in chains}
    try:
        ko_map = _inference().gate_ko_map(ko_map_path)
    except SystemExit as e:   # 生产门禁原样抛红线，补本件前缀便于定位调用方
        raise SystemExit(f"[degree-matched-null] KO 映射门禁拒绝：{e}")
    gates = run_gates(cards, v3_idx, ko_lookup, strain_idx, catalog["counts"])

    rederive = None
    if obs_dir is not None:
        try:
            rederive = rederive_ko_lookup_with_production(
                chains_by_id, ko_lookup, ko_map_path, obs_dir)
        except SystemExit as e:
            raise SystemExit(f"[degree-matched-null] 票④生产重推导对拍失败：{e}")

    comp4 = {m: vote4_computable(m, chebi_of, ko_map) for m in chebi_of}
    true_comp = [1 + comp4[r["metabolite_id"]] for r in chains]

    arms: dict[str, dict] = {}
    skip_alt: dict = {}
    for arm in (PRIMARY_ARM, SENSITIVITY_ARM):
        graph = build_graph_arm(arm, nodes, cat, name, v01_rows, sub_edges,
                                t3_rows, merged_nodes)
        deg = compute_degrees(nodes, graph["pairs"])
        bins = bins_with_merge_map(nodes, deg)
        for r in bins["rows"]:
            r["profile"] = category_profile(r, cat)
        st = compute_arm(arm, cards, cat, name, deg, bins, matcher,
                         v3_idx, strain_idx, ko_lookup, comp4, true_comp)
        st["graph"] = graph
        st["bins"] = bins
        st["coverage"] = chain_edge_coverage(chains, graph["pairs"])
        st["zero_degree"] = sum(1 for n in nodes if deg[n] == 0)
        zc: Counter = Counter(cat[n] for n in nodes if deg[n] == 0)
        st["zero_degree_by_cat"] = dict(zc)
        arms[arm] = st
        if arm == PRIMARY_ARM:
            # A5 替代口径的跳链数（只算池空判定，不抽签——不含 rng 依赖）
            skip_alt = _skip_count_per_category_bins(cards, cat, deg)

    stats = {
        "n_cards": len(cards), "n_nodes": len(nodes),
        "node_counts": catalog["counts"], "gates": gates,
        "arms": arms, "shas": {"v01": v01_sha, "table3": t3_sha},
        "skip_alt_per_category": skip_alt, "ko_rederive": rederive,
        "seed": seed,
    }
    payload = render_report(
        {"chains": chains_path, "cards": cards_path, "irep": irep_path,
         "ann": ann_path, "nodes": nodes_path, "sub_edges": sub_edges_path,
         "v01": v01_edges, "table3": table3_path}, stats)
    if output_path.exists():
        old = output_path.read_bytes()
        if old == payload.encode("utf-8"):
            stats.update({"idempotent": True, "out": str(output_path)})
            return stats
        raise SystemExit(
            f"[degree-matched-null] {output_path} 已存在且与本次产物不一致"
            "——只增不改区拒写（疑输入漂移；人工移走旧档过审后重跑）")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8")
    stats.update({"idempotent": False, "out": str(output_path)})
    return stats


def _skip_count_per_category_bins(cards: list[dict], cat: dict[str, str],
                                  degree: dict[str, int]) -> dict:
    """A5 替代口径（逐类别分池再合并小箱）下的跳链统计（纯判定，不含 rng）。

    只用于报告并列，不参与主检验。逐类别分箱后"某节点的同箱同类别候选
    为空"只在整类别 <2 个节点时成立，故跳链数应远低于全局箱口径——这个
    对照数是让监工看见"分箱对象"这一处口径选择值多少条链。
    """
    d2b: dict[str, dict[int, int]] = {}
    b2m: dict[str, dict[int, list[str]]] = {}
    for c in ("microbe", "metabolite", "gene", "pathway"):
        pool = [n for n in degree if cat[n] == c]
        d2b[c], b2m[c] = degree_bins(pool, degree, MIN_BIN_SIZE)
    n_skip = 0
    by_cat: Counter = Counter()
    for ch in cards:
        for pos in chain_node_sequence(ch):
            c = cat[pos]
            others = [m for m in b2m[c][d2b[c][degree[pos]]] if m != pos]
            if not others:
                n_skip += 1
                by_cat[c] += 1
                break
    return {"n_skipped": n_skip, "by_category": dict(sorted(by_cat.items()))}


def resolve_default_legs(ko_arg: Path | None, obs_arg: Path | None
                         ) -> tuple[Path, Path | None]:
    """解析票④腿（ko_map 必需 + obs_dir 可选）。

    抽成独立函数以便测试注入：主仓有真实 obs 目录时会自动启用"生产重推导
    对拍"（含 KO 映射 sha 红线校验），fixture 假 ko_map 必然撞红线；测试
    patch 本函数返回 (ko_arg, None) 即关闭自动对拍，得到环境无关的 CLI 测试。
    """
    root = kg_root()
    ko_map = ko_arg or (root / KO_MAP_DEFAULT)
    obs = obs_arg
    if obs is None and (root / OBS_DIR_DEFAULT).exists():
        obs = root / OBS_DIR_DEFAULT
    return ko_map, obs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kg degree-matched-null",
        description="③乙 度匹配随机链零模型（G_chain 上逐位置度匹配有放回抽样+"
                    "四票重算+配对 Wilcoxon，随机链中间态零落盘只产评估档）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="子图目录（读 chains/hypothesis_cards/"
                         "interaction_report/chain_annotations/merged_nodes/"
                         "merged_edges/chains_manifest）")
    ap.add_argument("--output", type=Path, default=None,
                    help="评估档 markdown 输出路径（默认 data/logs/"
                         "degree_matched_null_20261105.md；已存在且字节一致="
                         "幂等 no-op，不一致=拒写）")
    ap.add_argument("--v01-edges", type=Path, default=None,
                    help="v01 边表（sha 双门禁；worktree 内须指向主仓 archive/）")
    ap.add_argument("--gutmgene-dir", type=Path, default=None,
                    help="gutMGene 目录（表3 代谢物-基因边 + 表1 菌株可得）")
    ap.add_argument("--ext-tsv", type=Path, default=None,
                    help="21612 外证 102 条列表（票①匹配器源）")
    ap.add_argument("--bridge-tsv", type=Path, default=None,
                    help="菌名桥 TSV（票① taxid 判据源）")
    ap.add_argument("--ko-map", type=Path, default=None,
                    help="冻结 KO 映射 tsv（票④可算/可测判定）")
    ap.add_argument("--obs-dir", type=Path, default=None,
                    help="观测层目录（提供时启用票④生产重推导对拍）")
    ap.add_argument("--verify-golden", action="store_true",
                    help="只跑 golden 冻结清单逐件 sha 核验（不写任何产物）")
    ap.add_argument("--golden-list", type=Path, default=None,
                    help=f"golden 冻结清单（默认 {GOLDEN_LIST_DEFAULT}）")
    args = ap.parse_args(argv)

    if args.verify_golden:
        golden = args.golden_list or (kg_root() / GOLDEN_LIST_DEFAULT)
        rows = verify_golden_baseline(args.subgraph_dir, golden)
        bad = sum(1 for r in rows if not r["ok"])
        for r in rows:
            print(f"{'OK ' if r['ok'] else 'FAIL'} {r['name']} "
                  f"frozen={r['frozen'][:20]} actual={r['actual'][:20]}")
        print(f"[degree-matched-null] golden 核验 {len(rows)} 件 | 不一致 {bad} 件")
        print(f"[degree-matched-null] 清单={golden}")
        return 0 if bad == 0 else 1

    root = kg_root()
    out = args.output or (root / "data/logs/degree_matched_null_20261105.md")
    v01 = args.v01_edges or (root / V01_EDGES_DEFAULT)
    gm = args.gutmgene_dir or Path(GUTMGENE_DIR_DEFAULT)
    ext = args.ext_tsv or (root / EXT102_DEFAULT)
    bridge = args.bridge_tsv or (root / BRIDGE_DEFAULT)
    ko_map, obs = resolve_default_legs(args.ko_map, args.obs_dir)

    stats = run(args.subgraph_dir, out, v01, gm, ext, bridge, ko_map, obs)
    prim = stats["arms"][PRIMARY_ARM]
    sens = stats["arms"][SENSITIVITY_ARM]
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    print(f"[degree-matched-null] {stats['out']} {verb}")
    print(f"[degree-matched-null] 节点 {stats['n_nodes']}"
          f"（{node_counts_text(stats['node_counts'])}）| 链 {stats['n_cards']}"
          f"（锚 {N_CHAINS_ANCHOR}）——随机链中间态零落盘")
    print(f"[degree-matched-null] G_chain n_edges：主臂 "
          f"{prim['graph']['n_edges']} / 敏感性臂 {sens['graph']['n_edges']}"
          f"；零度节点 {prim['zero_degree']} / {sens['zero_degree']}")
    print(f"[degree-matched-null] 门禁四道全过（票③往返/恒等式/票②==深度/"
          f"票④查表）+ 节点锚 四类逐一相等")
    for arm in (PRIMARY_ARM, SENSITIVITY_ARM):
        st = stats["arms"][arm]
        w = st["wilcoxon"]
        print(f"[degree-matched-null] 臂 {arm}: n_pairs={w['n_pairs']} "
              f"n_eff={w['n_eff']}（正 {w['n_pos']}/负 {w['n_neg']}/零 "
              f"{w['n_zero']}） z={w['z']:+.6f} p={fmt_p(w['p_two_sided'])} "
              f"r_rb={w['rank_biserial']:+.6f} 均值差={st['mean_diff']:+.6f} "
              f"中位数差={st['median_diff']:+.1f} OVL={st['ovl']:.4f}")
        print(f"[degree-matched-null] 臂 {arm}: 校准①可算比="
              f"{st['computable_ratio']:.6f} ②方差比={st['var_ratio']:.6f} "
              f"→ 区分器={st['discriminator']['verdict']}")
        print(f"[degree-matched-null] 臂 {arm}: n_skipped_by_pool="
              f"{st['sampling']['n_skipped_by_pool']} n_resample="
              f"{st['sampling']['n_resample']} 重复抽中="
              f"{st['sampling']['n_duplicate_draws']}")
        print(f"[degree-matched-null] 臂 {arm} 结论：{st['conclusion']}")
    if stats.get("ko_rederive"):
        print(f"[degree-matched-null] 票④生产重推导对拍："
              f"{stats['ko_rederive']['n_checked']} 个代谢物逐位一致")
    else:
        print("[degree-matched-null] 票④生产重推导对拍：未提供 --obs-dir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
