"""②乙 菌→代谢物网络邻近度（v0.3 四件之一；spec §一，冻结 v1.5，逐字遵循）。

定位与红线：
- 只读主子图四表（merged_nodes / merged_edges / top_hypotheses /
  hypothesis_cards），只新增 sidecar `microbe_metabolite_proximity.tsv` 到同一
  子图目录。落点是"只增不改区"（沿 U1/U2 惯例）：已存在且字节一致=幂等 no-op，
  不一致=fail-closed 拒写——防两次运行产物混放（AGENTS.md 血泪史 #5）。
- 四票排序键零改动：本模块不排序、不回写任何既有表。TOP100 的"按排名键取前
  100 卡"只在**内存副本**上排序——`hypothesis_cards.tsv` 文件本身是 chain_id
  字典序，规格 §0.2 明令弃用"文件前 100 行"，故排序必须发生（在副本上），但
  落盘零改动。
- 不碰 06_tool 生产逻辑；纯标准库（csv / hashlib / random / statistics +
  collections.deque 上的 BFS，零第三方）。

主指标（spec §1.3，v1.5 勘误口径，逐字）：
  **AMSPL**（closest distance，Guney 2016）：
      d_c(A,B) = (1/|A|) · Σ_{a∈A} min_{b∈B} d(a,b)
  ——每个种子取到靶集的最近距离，再对种子平均；**不是全部 A×B 配对平均**
  （辅助量 mean_all_pairs_distance 只作分布描述、不参与 z；因 sidecar schema
  9 列冻结无该列，该辅助量只在评估档内报）。
  为什么非要按"每种子最近"而不是"全部配对平均"：全部配对平均会把"一个菌贴近
  靶集、另一个菌远离"平均成中等距离，等于用靶集规模稀释信号；AMSPL 保留
  "任一种子贴近即算亲"的语义，与所引 Guney 2016 公式一致。

统计量（spec §1.3，预指定禁改）：
  z = (d_c − mean(d_c_rand)) / sd(d_c_rand)；**z<0 = 真配对更近**（H1 单侧）；
  单侧 α=0.05；单侧 p = 置换分布中 d_c_rand ≤ d_c 的比例（+1/(n+1) 小数修正）。
  sd 用 statistics.pstdev（总体标准差 ddof=0，与 numpy 默认及 U2 同口径）。

零模型（spec §1.3）：
- `random.Random(20261105)` 钉扎；n_perm=1000（文献下限口径：NetMedPy 示例常用
  10,000，本仓受算力约束取 1,000，p 分辨率 1e-3 量级，报告须写明）；
- 池=子图**同类别**节点：菌池=Species∪Microbe（287）、代谢物池=Metabolite（274）；
- 度匹配=**perfect degree match 的近似**：精确 degree 分箱 + 箱内 <5 向大侧合并
  （最大箱若 <5 并入其小一侧）——这是 NetMedPy 口径的近似实现，**非逐位复现**，
  报告须注明；
- 每次置换=保持种子集/靶集大小不变，逐位置从"同类别 + 同 degree 箱"的候选池中
  抽取一个替换节点。

Menche separation（附加输出，不参与主检验判定）：
  s_AB = d_AB − (d_AA + d_BB)/2，d_AA/d_BB=子集内部 AMSPL（**排除自身项**——
  每个成员取到"其它成员"的最近距离再平均；不排除自身则 min 恒为 0，量纲作废）。

实现裁定（spec 未写细节，按冻结规则入"规格歧义登记表"，见评估档 §八；
不静默自决）：
- A1 替换抽样是否排除原节点：§1.3 未写 → 沿同一冻结规格 §3.2 步 6 的既定口径
  （"排除该位置真链原节点"）执行；真实数据下各位置 degree 箱互不相交，
  有/无放回与是否重复抽中同节点对结果无影响（评估档披露）。
- A2 逐位置独立抽（每位置抽 1 个）而非整集联合抽：与 §3.2 步 6 "有放回是定量
  口径"同源，且避免"箱够不够大"的判断分支（该判断被规格明令禁止）。
- A3 内部 AMSPL 排除自身项（否则 |A|≥1 时 min 恒 0）——数学必需项。
- A4 不可达：d_c 含任一"全靶不可达"的种子 → 该读法 d_c 判不可算（不折算大数）；
  零模型抽到不可达的置换**不重抽**（spec"零模型中同样允许不可达"），只从
  mean/sd 计算中剔除并计数披露；观察侧不可达配对占比 >50% → "图不适用"，
  只报不可达率不出 z（spec 预指定）。
- A5 每个口径独立 `random.Random(20261105)` 起算（"逐口径重置"）——三口径互不
  传染，任意读法子集重跑逐字节相同（沿 U2"逐次重置"同制）。
- A6 pair_distance 列取**读法级 AMSPL d_c**（与 null_mean/null_sd/proximity_z/
  n_pairs 同为读法级量，随每行重复）：sidecar 必须自证——若该列改记行级
  d(菌,代谢物)，主指标 d_c 将不在 sidecar 内，审计需回查评估档；行级配对距离
  在评估档逐行列出备查。规格未定义该列语义，属 A 类歧义。

为什么本模块自建建图/读档而不 import graph_scores：U2 的 fail-closed 错误前缀
固定为 [graph-scores]，跨模块复用会把错误来源串线（审计时看不出是谁拒算）；
代价是约 40 行重复，由 test_proximity.py 的"两模块建图口径一致"（needs_data）
守住漂移面。
"""
import argparse
import csv
import hashlib
import os
import random
import statistics
import sys
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------
<<<<<<< HEAD
# 冻结常量（spec §一，逐字）——改这里=过审（评审+用户）。
=======
# 冻结常量（spec §一，逐字）——改这里=过审（监工+用户）。
>>>>>>> origin/main
# ---------------------------------------------------------------
RNG_SEED = 20261105          # spec §1.3：random.Random(20261105) 钉扎
N_PERM = 1000                # spec §1.3：n_perm=1000（文献下限口径）
ALPHA = 0.05                 # spec §1.3：单侧 α=0.05
BOUNDARY_LOW = 0.05          # spec §1.3：边界带 p∈(0.05,0.10)
BOUNDARY_HIGH = 0.10
MIN_BIN_SIZE = 5             # spec §1.3：箱内 <5 向大侧合并
UNREACHABLE_MAX = 0.50       # spec §1.3：不可达配对占比 >50% → 图不适用
TOP_N_SENS = 100             # v1.7：REF_TOP100 参考读数取前 100 卡
MAIN_READ_MODE = "ALL_CHAINS"   # v1.7：主口径=全链 hop1 distinct (菌,代谢物) 配对（710 对）
REF_READ_MODES = ("REF_TOP10", "REF_TOP100")  # v1.7：原两读法降为定性参考（n_pairs=2/4 无统计意义）

SEED_CATEGORIES = ("Species", "Microbe")   # 菌池=Species 286 + Microbe 1 = 287
TARGET_CATEGORY = "Metabolite"             # 代谢物池=274
# 池规模机器实测锚（spec §1.3 写死）——偏离只告警不拒算（夹具/他图会用得到），
# 但评估档必须显式打印 PASS/DEVIATE，防"以为跑的是规格图"。
ANCHOR_POOL = {"seed": 287, "target": 274}

SIDECAR_NAME = "microbe_metabolite_proximity.tsv"
# schema 9 列（spec §1.4，v1.2 加 n_pairs）——顺序即落盘顺序，冻结。
SIDECAR_COLUMNS = [
    "chain_id", "microbe_id", "metabolite_id", "pair_distance",
    "null_mean", "null_sd", "proximity_z", "read_mode", "n_pairs",
]

GOLDEN_LIST_DEFAULT = ("data/logs/golden_baseline_v3_20261102_sha256.txt")
SPEC_PATH = "data/logs/upgrade_v03_spec_20261105.md"


def kg_root() -> Path:
    """仓根定位（与 cli.kg_root 同法）：KG_ROOT 环境变量 > 自动探测。

    为什么允许环境变量：非 editable 打包安装时 parents[2] 不再指向仓，
    数据腿（golden 清单/规格档）的可移植性由 KG_ROOT 提供。
    """
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------
# 读档与建图（fail-closed 族，沿 U2 口径）
# ---------------------------------------------------------------

def load_graph(sg_dir: Path) -> tuple[list[str], dict[str, dict]]:
    """merged_nodes + merged_edges → (节点原序列表, {id: {"category","adj"}})。

    为什么悬空边拒算而非静默跳过：端点不在节点表的边若吞掉，度分布（=零模型
    分箱依据）与最短路全部失真且无痕（血泪史 #6）——宁可拒算让数据异常显形。
    """
    nodes_path = sg_dir / "merged_nodes.tsv"
    edges_path = sg_dir / "merged_edges.tsv"
    for p in (nodes_path, edges_path):
        if not p.exists():
            raise SystemExit(f"[proximity] 缺输入 {p}——无从建图")

    with open(nodes_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in ("id", "category")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[proximity] merged_nodes.tsv 缺列 {missing}"
                             "——fail-closed")
        nodes: list[str] = []
        cat: dict[str, str] = {}
        for r in reader:
            nid = (r["id"] or "").strip()
            if not nid:
                continue
            nodes.append(nid)
            cat[nid] = (r["category"] or "").strip()
    if not nodes:
        raise SystemExit("[proximity] merged_nodes.tsv 零节点——fail-closed")
    if len(set(nodes)) != len(nodes):
        # 重复 id 会稀释池规模与度分布——如实拒算而非去重吞掉
        raise SystemExit("[proximity] merged_nodes.tsv id 重复——fail-closed")

    adj: dict[str, set[str]] = {n: set() for n in nodes}
    dangling = 0
    with open(edges_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in ("subject", "object")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[proximity] merged_edges.tsv 缺列 {missing}"
                             "——fail-closed")
        for r in reader:
            s, o = (r["subject"] or "").strip(), (r["object"] or "").strip()
            if s not in adj or o not in adj:
                dangling += 1
                continue
            # 无向化：邻接集天然去重（合并图同边多证据行只算一条，度不双计）
            adj[s].add(o)
            adj[o].add(s)
    if dangling:
        raise SystemExit(f"[proximity] {dangling} 条边端点不在节点表"
                         "——fail-closed（子图完整性异常，先修数据）")
    g = {n: {"category": cat[n], "adj": adj[n]} for n in nodes}
    return nodes, g


def bfs_distances(adj: dict[str, set[str]], src: str) -> dict[str, int]:
    """单源 BFS 全距离表（无权图最短路；不可达节点不在返回表=缺席即不可达）。"""
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        du = dist[u] + 1
        for v in adj[u]:
            if v not in dist:
                dist[v] = du
                q.append(v)
    return dist


def degree_of(node: str, adj: dict[str, set[str]]) -> int:
    """度=无向邻接集成员数，自环不计（自环不是"连到别的节点"的信息）。"""
    return len(adj[node] - {node})


# ---------------------------------------------------------------
# 人口三口径（v1.7：全链主口径 + 两参考读数）
# ---------------------------------------------------------------

def load_all_chain_pairs(sg_dir: Path) -> list[dict]:
    """chains.tsv 全量行（只取三列）——v1.7 主口径人口来源。

    为什么这是正确的人口：链条实际声称的"菌→代谢物"关系全集（distinct 710 对，
    192 菌 × 201 代谢物），是邻近度问题的真实总体；TOP-N 卡片只是其中极小子集
    （n_pairs=2/4），置换检验无统计意义（v1.7 修正依据，AGENTS 规则 10）。
    """
    src = sg_dir / "chains.tsv"
    if not src.exists():
        raise SystemExit(f"[proximity] 缺输入 {src}——无从取主口径人口")
    rows = []
    with open(src, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        need = ("chain_id", "microbe_id", "metabolite_id")
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[proximity] chains.tsv 缺列 {missing}")
        for r in reader:
            rows.append({"chain_id": r["chain_id"],
                         "microbe_id": r["microbe_id"],
                         "metabolite_id": r["metabolite_id"]})
    if not rows:
        raise SystemExit("[proximity] chains.tsv 零数据行")
    return rows


def load_top_hypotheses(sg_dir: Path) -> list[dict]:
    """top_hypotheses.tsv 全量行（=主读法 TOP10 人口），文件原序。

    只取三列（chain_id/microbe_id/metabolite_id）——sidecar 每行必须可回连链。
    """
    src = sg_dir / "top_hypotheses.tsv"
    if not src.exists():
        raise SystemExit(f"[proximity] 缺输入 {src}——无从取主读法人口")
    with open(src, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in ("chain_id", "microbe_id", "metabolite_id")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[proximity] top_hypotheses.tsv 缺列 {missing}"
                             "——fail-closed")
        rows = []
        for i, r in enumerate(reader):
            cid = (r["chain_id"] or "").strip()
            if not cid:
                raise SystemExit(f"[proximity] top_hypotheses 第 {i + 2} 行"
                                 " chain_id 为空——fail-closed")
            rows.append(_card(r, cid))
    if not rows:
        raise SystemExit("[proximity] top_hypotheses.tsv 零链——fail-closed")
    return rows


def _card(r: dict, cid: str) -> dict:
    """卡片最小投影：链 id + 菌 + 代谢物 + 排名键两列（缺列由调用方 fail-closed）。"""
    return {"chain_id": cid,
            "microbe_id": (r.get("microbe_id") or "").strip(),
            "metabolite_id": (r.get("metabolite_id") or "").strip(),
            "votes_total": (r.get("votes_total") or "").strip(),
            "borda_score": (r.get("borda_score") or "").strip()}


def rank_key(card: dict) -> tuple[float, float, str]:
    """排名键（spec §0.2 写死）：votes_total desc, borda_score desc, chain_id asc。

    为什么用负号：Python 排序升序，取负即降序；chain_id 用升序=同分打平时
    按链 id 字典序——三键全序，排序结果唯一可复现（不许依赖文件行序）。
    """
    try:
        return (-float(card["votes_total"]), -float(card["borda_score"]),
                card["chain_id"])
    except ValueError as exc:
        raise SystemExit(
            f"[proximity] 卡片 {card.get('chain_id')!r} 排名键非数值"
            f"（votes_total={card.get('votes_total')!r}, "
            f"borda_score={card.get('borda_score')!r}）——fail-closed") from exc


def load_top_cards_ranked(sg_dir: Path, n: int = TOP_N_SENS) -> list[dict]:
    """hypothesis_cards.tsv → 按排名键取前 n 卡（**内存副本排序，文件零改动**）。

    为什么必须重排：该文件是 chain_id 字典序而非排名序（spec §0.2 明载），
    "文件前 100 行"会取到任意 100 条链而不是最高分 100 卡。
    """
    src = sg_dir / "hypothesis_cards.tsv"
    if not src.exists():
        raise SystemExit(f"[proximity] 缺输入 {src}——无从取敏感性读法人口")
    need = ("chain_id", "microbe_id", "metabolite_id",
            "votes_total", "borda_score")
    with open(src, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[proximity] hypothesis_cards.tsv 缺列 {missing}"
                             "——fail-closed")
        rows = [_card(r, (r["chain_id"] or "").strip()) for r in reader]
    if not rows:
        raise SystemExit("[proximity] hypothesis_cards.tsv 零卡——fail-closed")
    if any(not r["chain_id"] for r in rows):
        raise SystemExit("[proximity] hypothesis_cards 存在空 chain_id"
                         "——fail-closed")
    rows.sort(key=rank_key)
    return rows[:n]


def population(cards: list[dict]) -> tuple[list[str], list[str], int]:
    """卡片集 → (种子集, 靶集, n_pairs)，均按首现序去重。

    n_pairs=distinct (microbe, metabolite) 配对数——主读法=2、敏感性=4
    （spec §1.3 要求强制披露 n_pairs）。
    """
    seeds: list[str] = []
    targets: list[str] = []
    pairs: list[tuple[str, str]] = []
    for c in cards:
        if not c["microbe_id"] or not c["metabolite_id"]:
            raise SystemExit(
                f"[proximity] 卡片 {c['chain_id']} 菌或代谢物为空"
                "——fail-closed（配对距离无对象）")
        if c["microbe_id"] not in seeds:
            seeds.append(c["microbe_id"])
        if c["metabolite_id"] not in targets:
            targets.append(c["metabolite_id"])
        p = (c["microbe_id"], c["metabolite_id"])
        if p not in pairs:
            pairs.append(p)
    return seeds, targets, len(pairs)


def build_read_modes(sg_dir: Path) -> list[tuple[str, list[dict]]]:
    """三口径（v1.7：主口径在前，两个参考读数在后）：[(read_mode, rows), ...]。"""
    return [(MAIN_READ_MODE, load_all_chain_pairs(sg_dir)),
            ("REF_TOP10", load_top_hypotheses(sg_dir)),
            ("REF_TOP100", load_top_cards_ranked(sg_dir))]


# ---------------------------------------------------------------
# 度匹配分箱与零模型候选池（spec §1.3 + §3.2 分箱规则）
# ---------------------------------------------------------------

def degree_bins(pool: list[str], degree: dict[str, int],
                min_size: int = MIN_BIN_SIZE
                ) -> tuple[dict[int, int], dict[int, list[str]]]:
    """按精确 degree 分箱；箱内 <5 向**大侧**合并，最大箱 <5 并入**小侧**。

    返回 (degree→箱号, 箱号→成员列表(升序))。为什么用"升序累积到 ≥5"实现：
    规格的"小箱向 degree 更大一侧合并，至箱内 ≥5 或并尽"等价于"按 degree 升序
    累积、满 5 即封箱"；最后不足 5 的残余正是最大箱，按规格并入其小一侧。
    箱号按 degree 升序递增（确定性，无哈希顺序依赖）。
    """
    by_deg: dict[int, list[str]] = {}
    for n in pool:
        by_deg.setdefault(degree[n], []).append(n)
    groups: list[tuple[list[int], list[str]]] = []
    cur_degs: list[int] = []
    cur_members: list[str] = []
    for d in sorted(by_deg):
        cur_degs.append(d)
        cur_members.extend(sorted(by_deg[d]))
        if len(cur_members) >= min_size:
            groups.append((cur_degs, cur_members))
            cur_degs, cur_members = [], []
    if cur_members:                      # 残余=最大箱（<5）→ 并入小一侧
        if groups:
            pd, pm = groups[-1]
            groups[-1] = (pd + cur_degs, pm + cur_members)
        else:                            # 全池都不足 min_size：单箱兜住
            groups.append((cur_degs, cur_members))
    deg2bin: dict[int, int] = {}
    bin2mem: dict[int, list[str]] = {}
    for i, (degs, members) in enumerate(groups):
        for d in degs:
            deg2bin[d] = i
        bin2mem[i] = sorted(members)
    return deg2bin, bin2mem


def null_candidates(pool: list[str], degree: dict[str, int],
                    min_size: int = MIN_BIN_SIZE
                    ) -> tuple[dict[str, list[str]],
                               list[tuple[int, int, int]]]:
    """每个池成员的替换候选（同 degree 箱、排除自身），升序以便确定性抽取。

    返回 (node→候选列表, 箱摘要[(箱号, 最小度, 最大度, 成员数)])。
    候选为空 → fail-closed：该位置无法置换，静默跳过=零分布被系统性改写。
    """
    deg2bin, bin2mem = degree_bins(pool, degree, min_size)
    cands: dict[str, list[str]] = {}
    for n in pool:
        b = deg2bin[degree[n]]
        others = [m for m in bin2mem[b] if m != n]
        if not others:
            raise SystemExit(
                f"[proximity] 节点 {n}（度 {degree[n]}）所在 degree 箱仅其本身"
                "——置换候选为空，fail-closed（零模型不可构造）")
        cands[n] = others
    summary = []
    for b in sorted(bin2mem):
        members = bin2mem[b]
        ds = [degree[m] for m in members]
        summary.append((b, min(ds), max(ds), len(members)))
    return cands, summary


# ---------------------------------------------------------------
# 统计量（AMSPL / 全部配对平均 / Menche separation）
# ---------------------------------------------------------------

def amspl(adj: dict[str, set[str]], A: list[str], B: list[str]
          ) -> tuple[float | None, float]:
    """AMSPL d_c(A,B)=(1/|A|)Σ_a min_{b∈B} d(a,b) → (d_c, 不可达配对占比)。

    不可达处理（spec §1.3 "不可达记为 +∞"）：任一 a 对全 B 不可达 → d_c 判
    不可算（None），不折算大数——折大数会造出"伪精确"的 z（Menche 2015 惯例，
    与 U2 同制）。不可达占比按 (a,b) 配对计数，供 >50% 门禁与强制披露。
    空集 → None（调用方 fail-closed，不在此静默返回 0）。
    """
    if not A or not B:
        return None, 0.0
    per_seed: list[int | None] = []
    unreachable = 0
    total = 0
    for a in A:
        dist = bfs_distances(adj, a)
        best: int | None = None
        for b in B:
            total += 1
            d = dist.get(b)
            if d is None:
                unreachable += 1
            elif best is None or d < best:
                best = d
        per_seed.append(best)
    rate = unreachable / total if total else 0.0
    if any(x is None for x in per_seed):
        return None, rate
    return sum(per_seed) / len(per_seed), rate


def mean_all_pairs_distance(adj: dict[str, set[str]], A: list[str],
                            B: list[str]) -> float | None:
    """辅助描述量：全部 A×B 配对平均距离（**非 AMSPL、不引 Guney、不参与 z**）。

    为什么仍要算：规格把它列为辅助描述量（分布形状可比性），且它是"旧口径"
    的数值留痕——报告同列两者，读者可自证主指标不是它。
    """
    if not A or not B:
        return None
    vals: list[int] = []
    for a in A:
        dist = bfs_distances(adj, a)
        for b in B:
            d = dist.get(b)
            if d is None:
                return None
            vals.append(d)
    return sum(vals) / len(vals)


def internal_amspl(adj: dict[str, set[str]], S: list[str]) -> float | None:
    """集合内部 AMSPL（Menche d_AA/d_BB）：每成员取到**其它成员**最近距离再平均。

    为什么排除自身项：不排除则 min_{s'∈S} d(s,s') 恒为 0（自己到自己），
    d_AA/d_BB 变 0 使 s_AB 公式失去意义。|S|<2 时无"其它成员"→ None（na）。
    """
    if len(S) < 2:
        return None
    per: list[int] = []
    for s in S:
        dist = bfs_distances(adj, s)
        best = None
        for o in S:
            if o == s:
                continue
            d = dist.get(o)
            if d is None:
                return None
            if best is None or d < best:
                best = d
        per.append(best)          # type: ignore[arg-type]
    return sum(per) / len(per)


def menche_separation(adj: dict[str, set[str]], A: list[str], B: list[str],
                      d_ab: float | None) -> dict:
    """Menche separation s_AB = d_AB − (d_AA + d_BB)/2（附加输出，不参与判定）。

    s<0=模块重叠、s>0=拓扑分离；供文献（Menche 2015）可比性。任一内部量为
    None（|B|=1 或不可达）→ s_AB=None 如实 na，不硬造 0。
    """
    d_aa = internal_amspl(adj, A)
    d_bb = internal_amspl(adj, B)
    if d_ab is None or d_aa is None or d_bb is None:
        s = None
    else:
        s = d_ab - (d_aa + d_bb) / 2.0
    return {"d_ab": d_ab, "d_aa": d_aa, "d_bb": d_bb, "s_ab": s}


# ---------------------------------------------------------------
# 置换零模型 + z/p
# ---------------------------------------------------------------

def permutation_null(adj: dict[str, set[str]],
                     cands: dict[str, list[str]],
                     seed_positions: list[str], target_positions: list[str],
                     seed: int = RNG_SEED, n_perm: int = N_PERM) -> dict:
    """度匹配置换零分布 → {nulls, n_unreachable, rng_seed, n_perm}。

    口径（spec §1.3 + 裁定 A1/A2/A5）：
    - 每个置换逐位置独立抽（每位置 1 个），候选=同类别同 degree 箱、排除原节点；
    - 每个读法独立 random.Random(20261105) 起算（逐读法重置，互不传染）；
    - 抽到不可达置换不重抽（spec"零模型中同样允许不可达"），只计数并从
      mean/sd 剔除——否则 +∞ 会把均值拉爆，z 无定义。
    为什么按"位置"而不是"集合"抽：种子集/靶集规模固定必须逐位置替换；
    整集联合抽会出现"箱不够大"的判断分支（规格明令禁止的口径）。
    """
    rng = random.Random(seed)
    nulls: list[float] = []
    n_unreach = 0
    for _ in range(n_perm):
        a2 = [rng.choice(cands[n]) for n in seed_positions]
        b2 = [rng.choice(cands[n]) for n in target_positions]
        d, _rate = amspl(adj, a2, b2)
        if d is None:
            n_unreach += 1
            continue
        nulls.append(d)
    return {"nulls": nulls, "n_unreachable": n_unreach, "rng_seed": seed,
            "n_perm": n_perm}


def one_sided_p(d_obs: float, nulls: list[float]) -> float:
    """单侧 p=（#{d_rand ≤ d_obs} + 1）/（n + 1）（spec §1.3 小数修正）。

    +1 修正防 p=0 的"无限显著"假象；分母用实际入算的零分布长度——
    无不可达时恰为 n_perm+1（与规格字面式一致），有剔除时如实缩分母并在
    评估档披露剔除数。
    """
    le = sum(1 for x in nulls if x <= d_obs)
    return (le + 1) / (len(nulls) + 1)


def z_score(d_obs: float, nulls: list[float]) -> tuple[float | None, float | None,
                                                      float | None]:
    """z=(d_c−mean)/sd（sd=总体标准差）→ (z, mean, sd)；sd=0 → z None。

    为什么 pstdev 而非 stdev：与 numpy 默认 ddof=0 及 U2 口径对齐，
    报告里同一 z 不因库不同而漂移。
    """
    if len(nulls) < 2:
        return None, None, None
    mean = statistics.fmean(nulls)
    sd = statistics.pstdev(nulls)
    if sd == 0:
        return None, mean, sd
    return (d_obs - mean) / sd, mean, sd


def boundary_label(p: float | None) -> str:
    """边界带标签：p∈(0.05,0.10) → "边界带"（不追加任何事后检验，spec 预指定）。"""
    if p is not None and BOUNDARY_LOW < p < BOUNDARY_HIGH:
        return "边界带"
    return ""


def verdict_label(z: float | None, p: float | None) -> str:
    """单侧判定（z<0=更近=H1 方向）；不用"验证成立"式粉饰（负结果=正式结果）。"""
    if z is None or p is None:
        return "不可算"
    if p <= ALPHA:
        return "显著更近（H1 方向）" if z < 0 else "显著更远（与 H1 反向）"
    if boundary_label(p):
        return "边界带（不显著，不追加检验）"
    return "不显著"


def direction_of(z: float | None) -> str:
    """方向标签：closer/farther/none（用于两读法一致性判定）。"""
    if z is None:
        return "na"
    if z < 0:
        return "closer"
    if z > 0:
        return "farther"
    return "none"


def two_read_consistency(main_z: float | None,
                         sens_z: float | None) -> dict:
    """两读法方向一致性（spec §1.2）：同号=一致；异号或任一不可算=不一致。

    为什么必须一致才出主数字（v1.7 历史注：该前置已弃用，因 TOP-N 人口
    n_pairs=2 无统计意义；现主口径为全链 710 对，参考读数仅定性）：原主读法 n_pairs=2，单读法偶然性无法
    排除；两读法方向相反即"小样本偶然"的直接证据，只报不一致。
    """
    a, b = direction_of(main_z), direction_of(sens_z)
    if a == "na" or b == "na":
        return {"consistent": False, "reason": "存在不可算读法",
                "main": a, "sens": b}
    if a != b:
        return {"consistent": False, "reason": "两读法 z 异号", "main": a, "sens": b}
    if a == "none":
        return {"consistent": False, "reason": "z=0 无方向", "main": a, "sens": b}
    return {"consistent": True,
            "reason": "两读法 z 同号（" +
                      ("同为更近" if a == "closer" else "同为更远") + "）",
            "main": a, "sens": b}


# ---------------------------------------------------------------
# 单读法全算：门禁 → AMSPL → 零模型 → z/p → Menche
# ---------------------------------------------------------------

def evaluate_read_mode(nodes: list[str], adj: dict[str, set[str]],
                       cards: list[dict], read_mode: str,
                       cands_seed: dict[str, list[str]],
                       cands_target: dict[str, list[str]],
                       n_perm: int = N_PERM,
                       rng_seed: int = RNG_SEED) -> dict:
    """一个读法的全部数字（确定性纯函数：同输入同输出）。"""
    seeds, targets, n_pairs = population(cards)
    node_set = set(nodes)
    missing = [n for n in seeds + targets if n not in node_set]
    if missing:
        raise SystemExit(
            f"[proximity] {read_mode} 人口节点不在 merged_nodes：{missing}"
            "——fail-closed（缺节点≠不可达，无从定义距离）")
    # 类别校验：卡片若把疾病节点填成 microbe_id，池里查不到候选 → 置换必炸；
    # 提前 fail-closed 说清楚是"人口类别不符"，而不是深层的 KeyError。
    bad_seed = [n for n in seeds if n not in cands_seed]
    bad_target = [n for n in targets if n not in cands_target]
    if bad_seed or bad_target:
        raise SystemExit(
            f"[proximity] {read_mode} 人口类别不符池定义（菌=Species∪Microbe / "
            f"代谢物=Metabolite）：菌 {bad_seed} / 代谢物 {bad_target}"
            "——fail-closed")

    d_c, unreach_rate = amspl(adj, seeds, targets)
    not_applicable = unreach_rate > UNREACHABLE_MAX
    res = {"read_mode": read_mode, "n_cards": len(cards),
           "n_seeds": len(seeds), "n_targets": len(targets),
           "n_pairs": n_pairs, "seeds": seeds, "targets": targets,
           "d_c": d_c, "mean_all_pairs": mean_all_pairs_distance(adj, seeds,
                                                                 targets),
           "unreachable_rate": unreach_rate,
           "not_applicable": not_applicable,
           "null_mean": None, "null_sd": None, "z": None, "p": None,
           "ratio": None, "boundary": "", "verdict": "not_run",
           "n_null_used": 0, "n_null_unreachable": 0}
    if not_applicable:
        # spec §1.3：不可达配对 >50% → "图不适用"，只报不可达率不出 z
        res["verdict"] = "图不适用（不可达率>50%）"
        res["menche"] = menche_separation(adj, seeds, targets, None)
        return res
    if d_c is None:
        # 部分不可达（≤50%）但存在"全靶不可达"的种子 → d_c 不可算，如实 na
        res["verdict"] = "不可算（存在全靶不可达种子）"
        res["menche"] = menche_separation(adj, seeds, targets, None)
        return res

    seed_pos = [n for n in seeds]       # 逐位置=去重后的种子，顺序确定
    targ_pos = [n for n in targets]
    null = permutation_null(adj, {**cands_seed, **cands_target},
                            seed_pos, targ_pos,
                            seed=rng_seed, n_perm=n_perm)
    z, mean, sd = z_score(d_c, null["nulls"])
    p = one_sided_p(d_c, null["nulls"]) if null["nulls"] else None
    res.update({"null_mean": mean, "null_sd": sd, "z": z, "p": p,
                "ratio": (d_c / mean) if mean else None,
                "boundary": boundary_label(p),
                "verdict": verdict_label(z, p),
                "n_null_used": len(null["nulls"]),
                "n_null_unreachable": null["n_unreachable"],
                "menche": menche_separation(adj, seeds, targets, d_c)})
    return res


# ---------------------------------------------------------------
# 渲染（sidecar payload + 评估档 markdown）
# ---------------------------------------------------------------

def _fmt(x: float | None, nd: int = 6) -> str:
    return "na" if x is None else f"{x:.{nd}f}"


def render_sidecar(results: list[dict], cards_by_mode: dict[str, list[dict]]
                   ) -> str:
    """sidecar 文本（schema 9 列；每卡一行，读法级量随行重复）。

    行序：主读法在前、读法内按人口原序/排名序——行序即确定性来源之一，
    不排序（零排序变更）。
    """
    lines = ["\t".join(SIDECAR_COLUMNS)]
    for res in results:
        for card in cards_by_mode[res["read_mode"]]:
            lines.append("\t".join((
                card["chain_id"], card["microbe_id"], card["metabolite_id"],
                _fmt(res["d_c"]), _fmt(res["null_mean"]), _fmt(res["null_sd"]),
                _fmt(res["z"]), res["read_mode"], str(res["n_pairs"]))))
    return "\n".join(lines) + "\n"


def render_eval_md(results: list[dict], pool_report: dict,
                   bin_summary: dict, consistency: dict,
                   spec_sha: str, sidecar_sha: str) -> str:
    """评估档 markdown（**零时间戳=逐字节可复现**，沿 B3 惯例）。

    为什么把 n_pairs/不可达率/池清单/分箱表全写进评估档：spec §1.3 要求池成员
    清单与分箱映射随评估档落盘；n_pairs 与不可达率是强制披露项。
    """
    by_mode = {r["read_mode"]: r for r in results}
    main = by_mode[MAIN_READ_MODE]
    sens = by_mode.get("REF_TOP100")   # v1.7：参考读数（原敏感性读法）
    out: list[str] = []
    a = out.append
    a("# ②乙 菌→代谢物网络邻近度评估档（v0.3 四件之一）")
    a("")
    a(f"> 规格：`{SPEC_PATH}` §一（冻结 v1.5），规格档 sha256=`{spec_sha}`")
    a(f"> sidecar sha256=`{sidecar_sha}`（本档与 sidecar 均零时间戳，"
      "同输入同平台逐字节可复现）")
    a("> 统计族声明：②乙+③甲+③乙 为同一检验族，但三问互不替代 → "
      "**不做族内 α 校正**；论文报告须同段并列三组结果并注明此句（spec §0.3）。")
    a("")
    a("## 一、图版本锚与池规模锚（spec §0.1/§1.3）")
    a("")
    a("| 项 | 规格锚 | 实测 | 判定 |")
    a("|---|---|---|---|")
    for key, label in (("seed", "菌池（Species∪Microbe）"),
                       ("target", "代谢物池（Metabolite）")):
        p = pool_report[key]
        ok = "PASS" if p["n"] == p["anchor"] else "DEVIATE"
        a(f"| {label} | {p['anchor']} | {p['n']} | {ok} |")
    a(f"| 节点总数 | — | {pool_report['n_nodes']} | — |")
    a(f"| 边（去重无向） | — | {pool_report['n_edges_undirected']} | — |")
    a("")
    a("## 二、两读法主表（spec §1.2/§1.3）")
    a("")
    a("| 读法 | 卡数 | 种子 | 靶集 | **n_pairs** | d_c (AMSPL) | "
      "mean_all_pairs（辅助·不参与 z） | null_mean | null_sd | z | 单侧 p | "
      "d_c/null_mean | 不可达率 | 判定 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        a(f"| {r['read_mode']} | {r['n_cards']} | {r['n_seeds']} | "
          f"{r['n_targets']} | **{r['n_pairs']}** | {_fmt(r['d_c'], 4)} | "
          f"{_fmt(r['mean_all_pairs'], 4)} | {_fmt(r['null_mean'], 4)} | "
          f"{_fmt(r['null_sd'], 4)} | {_fmt(r['z'], 4)} | {_fmt(r['p'], 6)} | "
          f"{_fmt(r['ratio'], 4)} | {_fmt(r['unreachable_rate'], 4)} | "
          f"{r['verdict']} |")
    a("")
    a("注：`d_c` 即 sidecar 的 `pair_distance` 列（裁定 A6）；"
      "`mean_all_pairs_distance` 为辅助描述量，非 AMSPL、不参与 z（spec §1.3）。")
    if any(r["n_targets"] == 1 for r in results):
        a("注：靶集唯一的读法下 `mean_all_pairs` 与 AMSPL **数学恒等**"
          "（单靶时 min_{b∈B} 即该靶，两种口径退化同值）——不是口径混用；"
          "两口径的差异只在多靶读法可见，见上表 TOP100 行。")
    a(f"注：零模型 n_perm={N_PERM}（文献下限口径：NetMedPy 示例常用 10,000，"
      "本仓受算力约束取 1,000，p 分辨率 1e-3 量级）；"
      f"钉扎 `random.Random({RNG_SEED})` 逐读法重置。")
    if main["n_null_unreachable"] or (sens and sens["n_null_unreachable"]):
        a(f"注：不可达置换剔除数（不重抽，spec 允许不可达）："
          f"主读法 {main['n_null_unreachable']}、敏感性 "
          f"{sens['n_null_unreachable'] if sens else 'na'}。")
    a("")
    a("## 三、Menche separation（附加输出，**不参与主检验判定**）")
    a("")
    a("| 读法 | d_AB | d_AA（种子集内部） | d_BB（靶集内部） | s_AB | 解读 |")
    a("|---|---|---|---|---|---|")
    for r in results:
        m = r["menche"]
        if m["s_ab"] is None:
            note = "na（靶集成员数<2 或不可达，内部量不可定义）"
        elif m["s_ab"] < 0:
            note = "s<0=模块重叠"
        else:
            note = "s>0=拓扑分离"
        a(f"| {r['read_mode']} | {_fmt(m['d_ab'], 4)} | {_fmt(m['d_aa'], 4)} | "
          f"{_fmt(m['d_bb'], 4)} | {_fmt(m['s_ab'], 4)} | {note} |")
    a("")
    a("## 四、两读法方向一致性（spec §1.2 判定条款）")
    a("")
    zh = {"closer": "更近（z<0）", "farther": "更远（z>0）",
          "none": "无方向（z=0）", "na": "不可算"}
    a(f"- 主口径（{MAIN_READ_MODE}）方向：`{consistency['main']}`={zh.get(consistency['main'], '?')}；"
      f"参考读数 REF_TOP100 方向：`{consistency['sens']}`="
      f"{zh.get(consistency['sens'], '?')}")
    a(f"- 参考叙述（v1.7：**不作为出分前置**，样本量差 355 倍无可比性前提）："
      f"**{'同向' if consistency['consistent'] else '异向'}**"
      f"（{consistency['reason']}）")
    if consistency["consistent"]:
        a(f"- 处置：主口径 n_pairs={main['n_pairs']}（全链 distinct 配对）→ "
          "**可出主数字**；两参考读数（REF_TOP10/REF_TOP100，n_pairs=2/4）"
          "样本量不足，仅作定性参考（v1.7）。")
    else:
        a("- 处置：**只报方向不一致，不出主数字**（防小样本偶然，spec §1.2）。")
    a("")
    a("## 五、预写解读句（spec §1.1，禁成稿期改口径）")
    a("")
    # 撞下限披露（v1.7 新增）：置换 p 撞到 1/(n_perm+1) 时须显式说明"真实 p 更小"
    _floor = 1.0 / (N_PERM + 1)
    for _r in results:
        if _r.get("p") is not None and _r["p"] <= _floor * 1.0001:
            import math as _m
            _pz = 0.5 * _m.erfc(-(_r["z"] or 0.0) / _m.sqrt(2))
            a(f"注（{_r['read_mode']}）：实测 p={_r['p']:.6f} **已撞 n_perm={N_PERM} 的分辨率下限**"
              f"（1/{N_PERM + 1}）——真实 p 更小（正态近似 p≈{_pz:.2e}）；"
              f"报告与论文引用须写'p 达置换分辨率下限'，不得写具体小数值。")
    if main["z"] is not None and main["p"] is not None and main["p"] <= ALPHA \
            and main["z"] < 0:
        a(f"- H1 方向成立 → 按预注册报告真配对更近；**主口径 n_pairs="
          f"{main['n_pairs']}**（全链），结论由其承载；参考读数仅定性（v1.7）。")
    else:
        a("- **H1 不成立 → 预写解读句（原样照抄）**：")
        a("")
        a("  > 菌与其它称可产的代谢物在图结构上并无邻近优势——图谱的三层子图"
          "结构不携带菌-代谢物伙伴信息（与 B3/R1 的边际证据发现一致，"
          "第三重互证）。")
    if main["boundary"] or (sens and sens["boundary"]):
        a("")
        a("- **边界带**：p∈(0.05,0.10) 的读法标「边界带」；主结论句式不变，"
          "**不追加任何事后检验**（不换检验、不加样本、不改零模型）。")
    a("")
    a("## 六、逐行配对距离（行级，供审计；sidecar 的 pair_distance 是读法级 AMSPL）")
    a("")
    a("| 读法 | chain_id | microbe_id | metabolite_id | d(菌,代谢物) |")
    a("|---|---|---|---|---|")
    for r in results:
        for card in r["cards"]:
            d = r["pair_distances"][(card["microbe_id"], card["metabolite_id"])]
            a(f"| {r['read_mode']} | {card['chain_id']} | {card['microbe_id']} | "
              f"{card['metabolite_id']} | {_fmt(d, 0) if d is not None else 'na'} |")
    a("")
    a("## 七、度匹配分箱表（spec §1.3：精确 degree 分箱+箱内<5 向大侧合并）")
    a("")
    for key, label in (("seed", "菌池"), ("target", "代谢物池")):
        a(f"### {label}（{pool_report[key]['n']} 节点，"
          f"{len(bin_summary[key])} 箱）")
        a("")
        a("| 箱号 | degree 范围 | 成员数 |")
        a("|---|---|---|")
        for b, lo, hi, n in bin_summary[key]:
            a(f"| {b} | {lo}–{hi} | {n} |")
        a("")
    a("## 八、池内成员清单（spec §1.3 要求随评估档落盘）")
    a("")
    for key, label in (("seed", "菌池"), ("target", "代谢物池")):
        a(f"### {label}（{pool_report[key]['n']}）")
        a("")
        a("```")
        a(", ".join(pool_report[key]["members"]))
        a("```")
        a("")
    a("## 九、规格歧义登记表（A 类·实现期裁定，禁静默自决）")
    a("")
    a("| 编号 | 规格缺口 | 本实现口径 | 依据/理由 |")
    a("|---|---|---|---|")
    a("| A1 | 置换是否排除原节点 | 排除 | 沿同规格 §3.2 步 6 既定口径 |")
    a("| A2 | 逐位置抽 vs 整集联合抽；有/无放回 | 逐位置独立抽（每位置 1 个） | "
      "§3.2 步 6「有放回是定量口径」；避免「箱够不够大」判断分支 |")
    a("| A3 | 内部 AMSPL 是否排除自身项 | 排除 | 数学必需（否则 min 恒 0） |")
    a("| A4 | 部分不可达（≤50%）的 d_c | 判不可算（na） | 不折算大数，沿 U2/Menche 惯例 |")
    a("| A5 | 两读法 RNG 是否共用流 | 每读法独立起算 | 沿 U2「逐次重置」同制，任意读法重跑全等 |")
    a("| A6 | pair_distance 列语义 | 读法级 AMSPL d_c | sidecar 自证；行级距离见 §六 |")
    a("")
    a("## 十、如实披露")
    a("")
    a(f"- 零模型为 NetMedPy perfect degree match 的**近似**（精确分箱+<5 合并），"
      "非逐位复现（spec §1.3 要求注明）。")
    a("- 本档不含任何时间戳；两读法 RNG 与行序均确定性 → 同输入双跑逐字节全等。")
    a("- 负结果=正式结果：不出现「对照通过/验证成立」式粉饰（spec §0.4）。")
    a("")
    return "\n".join(out)


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------

def compute(sg_dir: Path) -> dict:
    """全流程 → {sidecar, eval_md, results, ...}（纯函数；同输入必同字节）。"""
    nodes, g = load_graph(sg_dir)
    adj = {n: g[n]["adj"] for n in nodes}
    degree = {n: degree_of(n, adj) for n in nodes}
    seed_pool = [n for n in nodes if g[n]["category"] in SEED_CATEGORIES]
    target_pool = [n for n in nodes if g[n]["category"] == TARGET_CATEGORY]
    if not seed_pool or not target_pool:
        raise SystemExit("[proximity] 菌池或代谢物池为空——fail-closed")

    cands_seed, summary_seed = null_candidates(seed_pool, degree)
    cands_target, summary_target = null_candidates(target_pool, degree)

    read_modes = build_read_modes(sg_dir)
    cards_by_mode = {name: cards for name, cards in read_modes}
    results = []
    for read_mode, cards in read_modes:
        res = evaluate_read_mode(nodes, adj, cards, read_mode,
                                 cands_seed, cands_target)
        # 逐行配对距离（评估档 §六；sidecar 不落该列，见裁定 A6）
        pd: dict[tuple[str, str], int | None] = {}
        for c in cards:
            key = (c["microbe_id"], c["metabolite_id"])
            if key not in pd:
                dist = bfs_distances(adj, c["microbe_id"])
                pd[key] = dist.get(c["metabolite_id"])
        res["pair_distances"] = pd
        res["cards"] = cards
        results.append(res)

    by_mode = {r["read_mode"]: r for r in results}
    # v1.7：一致性检查降为"主口径 vs REF_TOP100"的参考叙述（不再是出分前置）
    consistency = two_read_consistency(by_mode[MAIN_READ_MODE]["z"],
                                       by_mode["REF_TOP100"]["z"]
                                       if "REF_TOP100" in by_mode else None)

    n_edges_undirected = sum(len(a) for a in adj.values()) // 2
    pool_report = {
        "seed": {"anchor": ANCHOR_POOL["seed"], "n": len(seed_pool),
                 "members": sorted(seed_pool, key=lambda n: (degree[n], n))},
        "target": {"anchor": ANCHOR_POOL["target"], "n": len(target_pool),
                   "members": sorted(target_pool,
                                     key=lambda n: (degree[n], n))},
        "n_nodes": len(nodes),
        "n_edges_undirected": n_edges_undirected,
    }
    bin_summary = {"seed": summary_seed, "target": summary_target}

    sidecar = render_sidecar(results, cards_by_mode)
    sidecar_sha = hashlib.sha256(sidecar.encode("utf-8")).hexdigest()
    spec_path = kg_root() / SPEC_PATH
    spec_sha = (hashlib.sha256(spec_path.read_bytes()).hexdigest()
                if spec_path.exists() else "na")
    eval_md = render_eval_md(results, pool_report, bin_summary, consistency,
                             spec_sha, sidecar_sha)
    return {"sidecar": sidecar, "eval_md": eval_md, "results": results,
            "consistency": consistency, "pool_report": pool_report,
            "bin_summary": bin_summary, "sidecar_sha": sidecar_sha,
            "spec_sha": spec_sha}


def annotate(sg_dir: Path, eval_out: Path | None = None) -> dict:
    """落 sidecar（只增不改护栏）+ 可选评估档。"""
    stats = compute(sg_dir)
    sidecar_payload = stats["sidecar"]
    out_path = sg_dir / SIDECAR_NAME
    if out_path.exists():
        old = out_path.read_bytes()
        if old == sidecar_payload.encode("utf-8"):
            stats.update({"idempotent": True, "out": str(out_path)})
        else:
            raise SystemExit(
                f"[proximity] {out_path} 已存在且与本次产物不一致——"
                "只增不改区拒写（疑子图/人口/口径漂移；人工移走旧档过审后重跑）")
    else:
        out_path.write_text(sidecar_payload, encoding="utf-8")
        stats.update({"idempotent": False, "out": str(out_path)})
    if eval_out is not None:
        eval_out.parent.mkdir(parents=True, exist_ok=True)
        eval_out.write_text(stats["eval_md"], encoding="utf-8")
        stats["eval_out"] = str(eval_out)
    return stats


def verify_golden_baseline(sg_dir: Path, golden_list: Path) -> list[dict]:
    """golden 冻结清单逐行核验（证①）→ [{name, frozen, actual, ok}]。

    为什么在这里做而不是 scripts/golden_check.py：后者比对"整轮运行目录"
    （输入指纹+manifest 白名单），本件只新增一个 sidecar 件、不跑主链，
    逐件 sha 核验才是与"十三件零改动"对应的最小证据。
    清单第 13 行带 "(=runA_arm/hypothesis_cards.tsv)" 注记（同名异路径），
    解析时按第一个 "(" 截断。
    """
    if not golden_list.exists():
        raise SystemExit(f"[proximity] 缺 golden 冻结清单 {golden_list}")
    rows: list[dict] = []
    for line in golden_list.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        sha, name = line.split(None, 1)
        name = name.split("(", 1)[0].strip()
        p = sg_dir / name
        actual = (hashlib.sha256(p.read_bytes()).hexdigest()
                  if p.exists() else "MISSING")
        rows.append({"name": name, "frozen": sha, "actual": actual,
                     "ok": actual == sha})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kg proximity",
        description="②乙 菌→代谢物网络邻近度（AMSPL+度匹配置换零模型，"
                    "sidecar 落主子图目录）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="子图目录（读 merged_nodes/merged_edges/"
                         "top_hypotheses/hypothesis_cards）")
    ap.add_argument("--eval-out", type=Path, default=None,
                    help="评估档 markdown 落点（零时间戳，可逐字节复现）")
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
        print(f"[proximity] golden 核验 {len(rows)} 件 | 不一致 {bad} 件")
        print(f"[proximity] 清单={golden}")
        return 0 if bad == 0 else 1

    stats = annotate(args.subgraph_dir, args.eval_out)
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    print(f"[proximity] {stats['out']} {verb}")
    print(f"[proximity] sidecar sha256={stats['sidecar_sha']}")
    print(f"[proximity] 节点 {stats['pool_report']['n_nodes']} | "
          f"菌池 {stats['pool_report']['seed']['n']}"
          f"(锚 {ANCHOR_POOL['seed']}) | "
          f"代谢物池 {stats['pool_report']['target']['n']}"
          f"(锚 {ANCHOR_POOL['target']})")
    for r in stats["results"]:
        print(f"[proximity] {r['read_mode']}: 卡 {r['n_cards']} | "
              f"种子 {r['n_seeds']} | 靶 {r['n_targets']} | "
              f"n_pairs {r['n_pairs']} | d_c={_fmt(r['d_c'], 4)} | "
              f"mean_all_pairs={_fmt(r['mean_all_pairs'], 4)} | "
              f"null={_fmt(r['null_mean'], 4)}±{_fmt(r['null_sd'], 4)} | "
              f"z={_fmt(r['z'], 4)} | p={_fmt(r['p'], 6)} | "
              f"ratio={_fmt(r['ratio'], 4)} | 不可达率={_fmt(r['unreachable_rate'], 4)}"
              f" | s_AB={_fmt(r['menche']['s_ab'], 4)} | {r['verdict']}")
    c = stats["consistency"]
    print(f"[proximity] 两读法方向：主={c['main']} 敏感性={c['sens']} | "
          f"{'一致' if c['consistent'] else '不一致'}（{c['reason']}）")
    if stats.get("eval_out"):
        print(f"[proximity] 评估档 {stats['eval_out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
