"""中心级膳食-菌群全景图谱：全暴露 × 全特征表偏 Spearman + 图谱三分类定级。

产出（var/research/atlas_<run_id>/）：
  hits.tsv        全部 q<0.05 关联（含定级列）
  summary.json    计数与分布摘要
三分类规则：复制 = 图谱有同向边；相反 = 图谱有反向边；独特候选 = 该菌在图但无该
食物边，或图谱无此暴露概念（模式/指数类）；图谱外 = 实体未收录。
多中心复用：换 PROJECT01_DATA_DIR 即可对其他中心出同构图谱。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from ..kg.graph import KGGraph
from . import datasources as ds
from .rtools import run_partial_spearman

NEW_DIMENSIONS_FILE = ds.CLEANED / "exposures_dims.tsv"
EXPOSURE_SPECS: dict[str, list[str] | None] = {
    "dietary_patterns": None,          # 全部数值列（Pattern1-4）
    "chei": ["CHEI_ALL"],
    "di_gm": None,                     # 主指数列
    "food_groups": None,               # 全部 20 组
    "new_dimensions": ["pickled_load", "upf_g", "local_pca1", "spicy_freq"],
}
FOOD_NODE_HINTS = {
    "fruit_cup": "Fruit", "veg_cup": "Vegetable", "green_veg_cup": "Vegetable",
    "whole_grain_oz": "Whole grain", "refined_grain_oz": "Refined grain",
    "red_meat_oz": "Red Meat", "processed_meat_oz": "Processed Meat",
    "poultry_oz": "Poultry", "fish_oz": "Fish", "egg_oz": "Egg", "nuts_oz": "Nuts",
    "soy_oz": "Soy", "legumes_oz": "Legume", "dairy_cup": "Dairy", "ssb_serving": "Sugary",
}


def _load_exposure_table(name: str) -> pd.DataFrame:
    if name == "new_dimensions":
        return ds.load_table(NEW_DIMENSIONS_FILE)
    return ds.load_exposures(name)


def _numeric_columns(frame: pd.DataFrame, wanted: list[str] | None) -> list[str]:
    cols = [c for c in frame.columns if pd.to_numeric(frame[c], errors="coerce").notna().any()]
    if wanted is not None:
        cols = [c for c in cols if c in wanted]
    return cols


def grade_hit(graph: KGGraph, feature: str, exposure_table: str, exposure: str,
              rho: float) -> str:
    term = ds.species_to_term(feature) if "__" in feature else feature
    hits = graph.resolve(term)
    if not hits:
        return "图谱外"
    taxon_id = hits[0].id
    if exposure_table != "food_groups":
        return "暴露概念不在图（独特候选）"
    hint = FOOD_NODE_HINTS.get(exposure, exposure)
    food_hits = graph.resolve(hint)
    food_id = None
    for h in food_hits:
        if h.category == "Food" or "Food" in h.category:
            food_id = h.id
            break
    if food_id is None:
        return "食物节点不在图（独特候选）"
    edges = graph.edge_evidence(taxon_id, food_id)
    if not edges:
        return "无该食物边（独特候选）"
    kg_up = any(e.predicate == "increases_abundance_in" for e in edges)
    kg_down = any(e.predicate == "decreases_abundance_in" for e in edges)
    if (rho > 0 and kg_up) or (rho < 0 and kg_down):
        return "复制"
    if (rho > 0 and kg_down) or (rho < 0 and kg_up):
        return "相反"
    return "无方向边"


def run_sweep(graph: KGGraph, feature_tables: tuple[str, ...] = ("species", "pathway", "fungal", "viral"),
              max_features: int = 200, q_threshold: float = 0.05,
              out_dir: Path | None = None, run_id: str | None = None) -> dict:
    run_id = run_id or time.strftime("atlas_%Y%m%d-%H%M%S")
    out_dir = Path(out_dir) if out_dir else Path(__file__).resolve().parents[3] / "var" / "research" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata = ds.load_metadata()
    covariates = metadata[[c for c in ds.DEFAULT_COVARIATES if c in metadata.columns]]
    feature_frames = {name: ds.load_features(name) for name in feature_tables}
    # 生物表过滤到物种级行（原始表混有 k__/p__ 等高阶层级行，属伪信号；
    # 与图谱侧 BugSigDB ETL 当年的 s__ 过滤同款教训）
    for name in ("species", "fungal", "viral"):
        if name in feature_frames:
            frame = feature_frames[name]
            feature_frames[name] = frame.loc[:, [c for c in frame.columns
                                                 if c.split("|")[-1].startswith("s__")]]
    rows: list[dict] = []
    for exp_name in EXPOSURE_SPECS:
        exp_frame = _load_exposure_table(exp_name)
        for exposure in _numeric_columns(exp_frame, EXPOSURE_SPECS[exp_name]):
            for feat_name, feats in feature_frames.items():
                ids = ds.intersect_ids(exp_frame, feats, covariates)
                keep = pd.to_numeric(exp_frame.loc[ids, exposure], errors="coerce").notna() \
                    & covariates.loc[ids].notna().all(axis=1)
                ids = [i for i, k in zip(ids, keep) if k]
                sel = ds.top_features_by_prevalence(feats.loc[ids], max_features=max_features)
                try:
                    result = run_partial_spearman(
                        pd.to_numeric(exp_frame.loc[ids, exposure]),
                        feats.loc[ids, sel], covariates.loc[ids])
                except RuntimeError as exc:
                    rows.append({"exposure_table": exp_name, "exposure": exposure,
                                 "feature_table": feat_name, "feature": f"__ERROR__{exc}"[:60],
                                 "rho": float("nan"), "q": float("nan"), "n": 0, "grade": "失败"})
                    continue
                for _, r in result[result["q"] < q_threshold].iterrows():
                    rows.append({"exposure_table": exp_name, "exposure": exposure,
                                 "feature_table": feat_name, "feature": r["feature"],
                                 "rho": round(r["rho"], 4), "q": r["q"], "n": int(r["n"]),
                                 "grade": grade_hit(graph, r["feature"], exp_name, exposure, r["rho"])})
    hits = pd.DataFrame(rows).sort_values("q")
    hits.to_csv(out_dir / "hits.tsv", sep="\t", index=False)
    grade_counts = hits["grade"].value_counts().to_dict() if len(hits) else {}
    by_table = hits.groupby("feature_table").size().to_dict() if len(hits) else {}
    summary = {"run_id": run_id, "n_hits": len(hits), "grades": grade_counts,
               "by_feature_table": by_table,
               "n_tested": {f: len(ds.top_features_by_prevalence(fr, max_features))
                            for f, fr in feature_frames.items()}}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="中心级膳食-菌群全景图谱扫描")
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-features", type=int, default=200)
    parser.add_argument("--feature-tables", default="species,pathway,fungal,viral")
    args = parser.parse_args(argv)
    from ..kg.graph import KGGraph
    from ..kg.snapshot import latest_snapshot
    summary = run_sweep(KGGraph(latest_snapshot()),
                        feature_tables=tuple(args.feature_tables.split(",")),
                        max_features=args.max_features, out_dir=Path(args.out) if args.out else None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
