"""U2 图结构分 sidecar（B2/U2，spec v1.1 + LCC 前置检验先行落档）。

定位与红线：
- 只读子图三表（merged_nodes/merged_edges/top_hypotheses），只增 sidecar
  graph_structure_scores.tsv 到同一子图目录（只增不改区：sidecar 已存在且
  字节一致=幂等 no-op；不一致=fail-closed 拒写——防两次运行产物混放，
  与 U1 causal_marks 同一护栏）；
- 四票排序键零改动——本模块不排序、不回写任何既有表，行序=top_hypotheses
  原序（零排序变更红线）；对外明示"非排序分量，供复核"；
- 降级路线（LCC 报告 2026-11-05 结论，data/logs/lcc_report_20261105.md）：
  该 HTN 子图无基因层节点，靶集缺节点率 7/7=100% > 10% 阈值 → 不产
  proximity_z 硬分（置换零模型零消耗不运行），sidecar 只产描述性列。

三列口径（spec v1.1）：
①proximity_z：Guney 2016 邻近度公式自实现——d(S,T)=(1/|T|)Σ_t min_{s∈S}
  d(s,t)（平均最短路），z=(d_obs-d̄_rand)/σ；生产路径钉扎=random.Random
  (20261105)（零依赖标准库，验证条款①以此为准）**逐次重置=每条链的零模型
  独立从钉扎种子起算**（链间互不传染，任意链序重跑 sha 相等）；n_perm=1000。
  降级路线下不运行，列统一填 "na"。
②degree_centrality_disclosed：端点基因在子图中的度中心性=度/(n_nodes-1)。
  列名带 _disclosed：度中心性受文献热门度偏差影响（被研究多的基因连边多），
  供复核不作排序分量。
③community_degraded：leidenalg 转正未过（B0 依赖转正制；实测包在位但
  python-igraph 缺失，import 即断）→ 按钉扎降级为 connected_components
  实现（纯标准库 BFS）。取值=菌与端点基因是否同连通片（1/0；leidenalg
  转正后对应"同一社区"）；节点缺图=na。列名 community_degraded 如实标注。

T0 实现细节裁定（spec 未细指，记 CHANGELOG 台账）：
- 人口=top_hypotheses.tsv 全量行（TOP100 帽=min(100,行数)，任务钉扎源），
  行序=文件原序；种子集=人口唯一 microbe_id，靶集=人口唯一非空 hop2_gene；
- 描述性三常量列（lcc_share/seed_missing_rate/target_missing_rate）随每行
  重复+degraded_mode 标志列——消费方读单行即可知降级状态，免查外部报告；
- 度中心性分母=756（n_nodes-1，节点表满刻度）；自环不计度；
- 零模型不可达置换对的处置=拒绝重抽（上限 10×n_perm 次；连通图永不触发），
  σ=0（pstdev，总体标准差，与 numpy 默认 ddof=0 对齐）→ "na"。
"""
import argparse
import csv
import random
import statistics
import sys
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------
# 冻结常量（spec v1.1）——改这里=过审（评审+用户）。
# ---------------------------------------------------------------
RNG_SEED = 20261105        # 生产路径钉扎（spec P1-2：random.Random 标准库）
N_PERM = 1000              # 置换零模型次数
LCC_SHARE_MIN = 0.80       # 最大连通片占比 <80% → 降级
MISSING_RATE_MAX = 0.10    # 种子/靶集缺节点率 >10% → 降级
TOP_N_CAP = 100            # spec "TOP100 卡"帽：取 top_hypotheses 前 min(100,n) 行

SIDECAR_NAME = "graph_structure_scores.tsv"
# schema 10 列冻结：主键+复核锚+三分数列+三描述性常量列+降级标志
SIDECAR_COLUMNS = [
    "chain_id", "microbe_id", "hop2_gene",
    "proximity_z", "degree_centrality_disclosed", "community_degraded",
    "lcc_share", "seed_missing_rate", "target_missing_rate", "degraded_mode",
]


# ---------------------------------------------------------------
# 读档与建图
# ---------------------------------------------------------------

def load_graph(sg_dir: Path) -> tuple[list[str], dict[str, set[str]]]:
    """merged_nodes + merged_edges → (节点表原序列表, 无向邻接表)。

    为什么 fail-closed 悬空边：端点不在节点表的边若静默吞掉，连通片/度
    全部失真且无痕（血泪史 #6 静默丢错）——宁可拒算，让数据异常显形。
    """
    nodes_path = sg_dir / "merged_nodes.tsv"
    edges_path = sg_dir / "merged_edges.tsv"
    for p in (nodes_path, edges_path):
        if not p.exists():
            raise SystemExit(f"[graph-scores] 缺输入 {p}——sidecar 无从建图")

    with open(nodes_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if "id" not in (reader.fieldnames or []):
            raise SystemExit("[graph-scores] merged_nodes.tsv 缺列 id——fail-closed")
        nodes = [r["id"] for r in reader if r["id"]]
    if not nodes:
        raise SystemExit("[graph-scores] merged_nodes.tsv 零节点——fail-closed")
    node_set = set(nodes)
    # 重复 id 会稀释"757 节点"刻度——如实拒算而非去重吞掉
    if len(node_set) != len(nodes):
        raise SystemExit("[graph-scores] merged_nodes.tsv id 重复——fail-closed")

    adj: dict[str, set[str]] = {n: set() for n in nodes}
    dangling = 0
    with open(edges_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in ("subject", "object")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[graph-scores] merged_edges.tsv 缺列 {missing}"
                             "——fail-closed")
        for r in reader:
            s, o = r["subject"], r["object"]
            if s not in node_set or o not in node_set:
                dangling += 1
                continue
            # 无向化+去重（合并图同边多证据行 → 邻接集天然去重）
            adj[s].add(o)
            adj[o].add(s)
    if dangling:
        raise SystemExit(f"[graph-scores] {dangling} 条边端点不在节点表"
                         "——fail-closed（子图完整性异常，先修数据）")
    return nodes, adj


def load_chains(sg_dir: Path) -> list[dict]:
    """top_hypotheses.tsv → 链人口（TOP100 帽，文件原序=零排序变更）。

    只取三列（chain_id/microbe_id/hop2_gene）——sidecar 复核锚最小集。
    """
    src = sg_dir / "top_hypotheses.tsv"
    if not src.exists():
        raise SystemExit(f"[graph-scores] 缺输入 {src}——sidecar 无从取种子/靶集")
    with open(src, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        missing = [c for c in ("chain_id", "microbe_id", "hop2_gene")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[graph-scores] top_hypotheses.tsv 缺列 {missing}"
                             "——fail-closed")
        chains = []
        for i, r in enumerate(reader):
            if i >= TOP_N_CAP:      # spec "TOP100 卡"帽
                break
            cid = (r["chain_id"] or "").strip()
            if not cid:
                raise SystemExit(f"[graph-scores] 第 {i + 2} 行 chain_id 为空"
                                 "——fail-closed（sidecar 每行必须可回连链）")
            chains.append({"chain_id": cid,
                           "microbe_id": (r["microbe_id"] or "").strip(),
                           "hop2_gene": (r["hop2_gene"] or "").strip()})
    if not chains:
        raise SystemExit("[graph-scores] top_hypotheses.tsv 零链——fail-closed")
    return chains


# ---------------------------------------------------------------
# 连通片（纯标准库 BFS）与 LCC 门禁
# ---------------------------------------------------------------

def connected_components(nodes: list[str],
                         adj: dict[str, set[str]]) -> list[set[str]]:
    """全图连通片（迭代 BFS，防深图递归爆栈）；返回按发现序的成员集列表。"""
    seen: set[str] = set()
    comps: list[set[str]] = []
    for n in nodes:
        if n in seen:
            continue
        comp = {n}
        seen.add(n)
        q = deque([n])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    comp.add(v)
                    q.append(v)
        comps.append(comp)
    return comps


def component_index(comps: list[set[str]]) -> dict[str, int]:
    """节点→所在连通片编号（community_degraded 同片判定用）。"""
    idx: dict[str, int] = {}
    for i, comp in enumerate(comps):
        for n in comp:
            idx[n] = i
    return idx


def lcc_gate(nodes: list[str], comps: list[set[str]],
             seeds: list[str], targets: list[str]) -> dict:
    """LCC 前置检验（spec 冻结阈值）→ 各率+降级标志。

    缺节点率=种子/靶集成员不在 merged_nodes 的比例（图都没有，谈不上
    连通）；种子/靶在 LCC 内的占比另作披露项（连通性可达性）。
    """
    n = len(nodes)
    node_set = set(nodes)
    lcc_size = max(len(c) for c in comps)
    lcc_share = lcc_size / n
    s_miss = sum(1 for s in seeds if s not in node_set) / len(seeds)
    t_miss = sum(1 for t in targets if t not in node_set) / len(targets)
    lcc = max(comps, key=len)
    s_out_lcc = sum(1 for s in seeds if s in node_set and s not in lcc)
    t_out_lcc = sum(1 for t in targets if t in node_set and t not in lcc)
    degraded = (lcc_share < LCC_SHARE_MIN or s_miss > MISSING_RATE_MAX
                or t_miss > MISSING_RATE_MAX)
    return {"n_nodes": n, "n_components": len(comps), "lcc_size": lcc_size,
            "lcc_share": lcc_share, "seed_missing_rate": s_miss,
            "target_missing_rate": t_miss,
            "seed_outside_lcc": s_out_lcc, "target_outside_lcc": t_out_lcc,
            "degraded": degraded}


# ---------------------------------------------------------------
# 三分数（①自实现公式 ②度中心性 ③降级社区列）
# ---------------------------------------------------------------

def bfs_distances(adj: dict[str, set[str]], src: str) -> dict[str, int]:
    """单源 BFS 全距离表（无权图最短路；不可达节点不在返回表中）。"""
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def proximity_d(adj: dict[str, set[str]], S: list[str],
                T: list[str]) -> float | None:
    """Guney 2016 邻近度 d(S,T)=(1/|T|)Σ_t min_{s∈S} d(s,t)。

    任一靶点从全部种子不可达 → None（Menche 2015 惯例：不可达不折算
    大数硬给——由调用方记 na，避免"inf 拉爆均值"的伪精确）。
    每个 s 只跑一次 BFS（距离表复用），|S| 小时开销可忽略。
    """
    best: dict[str, int] = {}
    for s in S:
        dist = bfs_distances(adj, s)
        for t in T:
            if t in dist and (t not in best or dist[t] < best[t]):
                best[t] = dist[t]
    if len(best) != len(T):
        return None
    return sum(best[t] for t in T) / len(T)


def proximity_z(adj: dict[str, set[str]], nodes_sorted: list[str],
                S: list[str], T: list[str], seed: int = RNG_SEED,
                n_perm: int = N_PERM) -> float | None:
    """z=(d_obs-d̄_rand)/σ，置换零模型=等规模随机节点集。

    生产路径钉扎：每条链独立 random.Random(20261105) 起算（"逐次重置"）
    ——链间零传染，任意链序/任意子集重跑同输入 sha 相等（验证条款①）。
    零模型集=全节点均匀抽样（简化均匀置换；Guney 原文的度匹配分箱抽样
    未纳入，spec 简化口径，如实披露）；不可达置换=拒绝重抽（上限
    10×n_perm，连通图永不触发）；σ=总体标准差 pstdev（对齐 numpy 默认
    ddof=0），σ=0 → None（退化不硬造 z）。
    """
    d_obs = proximity_d(adj, S, T)
    if d_obs is None:
        return None
    rng = random.Random(seed)
    nulls: list[float] = []
    attempts = 0
    while len(nulls) < n_perm and attempts < n_perm * 10:
        attempts += 1
        s2 = rng.sample(nodes_sorted, len(S))
        t2 = rng.sample(nodes_sorted, len(T))
        d2 = proximity_d(adj, s2, t2)
        if d2 is not None:
            nulls.append(d2)
    if len(nulls) < 2:
        return None
    sigma = statistics.pstdev(nulls)
    if sigma == 0:
        return None
    return (d_obs - statistics.fmean(nulls)) / sigma


def degree_centrality(node: str, adj: dict[str, set[str]],
                      n_nodes: int) -> float | None:
    """度/(n_nodes-1)；自环不计度（adj 集合含自身时剔除）。

    _disclosed 提示：度受文献热门度偏差影响——被研究多的基因天然连边多，
    此列供复核，禁止用作排序分量（spec 明示）。
    """
    if node not in adj:
        return None
    deg = len(adj[node] - {node})
    return deg / (n_nodes - 1)


# ---------------------------------------------------------------
# 主流程：算分→拼 payload→只增不改落盘
# ---------------------------------------------------------------

def compute(sg_dir: Path) -> dict:
    """全流程：建图→LCC 门禁→逐链三列→确定性 payload。

    返回统计 dict（含门禁各率与降级标志），供 CLI 汇报与测试断言；
    payload 为纯函数产物（同输入必同字节——钉扎 RNG+文件原序）。
    """
    nodes, adj = load_graph(sg_dir)
    chains = load_chains(sg_dir)
    # 种子/靶集=人口唯一值（保持首现序，稳定可复核）；空 hop2_gene 不入靶集
    seeds: list[str] = []
    targets: list[str] = []
    for ch in chains:
        if ch["microbe_id"] and ch["microbe_id"] not in seeds:
            seeds.append(ch["microbe_id"])
        if ch["hop2_gene"] and ch["hop2_gene"] not in targets:
            targets.append(ch["hop2_gene"])
    if not seeds or not targets:
        raise SystemExit("[graph-scores] 种子集或靶集为空——fail-closed")

    comps = connected_components(nodes, adj)
    gate = lcc_gate(nodes, comps, seeds, targets)
    comp_idx = component_index(comps)
    nodes_sorted = sorted(nodes)          # 零模型抽样的稳定底座（排序=可复现）

    def fmt(x: float | None) -> str:
        return "na" if x is None else f"{x:.6f}"

    lines = ["\t".join(SIDECAR_COLUMNS)]
    n_prox = 0
    for ch in chains:
        m, g = ch["microbe_id"], ch["hop2_gene"]
        # ① 降级路线硬分不产（零模型零消耗）；未降级才允许算
        if gate["degraded"]:
            z = None
        else:
            z = (proximity_z(adj, nodes_sorted, [m], [g])
                 if m in adj and g else None)
            if z is not None:
                n_prox += 1
        # ② 度中心性=描述性披露列，降级与否都算（节点缺图=na 如实）
        dc = degree_centrality(g, adj, len(nodes)) if g else None
        # ③ 同连通片 1/0（两节点都在图才可判；缺任一=na）
        if m in comp_idx and g in comp_idx:
            comm = "1" if comp_idx[m] == comp_idx[g] else "0"
        else:
            comm = "na"
        lines.append("\t".join((
            ch["chain_id"], m, g, fmt(z), fmt(dc), comm,
            f"{gate['lcc_share']:.6f}",
            f"{gate['seed_missing_rate']:.6f}",
            f"{gate['target_missing_rate']:.6f}",
            "1" if gate["degraded"] else "0")))
    payload = "\n".join(lines) + "\n"
    return {"gate": gate, "n_chains": len(chains), "n_seeds": len(seeds),
            "n_targets": len(targets), "n_proximity_computed": n_prox,
            "payload": payload, "degraded": gate["degraded"]}


def annotate(sg_dir: Path) -> dict:
    """算分并落 sidecar（只增不改护栏，与 U1 causal_marks 同制）。"""
    stats = compute(sg_dir)
    out_path = sg_dir / SIDECAR_NAME
    payload = stats.pop("payload")
    if out_path.exists():
        old = out_path.read_bytes()
        new = payload.encode("utf-8")
        if old == new:
            stats.update({"idempotent": True, "out": str(out_path)})
            return stats
        raise SystemExit(
            f"[graph-scores] {out_path} 已存在且与本次产物不一致——"
            "只增不改区拒写（疑子图/人口漂移；人工移走旧档过审后重跑）")
    out_path.write_text(payload, encoding="utf-8")
    stats.update({"idempotent": False, "out": str(out_path)})
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="U2 图结构分 sidecar（LCC 门禁先行，降级=描述性列）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="子图目录（读 merged_nodes/merged_edges/"
                         "top_hypotheses，增写 graph_structure_scores.tsv）")
    args = ap.parse_args(argv)

    stats = annotate(args.subgraph_dir)
    g = stats["gate"]
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    mode = ("降级（LCC 门禁触发：只产描述性列，不产 proximity_z 硬分）"
            if stats["degraded"] else
            "全列（LCC 门禁未触发：proximity_z/度中心性/社区列全产）")
    print(f"[graph-scores] {stats['out']} {verb}")
    print(f"[graph-scores] 模式：{mode}")
    print(f"[graph-scores] 节点 {g['n_nodes']} | 连通片 {g['n_components']} | "
          f"LCC 占比 {g['lcc_share']:.6f} | "
          f"种子缺 {g['seed_missing_rate']:.6f} | "
          f"靶集缺 {g['target_missing_rate']:.6f}")
    print(f"[graph-scores] 链 {stats['n_chains']} 行零跳过 | "
          f"proximity_z 实算 {stats['n_proximity_computed']} 条 | "
          f"种子 {stats['n_seeds']} 菌 / 靶 {stats['n_targets']} 基因")
    print("[graph-scores] 提示：本 sidecar 非排序分量，供复核（spec 明示）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
