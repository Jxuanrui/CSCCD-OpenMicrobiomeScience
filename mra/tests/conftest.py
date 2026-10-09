"""mra 测试套件共享夹具（监工 U2-B 裁决，2026-10-08）。

默认合成队列契约：全部测试消费确定性生成的合成 species/pathway/fungal 表，
不再依赖部署机真实受试者队列——CI 与任意机器可跑。真表端到端验证保留为
单独的部署级冒烟测试（REAL_COHORT_CONFIG 指向真实契约才运行，否则 skip）。

复用自 test_h5_multi_omics 的合成表工具（原为该文件私有，U2-B 起上移共享；
测试内自建契约（如 omics_contract）在本夹具之后执行，monkeypatch 依序生效
自然覆盖）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mra.research import datasources as ds

N_SAMPLES = 12


def _write_omics_table(path: Path, prefix: str, n_feats: int = 8) -> None:
    """确定性合成特征表（特征行×样本列，全正值；load_table 转置为样本×特征）。"""
    cols = [f"S{j}" for j in range(N_SAMPLES)]
    lines = ["feature\t" + "\t".join(cols)]
    for i in range(n_feats):
        vals = [f"{0.01 * ((i * 7 + j * 3) % 9 + 1):.6f}" for j in range(N_SAMPLES)]
        lines.append(f"{prefix}{i}\t" + "\t".join(vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _use_contract(monkeypatch, tmp_path: Path, features: dict) -> None:
    """临时数据契约 + 重置 datasources 配置缓存（契约切换必须刷缓存）。"""
    cfg = tmp_path / "cohort.json"
    cfg.write_text(json.dumps({"exposures": {}, "features": features,
                               "metadata": "", "default_covariates": []}),
                   encoding="utf-8")
    monkeypatch.setenv("COHORT_CONFIG", str(cfg))
    monkeypatch.setattr(ds, "_CONFIG_CACHE", None)


@pytest.fixture(autouse=True)
def synthetic_cohort(tmp_path_factory, monkeypatch):
    """默认合成队列契约（autouse）。

    - species 列名 s__ 前缀 / fungal 谱系 |s__ 尾段：与 diversity 过滤器语义对齐
    - 数值含跨样本变化：避免 Shannon/统计路径撞常数列守卫
    - 用独立的 mktemp 目录而非测试自己的 tmp_path：避免污染测试对
      自身 tmp_path 内容的断言（如"失败路径不落盘"类用例）
    """
    workdir = tmp_path_factory.mktemp("synthetic_cohort")
    sp = workdir / "species.tsv"
    pw = workdir / "pathway.tsv"
    fu = workdir / "fungal.tsv"
    _write_omics_table(sp, "s__syn_")
    _write_omics_table(pw, "PWY")
    _write_omics_table(fu, "k__Fungi|p__Syn|s__syn")
    _use_contract(monkeypatch, workdir,
                  {"species": str(sp), "pathway": str(pw), "fungal": str(fu)})
    return workdir
