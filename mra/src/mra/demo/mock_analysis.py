"""Deterministic executable for the registered association demo task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from scipy.stats import pearsonr

from mra.demo.synthetic import generate_synthetic_data


def build_analysis_document(seed: int) -> dict[str, Any]:
    """Build the JSON-compatible result for one deterministic demo analysis."""

    data = generate_synthetic_data(seed=seed)
    correlations = [
        pearsonr(data.exposure, data.abundance[:, index])
        for index in range(len(data.taxa))
    ]
    return {
        "method": "pearson_corr",
        "alpha": 0.05,
        "pvalues": [float(result.pvalue) for result in correlations],
        "coefficients": [float(result.statistic) for result in correlations],
        "taxa": list(data.taxa),
        "sample_ids": data.sample_ids.tolist(),
        "exposure": data.exposure.tolist(),
        "batch": data.batch.tolist(),
        "abundance": data.abundance.tolist(),
        "data_scale": "relative",
        "zero_policy": "none",
        "family_id": "single_family",
        "claimed_adjusted": None,
        "seed": seed,
    }


def encode_analysis_document(seed: int) -> bytes:
    """Return the canonical UTF-8 representation used for artifact hashing."""

    return json.dumps(
        build_analysis_document(seed),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def write_analysis(seed: int, output_path: str | Path) -> Path:
    """Write one analysis result and return its path."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_analysis_document(seed))
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args(argv)
    write_analysis(arguments.seed, arguments.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_analysis_document",
    "encode_analysis_document",
    "main",
    "write_analysis",
]
