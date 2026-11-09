#!/usr/bin/env python3
"""R2'-HT 观测层入库器（重排路线图 A0.5-2 / 轨 1-1 数据入库报审）。

功能定位：把三源（SelfData_222 / PRJEB13870 / PRJEB21612）的 metaphlan4
种属表 + humann3 KO 表 + metadata 复制入库 `data/observation/htn_v1/`，
并逐源做样本对账（metadata ID 集 ≡ taxonomy 表头 ≡ KO 表头）+ 全文件
sha256 指纹契约——Aging R2 的指纹契约法平移（htn_verdict:11 沉没成本回
收项）。报审材料（md）由本工具程序化生成，数字全部机器输出。

为什么复制而不引用：<NAS> 是网络挂载（可能离线/漂移），观测层分析
（推演模块）要跑在仓内数据上；入库=把"分析的输入"钉死在带 sha 的快照上。

入库文件集（每源五件）：
- metadata.txt（分组/别名标签）
- metaphlan4/taxonomy.tsv（菌→丰度）
- humann3/ko.tsv、ko_unstratified.tsv、ko_stratified.tsv（功能谱三件）
不入库：pathabundance 族/KEGG.Pathway 族（本轮推演只吃 KO 与种属表——
按需再入，避免 ~600MB 冗余）；kraken2/seq（原始层，phase0 已指纹在案）。

对账规则（fail-closed，任一不等即非零退出）：
- 逐源三方集合相等：metadata 数据行 ID 集 == taxonomy 表头样本集 ==
  ko 三件表头样本集（ko_stratified 含分层样本名，另行宽松对账并披露）
- 样本数与 Phase 0 数字（222/196/116）一致——htn_phase0_audit_20261029
  的机器数作锚，不一致=源数据变了，停下报数字不硬灌。
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 同目录模块导入（CLI 路径加载时不会自动挂本目录）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 三源根（Nas 挂载）与 Phase 0 样本数锚（数据变了就停——不硬灌新分母）
SOURCES = {
    "SelfData_222": {
        "root": Path("<HTN_NAS_ROOT>/SelfData_222"),
        "expected_n": 222,
    },
    "PRJEB13870": {
        "root": Path("<HTN_NAS_ROOT>/PRJEB13870"),
        "expected_n": 196,
    },
    "PRJEB21612": {
        "root": Path("<HTN_NAS_ROOT>/PRJEB21612"),
        "expected_n": 116,
    },
}

# 每源入库相对路径（root 之下）
REL_FILES = {
    "metadata.txt": "result/metadata.txt",
    "metaphlan4/taxonomy.tsv": "result/metaphlan4/taxonomy.tsv",
    "humann3/ko.tsv": "result/humann3/ko.tsv",
    "humann3/ko_unstratified.tsv": "result/humann3/ko_unstratified.tsv",
    "humann3/ko_stratified.tsv": "result/humann3/ko_stratified.tsv",
}


def sha256_file(path: Path) -> str:
    """分块 sha256——KO 表最大 134MB，分块防内存尖峰。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv_header_ids(path: Path, skip_first_col: bool = True) -> set[str]:
    """读 TSV 表头样本列集合（去首列特征名列）；物理行读法，容忍无尾换行。"""
    with open(path, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
    cols = header[1:] if skip_first_col else header
    return {c for c in cols if c}


def read_metadata_ids(path: Path) -> set[str]:
    """metadata.txt 数据行首列 ID 集（首行=表头）。"""
    ids = set()
    with open(path, encoding="utf-8") as f:
        f.readline()
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            ids.add(line.split("\t", 1)[0].strip())
    return ids


def ingest_source(name: str, cfg: dict, out_root: Path) -> dict:
    """单源入库：复制→sha 双算（源/目的地各一次，防复制过程损坏）→三方对账。"""
    src_root: Path = cfg["root"]
    dst_root = out_root / name
    files = {}
    for rel_dst, rel_src in sorted(REL_FILES.items()):
        src = src_root / rel_src
        dst = dst_root / rel_dst
        if not src.exists():
            raise SystemExit(f"[{name}] 源缺失：{src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # 双 sha：源端与目的地端各自算一遍并断言相等——防网络挂载读半截
        s_sha, d_sha = sha256_file(src), sha256_file(dst)
        if s_sha != d_sha:
            raise SystemExit(f"[{name}] 复制损坏（源/目的地 sha 不等）：{rel_dst}")
        files[rel_dst] = {
            "source_path": str(src), "bytes": dst.stat().st_size,
            "sha256": d_sha,
        }

    # 三方对账（metadata ≡ taxonomy ≡ ko ≡ ko_unstratified）
    meta_ids = read_metadata_ids(dst_root / "metadata.txt")
    tax_ids = read_tsv_header_ids(dst_root / "metaphlan4/taxonomy.tsv")
    ko_ids = read_tsv_header_ids(dst_root / "humann3/ko.tsv")
    kou_ids = read_tsv_header_ids(dst_root / "humann3/ko_unstratified.tsv")
    # ko_stratified 的列=样本×菌分层名（如 `ERR1|Bacteroides`）——取 | 前缀
    # 还原样本集，只对账"样本都在"，分层名不进对账（口径宽松+披露）
    kos_raw = read_tsv_header_ids(dst_root / "humann3/ko_stratified.tsv")
    kos_ids = {c.split("|", 1)[0] for c in kos_raw}

    checks = {
        "n_metadata": len(meta_ids),
        "n_taxonomy": len(tax_ids),
        "n_ko": len(ko_ids),
        "n_ko_unstratified": len(kou_ids),
        "metadata==taxonomy": meta_ids == tax_ids,
        "metadata==ko": meta_ids == ko_ids,
        "metadata==ko_unstratified": meta_ids == kou_ids,
        "ko_stratified_covers_metadata": meta_ids <= kos_ids,
        "n_ko_stratified_samples": len(kos_ids),
        "phase0_anchor_expected_n": cfg["expected_n"],
        "phase0_anchor_match": len(meta_ids) == cfg["expected_n"],
    }
    return {"files": files, "reconciliation": checks,
            "sample_ids": sorted(meta_ids)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R2'-HT 三源观测层入库器")
    ap.add_argument("--out-root", type=Path,
                    default=ROOT / "data/observation/htn_v1",
                    help="入库输出根（注意：禁止指向已冻结观测层目录——"
                         "htn_v1 是 golden 闸锚，重灌请用全新目录再对账）")
    # W2.4 队列适配器：--cohort 时三源清单从队列档案（含本机覆盖件）供应；
    # 不传时用模块内 SOURCES（golden 等价前提：值逐字一致）
    ap.add_argument("--cohort", default=None,
                    help="队列档案 key（configs/cohorts/；三源清单由档案供）")
    ap.add_argument("--dryrun", action="store_true",
                    help="只列将读/将写清单，不复制不落盘")
    args = ap.parse_args(argv)
    sources = SOURCES
    if args.cohort:
        import cohort_profile  # noqa: E402  同目录（下有 sys.path 注入）
        prof = cohort_profile.load(args.cohort)
        if not prof.get("ingest_sources"):
            sys.exit("[ingest] 队列档案无 ingest_sources（本地覆盖件 "
                     "configs/local/cohorts_local.json 缺该键）")
        sources = {k: {"root": Path(v["root"]), "expected_n": v["expected_n"]}
                   for k, v in prof["ingest_sources"].items()}
    if args.dryrun:
        print(f"[dryrun] 将写根目录：{args.out_root}")
        for name, cfg in sources.items():
            print(f"[dryrun] {name}：读 {cfg['root']}/{list(REL_FILES.values())}"
                  f"（锚 n={cfg['expected_n']}）→ 写 {args.out_root / name}")
        print("[dryrun] 干跑结束，未读写任何文件")
        return 0
    args.out_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "layer": "observation_htn_v1",
        "module": "src/06_tool/ingest.py",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 许可口径：自有源=own（无许可项）；公共源=ENA 平台条款（A1 档案
        # api_license_mgnify 系同族）——正式引用时按许可档案补引
        "licensing": {
            "SelfData_222": "own（自有数据，简化档：sha 指纹契约即可）",
            "PRJEB13870": "ENA 公共项目（版本钉扎=本 manifest sha 快照）",
            "PRJEB21612": "ENA 公共项目（版本钉扎=本 manifest sha 快照）",
        },
        "phase0_anchor": "data/logs/htn_phase0_audit_20261029.{md,json}",
        "sources": {},
    }
    fail = False
    for name, cfg in sources.items():
        print(f"[{name}] 入库 → {args.out_root / name}")
        rep = ingest_source(name, cfg, args.out_root)
        manifest["sources"][name] = rep
        rc = rep["reconciliation"]
        ok = all(rc[k] for k in ("metadata==taxonomy", "metadata==ko",
                                 "metadata==ko_unstratified",
                                 "ko_stratified_covers_metadata",
                                 "phase0_anchor_match"))
        if not ok:
            fail = True
        print(f"  对账：n={rc['n_metadata']}（锚 {rc['phase0_anchor_expected_n']} "
              f"{'一致' if rc['phase0_anchor_match'] else '不一致!'}）| "
              f"四方集合{'全等' if ok else '存在不等——见 manifest'}")
    mpath = args.out_root / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print(f"[done] manifest={mpath} | 对账{'全部通过' if not fail else '存在失败项'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
