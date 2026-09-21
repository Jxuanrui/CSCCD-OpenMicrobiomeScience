"""队列数据契约：暴露表 × 菌群特征表 × 协变量（部署配置驱动，代码零课题细节）。

表注册表外置为不入库的 JSON 配置（示例见 cohort_config.example.json）：
  环境变量 COHORT_CONFIG 指向配置，缺省 var/cohort_config.json。
  结构：{"exposures": {名: 绝对路径}, "features": {名: 绝对路径},
         "metadata": 绝对路径, "default_covariates": [...],
         "atlas_exposure_cols": {暴露表名: null(全部数值列) 或 [列名...]}}
本模块对源数据永远只读；ID 为各表首列，跨表一致。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "var" / "cohort_config.json"
_CONFIG_CACHE: dict | None = None


class ConfigNotReady(RuntimeError):
    pass


def load_config(refresh: bool = False) -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None or refresh:
        path = Path(os.environ.get("COHORT_CONFIG", DEFAULT_CONFIG_PATH))
        if not path.is_file():
            raise ConfigNotReady(
                f"缺少队列配置 {path}；复制 cohort_config.example.json 为该路径并填写部署机实际表路径")
        _CONFIG_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _CONFIG_CACHE


def load_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"数据表缺失：{path}")
    frame = pd.read_csv(path, sep="\t", index_col=0)
    frame.index = frame.index.astype(str).str.strip()
    return frame


def load_exposures(name: str) -> pd.DataFrame:
    table = load_config()["exposures"]
    if name not in table:
        raise KeyError(f"未知暴露表 {name}，可选：{sorted(table)}")
    return load_table(table[name])


def load_features(name: str) -> pd.DataFrame:
    """载入特征表并统一为 样本×特征 方向（原始文件为 特征行×样本列）。"""
    table = load_config()["features"]
    if name not in table:
        raise KeyError(f"未知特征表 {name}，可选：{sorted(table)}")
    return load_table(table[name]).T


def load_metadata() -> pd.DataFrame:
    return load_table(load_config()["metadata"])


def default_covariates() -> list[str]:
    return list(load_config().get("default_covariates", []))


def atlas_exposure_cols(exposure_table: str) -> list[str] | None:
    return load_config().get("atlas_exposure_cols", {}).get(exposure_table)


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
