"""③甲 hop1（菌↔代谢物）换跳洗牌（v0.3 四件之一；spec §二，冻结 v1.5，逐字遵循）。

定位与红线：
- 只读主子图四表（chains / hypothesis_cards / interaction_report /
  chain_annotations——后三者是 golden 冻结件），唯一写件=评估档 markdown
  （本工具 --output）。**洗牌卡中间态零落盘**（spec §2.4）：不产卡文件，
  卡片/索引/主链产物全只读，防两次运行产物混放（AGENTS.md 血泪史 #5）。
- 不碰 06_tool 生产文件（只按路径 import 复用）：票③规则来自
  `hypothesize.build_vote3_index/vote3` 原件，票④ KO 可测性推导来自
<<<<<<< HEAD
  `inference.annotate_ko` 原件（评审硬门禁：禁私搭查询——复制粘贴一份规则
=======
  `inference.annotate_ko` 原件（监工硬门禁：禁私搭查询——复制粘贴一份规则
>>>>>>> origin/main
  就等着两边漂移）。
- 四票排序键零改动：本模块不排序、不回写任何既有表。
- 包内零第三方依赖（纯标准库）——Wilcoxon 正态近似手算（沿 B3 冻结实现），
  scipy 只在对拍留痕时**外部**调用（见 evidence 档，不进包内运行路径）。

洗牌口径（spec §2.2，v1.1 勘误后版本）：
- **菌列固定**，代谢物列 `random.Random(20261105)` 在**全库 43,773 链内重排**
  （多重集不变）；**不做分层**——磁盘事实：depth=1 的 537 条链 metabolite_id
  全部有值（空的是 hop2_gene/hop3_pathway），代谢物列全库非空，全部 43,773
  链均参与（首版"无代谢物链不参与"系把 B3"无基因行不动"的分层错移过来）。
<<<<<<< HEAD
- 重排后**票③与票④均按新代谢物重查重算**（评审 P0-2 指定联动重算——固定
=======
- 重排后**票③与票④均按新代谢物重查重算**（监工 P0-2 指定联动重算——固定
>>>>>>> origin/main
  票④会人为锁死代谢物信息，与检验目标矛盾）；票①（查菌）与票②（深度）
  查询输入不动 → 逐位不动。

为什么选 hop1 而不是基因列（spec §2.2 的论证改写）：
- 基因域仅 197 个且 4.3 万链共用 → B3 基因置换中 1,917 行换回原样，**尺子
  过弱**；代谢物置换同时扰动**票③两桶**（gutMGene 交叉：菌或代谢物任一命中
  即加分）**与票④**（KO 可测性由链代谢物 chebi 推导），而基因置换只扰动票③。

统计量（spec §2.3，预指定禁改）：
- 配对 Wilcoxon 符号秩，**双侧 α=0.05**；n=43,773（零差行弃置，n_eff 须报）；
  正态近似 + 并列校正 + 连续性校正（沿 B3 骨架）。
- 效应量 rank-biserial r；另报均值差与中位数差。
<<<<<<< HEAD
- **效应量必须与 p 同段报告**（评审风险提醒：巨大样本下 p 极易"显著到没
=======
- **效应量必须与 p 同段报告**（监工风险提醒：巨大样本下 p 极易"显著到没
>>>>>>> origin/main
  意义"，防止 "p=1e-200 但 r≈0" 被误读为强信号——B3 先例正是 r≈0）。
- 边界带 p∈(0.05,0.10)：报"边界带"标签、主结论句式不变、**不追加任何事后
  检验**（不换检验、不加样本、不改零模型）。

规格歧义登记（A1-A12，见评估档 §十二）：冻结规格未写的实现细节一律登记，
不静默自决（冻结后修改规则=过审制 A/A' 勘误留痕）。
"""
import argparse
import csv
import hashlib
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# ---------------------------------------------------------------
<<<<<<< HEAD
# 冻结常量（spec §二，逐字）——改这里=过审（评审+用户）。
=======
# 冻结常量（spec §二，逐字）——改这里=过审（监工+用户）。
>>>>>>> origin/main
# ---------------------------------------------------------------
RNG_SEED = 20261105          # spec §2.2：random.Random(20261105) 钉扎
ALPHA = 0.05                 # spec §2.3：双侧 α=0.05
BOUNDARY_LOW = 0.05          # spec §2.3：边界带 p∈(0.05,0.10)
BOUNDARY_HIGH = 0.10
N_CHAINS_ANCHOR = 43773      # spec §0.2：总链数（chains_manifest counts）

# 产卡时的契约列（缺一=fail-closed；只取洗牌+四票+门禁涉及的最小集）
REQUIRED_CARD_COLS = [
    "chain_id", "microbe_id", "microbe_name", "metabolite_id",
    "metabolite_name", "hop2_gene", "hop2_gene_name", "hop3_pathway",
    "depth", "vote1_ext_cohort", "vote2_kg_depth", "vote3_gutmgene_cross",
    "vote4_measurability", "endpoint_meas_ko", "strain_available",
    "votes_total",
]
# chains.tsv 必需的链结构列：代谢物身份三元组（票③④重算的输入）+ 深度
REQUIRED_CHAIN_COLS = ["chain_id", "microbe_id", "metabolite_id",
                       "metabolite_name", "metabolite_chebi", "depth"]
# 三桶对账报告列（票③索引重建源）
REQUIRED_IREP_COLS = ["bucket", "entity_a", "entity_b",
                      "matched_node_a", "matched_node_b"]
# D1 标注列（票④ KO 可测性冻结查表源；三队列任一 measurable 即 1）
REQUIRED_ANN_COLS = ["chain_id", "cohort", "ko_label"]

# 预写解读句（spec §2.1 逐字；测试里有"必须出现在规格档"的防手抄断言，
# 防止某天有人"顺手润色"改写预注册措辞）
PREWRITTEN_NULL_SENTENCE = (
    "菌-代谢物配对本身不携带四票可捕捉的特异信号——四票对配对的敏感性"
    "为零，与其边际证据驱动的性质自洽（B3 第三重互证）。")

# 统计族声明句（spec §0.3 预写死，禁成稿期改口径）
FAMILY_NOTE = ("本批 ②乙 + ③甲 + ③乙 构成一个检验族（3 次假设检验）；"
               "三问互不替代，**不做族内 α 校正**（Bonferroni 等）。")

SPEC_PATH = "data/logs/upgrade_v03_spec_20261105.md"
GOLDEN_LIST_DEFAULT = "data/logs/golden_baseline_v3_20261102_sha256.txt"
# 生产 KO 映射与观测层（票④重推导对拍用；缺省指向主仓只读路径，可由参数覆盖）
KO_MAP_DEFAULT = ("archive/Version01/data/logs/external_evidence/"
                  "metabolite_ko_map_v2.tsv")
OBS_DIR_DEFAULT = "data/observation/htn_v1"
COHORTS = ("PRJEB13870", "PRJEB21612", "SelfData_222")


def kg_root() -> Path:
    """仓根定位（与 cli.kg_root 同法）：KG_ROOT 环境变量 > 自动探测。

    为什么允许环境变量：非 editable 打包安装时 parents[2] 不再指向仓——
    规格档/golden 清单/私有数据腿的可移植性全靠它。
    """
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------
# 冻结生产入口装载（06_tool 目录名带连字符不是合法包名——按仓内约定
# 路径加载；懒加载+缓存：pytest 收集 import 本模块时不触发副作用）
# ---------------------------------------------------------------
_HYP = None
_INF = None


def _load_06_tool(mod_name: str, global_key: str):
    """按路径加载 src/06_tool/<mod_name>.py 并缓存（同一进程只执行一次）。"""
    g = globals()
    if g[global_key] is not None:
        return g[global_key]
    tool_dir = kg_root() / "src" / "06_tool"
    p = tool_dir / f"{mod_name}.py"
    if not p.exists():
        raise SystemExit(f"[hop1-shuffle] 生产模块缺失 {p}——fail-closed"
                         "（票③④规则须来自 06_tool 原件，禁私搭查询）")
    # 06_tool 内部是平铺 import（from subgraph import read_tsv 等），
    # 路径加载不自动加 sys.path——先补上，否则 exec_module 直接 ImportError
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    spec = spec_from_file_location(f"{mod_name}_frozen_for_u3a", p)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    g[global_key] = mod
    return mod


def load_hypothesize():
    """路径加载主链冻结件 src/06_tool/hypothesize.py（票③规则原件）。"""
    return _load_06_tool("hypothesize", "_HYP")


def load_inference():
    """路径加载 src/06_tool/inference.py（票④ KO 可测性推导原件）。"""
    return _load_06_tool("inference", "_INF")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: Path, required: list[str], what: str) -> list[dict]:
    """TSV 读取 + 缺档/缺列 fail-closed（列按名读，不按位）。"""
    if not path.exists():
        raise SystemExit(f"[hop1-shuffle] 缺输入 {path}——{what} 无从读起")
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in required
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[hop1-shuffle] {path.name} 缺列 {missing}"
                             "——fail-closed")
        return list(reader)


# ---------------------------------------------------------------
# 票④ 联动重算的输入准备：metabolite_id → 端点 KO 可测标志
# ---------------------------------------------------------------

def build_ko_lookup(chains_by_id: dict[str, dict],
                    annotations: list[dict]) -> dict:
    """从 golden chain_annotations.tsv 聚合 metabolite_id → endpoint_meas_ko。

    为什么这样算是"复用生产推导"而不是"私搭查询"：
    D1 标注里 ko_label 由 `inference.annotate_ko(链的 metabolite_chebi)` 产出，
    而 annotate_ko 只吃 chebi（+队列 KO 表），**与链 id 无关**——同一代谢物的
    所有链必然同标签。于是"该代谢物在任一队列 measurable"就是生产口径下
    端点可测票的取值；本函数只做聚合，不重写任何判定规则。
    两道自检兜住"没想当然"：①同一 metabolite_id 的 chebi 必须唯一（否则
    聚合口径不成立）②同一 metabolite_id 的 measurable 标志必须唯一。
    """
    # ko_label 按 (chain_id, cohort) 登记——先收成 chain → {cohort: label}
    labels: dict[str, dict[str, str]] = defaultdict(dict)
    for r in annotations:
        labels[r["chain_id"]][r["cohort"]] = r["ko_label"]
    lookup: dict[str, int] = {}
    chebi_of: dict[str, str] = {}
    conflicts: list[str] = []
    for cid, ch in chains_by_id.items():
        mid = ch["metabolite_id"]
        chebi = ch.get("metabolite_chebi", "")
        if mid in chebi_of and chebi_of[mid] != chebi:
            conflicts.append(f"{mid}: chebi 不唯一")
        chebi_of[mid] = chebi
        lab = labels.get(cid, {})
        if not lab:
            # 标注缺链=fail-closed：缺了就没法判"任一队列 measurable"，
            # 硬当 0 会把数据缺口伪装成负结果（AGENTS.md 不要做 #6）
            raise SystemExit(f"[hop1-shuffle] 链 {cid} 缺 D1 标注——"
                             "票④可测标志无从判定，fail-closed")
        flag = 1 if any(v == "measurable" for v in lab.values()) else 0
        if mid in lookup and lookup[mid] != flag:
            conflicts.append(f"{mid}: measurable 标志不唯一")
        lookup[mid] = flag
    if conflicts:
        raise SystemExit("[hop1-shuffle] 票④查表聚合口径不成立："
                         + "；".join(sorted(set(conflicts))[:5])
                         + "——fail-closed（代谢物身份不自洽）")
    return {"lookup": lookup,
            "n_metabolites": len(lookup),
            "n_measurable": sum(1 for v in lookup.values() if v == 1)}


def rederive_ko_lookup_with_production(chains_by_id: dict[str, dict],
                                       lookup: dict[str, int],
                                       ko_map_path: Path,
                                       obs_dir: Path) -> dict:
    """用生产 annotate_ko 从 chebi 重推导，逐位对拍聚合查表（证③）。

    为什么还要这一步：聚合查表虽源自生产机器输出，但它是"二手"的；重推导
    是把 production 的整条链（gate_ko_map 的 sha 门禁 → load_ko_table 的
    队列 KO 表 → annotate_ko 的三值判定）再跑一遍，证明"表的每个 0/1 都是
    生产口径的结论"。任一不等=fail-closed：宁可拒出统计，也不出"票④是查表
    还是拍脑袋"含糊的结论。
    """
    inf = load_inference()
    ko_map = inf.gate_ko_map(ko_map_path)          # sha16 门禁在装载内
    obs_report = inf.gate_observation_files(obs_dir, list(COHORTS))
    ko_tables = {c: inf.load_ko_table(obs_dir, c)[1] for c in COHORTS}
    chebi_of: dict[str, str] = {}
    for ch in chains_by_id.values():
        chebi_of[ch["metabolite_id"]] = ch.get("metabolite_chebi", "")
    mismatches = []
    n_checked = 0
    for mid, chebi in sorted(chebi_of.items()):
        labs = [inf.annotate_ko(chebi, ko_map, ko_tables[c])["ko_label"]
                for c in COHORTS]
        derived = 1 if any(v == "measurable" for v in labs) else 0
        n_checked += 1
        if derived != lookup[mid]:
            mismatches.append(f"{mid}(chebi={chebi or '-'}): "
                              f"生产={derived} 查表={lookup[mid]}")
    if mismatches:
        raise SystemExit("[hop1-shuffle] 票④生产重推导对拍失配："
                         + "；".join(mismatches[:5])
                         + f"（共 {len(mismatches)} 个代谢物）——fail-closed")
    return {"n_checked": n_checked, "mismatches": 0,
            "ko_map_sha16": sha256_file(ko_map_path)[:16],
            "obs_files_checked": obs_report["files_checked"],
            "pass": True}


# ---------------------------------------------------------------
# 机器门禁（fail-closed，任一不过=拒出统计）
# ---------------------------------------------------------------

def run_gates(cards: list[dict], idx: dict,
              ko_lookup: dict[str, int]) -> dict:
    """三道门禁（模块 docstring 门禁族；全过才允许出统计）。

    ① 票③往返核验全等：用 interaction_report 重建索引对**原实体**重算票③，
       必须与卡片 vote3 列逐行全等——证明索引与产卡同源（拿错报告算出的
       Δ 毫无意义）。
    ② votes_total 恒等式：votes_total == 票①+②+③+④ 逐行成立。
    ③ 票② == depth 逐行成立（票②是链结构属性，洗牌不得动它）。

    另加一道"票④保真"自检（不算门禁③之外的第四门禁，是门禁①的同型对拍）：
    卡片 endpoint_meas_ko 必须与本次查表一致——若不一致，说明洗牌用的
    metabolite→可测映射与产卡时不同源，票④重算就成了换尺子量。
    """
    hyp = load_hypothesize()
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
        raise SystemExit(f"[hop1-shuffle] 票③往返核验失配（如 {rt_bad}）"
                         "——interaction_report 与卡片非同源，Δ 无意义，"
                         "fail-closed 拒出统计")
    id_bad, dep_bad, v4_bad = [], [], []
    for c in cards:
        s = (int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"])
             + int(c["vote3_gutmgene_cross"]) + int(c["vote4_measurability"]))
        if s != int(c["votes_total"]):
            id_bad.append(c["chain_id"])
        if int(c["vote2_kg_depth"]) != int(c["depth"]):
            dep_bad.append(c["chain_id"])
        want = ko_lookup[c["metabolite_id"]] + int(c["strain_available"])
        if want != int(c["vote4_measurability"]):
            v4_bad.append(c["chain_id"])
    if id_bad or dep_bad or v4_bad:
        raise SystemExit(
            f"[hop1-shuffle] 恒等式门禁失配：votes_total {len(id_bad)} 行"
            f"（如 {id_bad[:3]}）/票②≠深度 {len(dep_bad)} 行（如 "
            f"{dep_bad[:3]}）/票④查表≠卡片 {len(v4_bad)} 行（如 "
            f"{v4_bad[:3]}）——fail-closed")
    return {"vote3_roundtrip_rows": len(cards),
            "votes_identity_rows": len(cards),
            "vote2_depth_rows": len(cards),
            "vote4_lookup_rows": len(cards),
            "gate_pass": True}


# ---------------------------------------------------------------
# 洗牌与四票重算（纯函数）
# ---------------------------------------------------------------

def metabolite_permutation(n_rows: int, seed: int = RNG_SEED) -> list[int]:
    """全库行号 → 供体行号（钉扎洗牌，**不做分层**）。

    全部 n_rows 行参与（spec §2.2 v1.1 勘误：代谢物列全库非空，不存在
    "无代谢物链"这种豁免层）。random.Random(seed).shuffle 对行号列表原地
    重排——同输入同平台逐位可复现；行序=卡片文件原序（A2 登记）。
    """
    donors = list(range(n_rows))
    random.Random(seed).shuffle(donors)
    return donors


def recompute_shuffled_votes(cards: list[dict], idx: dict,
                             ko_lookup: dict[str, int],
                             donors: list[int]) -> dict:
    """按供体行重算四票'（纯函数：只读 cards/idx/ko_lookup，不写任何东西）。

    单拆出来的理由：门禁、往返核验、测试三方都要"逐行票值"，如果都从
    compute_shuffle 的聚合结果反推，任何一处口径漂移都看不出来。这里一次
    算清，谁要谁调用——单一实现，无第二份规则。
    """
    hyp = load_hypothesize()
    n = len(cards)
    v3p = [0] * n
    v4p = [0] * n
    vt = [0] * n
    diffs = [0] * n
    for i, c in enumerate(cards):
        new_mid = cards[donors[i]]["metabolite_id"]
        # 票③：生产 vote3 原件，只换代谢物（菌/基因子句输入不变）
        v3p[i] = hyp.vote3({"microbe_id": c["microbe_id"],
                            "metabolite_id": new_mid,
                            "hop2_gene_name": c["hop2_gene_name"]}, idx)
        # 票④：端点可测随新代谢物，菌株可得随菌（不动）
        v4p[i] = ko_lookup[new_mid] + int(c["strain_available"])
        vt[i] = (int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"])
                 + v3p[i] + v4p[i])
        diffs[i] = int(c["votes_total"]) - vt[i]
    return {"vote3": v3p, "vote4": v4p, "votes_total": vt, "diffs": diffs}


def compute_shuffle(cards: list[dict], chains_by_id: dict[str, dict],
                    interaction_rows: list[dict], annotations: list[dict],
                    seed: int = RNG_SEED,
                    ko_rederive: dict | None = None) -> dict:
    """洗牌臂全流程（纯内存，零卡片落盘）→ 机器数 dict。

    四票口径（spec §2.2）：
      票①' = 票①（查询输入=菌；菌列固定 → 输入逐位相同）
      票②' = 票②（链深结构属性）
      票③' = 用生产 vote3 以**新代谢物**重查同一三桶索引（菌/基因不变）
      票④' = 新代谢物的端点 KO 可测（生产推导聚合表）+ 菌株可得（查菌，
              菌不动 → 卡片原值）
      votes_total' = 票①+票②+票③'+票④'
    """
    hyp = load_hypothesize()
    idx = hyp.build_vote3_index(interaction_rows)
    ko = build_ko_lookup(chains_by_id, annotations)
    ko_lookup = ko["lookup"]
    gates = run_gates(cards, idx, ko_lookup)

    donors = metabolite_permutation(len(cards), seed)
    re = recompute_shuffled_votes(cards, idx, ko_lookup, donors)
    vt_prime, v3_prime, v4_prime, diffs = (re["votes_total"], re["vote3"],
                                           re["vote4"], re["diffs"])
    same_metabolite = sum(
        1 for i, c in enumerate(cards)
        if cards[donors[i]]["metabolite_id"] == c["metabolite_id"])
    v3_changed = sum(1 for i, c in enumerate(cards)
                     if v3_prime[i] != int(c["vote3_gutmgene_cross"]))
    v4_changed = sum(1 for i, c in enumerate(cards)
                     if v4_prime[i] != int(c["vote4_measurability"]))

    # —— 置换往返核验（门禁①的同型加强）：把洗牌后的代谢物列按逆置换还原，
    # 必须逐位回到原列；再用还原后的列重算票③④，必须逐位回到卡片原值。
    # 任何索引错位（donors 不是双射/逆置换算错）都会在这里炸出来。
    shuffled_ids = [cards[d]["metabolite_id"] for d in donors]
    inv = [0] * len(cards)
    for i, d in enumerate(donors):
        inv[d] = i
    back = [shuffled_ids[inv[j]] for j in range(len(cards))]
    bad_trip = [i for i, (b, c) in enumerate(zip(back, cards))
                if b != c["metabolite_id"]]
    if bad_trip:
        raise SystemExit(f"[hop1-shuffle] 置换往返失配 {len(bad_trip)} 行"
                         f"（如 {bad_trip[:3]}）——置换实现有索引错位，"
                         "fail-closed")
    re_v3_bad = [i for i, c in enumerate(cards)
                 if hyp.vote3({"microbe_id": c["microbe_id"],
                               "metabolite_id": back[i],
                               "hop2_gene_name": c["hop2_gene_name"]}, idx)
                 != int(c["vote3_gutmgene_cross"])]
    re_v4_bad = [i for i, c in enumerate(cards)
                 if ko_lookup[back[i]] + int(c["strain_available"])
                 != int(c["vote4_measurability"])]
    if re_v3_bad or re_v4_bad:
        raise SystemExit(f"[hop1-shuffle] 往返重算失配：票③ {len(re_v3_bad)}"
                         f" 行/票④ {len(re_v4_bad)} 行——fail-closed")

    wil = wilcoxon_signed_rank_normal(diffs)
    dist_true = Counter(int(c["votes_total"]) for c in cards)
    dist_shuf = Counter(vt_prime)
    keys = sorted(set(dist_true) | set(dist_shuf))
    n = len(cards)
    mean_true = sum(int(c["votes_total"]) for c in cards) / n
    mean_shuf = sum(vt_prime) / n
    # —— 结构性不变量（算术恒等，不是检验）：置换保边际 → 代谢物多重集不变；
    # 而票④的端点可测子分只是代谢物的函数、菌株子分随菌固定 → 票④总量守恒。
    # 任一破=实现有 bug（例如查表偷偷掺了链 id），fail-closed 拒出统计。
    if Counter(c["metabolite_id"] for c in cards) != Counter(
            cards[d]["metabolite_id"] for d in donors):
        raise SystemExit("[hop1-shuffle] 代谢物多重集被破坏——置换实现有 bug，"
                         "fail-closed")
    v4_sum_true = sum(int(c["vote4_measurability"]) for c in cards)
    v4_sum_shuf = sum(v4_prime)
    if v4_sum_true != v4_sum_shuf:
        raise SystemExit(
            f"[hop1-shuffle] 票④总量不守恒（{v4_sum_true} vs {v4_sum_shuf}）"
            "——可测标志不是纯代谢物函数，fail-closed")
    v3_sum_true = sum(int(c["vote3_gutmgene_cross"]) for c in cards)
    # 描述性分票均值（不构成检验——§2.3 只预指定 votes_total 的 Wilcoxon）
    comp = {}
    for col, arr in (("vote3", v3_prime), ("vote4", v4_prime)):
        comp[col] = {
            "mean_true": sum(int(c[{"vote3": "vote3_gutmgene_cross",
                                    "vote4": "vote4_measurability"}[col]]
                                 ) for c in cards) / n,
            "mean_shuffled": sum(arr) / n,
            "n_changed": v3_changed if col == "vote3" else v4_changed,
        }
    return {
        "n_cards": n,
        "n_same_metabolite": same_metabolite,
        "n_distinct_metabolites": len({c["metabolite_id"] for c in cards}),
        "gates": gates,
        "ko_lookup_report": {k: v for k, v in ko.items() if k != "lookup"},
        "ko_rederive": ko_rederive,
        "vote4_sum_true": v4_sum_true,
        "vote4_sum_shuffled": v4_sum_shuf,
        "vote3_net": sum(v3_prime) - v3_sum_true,
        "mean_true": mean_true,
        "mean_shuffled": mean_shuf,
        "mean_diff": mean_true - mean_shuf,
        "median_diff": statistics.median(diffs),
        "sum_diff": sum(diffs),
        "dist_true": {k: dist_true.get(k, 0) for k in keys},
        "dist_shuffled": {k: dist_shuf.get(k, 0) for k in keys},
        "components": comp,
        "wilcoxon": wil,
        "seed": seed,
    }


# ---------------------------------------------------------------
# Δ 检验：配对 Wilcoxon 符号秩，手算正态近似（零 scipy）
# ---------------------------------------------------------------

def wilcoxon_signed_rank_normal(diffs: list[int | float]) -> dict:
    """配对 Wilcoxon 符号秩正态近似（spec §2.3 预指定）。

    惯例与披露：零差弃置（Wilcoxon zero-discard，n_eff=非零对数）；
    并列 |d| 取中间秩（midrank）；方差含并列校正 Σ(t³−t)/48；
    z 含 0.5 连续性校正；双侧 p=erfc(|z|/√2)（math 标准库正态尾）；
    效应量 rank-biserial r=(W+−W−)/S，S=n_eff(n_eff+1)/2
    （正值=真链高于洗牌链）。
    已与 scipy.stats.wilcoxon(mode="approx", correction=True) 逐位对拍
    （见 data/logs/hop1_shuffle_evidence_20261105.txt 证③）。
    """
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        # 退化：全零差（如全库同一代谢物）——不硬造 z，如实报无有效对
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
    if var <= 0:
        # 方差为 0 = 所有 |d| 相同且全并列到极限——正态近似无定义
        return {"n_pairs": len(diffs), "n_eff": n,
                "n_pos": sum(1 for x in d if x > 0),
                "n_neg": sum(1 for x in d if x < 0),
                "n_zero": len(diffs) - n, "w_plus": w_plus,
                "w_minus": w_minus, "rank_sum": s_total,
                "tie_correction": tie, "z": 0.0, "p_two_sided": 1.0,
                "rank_biserial": 0.0, "degenerate": True}
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


def boundary_label(p: float) -> str:
    """边界带标签（spec §2.3：p∈(0.05,0.10)）——只加标签不改结论句式。"""
    if BOUNDARY_LOW < p < BOUNDARY_HIGH:
        return "边界带"
    return ""


def direction_conclusion(w: dict, mean_true: float, mean_shuf: float) -> str:
    """方向结论（预注册 双侧 α=0.05；负结果也如实返回，不粉饰）。

<<<<<<< HEAD
    评审风险提醒落地：这一句与效应量 r 在评估档里同段出现——巨样本下
=======
    监工风险提醒落地：这一句与效应量 r 在评估档里同段出现——巨样本下
>>>>>>> origin/main
    "p 极小"不等于"差异有实质意义"，必须让 r 和均值差一起被看见。
    """
    if w["degenerate"] or w["n_eff"] == 0:
        return ("零有效对（配对差全零）——Δ 检验退化，无方向可判"
                "（如实入档）")
    band = boundary_label(w["p_two_sided"])
    band_txt = f"【{band}】" if band else ""
    if w["p_two_sided"] < ALPHA:
        if mean_true > mean_shuf:
            return (f"真链 votes_total 显著高于洗牌链（H1 方向成立，"
                    f"p={w['p_two_sided']:.4e} < {ALPHA}）{band_txt}")
        if mean_true < mean_shuf:
            return (f"真链 votes_total 显著低于洗牌链（方向与 H1 相反，"
                    f"p={w['p_two_sided']:.4e} < {ALPHA}，如实入档）{band_txt}")
        return "显著但均值相等（秩方向与均值方向矛盾，人工复核）"
    return (f"无显著差异（p={w['p_two_sided']:.4e} ≥ {ALPHA}）——"
            "H1「真链 votes_total 分布显著高于洗牌链」不成立"
            f"（负结果如实入档=预注册精神）{band_txt}")


# ---------------------------------------------------------------
# 评估档渲染（确定性 markdown：无时间戳——同输入同产物逐字节可复现；
# 日期由文件名携带，杜绝双跑 sha 不等的伪不可复现）
# ---------------------------------------------------------------

AMBIGUITIES = [
    ("A1", "「代谢物列」的边界（连带哪些列）",
     "§2.2 只说代谢物列重排。实现按**代谢物身份三元组 "
     "(metabolite_id, metabolite_name, metabolite_chebi) 成对整体搬运**："
     "①沿 B3 基因列 id+name 成对口径；②chebi 是票④推导输入，不搬则票④"
     "无法按新代谢物重算（与 §2.2 联动重算条款自相矛盾）。"),
    ("A2", "置换的作用行序",
     "§2.2 未写行序。实现=hypothesis_cards.tsv 原序（chain_id 升序，产卡"
     "装配序），沿 B3 先例；置换结果依赖行序，故必须钉扎并披露。"),
    ("A3", "票①/票② 是否重算",
     "票①重算需外部队列 102 条列表（非本件输入）→ 取卡片原值；票②=depth "
     "结构列，取卡片原值并以门禁③逐行验证。二者查询输入（菌/深度）在洗牌"
     "中不动，故取值与重算等价。"),
    ("A4", "票④的重算入口（生产 vote4 是 chain_id 键）",
     "生产 vote4=endpoint_meas_ko(chain_id)+strain_available(microbe)，前者"
     "按 chain_id 查 D1 标注、但标签由链代谢物 chebi 经 inference.annotate_ko "
     "推导（与链 id 无关）。§2.2 要求按新代谢物重算 → 实现聚合 "
     "metabolite_id→可测标志 的查表（源=golden chain_annotations.tsv），"
     "并以生产 annotate_ko 从 chebi 全量重推导对拍（证③）证明同源。"),
    ("A5", "§2.1「单侧」与 §2.3「双侧」的措辞冲突",
     "统计量节 §2.3 明写「配对 Wilcoxon ... 双侧 α=0.05」=预指定统计口径；"
     "§2.1 的「单侧」是 H1 方向叙述。实现按 §2.3 双侧检验，方向由均值差/"
     "效应量符号读出——不改成单侧 p（事后改口径=规格漂移）。"),
    ("A6", "rank-biserial r 的公式",
     "规格未给公式。实现 r=(W+−W−)/S，S=n_eff(n_eff+1)/2（沿 B3 冻结实现）；"
     "正值=真链高于洗牌链，与方向结论同向可读。"),
    ("A7", "n_eff 与配对总数 n 的关系",
     "§2.3「n=43,773（零差行弃置，n_eff 须报）」→ z/p 按弃置后的 n_eff 计算，"
     "同时报 n_pairs（恒定 43,773）与 正/负/零 三计数。"),
    ("A8", "「中位数差」的定义",
     "实现=median(逐链配对差)，同时报 mean(diff)；mean 侧以恒等式断言与 "
     "mean_true−mean_shuffled 逐位相等（防两处口径各自漂移）。"),
    ("A9", "洗牌后换回原代谢物的行",
     "置换零分布的组成部分（多重集不变 → 必然有一部分行回到原值），"
     "如实计数、不剔除（沿 B3 多重集披露口径）。"),
    ("A10", "溯源四键与 KG-CoI 步进列是否随洗牌重算",
     "不重算：它们不参与四票，且 §2.4 明令洗牌中间态零落盘（无卡可写）。"
     "评估档显式披露「这些列在洗牌臂中无定义」，防被读成「也验过了」。"),
    ("A11", "评估档的时间戳",
     "零时间戳（沿 B3/②乙），日期由文件名携带——否则三跑 sha 不可能全等，"
     "复现性验收自毁。"),
    ("A12", "边界带判定用哪个 p",
     "用主检验的双侧 p（同一量），只加「边界带」标签，主结论句式不变、"
     "不追加任何事后检验（§2.3 预指定）。"),
]


def render_report(cards_path: Path, chains_path: Path, irep_path: Path,
                  ann_path: Path, stats: dict) -> str:
    w = stats["wilcoxon"]
    g = stats["gates"]
    comp = stats["components"]
    ko = stats["ko_lookup_report"]
    kr = stats.get("ko_rederive")
    lines = [
        "# ③甲 hop1（菌↔代谢物）换跳洗牌评估档（机器生成——数字全部程序转写）",
        "",
        "- 规格：`data/logs/upgrade_v03_spec_20261105.md` §二（冻结 v1.5，"
        "tag `v03-upgrade-prereg-v1.5`）；本档=该规格的机器化产出",
        "- 定位：菌列固定、代谢物列全库重排后**票③④联动重算**——检验"
        "「菌-代谢物配对本身是否携带四票可捕捉的特异信号」",
        "",
        "## 一、输入与指纹",
        f"- chains.tsv：`{chains_path}`（sha256="
        f"{sha256_file(chains_path)}，{stats['n_cards']} 行）",
        f"- hypothesis_cards.tsv：`{cards_path}`（sha256="
        f"{sha256_file(cards_path)}，{stats['n_cards']} 行）",
        f"- interaction_report.tsv：`{irep_path}`（sha256="
        f"{sha256_file(irep_path)}，票③三桶索引重建源）",
        f"- chain_annotations.tsv：`{ann_path}`（sha256="
        f"{sha256_file(ann_path)}，票④可测标志聚合源）",
        f"- 洗牌种子：`random.Random({stats['seed']})`（spec §2.2 钉扎；"
        "行序=卡片文件原序，见 §十二 A2）",
        f"- 链数锚：{stats['n_cards']} vs 规格 §0.2 锚 {N_CHAINS_ANCHOR} → "
        f"{'PASS' if stats['n_cards'] == N_CHAINS_ANCHOR else 'DEVIATE'}",
        "",
        "## 二、洗牌口径（spec §2.2，v1.1 勘误后）",
        f"- **菌列固定**；代谢物列在**全库 {stats['n_cards']} 链内重排**"
        "（多重集不变：每个代谢物出现次数逐元素保持）——"
        f"池={stats['n_distinct_metabolites']} 个不同代谢物",
        "- **不做分层**：代谢物列全库非空（depth=1 的 537 条链 metabolite_id "
        "全部有值，空的是 hop2_gene/hop3_pathway）→ 全部链均参与洗牌，"
        "与 §2.3 n=43,773 自洽",
        f"- 多重集披露：重排后 {stats['n_same_metabolite']} 行换回原代谢物"
        f"（占 {stats['n_same_metabolite'] / stats['n_cards']:.4%}）——"
        "置换零分布的组成部分，如实计数不剔除（A9）",
        "",
        "## 三、四票重算口径（联动重算，无一票近似）",
        "- 票①'=票①（查询输入=菌，hypothesize.py 的 vote1；菌列固定 → 输入"
        "逐位相同）；票②'=票②（链深=结构属性，spec §2.2 明文不动）",
        "- 票③'=**以新代谢物重查**：路径加载 `06_tool/hypothesize.py` 的 "
        "`build_vote3_index`/`vote3` 原件对同一三桶索引重算（菌/基因子句输入"
        "不变）——规则零漂移，禁复制粘贴",
        "- 票④'=**以新代谢物重算端点 KO 可测** + 菌株可得（查菌，菌不动 → "
        "卡片原值）。可测标志=生产 `inference.annotate_ko(链 chebi)` 在任一"
        "队列 measurable 的聚合查表（metabolite→flag），"
        f"覆盖 {ko['n_metabolites']} 个代谢物（其中 {ko['n_measurable']} 个"
        "可测）",
        (f"- 票④生产重推导对拍：{kr['n_checked']} 个代谢物逐位一致"
         f"（ko_map sha16={kr['ko_map_sha16']}，观测层 {kr['obs_files_checked']} "
         "文件 sha 契约通过）=证③" if kr else
         "- 票④生产重推导对拍：本次未提供 --ko-map/--obs-dir（查表来源="
         "golden chain_annotations.tsv 冻结件）——对拍证据见 evidence 档"),
        f"- 联动幅度（描述性，不构成检验）：票③ {comp['vote3']['n_changed']} "
        f"行改值（均值 {comp['vote3']['mean_true']:.6f}→"
        f"{comp['vote3']['mean_shuffled']:.6f}）；票④ "
        f"{comp['vote4']['n_changed']} 行改值（均值 "
        f"{comp['vote4']['mean_true']:.6f}→{comp['vote4']['mean_shuffled']:.6f}）",
        f"- **结构性恒等式（算术恒等，非检验）**：票④' 总量守恒——菌株子分"
        f"随菌固定、端点可测子分只是代谢物的函数（多重集又守恒）→ "
        f"Σ票④'≡Σ票④（实测 {stats['vote4_sum_true']} vs "
        f"{stats['vote4_sum_shuffled']}）。含义：票④在洗牌中只**重分配**、"
        "不改总量，均值位移全部来自票③"
        f"（洗牌 vs 真链参照系：票③净 {stats['vote3_net']:+d} 票；等价于真 vs 洗牌 Σ差 {stats['sum_diff']:+d} 票——两数互为相反数，因参照系相反，勿混读）；票④对配对的敏感性只能通过逐行"
        "重配体现，不可能通过总量/均值检出——这与 §2.2「联动重算」的要求"
        "并行不悖，但报告时不得把它写成「票④也检出了信号」。",
        "- votes_total'=票①+票②+票③'+票④'",
        "",
        "## 四、与 HypoKG 洗牌的设计差异（spec §2.2 要求入档）",
        "- HypoKG（arXiv 2609.12260）用「端点固定 + 中间步骤打乱」检验"
        "「LLM 是否利用中间路径」；本件固定菌侧、重排代谢物侧=**部分端点"
        "不固定**——因为检验对象不同：我们测「四票评分对配对的敏感性」，"
        "必须扰动配对本身；HypoKG 测「LLM 对路径结构的利用」，必须固定端点"
        "以排除端点贡献。",
        "",
        "## 五、机器门禁（fail-closed，全过才出统计）",
        f"- ①票③往返核验全等：{g['vote3_roundtrip_rows']} 行原实体重算与"
        "卡片列逐行全等",
        f"- ②votes_total 恒等式：{g['votes_identity_rows']} 行逐行成立",
        f"- ③票②==depth：{g['vote2_depth_rows']} 行逐行成立",
        f"- 附：票④查表保真 {g['vote4_lookup_rows']} 行（卡片 endpoint 列 == "
        "本次查表）；置换往返（逆置换还原后票③④逐位回原值）通过",
        "",
        "## 六、Δ 检验（预注册：配对 Wilcoxon 符号秩正态近似，"
        f"双侧 α={ALPHA}）",
        f"- 配对 n={w['n_pairs']}；零差弃置后 **n_eff={w['n_eff']}**"
        f"（正 {w['n_pos']} / 负 {w['n_neg']} / 零 {w['n_zero']}）",
        f"- W+={w['w_plus']:.1f}，W-={w['w_minus']:.1f}，"
        f"S={w['rank_sum']:.0f}；并列校正项 Σ(t³−t)={w['tie_correction']:.0f}；"
        "z 含 0.5 连续性校正",
        f"- **z={w['z']:+.6f}；双侧 p={w['p_two_sided']:.6f}"
        f"（{w['p_two_sided']:.6e}）；效应量 rank-biserial "
        f"r={w['rank_biserial']:+.6f}**（r 与 p 同段强制并列："
        "巨样本下 p 可极小而 r≈0——此时效应为零，不得读成强信号；"
        "B3 先例正是 r≈0）",
        f"- 均值差 = {stats['mean_diff']:+.6f}（真 {stats['mean_true']:.6f} "
        f"vs 洗牌 {stats['mean_shuffled']:.6f}）；"
        f"中位数差 = {stats['median_diff']:+.6g}；Σ差 = {stats['sum_diff']:+d}",
        f"- 边界带判定：p∈(0.05,0.10) → "
        f"{boundary_label(w['p_two_sided']) or '不在边界带'}",
        "",
        "## 七、分布对照（机器计数）",
        "",
        "| votes_total | 真链行数 | 洗牌链行数 |",
        "|---|---|---|",
    ]
    for k in stats["dist_true"]:
        lines.append(f"| {k} | {stats['dist_true'][k]} "
                     f"| {stats['dist_shuffled'][k]} |")
    concl = direction_conclusion(w, stats["mean_true"], stats["mean_shuffled"])
    lines += [
        "",
        "## 八、方向结论（预注册口径，禁成稿期改）",
        f"- **{concl}**",
        "",
        "## 九、预写解读句（spec §2.1 逐字；H1 不成立时按此引用）",
    ]
    if w["n_eff"] == 0 or w["degenerate"] or w["p_two_sided"] >= ALPHA:
        lines += [f"- **{PREWRITTEN_NULL_SENTENCE}**",
                  "- 判据：本次检验未达预注册显著（α=0.05）→ 预写解读句"
                  "生效；论文措辞照抄，禁「对照通过/验证成立」式粉饰。"]
    else:
        lines += ["- 本次检验达到预注册显著 → 预写解读句不启用（它在 H1 "
                  "不成立时才有定义）；成稿须按 §0.4 纪律如实报效应量与"
                  "方向，不得反向套用负结果措辞。"]
    if boundary_label(w["p_two_sided"]):
        lines += ["", "## 十、边界带声明",
                  "- p 落在 (0.05, 0.10) 边界带：**主结论句式不变**"
                  "（仍判「无显著差异」）、**不追加任何事后检验**（不换检验、"
                  "不加样本、不改零模型）。"]
    else:
        lines += ["", "## 十、边界带声明", "- 本次 p 不在边界带。"]

    lines += [
        "",
        "## 十一、统计族与红线",
        f"- {FAMILY_NOTE}",
        "- 洗牌卡中间态零落盘：本工具唯一写件=本档；卡片/索引/标注全只读，"
        "golden v3 十三件零改动",
        "- 排序键零改动：本模块不排序、不回写任何既有表（四票排序键 "
        "(votes_total, borda, kg_coi_rate, penalty, chain_id) 一字未动）",
        "- 统计口径披露：置换保边际（代谢物多重集不变）→ Δ 只能检出"
        "「真配对上的证据锚定重叠结构」，检不出边际质量差——这是该对照臂"
        "问题的正确零分布，非实现局限",
        "- scipy 对拍：`scipy.stats.wilcoxon(mode=\"approx\", correction=True, "
        "zero_method=\"wilcox\")` 与本模块手算逐位一致，留痕见 "
        "`data/logs/hop1_shuffle_evidence_20261105.txt` 证③",
        "- 复现命令：`python3 src/kg_tool/hop1_shuffle.py --subgraph-dir "
        "<子图目录> --output <本档路径>`（同输入同平台逐字节复现；"
        "本档零时间戳）",
        "",
        "## 十二、规格歧义登记表（A 类攒批；冻结规格未写细节，禁静默自决）",
        "",
        "| # | 歧义点 | 本件实现口径与依据 |",
        "|---|---|---|",
    ]
    for code, topic, how in AMBIGUITIES:
        lines.append(f"| {code} | {topic} | {how} |")
    lines += [
        "",
        "> 登记口径：以上 12 项都是「规格没写、但实现必须选一个」的细节。"
        "选法一律优先沿用已冻结先例（B3/U2/②乙）；攒批报 A 类勘误，"
        "不改冻结正文。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------
# 落档（只增不改护栏，沿 U1/U2/B3 同制——防两次运行产物混放）
# ---------------------------------------------------------------

def run(subgraph_dir: Path, output_path: Path,
        ko_map: Path | None = None, obs_dir: Path | None = None,
        seed: int = RNG_SEED) -> dict:
    """读档→门禁→洗牌→Δ→评估档落盘（幂等 no-op/漂移拒写）。"""
    cards_path = subgraph_dir / "hypothesis_cards.tsv"
    chains_path = subgraph_dir / "chains.tsv"
    irep_path = subgraph_dir / "interaction_report.tsv"
    ann_path = subgraph_dir / "chain_annotations.tsv"
    cards = read_tsv(cards_path, REQUIRED_CARD_COLS, "卡片")
    chains = read_tsv(chains_path, REQUIRED_CHAIN_COLS, "链表")
    irep = read_tsv(irep_path, REQUIRED_IREP_COLS, "三桶对账报告")
    ann = read_tsv(ann_path, REQUIRED_ANN_COLS, "D1 标注")
    chains_by_id = {c["chain_id"]: c for c in chains}
    missing = [c["chain_id"] for c in cards if c["chain_id"] not in chains_by_id]
    if missing:
        raise SystemExit(f"[hop1-shuffle] 卡片链在 chains.tsv 缺失 "
                         f"{len(missing)} 条（如 {missing[:3]}）——fail-closed")
    # 代谢物身份三元组以 chains.tsv 为准回填（卡片不携带 chebi）
    for c in cards:
        ch = chains_by_id[c["chain_id"]]
        for k in ("metabolite_name",):
            if c[k] != ch[k]:
                raise SystemExit(
                    f"[hop1-shuffle] 卡片与链表 {k} 不一致（{c['chain_id']}："
                    f"{c[k]!r} vs {ch[k]!r}）——fail-closed")

    rederive = None
    if ko_map is not None and obs_dir is not None:
        lookup = build_ko_lookup(chains_by_id, ann)["lookup"]
        rederive = rederive_ko_lookup_with_production(
            chains_by_id, lookup, ko_map, obs_dir)

    stats = compute_shuffle(cards, chains_by_id, irep, ann, seed, rederive)
    # 均值差恒等式自检（A8：两处口径必须一致，不靠"看起来一样"）
    if abs(stats["mean_diff"] - stats["sum_diff"] / stats["n_cards"]) > 1e-12:
        raise SystemExit("[hop1-shuffle] 均值差恒等式失配——fail-closed")
    payload = render_report(cards_path, chains_path, irep_path, ann_path,
                            stats)
    if output_path.exists():
        old = output_path.read_bytes()
        if old == payload.encode("utf-8"):
            stats.update({"idempotent": True, "out": str(output_path)})
            return stats
        raise SystemExit(
            f"[hop1-shuffle] {output_path} 已存在且与本次产物不一致——"
            "只增不改区拒写（疑输入漂移；人工移走旧档过审后重跑）")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload, encoding="utf-8")
    stats.update({"idempotent": False, "out": str(output_path)})
    return stats


def verify_golden_baseline(sg_dir: Path, golden_list: Path) -> list[dict]:
    """golden 冻结清单逐行核验（证①）→ [{name, frozen, actual, ok}]。

    为什么不用 scripts/golden_check.py：后者比对"整轮运行目录"（输入指纹+
    manifest 白名单），本件只读不跑主链，逐件 sha 核验才是与"十三件零改动"
    对应的最小证据（沿 ②乙 同法）。第 13 行带 "(=runA_arm/...)" 注记，
    解析时按第一个 "(" 截断。
    """
    if not golden_list.exists():
        raise SystemExit(f"[hop1-shuffle] 缺 golden 冻结清单 {golden_list}")
    rows: list[dict] = []
    for line in golden_list.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        sha, name = line.split(None, 1)
        name = name.split("(", 1)[0].strip()
        p = sg_dir / name
        actual = (sha256_file(p) if p.exists() else "MISSING")
        rows.append({"name": name, "frozen": sha, "actual": actual,
                     "ok": actual == sha})
    return rows


def resolve_default_legs(ko_arg: Path | None, obs_arg: Path | None
                         ) -> tuple[Path | None, Path | None]:
    """解析生产数据腿（--ko-map/--obs-dir 的默认来源）。

    抽成独立函数是为了**可测试性**：主仓有真实数据腿时会自动启用生产对拍
    （票④重推导），而 fixture 假数据必然与之冲突；测试可 patch 本函数返回
    (None, None) 得到环境无关的 CLI 端到端测试（v0.3 合入修正）。
    优先级：显式参数 > 环境变量 > 仓内默认路径（存在才用）。
    """
    ko_map = ko_arg or (Path(os.environ["KG_KO_MAP"])
                        if os.environ.get("KG_KO_MAP") else None)
    obs_dir = obs_arg or (Path(os.environ["KG_OBS_DIR"])
                          if os.environ.get("KG_OBS_DIR") else None)
    if ko_map is None and (kg_root() / KO_MAP_DEFAULT).exists():
        ko_map = kg_root() / KO_MAP_DEFAULT
    if obs_dir is None and (kg_root() / OBS_DIR_DEFAULT).exists():
        obs_dir = kg_root() / OBS_DIR_DEFAULT
    return ko_map, obs_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kg hop1-shuffle",
        description="③甲 hop1（菌↔代谢物）换跳洗牌（代谢物列全库重排+票③④"
                    "联动重算+Δ 检验，洗牌卡零落盘只产评估档）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="子图目录（读 chains/hypothesis_cards/"
                         "interaction_report/chain_annotations）")
    ap.add_argument("--output", type=Path, default=None,
                    help="评估档 markdown 输出路径（默认 data/logs/"
                         "hop1_shuffle_20261105.md；已存在且字节一致=幂等 "
                         "no-op，不一致=拒写）")
    ap.add_argument("--ko-map", type=Path, default=None,
                    help="生产 KO 映射 tsv（提供且 --obs-dir 也在场时启用"
                         "票④生产重推导对拍）")
    ap.add_argument("--obs-dir", type=Path, default=None,
                    help="观测层目录（三队列 humann3/ko_unstratified.tsv）")
    ap.add_argument("--verify-golden", action="store_true",
                    help="只跑 golden 冻结清单逐件 sha 核验（不写任何产物）")
    ap.add_argument("--golden-list", type=Path, default=None,
                    help=f"golden 冻结清单（默认 {GOLDEN_LIST_DEFAULT}）")
    args = ap.parse_args(argv)

    if args.verify_golden:
        golden = args.golden_list or (kg_root() / GOLDEN_LIST_DEFAULT)
        rows = verify_golden_baseline(args.subgraph_dir, golden)
        bad = 0
        for r in rows:
            mark = "OK " if r["ok"] else "FAIL"
            if not r["ok"]:
                bad += 1
            print(f"{mark} {r['name']} frozen={r['frozen'][:20]} "
                  f"actual={r['actual'][:20]}")
        print(f"[hop1-shuffle] golden 核验 {len(rows)} 件 | 不一致 {bad} 件")
        print(f"[hop1-shuffle] 清单={golden}")
        return 0 if bad == 0 else 1

    out = args.output or (kg_root() / "data/logs/hop1_shuffle_20261105.md")
    # 生产重推导对拍需要私有数据腿（KO 映射在 archive/、观测层在 data/
    # observation/）——worktree 里这两处都不随 git 复制，故允许显式参数或
    # 环境变量；都拿不到就跳过对拍（评估档会如实写"未提供"，不假装验过）。
    ko_map, obs_dir = resolve_default_legs(args.ko_map, args.obs_dir)

    stats = run(args.subgraph_dir, out, ko_map, obs_dir)
    w = stats["wilcoxon"]
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    print(f"[hop1-shuffle] {stats['out']} {verb}")
    print(f"[hop1-shuffle] 卡片 {stats['n_cards']} 行"
          f"（锚 {N_CHAINS_ANCHOR}）| 换回原代谢物 "
          f"{stats['n_same_metabolite']} 行——洗牌卡中间态零落盘")
    print(f"[hop1-shuffle] 门禁三道全过（票③往返/恒等式/票②==深度）；"
          f"票④查表覆盖 {stats['ko_lookup_report']['n_metabolites']} 个代谢物")
    if stats.get("ko_rederive"):
        print(f"[hop1-shuffle] 票④生产重推导对拍："
              f"{stats['ko_rederive']['n_checked']} 个代谢物逐位一致")
    else:
        print("[hop1-shuffle] 票④生产重推导对拍：未提供 --ko-map/--obs-dir"
              "（查表源=golden 标注冻结件）")
    print(f"[hop1-shuffle] Δ 检验：n_eff={w['n_eff']}（正 {w['n_pos']}"
          f"/负 {w['n_neg']}/零 {w['n_zero']}） z={w['z']:+.6f} "
          f"p={w['p_two_sided']:.6e} r_rb={w['rank_biserial']:+.6f} "
          f"均值差={stats['mean_diff']:+.6f} 中位数差={stats['median_diff']:+.1f}")
    print(f"[hop1-shuffle] 方向结论："
          f"{direction_conclusion(w, stats['mean_true'], stats['mean_shuffled'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
