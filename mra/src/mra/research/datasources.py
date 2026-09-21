"""目标队列中心数据契约（项目X 只读）：膳食暴露 × 三界菌群/通路 × 协变量。

路径可用环境变量覆盖（PROJECT01_DATA_DIR / RSCRIPT_BIN），默认指向共享服务器既定位置。
本模块对 项目X 永远只读；ID 为各表首列（如 WC1），跨表一致。
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

PROJECT01_DATA = Path(os.environ.get(
    "PROJECT01_DATA_DIR",
    "~/work/Project/projroot/项目X/Data",
))
CLEANED = PROJECT01_DATA / "Cleaned" / "CohortA"
MICROBIOME = PROJECT01_DATA / "Microbiome" / "CohortA"

EXPOSURE_FILES = {
    "dietary_patterns": CLEANED / "exposures_patterns.tsv",
    "chei": CLEANED / "exposures_index.tsv",
    "food_groups": CLEANED / "exposures_groups.tsv",
    "di_gm": CLEANED / "exposures_index2.tsv",
}
FEATURE_FILES = {
    "species": MICROBIOME / "features_species.tsv",
    "pathway": MICROBIOME / "features_pathway.tsv",
    "fungal": MICROBIOME / "features_fungal.tsv",
    "viral": MICROBIOME / "features_viral.tsv",
}
METADATA_FILE = CLEANED / "metadata.tsv"
DEFAULT_COVARIATES = ["Age", "Gender", "Energy_kcal_方案B", "Batch"]


def load_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"数据表缺失：{path}")
    frame = pd.read_csv(path, sep="\t", index_col=0)
    frame.index = frame.index.astype(str).str.strip()
    return frame


def load_exposures(name: str) -> pd.DataFrame:
    if name not in EXPOSURE_FILES:
        raise KeyError(f"未知暴露表 {name}，可选：{sorted(EXPOSURE_FILES)}")
    return load_table(EXPOSURE_FILES[name])


def load_features(name: str) -> pd.DataFrame:
    """载入菌群特征表并统一为 样本×特征 方向（原始文件为 特征行×样本列）。"""
    if name not in FEATURE_FILES:
        raise KeyError(f"未知特征表 {name}，可选：{sorted(FEATURE_FILES)}")
    return load_table(FEATURE_FILES[name]).T


def load_metadata() -> pd.DataFrame:
    return load_table(METADATA_FILE)


def intersect_ids(*frames: pd.DataFrame) -> list[str]:
    """多表样本 ID 交集（保持首表顺序）。"""
    if not frames:
        return []
    keep = set(frames[0].index)
    for frame in frames[1:]:
        keep &= set(frame.index)
    return [i for i in frames[0].index if i in keep]


def top_features_by_prevalence(features: pd.DataFrame, max_features: int = 100) -> list[str]:
    """按流行度（非零样本比例）选 top 特征，控制进入检验的多次比较规模。"""
    prevalence = (features > 0).mean(axis=0)
    return list(prevalence.sort_values(ascending=False).head(max_features).index)


def species_to_term(species_name: str) -> str:
    """MetaPhlAn 行名（s__Faecalibacterium_prausnitzii）→ 图谱检索名（Faecalibacterium prausnitzii）。"""
    name = species_name.split("|")[-1]
    for prefix in ("s__", "g__", "f__"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ").strip()
