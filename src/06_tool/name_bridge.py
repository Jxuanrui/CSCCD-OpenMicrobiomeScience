#!/usr/bin/env python3
"""NCBITaxon ↔ MetaPhlAn 名称桥接器（重排路线图 A0.5-1 / R4 菌名桥）。

功能定位：把三队列（SelfData_222 / PRJEB13870 / PRJEB21612）MetaPhlAn4 特征名
（k__|p__|…|s__Escherichia_coli 形态）桥接到 NCBITaxon taxid——主图菌节点
ID 体系就是 NCBITaxon:<taxid>，这座桥是后续"在子图上做测序推演"的法定前置
（fail-closed，见 r3_spec:80：桥不通就不许把队列特征对到图节点）。

为什么用 names.dmp+nodes.dmp 而不是第三方库：
- 主图菌节点 ID 体系=NCBITaxon，官方谱系本体就是本地 NCBI taxonomy dump
  （INHERITANCE.md 2026-10-27 登记，public domain，版本快照钉扎）；
- 菌名→taxid 是一次字典查找，为此引入外部依赖不值（轮子三栏表口径）。

匹配规则（全机械、逐字可查，测试逐一覆盖）：
R1 只桥接 s__（种级）与 g__（属级）两个档位；t__（株级）超范围，披露不桥；
R2 MetaPhlAn 名下划线→空格、去首尾空白、casefold 后与 names.dmp 比对；
R3 三档匹配按优先级（前一档命中就不看后档）：
   ① scientific name 精确命中；
   ② scientific name 剥行尾完整括注后命中（正则 r"\\s*\\([^)]*\\)\\s*$"，
     只剥行尾、不碰名中括号——救 "Anaerostipes hominis (ex Lee et al. 2021)"
     vs MetaPhlAn 裸名 "Anaerostipes_hominis" 这类）；
   ③ 同义词类（synonym / equivalent name / genbank synonym）命中；
   三档都未命中→未桥接（name_not_found）；
R4 界域收窄：沿 nodes.dmp parent 链上溯判界——
   ① 途中命中 Bacteria(2)/Archaea(2157)/Fungi(4751)/Viruses(10239) 任一
     → 界内（MetaPhlAn 标记库命名的四大微生物域）；
   ② 途中命中 Animalia(33208)/Plantae(33090) 任一 → 界外——NCBI 属名跨界
     同名不少（Morganella/Proteus/Bacillus 在动物界/植物界也有同名属），
     不排除会把肠道属桥到蛾子/植物上；
   ③ 未命中任何锚而到 root(1) → 界内（Giardia/Blastocystis 等原生生物走
     这条路——微生物真核域，MetaPhlAn 可命名且无动植物同名风险）；
   parent 缺失（断链）→ 界外（fail-closed）。
   收窄后为空→kingdom_scope_empty；仍多个→duplicate_name（候选全披露）；
5. rank 校验：命中但 NCBI rank 与档位不符（s__ 应=species、g__ 应=genus）
   →不桥（rank_mismatch）——防把种名桥到 no-rank/strain 节点污染下游 join；
6. 输出桥接表带 in_main_graph 列（该 taxid 是否为主图节点）——桥到图外的
   taxid 对推演无用，覆盖度报告分列统计。
<<<<<<< HEAD
P0 named-denominator 口径（review_b_phase_review_20261101 整改）：
=======
P0 named-denominator 口径（supervisor_b_phase_review_20261101 整改）：
>>>>>>> origin/main
覆盖度在"全特征"分母之外增设"有名特征"分母（named_d/named_cov 机器字段）
——剔除 SGB/GGB/UMGS 基因组码（token 级：MetaPhlAn4 未命名基因组物种常以
复合名出现，如 Prevotella_SGB1680）、_unclassified 占位、_sp_ 菌株指代；
这些特征在 NCBI taxonomy 本就无正式种/属学名可桥，属"无桥可桥"而非桥缺陷，
混进分母会把桥质量报虚低。UBA 码（如 Clostridiales_bacterium_UBA1390）
不在剔除清单（裁定枚举仅 SGB/GGB/UMGS），按有名未命中披露。
已知留白（披露不静默）：MetaPhlAn 的 Candidatus_* 系名（3 源并集 28 个）
在本地 names.dmp 无对应学名行，按 name_not_found 落未桥接名单——
"Candidatus X" 与正式定名 "X" 不能机械划等号，不做剥前缀重试。

输出（--out-dir）：
- name_bridge_cohorts.tsv  逐特征桥接行（确定性排序，无时间戳列）
- coverage_summary.json    逐源/总体覆盖率 + 未桥接名单（含原因与候选）
确定性：同输入同输出（排序键固定；json 的 generated_at 仅元数据不作数据）。
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 档位→期望 NCBI rank 的对照表（规则 R5 的机械依据）
RANK_EXPECTED = {"s__": "species", "g__": "genus"}

# names.dmp 里可信的同义词 name class 集合（R3 第三档用）。
# 为什么不含 misspelling/authority：错拼与命名人注记桥过去等于把脏名引进图
SYNONYM_CLASSES = {"synonym", "equivalent name", "genbank synonym"}

# 规则 R4 的界域锚与排除表（NCBI taxonomy 官方节点号）：
# 入界锚=MetaPhlAn 标记库命名的四大微生物域；排除锚=动植物（跨界同名重灾区）
KINGDOM_SCOPE = {2, 2157, 4751, 10239}          # Bacteria/Archaea/Fungi/Viruses
KINGDOM_EXCLUDE = {33208, 33090}                # Animalia/Plantae

# 剥行尾括注的正则（R3 第二档）——只作用于行尾的完整 (...) 组
STRIP_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")

# MetaPhlAn lineage 分隔符；特征名=最后一个 | 段
LINEAGE_SPLIT = re.compile(r"\|")

<<<<<<< HEAD
# P0 named-denominator：无名码 token 判定（评审裁定枚举的三族基因组码）
=======
# P0 named-denominator：无名码 token 判定（监工裁定枚举的三族基因组码）
>>>>>>> origin/main
# 为什么 token 级而不是整串匹配：MetaPhlAn4 未命名基因组物种多写成复合名
# （Prevotella_SGB1680 / GGB80011_SGB15265），整串匹配会把它们漏进有名分母
UNNAMED_CODE_TOKEN_RE = re.compile(r"(?:SGB|GGB|UMGS)\d+$", re.IGNORECASE)


def unnamed_reason(name: str) -> str | None:
    """P0 口径：一个 MetaPhlAn 特征名是否"无名"（无 NCBI 学名可桥）。

    返回剔除原因（进披露），None=有名（进 named 分母）：
    - genome_code：任一 token 是 SGB/GGB/UMGS 数字码；
    - unclassified：名字含 _unclassified（谱系占位，非命名实体）；
    - sp_ref：含 _sp_ 或行尾 _sp（菌株指代，无种本名）。
    """
    if any(UNNAMED_CODE_TOKEN_RE.match(p) for p in name.split("_")):
        return "genome_code"
    if "_unclassified" in name:
        return "unclassified"
    if "_sp_" in name or name.endswith("_sp"):
        return "sp_ref"
    return None


def parse_dmp_line(line: str) -> list[str]:
    """拆 names/nodes.dmp 的一行（NCBI dump 格式）。

    格式事实：字段以 "\\t|\\t" 相连，行尾还有一个 "\\t|" 结束符——
    所以必须先剥行尾的 "\\t|" 再 split，否则最后一个字段会黏着竖线
    （name_class 恒不等于 "scientific name"，全表静默桥空——测试锁此坑）。
    """
    return [f.strip() for f in
            line.rstrip("\n").removesuffix("\t|").split("\t|\t")]


def norm_name(name: str) -> str:
    """MetaPhlAn 名→可比对形式：下划线换空格（MetaPhlAn 用 _ 代空格）+casefold。"""
    return name.replace("_", " ").strip().casefold()


def sha256_file(path: Path) -> str:
    """分块 sha256——大文件（names.dmp 2GB+）一次读入会爆内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_metaphlan_features(taxonomy_tsv: Path, source: str) -> list[dict]:
    """读 MetaPhlAn taxonomy.tsv 的特征列（第一列），只留 s__/g__ 两档。

    为什么只取这两档：任务口径=种属两级（差异菌分析的主战场）；t__ 株级
    数量与图谱株节点都极稀，纳入只会稀释覆盖率——计入"超范围"披露不静默。
    """
    feats = []
    with open(taxonomy_tsv, encoding="utf-8") as f:
        f.readline()  # 首行是样本表头（ID\t样本1\t样本2…），不是特征
        for line in f:
            lineage = line.split("\t", 1)[0].strip()
            if not lineage:
                continue
            last = LINEAGE_SPLIT.split(lineage)[-1]
            for rank in RANK_EXPECTED:
                if last.startswith(rank):
                    name = last[len(rank):]
                    if name:
                        feats.append({
                            "source": source,
                            "rank_token": rank,
                            "metaphlan_name": name,
                            "metaphlan_lineage": lineage,
                        })
                    break
    return feats


def build_name_lookup(features: list[dict], names_dmp: Path
                      ) -> dict[str, tuple[str, set[str]]]:
    """单遍扫 names.dmp：目标名 → (match_method, taxid 候选集)。

    三档优先级的实现=三张表按序合入同一张查找表（先到先占位）。
    """
    targets = {norm_name(f["metaphlan_name"]) for f in features}
    sci_hit: dict[str, set[str]] = {}
    sci_strip_hit: dict[str, set[str]] = {}
    syn_hit: dict[str, set[str]] = {}
    with open(names_dmp, encoding="utf-8") as f:
        for line in f:
            fields = parse_dmp_line(line)
            if len(fields) < 4:
                continue
            taxid, name, name_class = fields[0], fields[1], fields[3]
            key = name.casefold()
            in_targets = key in targets
            # 剥行尾括注后的键（R3 第二档）——只对"剥后命中目标"的名字有意义
            skey = STRIP_PAREN_RE.sub("", name).strip().casefold()
            near = (not in_targets) and skey in targets and skey != key
            if not (in_targets or near):
                continue
            if name_class == "scientific name":
                if in_targets:
                    sci_hit.setdefault(key, set()).add(taxid)
                if near:
                    sci_strip_hit.setdefault(skey, set()).add(taxid)
            elif name_class in SYNONYM_CLASSES and in_targets:
                syn_hit.setdefault(key, set()).add(taxid)

    lookup: dict[str, tuple[str, set[str]]] = {
        k: ("sci_exact", v) for k, v in sci_hit.items()}
    for k, v in sci_strip_hit.items():
        if k not in lookup:
            lookup[k] = ("sci_exact_stripped", v)
    for k, v in syn_hit.items():
        if k not in lookup:
            lookup[k] = ("synonym_exact", v)
    return lookup


def build_parent_and_rank(nodes_dmp: Path) -> tuple[dict[int, int], dict[int, str]]:
    """单遍扫 nodes.dmp 建 parent 链与 rank 表（全量——3M 行 int 字典约 300MB，
    换一次性建好后 O(树深) 判界；比分批多遍扫省时的记忆换时间取舍）。"""
    parent: dict[int, int] = {}
    rank: dict[int, str] = {}
    with open(nodes_dmp, encoding="utf-8") as f:
        for line in f:
            fields = parse_dmp_line(line)
            if len(fields) < 3:
                continue
            try:
                taxid, par = int(fields[0]), int(fields[1])
            except ValueError:
                continue  # 非数字 taxid 行不进谱系（防御，正常 dump 不会出现）
            parent[taxid] = par
            rank[taxid] = fields[2]
    return parent, rank


def in_kingdom_scope(taxid: str, parent: dict[int, int]) -> bool:
    """R4：沿 parent 链上溯判界（锚点+排除表+原生生物兜底三段式）。

    终止条件：命中入界锚→True；命中排除锚→False；到 root(1)→True
    （Giardia/Blastocystis 等原生生物路径）；parent 断链→False（fail-closed）；
    走超 64 步→False（防御环——正常 NCBI 树无环，但不信任外部数据格式）。
    """
    cur, steps = int(taxid), 0
    while steps < 64:
        if cur in KINGDOM_SCOPE:
            return True
        if cur in KINGDOM_EXCLUDE:
            return False
        if cur == 1:
            return True
        nxt = parent.get(cur)
        if nxt is None:
            return False
        cur = nxt
        steps += 1
    return False


def bridge(features: list[dict], lookup: dict, parent: dict[int, int],
           rank: dict[int, str], kg_node_ids: set[str]
           ) -> tuple[list[dict], list[dict]]:
    """逐特征裁决（R3 命中→R4 界域收窄+查重→R5 验 rank；任一不过即未桥接）。

    返回 (桥接成功行, 未桥接名单)——未桥接行带 reason 与候选，绝不静默丢。
    """
    rows, unbridged = [], []
    for f in features:
        key = norm_name(f["metaphlan_name"])
        hit = lookup.get(key)
        if not hit:
            unbridged.append({**f, "reason": "name_not_found", "candidates": []})
            continue
        method, cands = hit
        # R4 界域收窄：同名跨界的候选只留细菌/古菌谱系内的
        scoped = sorted(c for c in cands if in_kingdom_scope(c, parent))
        if not scoped:
            unbridged.append({**f, "reason": "kingdom_scope_empty",
                              "candidates": sorted(cands)})
            continue
        if len(scoped) > 1:
            unbridged.append({**f, "reason": "duplicate_name",
                              "candidates": scoped})
            continue
        taxid = scoped[0]
        # R5 rank 校验（档位↔rank 对照）
        if rank.get(int(taxid), "") != RANK_EXPECTED[f["rank_token"]]:
            unbridged.append({**f, "reason": "rank_mismatch",
                              "candidates": [taxid]})
            continue
        rows.append({
            **f,
            "match_method": method,
            "taxid": taxid,
            # R6：桥的价值终点=主图节点；in_main_graph 便于覆盖率分口径
            "in_main_graph": f"NCBITaxon:{taxid}" in kg_node_ids,
        })
    return rows, unbridged


def enrich_scientific_names(rows: list[dict], names_dmp: Path) -> None:
    """回填 NCBI 规范学名（桥接表展示 NCBI 侧名字，供人工抽查桥对没对）。"""
    want = {r["taxid"] for r in rows}
    sci: dict[str, str] = {}
    with open(names_dmp, encoding="utf-8") as f:
        for line in f:
            fields = parse_dmp_line(line)
            if len(fields) >= 4 and fields[3] == "scientific name" \
                    and fields[0] in want and fields[0] not in sci:
                sci[fields[0]] = fields[1]
    for r in rows:
        r["ncbi_scientific_name"] = sci.get(r["taxid"], "")
        r["ncbi_rank"] = RANK_EXPECTED[r["rank_token"]]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NCBITaxon↔MetaPhlAn 名称桥接器")
    # W2.2：三个数据腿参数改注册表供应（required → 哨兵 None；仍无值=报错提示，
<<<<<<< HEAD
    # 不静默通过——评审 W2.1 审 P2 要求）。--taxonomy/--source 属 cohort（W2.4）不动。
=======
    # 不静默通过——监工 W2.1 审 P2 要求）。--taxonomy/--source 属 cohort（W2.4）不动。
>>>>>>> origin/main
    ap.add_argument("--names-dmp", type=Path, default=None,
                    help="NCBI names.dmp（缺省=注册表 ncbi_names）")
    ap.add_argument("--nodes-dmp", type=Path, default=None,
                    help="NCBI nodes.dmp（缺省=注册表 ncbi_nodes）")
    ap.add_argument("--cohort", default=None,
                    help="队列档案 key（taxonomy/source 可由档案供）")
    ap.add_argument("--taxonomy", action="append", type=Path, default=None,
                    help="MetaPhlAn taxonomy.tsv（可重复；缺省=cohort 档案）")
    ap.add_argument("--source", action="append", default=None,
                    help="队列源名（可重复；缺省=cohort 档案）")
    ap.add_argument("--kg-nodes", type=Path, default=None,
                    help="主图 merged_nodes.tsv（缺省=注册表 kg_nodes）")
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    # 同目录模块导入（CLI 路径加载时不会自动挂本目录——与 interact.py:29 同式）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import data_source_profile  # noqa: E402  W2.2 数据腿注册表
    args = data_source_profile.resolve(
        args, {"names_dmp": ("ncbi_names", False),
               "nodes_dmp": ("ncbi_nodes", False),
               "kg_nodes": ("kg_nodes", False)},
        {"names_dmp": str(ROOT / "data/raw/names.dmp"),
         "nodes_dmp": str(ROOT / "data/raw/nodes.dmp"),
         "kg_nodes": str(ROOT / "data/kg/k4_full_20261028_155020/merged_nodes.tsv")})
    import cohort_profile  # noqa: E402  W2.4 队列档案
    args = cohort_profile.resolve(args, {})
    for _dest in ("names_dmp", "nodes_dmp", "kg_nodes", "taxonomy", "source"):
        if getattr(args, _dest) is None:
            _hint = ("configs/data_sources.json 无对应条目" if _dest in ("names_dmp", "nodes_dmp", "kg_nodes") else "队列档案 configs/cohorts/ 未提供（或未传 --cohort）")
            ap.error(f"--{_dest.replace('_', '-')} 缺值：{_hint}且未显式传参")
    if len(args.taxonomy) != len(args.source):
        ap.error("--taxonomy 与 --source 必须一一对应")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # 主图节点 ID 集（只收 NCBITaxon: 前缀——桥的落点是菌节点）
    kg_node_ids = set()
    with open(args.kg_nodes, encoding="utf-8") as f:
        f.readline()
        for line in f:
            nid = line.split("\t", 1)[0]
            if nid.startswith("NCBITaxon:"):
                kg_node_ids.add(nid)

    # 三源特征一次并拢桥接：names/nodes 各只扫一遍（分源各扫一遍=3 倍耗时，
    # 且界域判定的 parent 表可全源共享——量级 3M 行字典，建一次就够）
    per_source_feats = {src: load_metaphlan_features(tsv, src)
                        for tsv, src in zip(args.taxonomy, args.source)}
    all_feats = [f for feats in per_source_feats.values() for f in feats]
    lookup = build_name_lookup(all_feats, args.names_dmp)
    parent, rank = build_parent_and_rank(args.nodes_dmp)

    def cov(feats: list[dict], rows: list[dict], rk: str,
            only_graph: bool) -> dict:
        """覆盖度双口径：全特征分母（既有）+ 有名特征分母（P0 整改新增）。

        named 口径的分子只数有名特征里桥通的行——桥通行必是有名名
        （无名码不可能命中 NCBI），两者相除=剔掉"无桥可桥"后的真实桥质量。
        """
        sub = [f for f in feats if f["rank_token"] == rk]
        named = [f for f in sub if unnamed_reason(f["metaphlan_name"]) is None]
        named_names = {f["metaphlan_name"] for f in named}
        got = [r for r in rows if r["rank_token"] == rk
               and (r["in_main_graph"] or not only_graph)]
        named_got = [r for r in got if r["metaphlan_name"] in named_names]
        return {"n_features": len(sub), "n_bridged": len(got),
                "coverage": round(len(got) / len(sub), 6) if sub else None,
                "named_d": len(named),
                "named_bridged": len(named_got),
                "named_cov": round(len(named_got) / len(named), 6)
                if named else None}

    def named_excluded(feats: list[dict]) -> dict:
        """P0 审计披露：被剔出有名分母的特征按档位×原因计数（可复算）。"""
        out: dict[str, dict[str, int]] = {}
        for rk in RANK_EXPECTED:
            cnt: dict[str, int] = {}
            for f in feats:
                if f["rank_token"] != rk:
                    continue
                r = unnamed_reason(f["metaphlan_name"])
                if r:
                    cnt[r] = cnt.get(r, 0) + 1
            out[rk] = dict(sorted(cnt.items()))
        return out

    def fmt(c: dict) -> str:
        # 无该档特征时 coverage=None——打印层兜底，不让报告在边角输入上崩
        pct = f"{c['coverage']:.1%}" if c["coverage"] is not None else "n/a"
        return f"{c['n_bridged']}/{c['n_features']}（{pct}）"

    def fmt_named(c: dict) -> str:
        # P0 有名口径打印（named_d/named_cov 机器字段的控制台回显）
        pct = f"{c['named_cov']:.1%}" if c["named_cov"] is not None else "n/a"
        return f"{c['named_bridged']}/{c['named_d']}（{pct}）"

    all_rows, report = [], {"sources": [], "inputs": {
        "names_dmp_sha256": sha256_file(args.names_dmp),
        "nodes_dmp_sha256": sha256_file(args.nodes_dmp),
        "kg_nodes_sha256": sha256_file(args.kg_nodes),
        "kg_nodes": str(args.kg_nodes),
        "kingdom_scope": sorted(KINGDOM_SCOPE),
        "kingdom_exclude": sorted(KINGDOM_EXCLUDE),
        "taxonomy": [],
    }}
    for tsv, src in zip(args.taxonomy, args.source):
        feats = per_source_feats[src]
        rows, unb = bridge(feats, lookup, parent, rank, kg_node_ids)
        enrich_scientific_names(rows, args.names_dmp)
        all_rows.extend(rows)
        report["sources"].append({
            "source": src,
            "taxonomy_sha256": sha256_file(tsv),
            # 双口径覆盖度：taxid 桥接率（桥本身的质量）与 in_main_graph 率
            # （桥完还落在主图节点上的可用率——推演真正能用的是后者）
            "coverage_s": cov(feats, rows, "s__", False),
            "coverage_g": cov(feats, rows, "g__", False),
            "coverage_s_in_main_graph": cov(feats, rows, "s__", True),
            "coverage_g_in_main_graph": cov(feats, rows, "g__", True),
            "named_excluded": named_excluded(feats),
            "unbridged_by_reason": {
                reason: sum(1 for u in unb if u["reason"] == reason)
                for reason in sorted({u["reason"] for u in unb})
            },
            "unbridged": sorted(unb, key=lambda u: (u["rank_token"],
                                                    u["metaphlan_name"])),
        })
        report["inputs"]["taxonomy"].append(
            {"source": src, "path": str(tsv), "sha256": sha256_file(tsv)})
        print(f"[{src}] s__: {fmt(cov(feats, rows, 's__', False))} | "
              f"g__: {fmt(cov(feats, rows, 'g__', False))} | 未桥接 {len(unb)}"
              f" | 有名口径 s__: {fmt_named(cov(feats, rows, 's__', False))} | "
              f"g__: {fmt_named(cov(feats, rows, 'g__', False))}")

    union_keys = {(r["source"], r["rank_token"], r["metaphlan_name"])
                  for r in all_rows} | \
                 {(u["source"], u["rank_token"], u["metaphlan_name"])
                  for s in report["sources"] for u in s["unbridged"]}
    report["overall"] = {
        "n_distinct_source_features": len(union_keys),
        "n_bridged_rows": len(all_rows),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 桥接表（排序键固定→同输入同输出）
    all_rows.sort(key=lambda r: (r["source"], r["rank_token"],
                                 r["metaphlan_name"]))
    cols = ["source", "rank_token", "metaphlan_name", "metaphlan_lineage",
            "match_method", "taxid", "ncbi_scientific_name", "ncbi_rank",
            "in_main_graph"]
    tsv_path = args.out_dir / "name_bridge_cohorts.tsv"
    with open(tsv_path, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(cols) + "\n")
        for r in all_rows:
            f.write("\t".join(str(r[c]) for c in cols) + "\n")

    report["outputs"] = {
        "name_bridge_cohorts.tsv": {
            "sha256": sha256_file(tsv_path), "rows": len(all_rows)},
    }
    json_path = args.out_dir / "coverage_summary.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"[done] {tsv_path}（{len(all_rows)} 行）| {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
