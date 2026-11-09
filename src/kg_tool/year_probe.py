"""序0：年份覆盖探测（规格 v04_seq0_seq4_spec_20261107.md §一，阈值先冻结后探测）。

探测链路：
  3,683 边 pmids 字段 → 3,313 distinct PMID → PubMed efetch 取发表年份 →
  边级"知识时间"=max(证据年份) → 覆盖率 vs 冻结阈值 80% → 序1 设计 A/B 判定。

纪律：
- 阈值 80% 已在规格冻结（本脚本只判过/不过，无参数可调）；
- PubMed=已许可源（用户裁定 3"限已许可源"）；efetch 批量 + 礼貌间隔；
- 年份快照缓存落盘（pmid_year_snapshot.tsv，带 sha），重复探测零外呼；
- efetch 失败的 PMID 单列（不算"无年份"，算"取数失败"——与真缺失分账）。
"""
import argparse
import csv
import hashlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import sha256_file

ROOT = Path(__file__).resolve().parents[2]
FROZEN_THRESHOLD = 0.80     # 规格冻结值，禁止改


def edge_pmids(kg_dir: Path):
    """边 → (边键, {pmid})；同时返回每边 pmid 列表供 max-year 计算。"""
    out = []
    for r in csv.DictReader(open(kg_dir / "merged_edges.tsv", encoding="utf-8"),
                            delimiter="\t"):
        ps = [p.strip() for p in (r.get("pmids") or "").split(";")
              if p.strip().isdigit()]
        if ps:
            out.append((r["edge_id"], ps))
    return out


def load_year_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"):
        if r["year"] and r["year"].isdigit():
            out[r["pmid"]] = int(r["year"])
    return out


def fetch_years(pmids: list[str]) -> tuple[dict, list[str]]:
    """PubMed efetch 批量取年份 → (pmid→year, 取数失败列表)。"""
    import urllib.parse
    import subprocess
    got, failed = {}, []
    B = 200
    for i in range(0, len(pmids), B):
        chunk = pmids[i:i + B]
        cmd = ["curl", "-sSL", "--max-time", "120", "--retry", "3",
               "--retry-delay", "3",
               "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
               + urllib.parse.urlencode(
                   {"db": "pubmed", "id": ",".join(chunk),
                    "rettype": "docsum", "retmode": "xml"})]
        try:
            raw = subprocess.run(cmd, capture_output=True, check=True).stdout
            root = ET.fromstring(raw)
            found = set()
            for doc in root.iter("DocSum"):
                pid = doc.findtext("Id")
                year = None
                for item in doc.iter("Item"):
                    if item.get("Name") == "PubDate":
                        txt = (item.text or "")
                        for tok in txt.replace("-", " ").split():
                            if tok.isdigit() and len(tok) == 4:
                                year = int(tok)
                                break
                if pid and year:
                    got[pid] = year
                    found.add(pid)
            failed += [p for p in chunk if p not in found]
        except Exception:
            failed += chunk
        time.sleep(0.4)  # NCBI 礼貌间隔（≤3 req/s）
    return got, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序0 年份覆盖探测（阈值 0.80 冻结）")
    ap.add_argument("--kg-dir", type=Path,
                    default=ROOT / "data/kg/k4_full_20261028_155020")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[year-probe] {args.out} 非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    import glob
    gt = sorted(glob.glob(str(ROOT / "data/external/gutmdisorder_v3_*")))[-1]
    edges = edge_pmids(args.kg_dir)
    all_pmids = sorted({p for _, ps in edges for p in ps})

    cache = args.out / "pmid_year_snapshot.tsv"
    years = load_year_cache(cache)
    missing = [p for p in all_pmids if p not in years]
    if missing:
        got, failed = fetch_years(missing)
        years.update(got)
        with open(cache, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["pmid", "year"])
            for p in all_pmids:
                if p in years:
                    w.writerow([p, years[p]])
        n_failed = len(failed)
    else:
        n_failed = 0

    # 边级知识时间 = max(证据年份)；不可解析 = 任一 PMIUD 无年份即该边按
    # "可解析率"口径①计入未解析（口径②另有全解析率，两口径同报）
    resolvable = partial = unresolvable = 0
    edge_max_year = {}
    for eid, ps in edges:
        ys = [years[p] for p in ps if p in years]
        n_miss = len(ps) - len(ys)
        if len(ys) == len(ps) and ys:
            edge_max_year[eid] = max(ys)
            resolvable += 1
        elif ys:
            edge_max_year[eid] = max(ys)
            partial += 1
        else:
            unresolvable += 1
    n = len(edges)
    rate_any = (resolvable + partial) / n
    rate_full = resolvable / n

    verdict = "DESIGN_A_EDGE_SLICING" if rate_any >= FROZEN_THRESHOLD \
        else "DESIGN_B_LEADTIME"
    # 考卷规模：gutMDisorder 文献层 Year>cutoff 的 (taxid,mesh) 对
    import re
    import openpyxl
    UID = re.compile(r"^[DC]\d{6,}$", re.I)
    wb = openpyxl.load_workbook(
        f"{gt}/gutMDisorder_v3_Literature-based_All.xlsx", read_only=True)
    lit = {}
    for r in wb["Literature"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None:
            continue
        y = int(r[3]) if isinstance(r[3], (int, float)) and r[3] else None
        cell = (r[8] or "").strip()
        meshes = set() if "*" in cell else {
            t.strip().upper() for t in re.split(r"[,;]", cell)
            if UID.match(t.strip())}
        lit[idx] = (y, meshes)
    tax_pairs = {}
    for r in wb["Association"].iter_rows(min_row=2, values_only=True):
        idx = str(r[0]) if r[0] is not None else None
        if idx is None or r[3] in (None, "") or idx not in lit:
            continue
        y, meshes = lit[idx]
        if not y:
            continue
        t = str(int(float(r[3])))
        for m in meshes:
            tax_pairs.setdefault((t, m), set()).add(y)
    wb.close()
    exam = {str(c): sum(1 for ys in tax_pairs.values() if min(ys) > c)
            for c in (2022, 2023, 2024)}

    res = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_threshold": FROZEN_THRESHOLD,
        "n_edges": n,
        "n_distinct_pmids": len(all_pmids),
        "pmid_year_resolved": len([p for p in all_pmids if p in years]),
        "pmid_fetch_failed": n_failed,
        "edge_coverage": {
            "resolvable_any": resolvable + partial,
            "resolvable_any_rate": round(rate_any, 6),
            "full_resolved": resolvable,
            "full_resolved_rate": round(rate_full, 6),
            "unresolvable": unresolvable,
            "partial_note": "partial=部分 PMID 有年份（max 年份可用但非全解析）",
        },
        "verdict": verdict,
        "verdict_basis": f"口径① rate_any={rate_any:.4f} vs 冻结阈值 {FROZEN_THRESHOLD}",
        "exam_sizes_by_cutoff": exam,
        "exam_definition": "gutMDisorder (taxid,mesh) 对，其最早文献年 > cutoff",
        "edge_year_hist_top": dict(sorted(Counter(edge_max_year.values())
                                          .items())[-10:]),
        "inputs_sha256": {
            "merged_edges": sha256_file(args.kg_dir / "merged_edges.tsv"),
            "year_snapshot": sha256_file(cache) if cache.exists() else None,
            "script_sha256": sha256_file(Path(__file__)),
        },
    }
    (args.out / "year_probe.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[year-probe] {args.out}")
    print(f"  PMID {len(all_pmids)}，解析 {res['pmid_year_resolved']}，"
          f"取数失败 {n_failed}")
    print(f"  边级覆盖：口径① {rate_any:.1%} / 口径② {rate_full:.1%}"
          f" vs 阈值 {FROZEN_THRESHOLD:.0%} → **{verdict}**")
    print(f"  考卷规模：{exam}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
