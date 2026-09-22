#!/usr/bin/env python3
"""可执行节点分流器：confirmed artifacts 三路分类（确定性启发式 v1）。

分类规则：
- mature_package   GitHub 仓库，stars >= 阈值(默认30) 或 仓库属知名组织（bioc
                   生态关键词），已有社区维护——agent 直接安装使用，不做转化；
- data_deposit     Zenodo/数据仓或仓库名含 data/dataset/db/atlas——知识性存档，
                   不做方法转化；
- conversion_candidate 其余 GitHub 方法类仓库——Paper2Agent 转化试点池。
用法：uv run python triage.py [--db data/fullscan.db] [--stars 30] [--out data/triage.tsv]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

KNOWN_ORG_HINTS = ("bioc", "bioconductor", "qiime", "nf-core", "snakemake")
DATA_NAME_HINTS = re.compile(r"(data|dataset|database|db|atlas|resource|repo-data)", re.I)


def triage_row(source: str, owner: str, name: str, stars, language: str) -> str:
    if source != "github" or not name:
        return "data_deposit"
    if DATA_NAME_HINTS.search(name or ""):
        return "data_deposit"
    if (stars or 0) >= 30:
        return "mature_package"
    if any(h in (owner or "").lower() for h in KNOWN_ORG_HINTS):
        return "mature_package"
    return "conversion_candidate"


def run(db_path: Path, stars: int, out_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT a.artifact_id, a.paper_id, a.url, a.source, a.language,
                  r.owner, r.name, r.stars
           FROM Artifact a LEFT JOIN Repository r ON a.repo_id = r.repo_id
           WHERE a.status = 'confirmed'"""
    ).fetchall()
    out_rows = []
    counts: Counter = Counter()
    for aid, pid, url, source, lang, owner, name, st in rows:
        klass = triage_row(source or "", owner or "", name or "", st, lang or "")
        counts[klass] += 1
        out_rows.append((aid, pid, url, klass, owner or "", name or "", st or 0, lang or ""))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("artifact_id\tpaper_id\turl\tclass\towner\tname\tstars\tlanguage\n")
        for row in out_rows:
            f.write("\t".join(str(x).replace("\t", " ") for x in row) + "\n")
    summary = {"total_confirmed": len(out_rows), "classes": dict(counts)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path(__file__).parent / "data/fullscan.db"))
    parser.add_argument("--stars", type=int, default=30)
    parser.add_argument("--out", default=str(Path(__file__).parent / "data/triage.tsv"))
    args = parser.parse_args()
    run(Path(args.db), args.stars, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
