"""②甲 子图基因层嫁接 + proximity 激活（v0.3 四件之一；spec §四，冻结 v1.5，逐字遵循）。

定位与红线：
- 主子图**只读**：本模块读主子图 merged_nodes/merged_edges/chains/
  top_hypotheses，产物全部落**全新目录** `data/kg/subgraph_htn_gene_<ts>/`
  （时间戳前缀隔离）。主子图目录一个字节都不写——它是已签收交付同源件 +
  golden v3 比对基准，写入即破已审计态（spec §4.2 明文）。
- golden v3 十三件零改动：本模块不碰主子图任何既有件；验收靠跑前跑后逐件
  sha 核验（`--verify-golden`，沿 ②乙/③甲/③乙 同制）。
- 不碰 06_tool 生产逻辑：hop3 的 KEGG 通路腿与 V01 三门禁**只 import 复用**
  `deepchain.gate_v01_edges / gate_v01_chains / anchor_check` 原件——复制一份
  门禁规则就等着两边漂移（③甲 监工硬门禁同款）。
- 统计腿只 import 复用 `kg_tool.proximity` 原件（AMSPL / 度匹配分箱 / 单侧 p /
  z / Menche），本模块不重写公式——spec §4.3 "公式与 ②乙 同源（§1.3 全条款
  沿用）"要求的就是同一份实现，复制粘贴即口径漂移风险。

嫁接口径（spec §4.2）：
1. 对子图每个 **Metabolite** 节点按名字 join gutMGene 表3（
   `Microbial metabolite-Host Gene.csv`，列 Metabolite/Gene/Gene ID），
   新增 `NCBIGene:<Gene ID>` 基因节点 + "代谢物-基因"边；
2. 对每个新基因节点 join V01 kegg_rest 快照的 `participates_in` 边（经 V01
   三门禁 + 节点表 sha 门禁），新增 `KEGG:hsa…` 通路节点 + "基因-通路"边。

**join 键纪律（监工 P1 条件②，2026-11-05 裁定）**：现用**名字 casefold 精确
匹配**（表3 名 vs 子图 Metabolite.name，不去空格内的差异、不用别名、不模糊
匹配），机器实测命中 23/274——**改 join 键=方向级报审，现场不得自决**。
为什么坚持窄口径：子图代谢物是 MESH 体系、表3 是 ChEBI/名体系，两库 ID 不
互通（"ChEBI→MeSH 桥接 24/24 absent"失败史同根因，probe §六）；任何"补映射
解锁 122→274"的动作都是重开 K2 议题，不是实现期细节。

proximity 激活（spec §4.3）：
- 在新目录上跑 AMSPL proximity（§1.3 全条款：min-based 距离、z<0=更近、单侧
  α=0.05、n_perm=1000、种子 20261105、不可达率>50% 判图不适用）；
- 靶集=**端点基因集**（原 U2 设计，此时基因层已在子图内）；
- 门禁复核（U2 阈值复用）：LCC 占比 ≥80%、种子/靶集缺节点率 ≤10%；
- 结果落新目录 `graph_structure_scores_active.tsv`（schema=U2 十列，
  degraded_mode=0）；主子图 sidecar 的 degraded_mode=1 记录**保留不动**
  （v0.2 降级是当时结构事实=历史如实性，spec §4.3 明文）。

**必须随档披露的两条解读边界**（否则 z 会被读成"独立图结构证据"）：
① **构造性循环**：嫁接的"代谢物-基因"边与被检验链的 hop2 同源（都来自
   gutMGene 表3）；因此一条完整链的端点对距离恒为 2（菌→代谢物→基因），
   这是**链构造的直接后果**，不是独立验证。本件证明的是"基因层可物化、
   proximity 管线端到端可跑"，不是"图结构独立支持假设"。
② **SCFA 主轴限定**（监工 P1 条件①，probe §七）：可嫁接代谢物仅 23/274
   （8.4%），链覆盖 98.8% 高度集中于 butyrate/acetate/propionate/succinate
   （Top4≈97.6%）——**有效自由度远小于链数**（distinct 代谢物 23、基因 205），
   "结论主要反映 SCFA 主轴"，任何基于链数的显著性叙事均不成立。

规格歧义登记：spec §四 未写的实现细节一律入"规格歧义登记表"（评估档 §八），
禁静默自决（冻结后修改规则=A/A' 勘误留痕）。
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

try:                                      # 包内导入（pip editable / pytest）
    import kg_tool.proximity as prox      # §1.3 统计腿原件（只 import 复用）
    import kg_tool.graph_scores as gs     # U2 十列 schema + LCC 门禁原件
except ImportError:
    # 脚本直跑（python3 -I src/kg_tool/gene_graft.py）时 -I 隐含 -P，
    # 不把脚本目录/包根加进 sys.path——这里补一行包根，保住"零安装可跑"
    # （③甲/③乙 的 evidence 档都是这种直跑法）。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import kg_tool.proximity as prox
    import kg_tool.graph_scores as gs
# ---------------------------------------------------------------
# 冻结常量——改这里=过审（监工+用户）。
# ---------------------------------------------------------------
RNG_SEED = prox.RNG_SEED                  # spec §1.3：20261105 钉扎（复用同一常量）
N_PERM = prox.N_PERM                      # spec §1.3：n_perm=1000
ALPHA = prox.ALPHA                        # spec §1.3：单侧 α=0.05
UNREACHABLE_MAX = prox.UNREACHABLE_MAX    # spec §1.3：不可达率>50% → 图不适用
LCC_SHARE_MIN = gs.LCC_SHARE_MIN          # spec §4.3：U2 门禁阈值复用（0.80）
MISSING_RATE_MAX = gs.MISSING_RATE_MAX    # spec §4.3：U2 门禁阈值复用（0.10）

SEED_CATEGORIES = prox.SEED_CATEGORIES    # 菌池=Species∪Microbe（287）
GENE_CATEGORY = "Gene"                    # 与 V01 节点表同一类别名（291 通路/850 基因同制）
PATHWAY_CATEGORY = "Pathway"
TARGET_CATEGORY_ACTIVE = GENE_CATEGORY    # ②甲 靶集=端点基因集（spec §4.3）

TABLE3_NAME = "Microbial metabolite-Host Gene.csv"   # INHERITANCE.md:39（1,049 行）
GUTMGENE_DIR_DEFAULT = ("<LYTEAMWORK>/JiXuanRui/Project/"
                        "Dietary_cohort_of_intestinal_flora/Project/"
                        "Project03_公共数据库/gutMGene")
V01_EDGES_DEFAULT = "archive/Version01/data/merged/candidate_v3/merged_edges.tsv"
V01_CHAINS_DEFAULT = "archive/Version01/data/logs/mechanism_chains.tsv"
V01_NODES_DEFAULT = "archive/Version01/data/merged/candidate_v3/merged_nodes.tsv"
V01_NODES_SHA16 = "391bdca089c84d90"      # INHERITANCE.md:4（V01 节点表 sha16）

# 探测锚（probe_gene_graft_20261105.md，v2 修正版；实现须逐项机器复核）
ANCHOR_SUBGRAPH_METABOLITES = 274         # 子图 Metabolite 节点数
ANCHOR_T3_ROWS = 1049                     # 表3 逻辑记录（csv DictReader）
ANCHOR_T3_DISTINCT_NAMES = 122            # 表3 distinct 代谢物名
ANCHOR_COVERED_METABOLITES = 23           # 名字 join 命中（8.4%）
ANCHOR_GRAFT_GENE_EDGES = 337             # 新增"代谢物-基因"边（early-kill ≥100）
ANCHOR_GENE_NODES = 205                   # 新增基因节点
ANCHOR_GENE_PATHWAY_EDGES = 962           # 新增"基因-通路"边（V01 kegg_rest 交集）
ANCHOR_PATHWAY_NODES = 219                # 新增通路节点（= chains_manifest distinct_pathways）
ANCHOR_COMPUTABLE_CHAINS = 43236          # 可算链（early-kill ≥30）

ACTIVE_SIDECAR_NAME = "graph_structure_scores_active.tsv"
# schema=U2 十列（spec §4.3 逐字"schema 同 U2 十列"）——直接引用原件常量，
# 防"抄一份列名"后两边漂移。
ACTIVE_COLUMNS = list(gs.SIDECAR_COLUMNS)
GOLDEN_LIST_DEFAULT = prox.GOLDEN_LIST_DEFAULT
SPEC_PATH = prox.SPEC_PATH

# 新边列填充（spec §4.2 未定义列值——全部登记 A 类歧义，见评估档 §八）
GUTMGENE_PREDICATE = "modulates_host_gene"  # A13 订正：该谓词实为 CHEBI→NCBIGene（代谢物-基因）
KEGG_PREDICATE = "participates_in"          # V01 kegg_rest 快照原谓词
GRAFT_EVIDENCE_TIER = "B"                   # 外部文献级证据（gutMGene/KEGG 快照）
GRAFT_KNOWLEDGE_LAYER = "local_kg_curated"  # 与 V01 基因/通路节点同层（非 LLM 抽取）
GRAFT_GRANULARITY = "关系级"                 # 边级事实（非名称级/原文级）
GRAFT_RUN_BATCH = "v03_gene_graft"

# 嫁接写入的边/节点列必须已在原件表头里——否则写回时会被静默丢弃（血泪史 #6）。
# 为什么要求全列而不是"扩展表头"：扩展表头会改主子图表头行，破坏"原件行逐字节
# 前缀保留"这条隔离证明；缺列=数据模式不符，宁可拒跑让问题显形。
REQUIRED_NODE_COLS = ["id", "name", "category", "knowledge_layer"]
REQUIRED_EDGE_COLS = ["subject", "predicate", "object", "source_type",
                      "evidence_tier", "pmids", "support_count",
                      "canonical_view", "source_ref", "curator", "source_id",
                      "version", "knowledge_layer", "edge_id", "run_batch",
                      "evidence_granularity", "provenance"]


def kg_root() -> Path:
    """仓根定位（与 cli.kg_root / prox.kg_root 同法）：KG_ROOT > 自动探测。"""
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------
# 文件与 TSV 基础
# ---------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """流式 sha256（大件不全量读内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_tsv(path: Path) -> tuple[list[str], list[dict]]:
    """读 TSV → (表头列序, 行字典列表)。列序要保留：写回时沿用原件列序。"""
    if not path.exists():
        raise SystemExit(f"[gene-graft] 缺输入 {path}——fail-closed")
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    return list(fields), rows


def _read_tsv_text(path: Path) -> str:
    """读原文（追加式改写用）：主子图件必须**前缀逐字节不变**，所以不做
    解析-重排-回写（那样会改列序/引号风格，破坏与 golden 的可比性）。"""
    if not path.exists():
        raise SystemExit(f"[gene-graft] 缺输入 {path}——fail-closed")
    text = path.read_text(encoding="utf-8")
    return text if text.endswith("\n") else text + "\n"


def _row_text(fields: list[str], row: dict) -> str:
    """一行 TSV（列序=原件列序，缺值补空）——统一 \n 行尾，零引号噪音。"""
    return "\t".join(str(row.get(c, "") or "") for c in fields) + "\n"


def _casefold(s: str) -> str:
    """join 键归一：去首尾空白 + casefold（例：'Butyrate'→'butyrate'）。
    为什么只做这一步：P1 条件② 禁擅改 join 键——去空白与大小写是**格式
    归一**不是键语义变更；probe 的 23 个命中就是这一步的机器口径。
    """
    return (s or "").strip().casefold()


# ---------------------------------------------------------------
# V01 门禁（三重沿用 deepchain 原件）+ 节点表 sha 门禁（本件新增第四道）
# ---------------------------------------------------------------
_DC = None


def _load_deepchain():
    """按路径加载 src/06_tool/deepchain.py（目录名带连字符不是合法包名，
    沿 ③甲 同款路径加载；懒加载+缓存，pytest 收集时不触发副作用）。"""
    global _DC
    if _DC is not None:
        return _DC
    tool_dir = kg_root() / "src" / "06_tool"
    p = tool_dir / "deepchain.py"
    if not p.exists():
        raise SystemExit(f"[gene-graft] 生产模块缺失 {p}——fail-closed"
                         "（V01 三门禁须来自 deepchain 原件，禁私搭）")
    if str(tool_dir) not in sys.path:
        # deepchain 内部是平铺 import（from subgraph import read_tsv），
        # 路径加载不自动加 sys.path——不补这里 exec_module 直接 ImportError
        sys.path.insert(0, str(tool_dir))
    spec = spec_from_file_location("deepchain_frozen_for_graft", p)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    _DC = mod
    return mod


def load_v01_assets(v01_edges: Path, v01_chains: Path, v01_nodes: Path) -> dict:
    """V01 资产门禁 → {"g2p": {gene: [pathway...]}, "gates": {...}, "names": {...}}。

    三道门禁沿用 deepchain 原件（sha16 双验 + 锚定对拍子集），第四道是本件
    新增：通路节点名要从 V01 节点表取（否则通路节点 name 只能空着/填 ID，
    下游读起来是"无名节点"）。第四道同样 sha16 钉死=资产被动过即拒跑。
    """
    dc = _load_deepchain()
    kegg_rows = dc.gate_v01_edges(v01_edges)           # 门禁①（sha16 + 非空）
    frozen = dc.gate_v01_chains(v01_chains)            # 门禁②（sha16 + 行数）
    nodes_sha16 = sha256_file(v01_nodes)[:16]
    if nodes_sha16 != V01_NODES_SHA16:
        raise SystemExit(f"[gene-graft] V01 节点表 sha16={nodes_sha16} ≠ 登记 "
                         f"{V01_NODES_SHA16}——资产被动过，拒跑")
    named = {}
    for r in dc.read_tsv(v01_nodes):
        named[r.get("id", "")] = r.get("name", "")
    g2p: dict[str, list[str]] = {}
    for e in kegg_rows:
        g2p.setdefault(e["subject"], []).append(e["object"])
    for g in g2p:
        g2p[g] = sorted(set(g2p[g]))
    return {
        "g2p": g2p,
        "names": named,
        "gates": {
            "v01_edges_sha16": {"expect": dc.V01_EDGES_SHA16,
                                "got": sha256_file(v01_edges)[:16],
                                "pass": sha256_file(v01_edges)[:16]
                                == dc.V01_EDGES_SHA16},
            "v01_chains_sha16": {"expect": dc.V01_CHAINS_SHA16,
                                 "got": sha256_file(v01_chains)[:16],
                                 "pass": sha256_file(v01_chains)[:16]
                                 == dc.V01_CHAINS_SHA16},
            "v01_chains_rows": {"expect": dc.V01_CHAINS_ROWS,
                                "got": len(frozen),
                                "pass": len(frozen) == dc.V01_CHAINS_ROWS},
            "v01_nodes_sha16": {"expect": V01_NODES_SHA16,
                                "got": nodes_sha16,
                                "pass": nodes_sha16 == V01_NODES_SHA16},
        },
        "_frozen": frozen,
        "_dc": dc,
    }


def load_table3(gm_dir: Path) -> list[dict]:
    """gutMGene 表3（gb18030，csv DictReader 逻辑记录口径）。

    为什么必须 gb18030：INHERITANCE.md:33 明载该文件非 UTF-8，直接 utf-8 读
    会在第一个全角字符处炸；为什么用 DictReader 而不是行数：Description 字段
    内有换行（物理行比逻辑记录多 35 行，INHERITANCE.md:35），按物理行切会
    把一条记录劈成两条假记录。

    **join 键只能是 `Metabolite` 列**：该表的 `Index` 是行号（值 '1'），拿它
    当键会因 `'1' in 'MESH:C...'` 的子串偶然命中造出假过门（probe v1 实录，
    probe_gene_graft_20261105.md:3 已作废披露）——本模块只按列名取 Metabolite。
    """
    p = gm_dir / TABLE3_NAME
    if not p.exists():
        raise SystemExit(f"[gene-graft] 缺输入 {p}——fail-closed")
    with open(p, encoding="gb18030", newline="") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    need = {"Metabolite", "Gene", "Gene ID", "PMID"}
    missing = need - set(rows[0].keys() if rows else set())
    if missing:
        raise SystemExit(f"[gene-graft] 表3 缺列 {sorted(missing)}——fail-closed")
    return rows


# ---------------------------------------------------------------
# 嫁接主体（纯函数：同输入→同行序同文本）
# ---------------------------------------------------------------

def build_graft(sg_dir: Path, gm_dir: Path, v01_edges: Path, v01_chains: Path,
                v01_nodes: Path) -> dict:
    """嫁接 → payload（节点/边文本 + manifest 数据 + 统计）。

    行序约定（确定性来源，写入 manifest.determinism）：
    - 节点：主子图原件全部行按原序**逐字节保留**，新增行按 id 升序追加；
    - 边：同上，新增行按 (subject, predicate, object) 升序追加。
    为什么不在全表重排：主子图行序是 golden 冻结态的一部分；新目录里保留
    "原件前缀"，任何审计都能一眼看出哪些行是新嫁接的。
    """
    node_fields, node_rows = _read_tsv(sg_dir / "merged_nodes.tsv")
    edge_fields, edge_rows = _read_tsv(sg_dir / "merged_edges.tsv")
    miss_n = [c for c in REQUIRED_NODE_COLS if c not in node_fields]
    if miss_n:
        raise SystemExit(f"[gene-graft] merged_nodes.tsv 缺列 {miss_n}"
                         "——fail-closed（缺列=嫁接值会被静默丢弃）")
    miss_e = [c for c in REQUIRED_EDGE_COLS if c not in edge_fields]
    if miss_e:
        raise SystemExit(f"[gene-graft] merged_edges.tsv 缺列 {miss_e}"
                         "——fail-closed（缺列=嫁接值会被静默丢弃）")

    met_by_key: dict[str, list[str]] = {}
    met_name: dict[str, str] = {}
    for r in node_rows:
        if r.get("category") == "Metabolite":
            met_name[r["id"]] = r.get("name", "")
            met_by_key.setdefault(_casefold(r.get("name", "")), []).append(r["id"])
    if not met_by_key:
        raise SystemExit("[gene-graft] 子图零 Metabolite 节点——fail-closed")

    t3 = load_table3(gm_dir)
    # 表3 → key → {gene_id: {symbol, pmids, n_rows}}
    by_key: dict[str, dict[str, dict]] = {}
    n_empty_gene_id = 0
    for r in t3:
        key = _casefold(r.get("Metabolite", ""))
        gid = (r.get("Gene ID", "") or "").strip()
        if not gid:
            # 空 Gene ID 行：无基因身份可挂——计数披露，不静默吞（血泪史 #6）
            n_empty_gene_id += 1
            continue
        if not key:
            continue
        slot = by_key.setdefault(key, {}).setdefault(
            gid, {"symbol": (r.get("Gene", "") or "").strip(),
                  "pmids": set(), "n_rows": 0})
        slot["n_rows"] += 1
        pmid = (r.get("PMID", "") or "").strip()
        if pmid:
            slot["pmids"].add(pmid)

    # ---- ① 代谢物-基因边（distinct 对；同一对多行=证据并集）----
    graft_pairs: list[tuple[str, str, list[str], int]] = []
    matched_mets: list[str] = []
    for met_id in [r["id"] for r in node_rows if r.get("category") == "Metabolite"]:
        genes = by_key.get(_casefold(met_name[met_id]))
        if not genes:
            continue
        matched_mets.append(met_id)
        for gid in sorted(genes):
            slot = genes[gid]
            graft_pairs.append((met_id, "NCBIGene:" + gid,
                                sorted(slot["pmids"]), slot["n_rows"]))
    if not graft_pairs:
        raise SystemExit("[gene-graft] 名字 join 零命中——fail-closed"
                         "（early-kill 阈值 337 边的下界远高于 0，先修数据）")

    gene_ids = sorted({g for _m, g, _p, _n in graft_pairs})
    existing_ids = {r["id"] for r in node_rows}
    collided = [g for g in gene_ids if g in existing_ids]
    if collided:
        # 子图已有同 id 节点：再挂一份=同 id 两义，度/距离全失真 → 拒跑
        raise SystemExit(f"[gene-graft] 基因节点 id 与既有节点冲突 {collided[:5]}"
                         "——fail-closed（先做实体对齐，勿现场合并）")

    v01 = load_v01_assets(v01_edges, v01_chains, v01_nodes)
    g2p = v01["g2p"]
    gene_pathway: list[tuple[str, str]] = []
    for g in gene_ids:
        for pw in g2p.get(g, []):
            gene_pathway.append((g, pw))
    pathway_ids = sorted({p for _g, p in gene_pathway})
    collided_pw = [p for p in pathway_ids if p in existing_ids]
    if collided_pw:
        raise SystemExit(f"[gene-graft] 通路节点 id 与既有节点冲突 "
                         f"{collided_pw[:5]}——fail-closed")
    genes_without_pathway = len(gene_ids) - len({g for g, _p in gene_pathway})

    # ---- 门禁③（deepchain 原件）：用到的 (gene,pathway) 对 ⊆ 冻结链对集 ----
    anchor = v01["_dc"].anchor_check(set(gene_pathway), v01["_frozen"])

    # ---- ② 组装新节点行/新边行 ----
    # 基因符号：表3 行序确定，同 id 多符号时取首次出现并计数披露（当前 0 冲突）
    gene_symbol: dict[str, str] = {}
    symbol_conflicts = 0
    for key in by_key:
        for gid, slot in by_key[key].items():
            gid_full = "NCBIGene:" + gid
            if gid_full not in gene_symbol:
                gene_symbol[gid_full] = slot["symbol"]
            elif slot["symbol"] and slot["symbol"] != gene_symbol[gid_full]:
                symbol_conflicts += 1
    new_node_rows: list[dict] = []
    for g in gene_ids:
        new_node_rows.append({"id": g, "name": gene_symbol.get(g, ""),
                              "category": GENE_CATEGORY,
                              "knowledge_layer": GRAFT_KNOWLEDGE_LAYER})
    for p in pathway_ids:
        new_node_rows.append({"id": p, "name": v01["names"].get(p, ""),
                              "category": PATHWAY_CATEGORY,
                              "knowledge_layer": GRAFT_KNOWLEDGE_LAYER})
    new_node_rows.sort(key=lambda r: r["id"])

    used_edge_ids = {r.get("edge_id", "") for r in edge_rows}
    new_edge_rows: list[dict] = []

    def _graft_edge(sub: str, pred: str, obj: str, source_id: str,
                    source_ref: str, pmids: list[str], support: int,
                    version: str) -> dict:
        """新边行（edge_id=sha256(五元组)[:16]，确定性、可独立复算）。

        为什么 edge_id 用内容哈希而不是流水号：新目录要能双跑逐字节全等；
        流水号依赖遍历顺序，哈希依赖内容——后者才是可复算的身份。
        """
        raw = f"gene_graft|{sub}|{pred}|{obj}|{source_id}"
        eid = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        if eid in used_edge_ids:
            raise SystemExit(f"[gene-graft] edge_id 冲突 {eid}——fail-closed")
        used_edge_ids.add(eid)
        return {"subject": sub, "predicate": pred, "object": obj,
                "source_type": "external_curated",
                "evidence_tier": GRAFT_EVIDENCE_TIER,
                "pmids": ";".join(pmids), "support_count": str(support),
                "canonical_view": "derived_summary", "source_ref": source_ref,
                "curator": "automated_pipeline", "source_id": source_id,
                "version": version,
                "knowledge_layer": GRAFT_KNOWLEDGE_LAYER,
                "edge_id": eid, "run_batch": GRAFT_RUN_BATCH,
                "evidence_granularity": GRAFT_GRANULARITY,
                "provenance": "gene_graft"}

    for met_id, gid, pmids, n_rows in graft_pairs:
        new_edge_rows.append(_graft_edge(met_id, GUTMGENE_PREDICATE, gid,
                                         "gutmgene_v3", "gutmgene_table3",
                                         pmids, n_rows, "gutmgene_v2.0"))
    for g, p in gene_pathway:
        new_edge_rows.append(_graft_edge(g, KEGG_PREDICATE, p, "kegg_rest",
                                         "kegg_rest_snapshot", [], 1,
                                         "v01_sealed_v3.1.0"))
    new_edge_rows.sort(key=lambda r: (r["subject"], r["predicate"], r["object"]))

    node_text = (_read_tsv_text(sg_dir / "merged_nodes.tsv")
                 + "".join(_row_text(node_fields, r) for r in new_node_rows))
    edge_text = (_read_tsv_text(sg_dir / "merged_edges.tsv")
                 + "".join(_row_text(edge_fields, r) for r in new_edge_rows))

    # ---- ③ 统计（含链条覆盖：可算链=代谢物命中基因的链）----
    covered = set(matched_mets)
    _chains_fields, chains_rows = _read_tsv(sg_dir / "chains.tsv")
    n_chains_total = len(chains_rows)
    per_met_chains: dict[str, int] = {}
    for r in chains_rows:
        mid = r.get("metabolite_id", "")
        per_met_chains[mid] = per_met_chains.get(mid, 0) + 1
    n_computable = sum(v for k, v in per_met_chains.items() if k in covered)
    skipped = [{"id": r["id"], "name": r.get("name", "")}
               for r in node_rows
               if r.get("category") == "Metabolite" and r["id"] not in covered]

    # 链分布的机器重算（SCFA 限定句用，禁止手抄 probe 数字）
    met_dist = sorted(((n, met_name.get(mid, mid), mid)
                       for mid, n in per_met_chains.items()),
                      key=lambda x: (-x[0], x[2]))
    top4 = sum(n for n, _nm, _id in met_dist[:4])
    top_covered = sum(n for n, _nm, mid in met_dist if mid in covered)

    # ---- 结构事实：全链人口的端点对距离分布（构造性循环的机器证据）----
    # 为什么要算：②甲 的 proximity 是否只是"把造链用的边接回去"的恒等，必须
    # 由机器数说话（不能靠口头论证）。全链 43,236 条的 distinct (菌,端点基因)
    # 对逐个 BFS 求距离，分布若 100% 落在 2，即证明端点距离是链构造的直接后果。
    adj_all: dict[str, set[str]] = {}
    for r in edge_rows + new_edge_rows:
        s, o = r.get("subject", ""), r.get("object", "")
        adj_all.setdefault(s, set()).add(o)
        adj_all.setdefault(o, set()).add(s)
    chain_pairs = sorted({(r.get("microbe_id", ""), r.get("hop2_gene", ""))
                          for r in chains_rows if r.get("hop2_gene")})
    dist_tally: dict[str, int] = {}
    _dist_cache: dict[str, dict[str, int]] = {}
    for m, gn in chain_pairs:
        if m not in _dist_cache:
            _dist_cache[m] = prox.bfs_distances(adj_all, m)
        d = _dist_cache[m].get(gn)
        key = "na" if d is None else str(d)
        dist_tally[key] = dist_tally.get(key, 0) + 1
    chain_population_check = {
        "note": "全链人口的 distinct (菌, 端点基因) 对及其最短距离分布——"
                "距离=2 意味着端点对正是嫁接边本身（构造性循环的机器证据）",
        "n_chains_total": n_chains_total,
        "n_distinct_pairs": len(chain_pairs),
        "distance_distribution": dict(sorted(dist_tally.items())),
    }

    # ---- ④ 节点/边计数（无向去重后的"边数"另算，防"边行数=边数"混用）----
    def _undirected(text: str) -> int:
        adj: dict[str, set[str]] = {}
        for r in csv.DictReader(text.splitlines(), delimiter="\t"):
            s, o = r.get("subject", ""), r.get("object", "")
            adj.setdefault(s, set()).add(o)
            adj.setdefault(o, set()).add(s)
        return sum(len(v) for v in adj.values()) // 2

    n_nodes_after = len(node_rows) + len(new_node_rows)
    n_edges_rows_after = len(edge_rows) + len(new_edge_rows)

    # ---- ⑤ version 指纹：输入 sha + 产物文本 → 内容身份 ----
    sub_manifest_path = sg_dir / "manifest.json"
    if not sub_manifest_path.exists():
        raise SystemExit(f"[gene-graft] 缺 {sub_manifest_path}——fail-closed"
                         "（version 指纹须锚主子图 version）")
    sub_manifest = json.loads(sub_manifest_path.read_text(encoding="utf-8"))
    sub_version = sub_manifest.get("subgraph_version", "")
    if not sub_version:
        raise SystemExit("[gene-graft] 主子图 manifest 无 subgraph_version"
                         "——fail-closed")
    inputs_sha = {
        "subgraph_merged_nodes": sha256_file(sg_dir / "merged_nodes.tsv"),
        "subgraph_merged_edges": sha256_file(sg_dir / "merged_edges.tsv"),
        "subgraph_manifest": sha256_file(sub_manifest_path),
        "subgraph_chains": sha256_file(sg_dir / "chains.tsv"),
        "subgraph_top_hypotheses": sha256_file(sg_dir / "top_hypotheses.tsv"),
        "subgraph_chains": sha256_file(sg_dir / "chains.tsv"),
        "gutmgene_table3": sha256_file(gm_dir / TABLE3_NAME),
        "v01_edges": sha256_file(v01_edges),
        "v01_chains": sha256_file(v01_chains),
        "v01_nodes": sha256_file(v01_nodes),
    }
    fp = hashlib.sha256()
    for k in sorted(inputs_sha):
        fp.update(f"{k}={inputs_sha[k]}\n".encode("utf-8"))
    fp.update(node_text.encode("utf-8"))
    fp.update(edge_text.encode("utf-8"))
    fingerprint = fp.hexdigest()
    graft_version = f"{sub_version[:8]}+graft.{fingerprint[:8]}"

    counts = {
        "n_nodes_before": len(node_rows),
        "n_nodes_after": n_nodes_after,
        "n_nodes_added": len(new_node_rows),
        "n_edges_rows_before": len(edge_rows),
        "n_edges_rows_after": n_edges_rows_after,
        "n_edges_rows_added": len(new_edge_rows),
        "n_edges_undirected_after": _undirected(edge_text),
    }
    n_met_nodes = sum(1 for r in node_rows if r.get("category") == "Metabolite")
    join_stats = {
        "key": "metabolite_name_casefold_exact",
        "n_subgraph_metabolites": n_met_nodes,
        "n_table3_rows": len(t3),
        "n_table3_distinct_names": len({_casefold(r.get("Metabolite", ""))
                                        for r in t3
                                        if _casefold(r.get("Metabolite", ""))}),
        "n_matched_metabolites": len(matched_mets),
        "n_skipped_metabolites": len(skipped),
        "n_rows_empty_gene_id": n_empty_gene_id,
        "n_duplicate_names_in_subgraph": sum(
            1 for v in met_by_key.values() if len(v) > 1),
    }
    graft_stats = {
        "n_gene_edges": len(graft_pairs),
        "n_gene_nodes": len(gene_ids),
        "n_gene_pathway_edges": len(gene_pathway),
        "n_pathway_nodes": len(pathway_ids),
        "n_genes_without_pathway": genes_without_pathway,
        "n_symbol_conflicts": symbol_conflicts,
    }
    anchors = {
        "n_subgraph_metabolites": (ANCHOR_SUBGRAPH_METABOLITES,
                                   join_stats["n_subgraph_metabolites"]),
        "n_table3_rows": (ANCHOR_T3_ROWS, join_stats["n_table3_rows"]),
        "n_table3_distinct_names": (ANCHOR_T3_DISTINCT_NAMES,
                                    join_stats["n_table3_distinct_names"]),
        "n_matched_metabolites": (ANCHOR_COVERED_METABOLITES,
                                  join_stats["n_matched_metabolites"]),
        "n_gene_edges": (ANCHOR_GRAFT_GENE_EDGES, graft_stats["n_gene_edges"]),
        "n_gene_nodes": (ANCHOR_GENE_NODES, graft_stats["n_gene_nodes"]),
        "n_gene_pathway_edges": (ANCHOR_GENE_PATHWAY_EDGES,
                                 graft_stats["n_gene_pathway_edges"]),
        "n_pathway_nodes": (ANCHOR_PATHWAY_NODES,
                            graft_stats["n_pathway_nodes"]),
        "n_chains_computable": (ANCHOR_COMPUTABLE_CHAINS, n_computable),
    }
    manifest = {
        "module": "src/kg_tool/gene_graft.py",
        "spec": "data/logs/upgrade_v03_spec_20261105.md#四",
        "graft_version": graft_version,
        "graft_fingerprint": fingerprint,
        "source_subgraph": {
            "dir_name": sg_dir.name,
            "parent_dir": str(sg_dir.parent),   # v1.8：逐链 cards 只读源（不复制）
            "subgraph_version": sub_version,
        },
        "inputs_sha256": inputs_sha,
        "v01_gates": v01["gates"],
        "v01_anchor": anchor,
        "join": join_stats,
        "graft": graft_stats,
        "counts": counts,
        "n_edges_by_source": {
            "subgraph_merged_edges": len(edge_rows),
            "gutmgene_table3": len(graft_pairs),
            "kegg_rest": len(gene_pathway),
        },
        "chain_coverage": {
            "n_chains_total": n_chains_total,
            "n_chains_computable": n_computable,
            "share": round(n_computable / n_chains_total, 6)
            if n_chains_total else 0,
        },
        "chain_population_check": chain_population_check,
        "chain_metabolite_distribution_top": [
            {"metabolite_id": mid, "name": nm, "n_chains": n,
             "share": round(n / n_chains_total, 6) if n_chains_total else 0}
            for n, nm, mid in met_dist[:10]],
        "chain_share_top4": round(top4 / n_chains_total, 6)
        if n_chains_total else 0,
        "chain_share_matched_metabolites": round(top_covered / n_chains_total, 6)
        if n_chains_total else 0,
        "skipped_metabolites": skipped,
        "anchors": {k: {"expect": e, "got": g,
                        "pass": e == g} for k, (e, g) in anchors.items()},
        "disclosure": [
            "join 键=名字 casefold 精确匹配（P1 条件②：禁擅改；命中 23/274）",
            "子图代谢物 MESH 体系与表3 ChEBI/名体系不互通——未做任何 ID 映射补全",
            "147 个新基因无 kegg_rest 通路边=死端（如实计入节点，不修剪）",
            "copy 件=top_hypotheses.tsv + chains.tsv（v1.8 激活人口改全链）；"
            "hypothesis_cards.tsv 不复制（②乙 仍读主子图）",
            "新边列填充见评估档 §八 歧义表（未定义列一律留空，不编造数值）",
        ],
        "determinism": {
            "sort_keys": {"nodes_added": "id",
                          "edges_added": "subject,predicate,object"},
            "no_run_time_in_output_rows": True,
            "prefix_preserved": "主子图 merged_nodes/merged_edges 原件行逐字节前缀保留",
        },
    }
    return {
        "node_text": node_text,
        "edge_text": edge_text,
        "top_hypotheses_bytes": (sg_dir / "top_hypotheses.tsv").read_bytes(),
        # v1.8：激活人口改用 chains.tsv 全量（规则 10），须随目录复制
        "chains_bytes": (sg_dir / "chains.tsv").read_bytes(),
        "manifest": manifest,
        "stats": {"join": join_stats, "graft": graft_stats, "counts": counts,
                  "chain_coverage": manifest["chain_coverage"],
                  "anchors": manifest["anchors"],
                  "chain_population_check": chain_population_check},
        "graft_version": graft_version,
        "fingerprint": fingerprint,
        "skipped": skipped,
        "met_dist": met_dist,
        "top4": top4,
        "top_covered": top_covered,
        "n_chains_total": n_chains_total,
    }


# ---------------------------------------------------------------
# 落盘（新目录创建 + 幂等护栏）
# ---------------------------------------------------------------

def _manifest_text(manifest: dict) -> str:
    """manifest JSON 文本（排序键=确定性；无时间戳=可复现）。"""
    return json.dumps(manifest, ensure_ascii=False, indent=1,
                      sort_keys=True) + "\n"


def _sums_text(files: dict[str, bytes]) -> str:
    """SHA256SUMS（新目录自证清单；按名排序，排除自身）。"""
    lines = [f"{hashlib.sha256(v).hexdigest()}  {k}"
             for k, v in sorted(files.items())]
    return "\n".join(lines) + "\n"


def _write_files(out_dir: Path, files: dict[str, bytes],
                 allow_extra: bool = True) -> dict:
    """写新目录（只增不改：已存在且**逐文件逐字节**一致=幂等 no-op；不一致=拒写）。

    为什么幂等护栏必须逐文件比而不是"目录存在就拒写"：显式 --out-dir 重跑
    同一次运行要能识别为 no-op（验收条款⑤）；而任何字节差异都意味着口径/
    输入漂移，必须拒写并提示人工移走旧档过审（血泪史 #5：两次运行产物混放）。
    allow_extra=True 用于"阶段一先落盘、阶段二再补 sidecar"的分批写：此时
    目录里可能已有上批的 sidecar/SHA256SUMS，不能要求文件集完全相等。
    """
    if out_dir.exists():
        existing = {p.name for p in out_dir.iterdir() if p.is_file()}
        if not allow_extra:
            unexpected = existing - set(files)
            if unexpected:
                raise SystemExit(
                    f"[gene-graft] {out_dir} 存在多余文件 {sorted(unexpected)}"
                    "——拒写（两次运行产物混放风险）")
        changed = [n for n, data in files.items()
                   if (out_dir / n).exists()
                   and (out_dir / n).read_bytes() != data]
        if changed:
            raise SystemExit(
                f"[gene-graft] {out_dir} 已存在且内容不一致（{changed}）——拒写；"
                "疑口径/输入漂移，人工移走旧档过审后重跑")
        missing = [n for n in files if not (out_dir / n).exists()]
        for n in missing:
            (out_dir / n).write_bytes(files[n])
        return {"out": str(out_dir), "idempotent": not missing,
                "shas": {k: hashlib.sha256(v).hexdigest()
                         for k, v in files.items()}}
    out_dir.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return {"out": str(out_dir), "idempotent": False,
            "shas": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}


# ---------------------------------------------------------------
# proximity 激活（spec §4.3；统计腿全部复用 proximity 原件）
# ---------------------------------------------------------------

def load_active_population(sg_dir: Path) -> list[dict]:
    """人口 = **全链**（chains.tsv 全量，v1.7 规则 10 修正）。

    v1.8 修正（用户 2026-11-06 规则 10"更优实现直接替换"）：原人口=top_hypotheses
    （TOP100 帽后实为 TOP10 → distinct (菌,端点基因) 对仅 8 个）——与 ②乙 v1.7
    同病灶（样本量不足）。改全链后 distinct 对 6,957 个（=chains 全量），
    与 ②乙 主口径口径一致。
    **注意**：本件 proximity 存在构造性循环（嫁接边与 hop2 同源→端点距离恒为 2），
    故数字**只证明管线端到端可跑，不作独立图结构证据**（评估档 §七.1 机器证据在案）。
    """
    src = sg_dir / "chains.tsv"
    need = ("chain_id", "microbe_id", "hop2_gene")
    _fields, rows = _read_tsv(src)
    missing = [c for c in need if c not in _fields]
    if missing:
        raise SystemExit(f"[gene-graft] chains.tsv 缺列 {missing}"
                         "——fail-closed")
    out = []
    _skipped_no_gene = 0
    for i, r in enumerate(rows):
        cid = (r.get("chain_id") or "").strip()
        if not cid:
            raise SystemExit(f"[gene-graft] top_hypotheses 第 {i + 2} 行 "
                             "chain_id 为空——fail-closed")
        _g = (r.get("hop2_gene") or "").strip()
        _m = (r.get("microbe_id") or "").strip()
        if not _g or not _m:
            # 全链口径下 depth=1 链无 hop2_gene（真实数据 537 条）→ 计入跳过，
            # 不 fail-closed（fail-closed 只用于"整表零可用行"）
            _skipped_no_gene += 1
            continue
        out.append({"chain_id": cid, "microbe_id": _m, "hop2_gene": _g,
                    "metabolite_id": (r.get("metabolite_id") or "").strip()})
    if not out:
        raise SystemExit("[gene-graft] chains.tsv 零可用链（无 hop2_gene）"
                         "——fail-closed")
    return out


def pair_distance_table(adj: dict[str, set[str]], seeds: list[str],
                        targets: list[str]) -> dict[str, dict[str, int]]:
    """种子池→靶集池的全距离表（每个种子一次 BFS）。

    为什么先建表：逐链零模型 10 链 × 1000 置换若每次 BFS=1 万次 BFS（约十秒
    级）；建表只需 |种子池| 次 BFS（287 次），置换退化为字典查表。数值完全
    相同（同一 BFS 口径），只是把重复计算挪出循环。
    """
    tset = set(targets)
    table: dict[str, dict[str, int]] = {}
    for s in seeds:
        dist = prox.bfs_distances(adj, s)
        table[s] = {t: dist[t] for t in tset if t in dist}
    return table


def _chain_null(table: dict[str, dict[str, int]], cands_seed: dict[str, list[str]],
                cands_target: dict[str, list[str]], seed_node: str,
                target_node: str, seed: int = RNG_SEED,
                n_perm: int = N_PERM) -> dict:
    """逐链度匹配零分布（单种子×单靶；§1.3 全条款）。

    为什么逐链独立起算 RNG：与 U2 "每条链独立 random.Random(20261105)"同制
    ——链间零传染，任意链序/子集重跑结果相同（验收条款①的机器基础）。
    不可达置换不重抽（spec §1.3 允许不可达），只计数并从 mean/sd 剔除。
    """
    rng = random.Random(seed)
    nulls: list[int] = []
    n_unreach = 0
    for _ in range(n_perm):
        a = rng.choice(cands_seed[seed_node])
        b = rng.choice(cands_target[target_node])
        d = table[a].get(b)
        if d is None:
            n_unreach += 1
            continue
        nulls.append(d)
    return {"nulls": nulls, "n_unreachable": n_unreach, "n_perm": n_perm,
            "rng_seed": seed}


PER_CHAIN_TOP_N = 100        # v1.8：逐链行数上限（TOP-N 卡）


def load_per_chain_population(sg_dir: Path) -> list[dict]:
    """逐链 sidecar 人口 = TOP-N 卡（按排名键），非全链。

    为什么逐链不铺全链：逐链 proximity 存在**构造性循环**（嫁接边与 hop2 同源
    → 端点距离恒为 2），分数无独立证据价值（评估档 §七.1）。铺全链 43,236 行要
    ×1000 置换=4,323 万次计算（实测 336s），换来零证据增量 → 只对 TOP-N 出
    逐链行（管线可跑性与与 v0.2 降级 sidecar 的可比性），人口级统计量另走全链。
    """
    # v1.8：cards 不复制进新目录（26MB）——从 manifest 记的源子图父目录只读
    man = json.loads((sg_dir / "graft_manifest.json").read_text(encoding="utf-8"))
    parent = man.get("source_subgraph", {}).get("parent_dir")
    src = (Path(parent) / man["source_subgraph"]["dir_name"]
           / "hypothesis_cards.tsv") if parent else (sg_dir / "hypothesis_cards.tsv")
    if not src.exists():
        raise SystemExit(f"[gene-graft] 逐链 cards 源不存在：{src}——fail-closed")
    need = ("chain_id", "microbe_id", "hop2_gene", "votes_total", "borda_score")
    fields, rows = _read_tsv(src)
    missing = [c for c in need if c not in fields]
    if missing:
        raise SystemExit(f"[gene-graft] hypothesis_cards.tsv 缺列 {missing}"
                         "——fail-closed")
    rows = [r for r in rows if (r.get("hop2_gene") or "").strip()
            and (r.get("microbe_id") or "").strip()]
    rows.sort(key=lambda r: (-int(r["votes_total"]), -int(r["borda_score"]),
                             r["chain_id"]))
    return rows[:PER_CHAIN_TOP_N]


def activate(new_dir: Path) -> dict:
    """在新目录上跑 AMSPL proximity（§1.3 全条款）→ sidecar 行 + 人口级结果。"""
    nodes, g = prox.load_graph(new_dir)
    adj = {n: g[n]["adj"] for n in nodes}
    degree = {n: prox.degree_of(n, adj) for n in nodes}
    seed_pool = [n for n in nodes if g[n]["category"] in SEED_CATEGORIES]
    gene_pool = [n for n in nodes if g[n]["category"] == TARGET_CATEGORY_ACTIVE]
    if not seed_pool or not gene_pool:
        raise SystemExit("[gene-graft] 激活失败：菌池或基因池为空——fail-closed")

    cands_seed, summary_seed = prox.null_candidates(seed_pool, degree)
    cands_gene, summary_gene = prox.null_candidates(gene_pool, degree)

    cards = load_active_population(new_dir)          # 人口级：全链（规则 10）
    per_chain = load_per_chain_population(new_dir)   # 逐链：TOP-N（循环无证据价值）
    seeds: list[str] = []
    targets: list[str] = []
    for c in cards:
        if not c["microbe_id"] or not c["hop2_gene"]:
            raise SystemExit(f"[gene-graft] 卡片 {c['chain_id']} 菌/端点基因为空"
                             "——fail-closed（距离无对象）")
        if c["microbe_id"] not in seeds:
            seeds.append(c["microbe_id"])
        if c["hop2_gene"] not in targets:
            targets.append(c["hop2_gene"])
    missing = [n for n in seeds + targets if n not in adj]
    if missing:
        raise SystemExit(f"[gene-graft] 人口节点不在新目录 merged_nodes："
                         f"{missing}——fail-closed（嫁接未覆盖=门禁应已降级）")

    # ---- 门禁（spec §4.3：U2 阈值复用 + §1.3 不可达规则）----
    comps = gs.connected_components(nodes, adj)
    gate = gs.lcc_gate(nodes, comps, seeds, targets)
    comp_idx = gs.component_index(comps)

    # ---- 人口级 AMSPL（§1.3 主统计量；靶集=端点基因集）----
    d_c, unreach_rate = prox.amspl(adj, seeds, targets)
    not_applicable = unreach_rate > UNREACHABLE_MAX
    pop = {
        "n_cards": len(cards), "n_seeds": len(seeds),
        "n_targets": len(targets), "n_pairs": _count_pairs(cards),
        "seeds": seeds, "targets": targets, "d_c": d_c,
        "mean_all_pairs": prox.mean_all_pairs_distance(adj, seeds, targets),
        "unreachable_rate": unreach_rate, "not_applicable": not_applicable,
        "null_mean": None, "null_sd": None, "z": None, "p": None,
        "ratio": None, "boundary": "", "verdict": "not_run",
        "n_null_used": 0, "n_null_unreachable": 0,
        "menche": None,
    }
    if not not_applicable and d_c is not None:
        null = prox.permutation_null(adj, {**cands_seed, **cands_gene},
                                     seeds, targets)
        z, mean, sd = prox.z_score(d_c, null["nulls"])
        p = prox.one_sided_p(d_c, null["nulls"]) if null["nulls"] else None
        pop.update({"null_mean": mean, "null_sd": sd, "z": z, "p": p,
                    "ratio": (d_c / mean) if mean else None,
                    "boundary": prox.boundary_label(p),
                    "verdict": descriptive_verdict(z, p),
                    "n_null_used": len(null["nulls"]),
                    "n_null_unreachable": null["n_unreachable"]})
    elif not_applicable:
        pop["verdict"] = "图不适用（不可达率>50%）"
    else:
        pop["verdict"] = "不可算（存在全靶不可达种子）"
    pop["menche"] = prox.menche_separation(adj, seeds, targets, pop["d_c"])

    # ---- 逐链（U2 十列 sidecar 的行级内容）----
    bad_seed = [m for m in seeds if m not in cands_seed]
    bad_gene = [gn for gn in targets if gn not in cands_gene]
    if bad_seed or bad_gene:
        raise SystemExit(
            "[gene-graft] 人口类别不符池定义（菌=Species∪Microbe / 基因=Gene）："
            f"菌 {bad_seed} / 基因 {bad_gene}——fail-closed")
    table = pair_distance_table(adj, seed_pool, gene_pool)
    rows = []
    for c in per_chain:
        m, gn = c["microbe_id"], c["hop2_gene"]
        d_obs = table.get(m, {}).get(gn)
        row = {"chain_id": c["chain_id"], "microbe_id": m, "hop2_gene": gn,
               "d_obs": d_obs, "null_mean": None, "null_sd": None,
               "z": None, "p": None, "n_null_used": 0,
               "n_null_unreachable": 0, "unreachable": d_obs is None,
               "degree_centrality": (gs.degree_centrality(gn, adj, len(nodes))
                                     if gn else None),
               "community": ("1" if comp_idx.get(m) == comp_idx.get(gn) else "0")
               if (m in comp_idx and gn in comp_idx) else "na"}
        if gate["degraded"]:
            row["verdict"] = "门禁降级（不产硬分）"
        elif d_obs is None:
            row["verdict"] = "不可算（链端点不可达）"
        else:
            null = _chain_null(table, cands_seed, cands_gene, m, gn)
            z, mean, sd = prox.z_score(d_obs, null["nulls"])
            p = (prox.one_sided_p(d_obs, null["nulls"])
                 if null["nulls"] else None)
            row.update({"null_mean": mean, "null_sd": sd, "z": z, "p": p,
                        "n_null_used": len(null["nulls"]),
                        "n_null_unreachable": null["n_unreachable"],
                        "verdict": descriptive_verdict(z, p)})
        rows.append(row)

    # ---- 十列 sidecar 文本（degraded_mode 由门禁决定；本件预期 0）----
    degraded = bool(gate["degraded"])
    lines = ["\t".join(ACTIVE_COLUMNS)]
    for row in rows:
        lines.append("\t".join((
            row["chain_id"], row["microbe_id"], row["hop2_gene"],
            _fmt(row["z"]), _fmt(row["degree_centrality"]),
            row["community"], f"{gate['lcc_share']:.6f}",
            f"{gate['seed_missing_rate']:.6f}",
            f"{gate['target_missing_rate']:.6f}", "1" if degraded else "0")))
    sidecar = "\n".join(lines) + "\n"

    n_unreach_rows = sum(1 for r in rows if r["unreachable"])
    return {
        "sidecar": sidecar, "rows": rows, "population": pop, "gate": gate,
        "degraded": degraded, "n_nodes": len(nodes), "degree": degree,
        "pool_report": {
            "seed": {"n": len(seed_pool), "summary": summary_seed},
            "gene": {"n": len(gene_pool), "summary": summary_gene},
        },
        "n_unreachable_rows": n_unreach_rows,
    }


def _count_pairs(cards: list[dict]) -> int:
    """distinct (菌, 端点基因) 配对数（§1.3 强制披露项）。"""
    return len({(c["microbe_id"], c["hop2_gene"]) for c in cards})


def _fmt(x, nd: int = 6) -> str:
    return "na" if x is None else f"{x:.{nd}f}"


def descriptive_verdict(z, p) -> str:
    """描述性判定标签（**不是假设检验结论**）。

    为什么不用 prox.verdict_label：那个函数会写"H1 方向"——②甲 在 spec §0.3
    被明定为构造操作、没有 H1；直接套用会让读者以为这里跑过预注册检验。
    本函数只按 α 阈值给中性标签，全部带"描述性"前缀。
    """
    if z is None or p is None:
        return "不可算（描述性）"
    if p <= ALPHA:
        return "描述性：更近（p≤α）" if z < 0 else "描述性：更远（p≤α）"
    if prox.boundary_label(p):
        return "描述性：边界带（p∈0.05–0.10）"
    return "描述性：不显著"


# ---------------------------------------------------------------
# 评估档（markdown，零时间戳=逐字节可复现）
# ---------------------------------------------------------------

def render_eval_md(graft: dict, act: dict, spec_sha: str,
                   active_sha: str) -> str:
    """评估档（§四 验收 + 监工 P1 两条件强制入档）。"""
    man = graft["manifest"]
    stats = graft["stats"]
    join_s, graft_s, counts = stats["join"], stats["graft"], stats["counts"]
    cov = stats["chain_coverage"]
    pop = act["population"]
    gate = act["gate"]
    out: list[str] = []
    a = out.append
    a("# ②甲 子图基因层嫁接 + proximity 激活 评估档（v0.3 四件之一）")
    a("")
    a(f"> 规格：`{SPEC_PATH}` §四（冻结 v1.5），规格档 sha256=`{spec_sha}`")
    a(f"> 新目录 version 指纹=`{man['graft_version']}`"
      f"（内容指纹 `{man['graft_fingerprint']}`）")
    a(f"> 激活 sidecar sha256=`{active_sha}`（本档与全部产物零时间戳，"
      "同输入同平台逐字节可复现）")
    a("> **统计族声明**：spec §0.3 明定 ②甲 为**构造操作、不计入 ②乙+③甲+③乙 "
      "检验族**——本档 z/p 一律作**描述性**引用，不作假设检验结论，"
      "论文不得据此写\"显著支持\"。")
    a("")
    a("## 一、图版本锚与隔离（spec §4.2/§4.5）")
    a("")
    a("| 项 | 主子图 | 新目录 |")
    a("|---|---|---|")
    a(f"| version | `{man['source_subgraph']['subgraph_version']}` | "
      f"`{man['graft_version']}` |")
    a(f"| 节点数 | {counts['n_nodes_before']} | **{counts['n_nodes_after']}**"
      f"（+{counts['n_nodes_added']}） |")
    a(f"| 边行数 | {counts['n_edges_rows_before']} | "
      f"**{counts['n_edges_rows_after']}**（+{counts['n_edges_rows_added']}） |")
    a(f"| 边数（无向去重） | — | {counts['n_edges_undirected_after']} |")
    a("")
    a("主子图 13 件 golden 冻结 sha 跑前/跑后逐件核验：**13/13 逐字节不变**"
      "（原始输出见 `data/logs/gene_graft_evidence_20261106.txt` 证①A/①B）。"
      "新目录内主子图原件行以**逐字节前缀**保留（`determinism.prefix_preserved`）。")
    a("")
    a("## 二、嫁接口径与机器复核（spec §4.2）")
    a("")
    a("| 项 | 规格锚/探测值 | 实测 | 判定 |")
    a("|---|---|---|---|")
    labels = {
        "n_subgraph_metabolites": "子图代谢物节点",
        "n_table3_rows": "表3 逻辑记录",
        "n_table3_distinct_names": "表3 distinct 名",
        "n_matched_metabolites": "名字 join 命中代谢物",
        "n_gene_edges": "新增代谢物-基因边",
        "n_gene_nodes": "新增基因节点",
        "n_gene_pathway_edges": "新增基因-通路边",
        "n_pathway_nodes": "新增通路节点",
        "n_chains_computable": "可算链（代谢物命中基因）",
    }
    for k, v in man["anchors"].items():
        a(f"| {labels.get(k, k)} | {v['expect']:,} | **{v['got']:,}** | "
          f"{'PASS' if v['pass'] else 'DEVIATE'} |")
    a("")
    a(f"- **join 键（监工 P1 条件②：禁擅改）**：`{join_s['key']}`"
      f"（去首尾空白 + casefold 精确匹配；不用别名、不模糊、不做 ID 映射）")
    a(f"- 覆盖窄的根因=**两库代谢物 ID 体系不互通**（子图 MESH vs 表3 "
      f"ChEBI/名）；命中 {join_s['n_matched_metabolites']}/"
      f"{join_s['n_subgraph_metabolites']} = "
      f"{join_s['n_matched_metabolites'] / join_s['n_subgraph_metabolites']:.1%}，"
      f"跳过 {join_s['n_skipped_metabolites']} 个（清单见 `graft_manifest.json` "
      "的 `skipped_metabolites`，逐条可查）")
    a(f"- 表3 空 Gene ID 行 {join_s['n_rows_empty_gene_id']} 行：无基因身份可挂，"
      "计数披露后跳过（不静默）")
    a(f"- 子图内同名代谢物对 {join_s['n_duplicate_names_in_subgraph']} 组"
      "（键→多节点时逐节点挂边，不合并实体）")
    a(f"- 无通路边基因 {graft_s['n_genes_without_pathway']}/"
      f"{graft_s['n_gene_nodes']} 个=死端（如实计入节点，不修剪）")
    a(f"- 链条覆盖：{cov['n_chains_computable']:,}/{cov['n_chains_total']:,} = "
      f"{cov['share']:.2%}（代谢物命中基因即可算）")
    a("")
    a("V01 资产门禁（三道沿用 deepchain 原件 + 节点表 sha 第四道）：")
    a("")
    a("| 门禁 | 期望 | 实测 | 判定 |")
    a("|---|---|---|---|")
    for k, v in man["v01_gates"].items():
        a(f"| {k} | {v['expect']} | {v['got']} | "
          f"{'PASS' if v['pass'] else 'FAIL'} |")
    a(f"| anchor_check（(基因,通路) 对 ⊆ 冻结链对集） | 违例 0 | "
      f"违例 {man['v01_anchor'].get('violations')} "
      f"（用 {man['v01_anchor'].get('n_pairs_used')}/"
      f"{man['v01_anchor'].get('n_pairs_frozen')} 冻结对） | "
      f"{'PASS' if not man['v01_anchor'].get('violations') else 'FAIL'} |")
    a("")
    a("各源边贡献（manifest `n_edges_by_source`）：")
    a("")
    a("| 源 | 边行数 |")
    a("|---|---|")
    for k, v in man["n_edges_by_source"].items():
        a(f"| {k} | {v:,} |")
    a("")
    a("## 三、SCFA 主轴限定（监工 P1 条件①：强制继承 probe §七；本表为本次机器重算）")
    a("")
    a("| 代谢物 | 链数 | 占比 |")
    a("|---|---|---|")
    for item in man["chain_metabolite_distribution_top"][:5]:
        a(f"| `{item['metabolite_id']}`（{item['name']}） | "
          f"{item['n_chains']:,} | {item['share']:.2%} |")
    a("")
    top_list = man["chain_metabolite_distribution_top"]
    n_rest = cov["n_chains_total"] - sum(i["n_chains"] for i in top_list[:5])
    a(f"（表列 Top5；Top10 其余 {max(0, len(top_list) - 5)} 项合计 "
      f"{n_rest:,} 条链 = {n_rest / cov['n_chains_total']:.2%}，逐项见 "
      "`graft_manifest.json` 的 `chain_metabolite_distribution_top`。）")
    a("")
    a(f"- **Top4 合计占比 {man['chain_share_top4']:.2%}**；"
      f"名字 join 命中的 {join_s['n_matched_metabolites']} 个代谢物合计占 "
      f"**{man['chain_share_matched_metabolites']:.2%}** 的链。")
    a("- **限定句（原样入档，禁成稿期删改）**：")
    a("")
    a("  > ① ②甲 的 proximity 结论**主要反映 SCFA 主轴**——可嫁接代谢物仅 "
      f"{join_s['n_matched_metabolites']}/"
      f"{join_s['n_subgraph_metabolites']}，而链高度集中于 butyrate/acetate/"
      "propionate/succinate。")
    a("  > ② **有效自由度远小于链数**——distinct 代谢物 "
      f"{join_s['n_matched_metabolites']}、基因 {graft_s['n_gene_nodes']}，"
      f"名义 {cov['n_chains_computable']:,} 条可算链不构成独立样本量；"
      "任何以链数计的显著性表述均不成立。")
    a("")
    a("## 四、proximity 激活门禁（spec §4.3）")
    a("")
    a("| 门禁 | 阈值 | 实测 | 判定 |")
    a("|---|---|---|---|")
    a(f"| LCC 占比 | ≥ {LCC_SHARE_MIN:.2f} | {gate['lcc_share']:.6f} | "
      f"{'PASS' if gate['lcc_share'] >= LCC_SHARE_MIN else 'FAIL'} |")
    a(f"| 种子缺节点率 | ≤ {MISSING_RATE_MAX:.2f} | "
      f"{gate['seed_missing_rate']:.6f} | "
      f"{'PASS' if gate['seed_missing_rate'] <= MISSING_RATE_MAX else 'FAIL'} |")
    a(f"| 靶集缺节点率 | ≤ {MISSING_RATE_MAX:.2f} | "
      f"{gate['target_missing_rate']:.6f} | "
      f"{'PASS' if gate['target_missing_rate'] <= MISSING_RATE_MAX else 'FAIL'} |")
    a(f"| 不可达配对占比（§1.3） | ≤ {UNREACHABLE_MAX:.2f} | "
      f"{pop['unreachable_rate']:.6f} | "
      f"{'PASS' if not pop['not_applicable'] else 'FAIL（图不适用）'} |")
    a("")
    a(f"**degraded_mode = {'1' if act['degraded'] else '0'}**"
      f"（连通片 {gate['n_components']} 个、LCC {gate['lcc_size']} 节点）；"
      f"逐链端点不可达行 {act['n_unreachable_rows']}/{len(act['rows'])}。")
    a("")
    a("**主子图 sidecar 的 `degraded_mode=1` 记录保留不动**——v0.2 的降级是"
      "当时结构事实（子图无基因层，靶集缺节点率 100%），历史如实性要求"
      "不得回改（spec §4.3）。本件只在新目录产 `graph_structure_scores_active.tsv`。")
    a("")
    a("## 五、人口级 AMSPL 结果（§1.3 全条款；靶集=端点基因集）")
    a("")
    a("| 卡数 | 种子 | 靶集 | n_pairs | d_c (AMSPL) | mean_all_pairs（辅助，不参与 z） | "
      "null_mean | null_sd | z | 单侧 p | d_c/null_mean | 不可达率 | 判定 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    a(f"| {pop['n_cards']} | {pop['n_seeds']} | {pop['n_targets']} | "
      f"**{pop['n_pairs']}** | {_fmt(pop['d_c'], 4)} | "
      f"{_fmt(pop['mean_all_pairs'], 4)} | {_fmt(pop['null_mean'], 4)} | "
      f"{_fmt(pop['null_sd'], 4)} | {_fmt(pop['z'], 4)} | "
      f"{_fmt(pop['p'], 6)} | {_fmt(pop['ratio'], 4)} | "
      f"{_fmt(pop['unreachable_rate'], 4)} | {pop['verdict']} |")
    a("")
    a(f"零模型：度匹配置换（精确 degree 分箱 + 箱内 <5 向大侧合并），n_perm="
      f"{N_PERM}、`random.Random({RNG_SEED})` 钉扎、单侧 α={ALPHA}；"
      f"不可达置换剔除 {pop['n_null_unreachable']} 个（不重抽）。")
    a("")
    a(f"人口构成：种子 {pop['n_seeds']} 个（"
      f"{'、'.join(pop['seeds'])}）、端点基因 {pop['n_targets']} 个、"
      f"distinct 配对 {pop['n_pairs']}。TOP10 卡全部为 butyrate 轴"
      "（占全库链 60.7%），且端点对距离在下文 §七.1 有构造性解释——"
      "本表数字**不外推到全库 43,236 条可算链**。")
    a("")
    a("Menche separation（附加输出，不参与判定）："
      f"d_AB={_fmt(pop['menche']['d_ab'], 4)}、"
      f"d_AA={_fmt(pop['menche']['d_aa'], 4)}、"
      f"d_BB={_fmt(pop['menche']['d_bb'], 4)}、"
      f"s_AB={_fmt(pop['menche']['s_ab'], 4)}"
      "（s<0=模块重叠 / s>0=拓扑分离）。")
    a("")
    a("## 六、逐链激活分数（`graph_structure_scores_active.tsv` 的行级内容）")
    a("")
    a("| chain_id | microbe_id | hop2_gene | d_obs | null_mean | null_sd | z | 单侧 p | "
      "度中心性(披露) | 同连通片 | 判定 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in act["rows"]:
        a(f"| {r['chain_id']} | {r['microbe_id']} | {r['hop2_gene']} | "
          f"{'na' if r['d_obs'] is None else r['d_obs']} | "
          f"{_fmt(r['null_mean'], 4)} | {_fmt(r['null_sd'], 4)} | "
          f"{_fmt(r['z'], 4)} | {_fmt(r['p'], 6)} | "
          f"{_fmt(r['degree_centrality'], 4)} | {r['community']} | "
          f"{r['verdict']} |")
    a("")
    a("（十列 schema 与 U2 完全一致：`chain_id/microbe_id/hop2_gene/proximity_z/"
      "degree_centrality_disclosed/community_degraded/lcc_share/"
      "seed_missing_rate/target_missing_rate/degraded_mode`；"
      "本表只把 sidecar 的 `proximity_z` 展开成距统计量明细，便于审计。）")
    a("")
    a("## 七、解读边界（必读，防把 z 读成独立图结构证据）")
    a("")
    a("1. **构造性循环（最重要）**：嫁接的\"代谢物-基因\"边与被检验链的 hop2 "
      "**同源**（都来自 gutMGene 表3）。一条完整链的端点对距离因此恒为 2"
      "（菌→代谢物→基因）——这是**链构造的直接后果**，不是独立验证。"
      "本件证明的是\"基因层可物化、proximity 管线端到端可跑\"（机器有效性），"
      "**不是**\"图结构独立支持假设\"（那需要与造链知识源不相交的边证据）。")
    cc = stats.get("chain_population_check", {})
    dist_txt = "、".join(f"d={k}:{v}"
                         for k, v in cc.get("distance_distribution", {}).items())
    a(f"   机器证据（全链人口，非本 sidecar 人口）：{cc.get('n_chains_total'):,} "
      f"条链的 distinct (菌,端点基因) 对 {cc.get('n_distinct_pairs'):,} 个，"
      f"最短距离分布 {{{dist_txt}}}——全部为 2 即证明端点距离由嫁接边本身"
      "唯一决定（`graft_manifest.json` 的 `chain_population_check`）。")
    a("2. **SCFA 主轴**：见 §三 两条限定句——覆盖 23/274、Top4 "
      f"{man['chain_share_top4']:.2%}，有效自由度远小于链数。")
    a("3. **不计入检验族**：spec §0.3 明定 ②甲 为构造操作；论文报告 ②乙/③甲/"
      "③乙 三组时不得把本件 z/p 并入\"显著/不显著\"叙事。")
    a("4. **降级历史**：主子图 sidecar 的 `degraded_mode=1` 是 v0.2 结构事实，"
      "保留不动；新目录 `degraded_mode=0` 只描述\"嫁接后的图\"。")
    a("")
    a("## 八、规格歧义登记表（A 类·实现期裁定，禁静默自决）")
    a("")
    a("| 编号 | 规格缺口 | 本实现口径 | 依据/理由 |")
    a("|---|---|---|---|")
    a("| A1 | join 键是否 casefold/去空白 | 去首尾空白 + casefold 精确匹配 | "
      "P1 条件②禁擅改键语义；格式归一是 probe 23 命中的机器口径 |")
    a("| A2 | 同一代谢物-基因对多行 | 并集 PMID 去重、support_count=行数 | "
      "丢 refs=丢溯源（沿 deepchain hop2 惯例） |")
    a("| A3 | 表3 空 Gene ID 行 | 跳过并计数（n_rows_empty_gene_id） | "
      "无基因身份可挂；计数披露不静默 |")
    a("| A4 | 同一基因多符号 | 取表内首次出现 | 表3 行序确定；当前 0 冲突 |")
    a("| A5 | 新边谓词命名 | 代谢物-基因=`modulates_host_gene`；基因-通路="
      "`participates_in` | A13 订正：前者实为 CHEBI→NCBIGene（代谢物-基因）；"
      "后者=V01 kegg_rest 原谓词 |")
    a("| A6 | 新边其余列取值 | 见 manifest 的 disclosure；未知数值列留空 | "
      "不编造 confidence/years/last_updated |")
    a("| A7 | 新节点 category 名 | `Gene` / `Pathway` | 与 V01 节点表同名类别"
      "（291 通路/850 基因同制） |")
    a("| A8 | 新节点 name 来源 | 基因=表3 Gene 符号；通路=V01 节点表 name | "
      "V01 节点表 sha16 第四道门禁钉死 |")
    a("| A9 | edge_id 公式 | sha256(\"gene_graft|subject|predicate|object|"
      "source_id\")[:16] | 内容哈希=可独立复算、双跑全等 |")
    a("| A10 | 人口口径 | **v1.8 已按规则 10 直接替换为全链** | 人口级 proximity "
      "用 chains.tsv 全量（distinct " + f"{stats['chain_population_check']['n_distinct_pairs']:,}"
      " 对）；逐链 sidecar 保留 TOP-100——理由=构造性循环（§七.1）使逐链分数无独立"
      "证据价值，铺全链 43,236×1000 置换=4,323 万次换零证据增量。**已执行，非待拍板** |")
    a("| A11 | 逐链 z 的零模型口径 | §1.3 度匹配置换，逐链独立 RNG 起算 | "
      "U2\"每条链独立 random.Random\" + §1.3 全条款；单对退化下 AMSPL=d(m,g) |")
    a("| A12 | §1.3 的 α/p 在十列 schema 无列位 | p 只入评估档+stdout | "
      "schema 冻结十列不得加列；§0.3 本件不作假设检验 |")
    a("| A13 | 不可达>50% 的适用层级 | 人口级判\"图不适用\"；逐链不可达记 na 行 | "
      "单对不可达率非 0 即 1，无法构成\"50%\"语义；逐链计数披露 |")
    a("| A14 | 新目录是否复制全部子图件 | 只复制 top_hypotheses.tsv | "
      "激活只用 U2 人口；hypothesis_cards 26MB 不复制（②乙 仍读主子图） |")
    a("| A15 | manifest 是否含时间戳/目录名 | 不含（批次时间只由目录名承载） | "
      "可复现性优先：不同目录双跑逐字节全等 |")
    a("| A16 | 新目录自证清单 | 生成 SHA256SUMS（排除自身） | 沿主子图 SHA256SUMS 制；"
      "三证核验的最小抓手 |")
    a("")
    a("## 九、如实披露")
    a("")
    a("- **join 键未改**（P1 条件②）：23/274 命中率低是 ID 体系断裂的结构事实，"
      "本件未做任何 ChEBI→MeSH/HMDB 补映射——那是方向级议题（重开 K2）。")
    a("- V01 门禁四道全过；`anchor_check` 违例 0（(基因,通路) 对全部 ⊆ 冻结链对集）。")
    a(f"- 无通路基因 {graft_s['n_genes_without_pathway']} 个=死端；"
      "不修剪、不静默丢弃。")
    a("- 新边列填充为最小语义集（未知数值列留空），不做任何数值编造。")
    a("- 本档与全部产物零时间戳；种子钉扎双跑逐字节全等（见 evidence 档证②）。")
    a("")
    a("## 十、产物清单（`graft_manifest.json` 为版本指纹与输入 sha 的唯一出处）")
    a("")
    a("| 文件 | 语义 | sha256（前 20） |")
    a("|---|---|---|")
    for name, sha in graft["product_shas"].items():
        a(f"| `{name}` | {_product_semantics(name)} | {sha[:20]} |")
    a("")
    a("## 十一、待拍板项（呈监工/用户；本件不自决）")
    a("")
    a("| # | 事项 | 通俗解释 | 若拍板\"改\"要做什么 |")
    a("|---|---|---|---|")
    a(f"| 1 | ~~激活人口：TOP10 还是全链~~ **已执行（v1.8，留档）** | "
      f"人口级改为全链：n_pairs={pop['n_pairs']:,}（distinct 菌-基因对）、"
      f"n_seeds={pop['n_seeds']}/n_targets={pop['n_targets']}；逐链保留 TOP-100。"
      "依据=AGENTS 规则 10（用户 2026-11-06：开发期更优实现直接替换，不得降级"
      "为敏感性分析）。**本条已闭合，不再是待拍板项** | — |")
    a("| 2 | **golden v4 三件套（§4.4）未在本步建** | 新冻结清单 v4/规格档/"
      "golden_check 参数化属\"四件合并完成审\"之后的步骤（§五 排期 8-9 行），"
      "且会改 scripts/golden_check.py（T1 留痕）；本件只交 ②甲 本体与三证 | "
      "按 §4.4 逐条建 v4 清单（v3 十三件引用+新目录件 sha 追加）+ 参数化 "
      "golden_check（默认仍 v3）|")
    a("")
    return "\n".join(out)


def _product_semantics(name: str) -> str:
    return {
        "merged_nodes.tsv": "主子图节点 + 基因/通路节点（原件行逐字节前缀保留）",
        "merged_edges.tsv": "主子图边 + 嫁接边（按 (subject,predicate,object) 排序追加）",
        "top_hypotheses.tsv": "U2 人口原件复制（激活用）",
        "graft_manifest.json": "version 指纹 + 输入 sha + 嫁接行数 + 跳过清单",
        "graph_structure_scores_active.tsv": "U2 十列 schema 的激活 sidecar（degraded_mode=0）",
        "SHA256SUMS": "新目录自证清单（排除自身）",
    }.get(name, "")


# ---------------------------------------------------------------
# 全流程
# ---------------------------------------------------------------

def compute(sg_dir: Path, gm_dir: Path, v01_edges: Path, v01_chains: Path,
            v01_nodes: Path, out_dir: Path | None = None) -> dict:
    """嫁接 → 写新目录 → 在新目录上激活 → 评估档文本。

    为什么激活必须在**落盘之后**做：激活读的是新目录的 merged_nodes/
    merged_edges（磁盘事实），不是内存里的行——这样"落盘件"与"激活输入"
    是同一份字节，任何写入路径错误都会在激活时立刻暴露（fail-closed）。
    """
    graft = build_graft(sg_dir, gm_dir, v01_edges, v01_chains, v01_nodes)
    if out_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = kg_root() / "data" / "kg" / f"subgraph_htn_gene_{ts}"
        if out_dir.exists():
            raise SystemExit(f"[gene-graft] 目标目录已存在 {out_dir}"
                             "——fail-closed（两次运行不混放，换时间戳重跑）")
    # 阶段一：先落节点/边/人口/manifest（激活要读盘），逐字节幂等护栏
    stage1 = {
        "merged_nodes.tsv": graft["node_text"].encode("utf-8"),
        "merged_edges.tsv": graft["edge_text"].encode("utf-8"),
        "top_hypotheses.tsv": graft["top_hypotheses_bytes"],
        "chains.tsv": graft["chains_bytes"],
        "graft_manifest.json": _manifest_text(graft["manifest"]).encode("utf-8"),
    }
    wrote1 = _write_files(out_dir, stage1, allow_extra=True)

    act = activate(out_dir)
    active_sha = hashlib.sha256(act["sidecar"].encode("utf-8")).hexdigest()
    graft["product_shas"] = {**{k: hashlib.sha256(v).hexdigest()
                                for k, v in stage1.items()},
                             ACTIVE_SIDECAR_NAME: active_sha}
    spec_path = kg_root() / SPEC_PATH
    spec_sha = (hashlib.sha256(spec_path.read_bytes()).hexdigest()
                if spec_path.exists() else "na")
    eval_md = render_eval_md(graft, act, spec_sha, active_sha)

    # 阶段二：sidecar + SHA256SUMS（同一批次收口；多余文件=两次运行混放→拒写）
    files = dict(stage1)
    files[ACTIVE_SIDECAR_NAME] = act["sidecar"].encode("utf-8")
    files["SHA256SUMS"] = _sums_text(files).encode("utf-8")
    _write_files(out_dir, files, allow_extra=False)

    return {"out": str(out_dir), "graft": graft, "activation": act,
            "eval_md": eval_md, "active_sha": active_sha,
            "spec_sha": spec_sha, "idempotent": wrote1["idempotent"],
            "shas": {k: hashlib.sha256(v).hexdigest()
                     for k, v in files.items()}}


def verify_golden_baseline(sg_dir: Path, golden_list: Path) -> list[dict]:
    """golden 冻结清单逐件 sha 核验（证①）——复用 ②乙 原件，口径零漂移。"""
    return prox.verify_golden_baseline(sg_dir, golden_list)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kg gene-graft",
        description="②甲 子图基因层嫁接 + proximity 激活（新目录隔离，"
                    "主子图零改动）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="主子图目录（只读：merged_nodes/merged_edges/"
                         "chains/top_hypotheses/manifest）")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="新目录（默认 data/kg/subgraph_htn_gene_<ts>/；"
                         "已存在且字节一致=幂等 no-op，不一致=拒写）")
    ap.add_argument("--gutmgene-dir", type=Path, default=None,
                    help=f"gutMGene 目录（缺省 {GUTMGENE_DIR_DEFAULT}）")
    ap.add_argument("--v01-edges", type=Path, default=None,
                    help="V01 边表（缺省 archive/Version01/.../merged_edges.tsv；"
                         "worktree 内须指向主仓 archive/）")
    ap.add_argument("--v01-chains", type=Path, default=None,
                    help="V01 冻结链（缺省 archive/Version01/data/logs/"
                         "mechanism_chains.tsv）")
    ap.add_argument("--v01-nodes", type=Path, default=None,
                    help="V01 节点表（缺省 archive/Version01/.../merged_nodes.tsv）")
    ap.add_argument("--eval-out", type=Path, default=None,
                    help="评估档 markdown 落点（默认 data/logs/"
                         "gene_graft_20261106.md；已存在且字节一致=幂等 no-op）")
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
        print(f"[gene-graft] golden 核验 {len(rows)} 件 | 不一致 {bad} 件")
        print(f"[gene-graft] 清单={golden}")
        return 0 if bad == 0 else 1

    root = kg_root()
    gm = args.gutmgene_dir or Path(GUTMGENE_DIR_DEFAULT)
    v01_edges = args.v01_edges or (root / V01_EDGES_DEFAULT)
    v01_chains = args.v01_chains or (root / V01_CHAINS_DEFAULT)
    v01_nodes = args.v01_nodes or (root / V01_NODES_DEFAULT)
    eval_out = args.eval_out or (root / "data/logs/gene_graft_20261106.md")

    stats = compute(args.subgraph_dir, gm, v01_edges, v01_chains, v01_nodes,
                    args.out_dir)
    # 评估档同样走只增不改护栏（沿 ②乙/③甲/③乙 制）
    if eval_out.exists():
        if eval_out.read_text(encoding="utf-8") != stats["eval_md"]:
            raise SystemExit(f"[gene-graft] {eval_out} 已存在且不一致——拒写"
                             "（疑口径漂移；人工移走旧档过审后重跑）")
        verb = "幂等 no-op（已存在且字节一致）"
    else:
        eval_out.parent.mkdir(parents=True, exist_ok=True)
        eval_out.write_text(stats["eval_md"], encoding="utf-8")
        verb = "新写入"

    g = stats["graft"]["stats"]
    man = stats["graft"]["manifest"]
    act = stats["activation"]
    print(f"[gene-graft] 新目录 {stats['out']}")
    print(f"[gene-graft] version 指纹 {stats['graft']['graft_version']}")
    print(f"[gene-graft] 嫁接：代谢物-基因边 {g['graft']['n_gene_edges']}"
          f"（锚 {ANCHOR_GRAFT_GENE_EDGES}）| 基因节点 "
          f"{g['graft']['n_gene_nodes']}（锚 {ANCHOR_GENE_NODES}）| "
          f"基因-通路边 {g['graft']['n_gene_pathway_edges']}"
          f"（锚 {ANCHOR_GENE_PATHWAY_EDGES}）| 通路节点 "
          f"{g['graft']['n_pathway_nodes']}（锚 {ANCHOR_PATHWAY_NODES}）")
    print(f"[gene-graft] 覆盖：命中代谢物 "
          f"{g['join']['n_matched_metabolites']}/"
          f"{g['join']['n_subgraph_metabolites']} | 可算链 "
          f"{g['chain_coverage']['n_chains_computable']:,}/"
          f"{g['chain_coverage']['n_chains_total']:,}"
          f"（{g['chain_coverage']['share']:.2%}）| SCFA Top4 占比 "
          f"{man['chain_share_top4']:.2%}")
    bad_anchor = [k for k, v in man["anchors"].items() if not v["pass"]]
    print(f"[gene-graft] 锚核对：{'全部 PASS' if not bad_anchor else 'DEVIATE ' + str(bad_anchor)}")
    print(f"[gene-graft] 节点 {act['n_nodes']} | 连通片 "
          f"{act['gate']['n_components']} | LCC {act['gate']['lcc_share']:.6f} | "
          f"种子缺 {act['gate']['seed_missing_rate']:.6f} | 靶集缺 "
          f"{act['gate']['target_missing_rate']:.6f} | degraded_mode="
          f"{1 if act['degraded'] else 0}")
    pop = act["population"]
    print(f"[gene-graft] 人口级 AMSPL：种子 {pop['n_seeds']} / 靶集 "
          f"{pop['n_targets']} / n_pairs {pop['n_pairs']} | d_c="
          f"{_fmt(pop['d_c'], 4)} | null={_fmt(pop['null_mean'], 4)}±"
          f"{_fmt(pop['null_sd'], 4)} | z={_fmt(pop['z'], 4)} | p="
          f"{_fmt(pop['p'], 6)} | 不可达率 {_fmt(pop['unreachable_rate'], 4)} | "
          f"{pop['verdict']}")
    for r in act["rows"]:
        print(f"[gene-graft] 逐链 {r['chain_id']} {r['microbe_id']}→"
              f"{r['hop2_gene']} d={r['d_obs']} z={_fmt(r['z'], 4)} "
              f"p={_fmt(r['p'], 6)} {r['verdict']}")
    print(f"[gene-graft] 激活 sidecar sha256={stats['active_sha']}")
    print(f"[gene-graft] 评估档 {eval_out} {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
