"""LitQA2 加载与菌群子集过滤（事实层评测锚点，对标 PaperQA2 报告的 86.97%）。

数据获取（二选一，均为 FutureHouse 开源）：
  1. github.com/Future-House/paper-qa（复现脚本随仓库）；
  2. github.com/Future-House/LAB-Bench（LitQA2 随 LAB-Bench 发布）。
fetch() 依次尝试若干 GitHub raw 候选路径（需 HTTPS_PROXY 指向可用代理）；
失败时人工下载 CSV 后用 load() 本地加载即可，不阻塞其余评测。
"""
from __future__ import annotations

import csv
import os
import urllib.request
from pathlib import Path

DEFAULT_DEST = Path(__file__).resolve().parents[3] / "var" / "eval" / "litqa2.csv"

_CANDIDATE_URLS = [
    "https://raw.githubusercontent.com/Future-House/LAB-Bench/main/datasets/LitQA2/LitQA2.csv",
    "https://raw.githubusercontent.com/Future-House/LAB-Bench/main/LitQA2/LitQA2.csv",
    "https://raw.githubusercontent.com/Future-House/paper-qa/main/paperqa/tests/data/LitQA2.csv",
]

MICROBIOME_KEYWORDS = (
    "microbiome", "microbiota", "microbe", "microbial", "bacteri", "gut ", "intestinal flora",
    "metagenom", "16s", "dysbiosis", "fecal",
)


def fetch(dest: Path = DEFAULT_DEST, urls: list[str] | None = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    last_error: Exception | None = None
    for url in urls or _CANDIDATE_URLS:
        try:
            with opener.open(url, timeout=30) as resp:
                data = resp.read()
            if not data.startswith((b"question", b'"', b"id", b"Q")):
                raise ValueError(f"非预期内容: {url}")
            dest.write_bytes(data)
            return dest
        except Exception as exc:  # noqa: BLE001 - 逐候选降级，最后统一抛
            last_error = exc
    raise RuntimeError(f"LitQA2 拉取失败（人工下载后走 load()）：{last_error}")


def load(path: Path) -> list[dict]:
    path = Path(path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def microbiome_subset(rows: list[dict]) -> list[dict]:
    """按整行文本关键词过滤菌群相关题目（宽松召回，宁多勿漏）。"""
    out = []
    for row in rows:
        text = " ".join(str(v) for v in row.values() if v).lower()
        if any(kw in text for kw in MICROBIOME_KEYWORDS):
            out.append(row)
    return out
