#!/usr/bin/env python3
"""菌群语料全量发现扫描驱动器：多域检索 → 去重 → 分块可断点批扫。

用法（建议后台运行）：
  NCBI_API_KEY=... HTTPS_PROXY=... uv run python full_scan.py [--per-query 1500] [--chunk 250]
断点：data/fullscan_progress.json 记录已完成 offset，重跑自动续。
环境：NCBI_API_KEY（10 req/s）；无 key 自动 3 req/s。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from providers import pmc_provider  # noqa: E402

ROOT = Path(__file__).parent
DATA = ROOT / "data"
QUERIES = [
    "gut microbiome metagenomics analysis",
    "microbiome association study",
    "dietary intervention microbiome",
    "microbiome statistical method",
    "metagenomic sequencing pipeline",
    "gut microbiota disease",
    "microbiome multi-omics integration",
    "16S rRNA microbiome",
]


def collect_papers(per_query: int) -> list[dict]:
    papers_path = DATA / "fullscan_papers.json"
    if papers_path.exists():
        return json.loads(papers_path.read_text(encoding="utf-8"))
    seen: dict[str, dict] = {}
    for query in QUERIES:
        for attempt in range(3):
            try:
                records = pmc_provider.search_papers(query, max_results=per_query)
                for r in records:
                    pmcid = r.get("pmcid") or r.get("paper_id")
                    if pmcid and r.get("title"):
                        seen.setdefault(str(pmcid), r)
                print(f"[collect] {query}: {len(records)} 篇（累计去重 {len(seen)}）", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 —— 检索层退避重试
                print(f"[collect] {query} 第{attempt+1}次失败: {exc}", flush=True)
                time.sleep(20 * (attempt + 1))
    papers = list(seen.values())
    DATA.mkdir(exist_ok=True)
    papers_path.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")
    print(f"[collect] 完成：{len(papers)} 篇待扫描", flush=True)
    return papers


def run_batches(papers: list[dict], chunk: int, db: Path) -> None:
    progress_path = DATA / "fullscan_progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"offset": 0}
    offset = int(progress["offset"])
    while offset < len(papers):
        end = min(offset + chunk, len(papers))
        papers_file = DATA / f"chunk_{offset}_{end}.json"
        papers_file.write_text(json.dumps(papers[offset:end], ensure_ascii=False),
                               encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "run_batch.py"), "run",
               "--papers-file", str(papers_file), "--db", str(db)]
        print(f"[scan] {offset}-{end}/{len(papers)} ...", flush=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        tail = (result.stdout or result.stderr or "").strip().splitlines()[-1:]
        print(f"[scan]   -> {tail[0] if tail else 'no output'}", flush=True)
        papers_file.unlink(missing_ok=True)
        offset = end
        progress_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
        time.sleep(2)
    print(f"[scan] 全量完成：{len(papers)} 篇 -> {db}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-query", type=int, default=1500)
    parser.add_argument("--chunk", type=int, default=250)
    parser.add_argument("--db", default=str(DATA / "fullscan.db"))
    args = parser.parse_args()
    papers = collect_papers(args.per_query)
    if papers:
        run_batches(papers, args.chunk, Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
