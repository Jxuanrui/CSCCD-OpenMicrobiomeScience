"""队列档案制（阶段 2 W2.4）——用户测序数据的唯一定义处。

与疾病档案（W2.1）/数据腿注册表（W2.2）同式三件套的最后一件：
换队列=写一张 configs/cohorts/<key>.json，不再改代码。

路径策略（同 W2.2）：git 内档案只放仓内相对路径与队列名/样本数锚；
Nas 绝对路径（ingest 三源 root、taxonomy 三件）在
configs/local/cohorts_local.json（gitignore 覆盖件）按 key 提供。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COHORT_DIR = ROOT / "configs" / "cohorts"
LOCAL_PATH = ROOT / "configs" / "local" / "cohorts_local.json"

REQUIRED_FIELDS = ("key", "obs_dir", "bridge_tsv", "source_names")

# argparse dest → 档案字段（四模块共用唯一映射）
DEST_TO_FIELD = {"obs_dir": "obs_dir", "bridge_tsv": "bridge_tsv"}


def load(key: str) -> dict:
    """载入队列档案=git 档案+本地覆盖件合并（本机路径类字段仅本地有）。"""
    p = COHORT_DIR / f"{key}.json"
    if not p.exists():
        avail = sorted(x.stem for x in COHORT_DIR.glob("*.json")) or ["（无）"]
        sys.exit(f"[cohort_profile] 无档案 {p.name}；configs/cohorts/ 可用: "
                 f"{', '.join(avail)}")
    d = json.loads(p.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_FIELDS if not d.get(f)]
    if missing:
        sys.exit(f"[cohort_profile] 档案 {p.name} 缺字段: {missing}")
    if LOCAL_PATH.exists():
        local = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
        d.update(local.get(key, {}))  # 本地覆盖件补 Nas 路径类字段
    return d


def resolve(args: argparse.Namespace, orig_defaults: dict) -> argparse.Namespace:
    """与 disease/data_source 同式：显式 > 队列档案 > 原默认。"""
    profile = load(args.cohort) if getattr(args, "cohort", None) else None
    for dest, field in DEST_TO_FIELD.items():
        if not hasattr(args, dest) or getattr(args, dest) is not None:
            continue
        if profile is not None:
            setattr(args, dest, ROOT / profile[field])  # 仓内相对路径锚定
        else:
            setattr(args, dest, orig_defaults[dest])
    # 列表型：name_bridge 的 --taxonomy（路径列表）与 --source（队列名列表）
    if profile is not None:
        if getattr(args, "taxonomy", None) is None and profile.get("taxonomy"):
            args.taxonomy = [Path(x) for x in profile["taxonomy"]]
        if getattr(args, "source", None) is None:
            args.source = list(profile["source_names"])
    return args
