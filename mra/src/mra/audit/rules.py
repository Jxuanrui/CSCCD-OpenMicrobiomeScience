"""Deterministic statistical audit rules for the MVP analysis workflow."""

from __future__ import annotations

from collections import Counter
import hashlib
import math
from typing import Any, Callable

import numpy as np
import pandas as pd
import scipy
from scipy.stats import chi2_contingency, fisher_exact
from statsmodels.stats.multitest import multipletests

from mra.audit.types import AnalysisSpec, AuditFinding, Verdict


_MULT_METHODS = {
    "bonf": "bonferroni",
    "bonferroni": "bonferroni",
    "bh": "fdr_bh",
    "fdr_bh": "fdr_bh",
    "benjamini-hochberg": "fdr_bh",
    "benjamini_hochberg": "fdr_bh",
    "by": "fdr_by",
    "fdr_by": "fdr_by",
    "benjamini-yekutieli": "fdr_by",
    "benjamini_yekutieli": "fdr_by",
}
_RELATIVE_SCALES = {"composition", "compositional", "proportion", "relative_abundance"}
_COUNT_SCALES = {"count", "counts", "absolute_abundance"}
_WITHIN_BLOCK_TYPES = {"blocked", "paired", "within_block", "within_blocks"}
_UNRESTRICTED_TYPES = {"global", "label", "unrestricted"}


def _finding(rule_id: str, verdict: Verdict, **details: Any) -> AuditFinding:
    return AuditFinding(rule_id=rule_id, verdict=verdict, details=details)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def audit_mult(spec: AnalysisSpec) -> AuditFinding:
    """Validate and reproduce a declared multiple-testing correction."""

    rule_id = "AUDIT-MULT-001"
    relevant = (spec.pvalues, spec.family_id, spec.method, spec.claimed_adjusted)
    if all(value is None for value in relevant):
        return _finding(rule_id, Verdict.NOT_APPLICABLE, reason="no multiple-testing analysis declared")

    missing = [
        name
        for name, value in (
            ("pvalues", spec.pvalues),
            ("family_id", spec.family_id),
            ("method", spec.method),
            ("claimed_adjusted", spec.claimed_adjusted),
        )
        if _missing(value)
    ]
    if missing:
        return _finding(rule_id, Verdict.FAIL, reason="incomplete multiple-testing declaration", missing=missing)

    assert spec.pvalues is not None
    assert spec.claimed_adjusted is not None
    assert spec.family_id is not None
    assert spec.method is not None
    pvalues = np.asarray(spec.pvalues, dtype=float)
    claimed = np.asarray(spec.claimed_adjusted, dtype=float)
    if pvalues.ndim != 1 or pvalues.size == 0:
        return _finding(rule_id, Verdict.FAIL, reason="pvalues must be a non-empty vector")
    invalid_p = np.flatnonzero(~np.isfinite(pvalues) | (pvalues < 0.0) | (pvalues > 1.0))
    if invalid_p.size:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="pvalues contain non-finite or out-of-range values",
            invalid_indices=invalid_p.tolist(),
        )
    if claimed.ndim != 1 or claimed.size != pvalues.size:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="claimed_adjusted must have the same length as pvalues",
            pvalue_count=int(pvalues.size),
            claimed_count=int(claimed.size),
        )
    invalid_claimed = np.flatnonzero(~np.isfinite(claimed) | (claimed < 0.0) | (claimed > 1.0))
    if invalid_claimed.size:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="claimed adjusted values are non-finite or out of range",
            invalid_indices=invalid_claimed.tolist(),
        )
    if not math.isfinite(spec.alpha) or not 0.0 < spec.alpha <= 1.0:
        return _finding(rule_id, Verdict.FAIL, reason="alpha must be finite and in (0, 1]")

    normalized_method = _MULT_METHODS.get(spec.method.strip().lower())
    if normalized_method is None:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="declared correction method is outside the deterministic MVP rule set",
            method=spec.method,
            supported_methods=sorted(set(_MULT_METHODS.values())),
        )

    if isinstance(spec.family_id, str):
        if not spec.family_id.strip():
            return _finding(rule_id, Verdict.FAIL, reason="family_id must not be empty")
        families = [spec.family_id] * int(pvalues.size)
    else:
        families = spec.family_id
        if len(families) != pvalues.size or any(not family.strip() for family in families):
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="per-test family_id values must be non-empty and match pvalues",
                pvalue_count=int(pvalues.size),
                family_count=len(families),
            )

    expected = np.empty_like(pvalues)
    family_sizes: dict[str, int] = {}
    for family in sorted(set(families)):
        indices = np.asarray([index for index, value in enumerate(families) if value == family])
        family_sizes[family] = int(indices.size)
        expected[indices] = multipletests(
            pvalues[indices], alpha=spec.alpha, method=normalized_method
        )[1]

    absolute_tolerance = 1e-10
    relative_tolerance = 1e-7
    matches = np.isclose(
        expected,
        claimed,
        rtol=relative_tolerance,
        atol=absolute_tolerance,
        equal_nan=False,
    )
    details = {
        "method": normalized_method,
        "alpha": spec.alpha,
        "family_sizes": family_sizes,
        "recomputed_adjusted": expected.tolist(),
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
    }
    if not np.all(matches):
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="claimed adjusted values do not match deterministic recomputation",
            mismatch_indices=np.flatnonzero(~matches).tolist(),
            max_absolute_difference=float(np.max(np.abs(expected - claimed))),
            **details,
        )
    return _finding(
        rule_id,
        Verdict.PASS,
        reason="claimed adjusted values match deterministic recomputation",
        **details,
    )


def audit_id(spec: AnalysisSpec) -> AuditFinding:
    """Detect provable identity collisions and partition leakage."""

    rule_id = "AUDIT-ID-001"
    if spec.sample_ids is None and spec.independence_unit is None and spec.partitions is None:
        return _finding(rule_id, Verdict.NOT_APPLICABLE, reason="no identity data declared")
    if spec.sample_ids is None:
        return _finding(rule_id, Verdict.FAIL, reason="sample_ids are required for identity auditing")
    sample_ids = spec.sample_ids
    if not sample_ids or any(not sample_id.strip() for sample_id in sample_ids):
        return _finding(rule_id, Verdict.FAIL, reason="sample_ids must be non-empty identifiers")
    if spec.independence_unit is not None and len(spec.independence_unit) != len(sample_ids):
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="independence_unit must align one-to-one with sample_ids rows",
            sample_count=len(sample_ids),
            independence_unit_count=len(spec.independence_unit),
        )

    duplicate_samples = sorted(
        identifier for identifier, count in Counter(sample_ids).items() if count > 1
    )
    repeated_units: list[str] = []
    if spec.independence_unit is not None:
        if any(not unit.strip() for unit in spec.independence_unit):
            return _finding(rule_id, Verdict.FAIL, reason="independence_unit contains empty identifiers")
        repeated_units = sorted(
            unit for unit, count in Counter(spec.independence_unit).items() if count > 1
        )

    overlaps: list[dict[str, Any]] = []
    unknown_partition_ids: dict[str, list[str]] = {}
    cross_partition_units: list[dict[str, Any]] = []
    empty_partitions: list[str] = []
    if spec.partitions is not None:
        known_ids = set(sample_ids)
        partition_names = sorted(spec.partitions)
        for name in partition_names:
            members = spec.partitions[name]
            if not members:
                empty_partitions.append(name)
            unknown = sorted(set(members) - known_ids)
            if unknown:
                unknown_partition_ids[name] = unknown
        for left_index, left_name in enumerate(partition_names):
            left_ids = set(spec.partitions[left_name])
            for right_name in partition_names[left_index + 1 :]:
                shared_ids = sorted(left_ids & set(spec.partitions[right_name]))
                if shared_ids:
                    overlaps.append(
                        {"partitions": [left_name, right_name], "sample_ids": shared_ids}
                    )

        if spec.independence_unit is not None and not duplicate_samples:
            sample_to_unit = dict(zip(sample_ids, spec.independence_unit))
            for left_index, left_name in enumerate(partition_names):
                left_units = {
                    sample_to_unit[sample_id]
                    for sample_id in spec.partitions[left_name]
                    if sample_id in sample_to_unit
                }
                for right_name in partition_names[left_index + 1 :]:
                    right_units = {
                        sample_to_unit[sample_id]
                        for sample_id in spec.partitions[right_name]
                        if sample_id in sample_to_unit
                    }
                    shared_units = sorted(left_units & right_units)
                    if shared_units:
                        cross_partition_units.append(
                            {"partitions": [left_name, right_name], "independence_units": shared_units}
                        )

    details = {
        "sample_count": len(sample_ids),
        "duplicate_sample_ids": duplicate_samples,
        "repeated_independence_units": repeated_units,
        "partition_overlaps": overlaps,
        "cross_partition_independence_units": cross_partition_units,
        "unknown_partition_ids": unknown_partition_ids,
        "empty_partitions": empty_partitions,
    }
    if overlaps or cross_partition_units or unknown_partition_ids:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="partition identities are not disjoint and valid",
            **details,
        )
    if duplicate_samples or repeated_units or spec.independence_unit is None:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="identity structure may represent repeated or dependent observations",
            **details,
        )
    if empty_partitions:
        return _finding(rule_id, Verdict.WARN, reason="one or more declared partitions are empty", **details)
    return _finding(rule_id, Verdict.PASS, reason="declared identities and partitions are disjoint", **details)


def _batch_groups(exposure: list[Any]) -> tuple[list[str], str] | None:
    series = pd.Series(exposure)
    numeric = pd.to_numeric(series, errors="coerce")
    unique_count = int(series.nunique(dropna=False))
    if numeric.notna().all() and unique_count > 5:
        bin_count = min(3, unique_count)
        quantiles = pd.qcut(numeric, q=bin_count, labels=False, duplicates="drop")
        if int(quantiles.nunique()) < 2:
            return None
        labels = [f"Q{int(value) + 1}" for value in quantiles]
        return labels, f"numeric_quantiles_{int(quantiles.nunique())}"
    return [str(value) for value in exposure], "categorical"


def audit_batch(spec: AnalysisSpec) -> AuditFinding:
    """Audit the observed exposure-by-batch contingency structure."""

    rule_id = "AUDIT-BATCH-001"
    if spec.exposure is None and spec.batch is None:
        return _finding(rule_id, Verdict.NOT_APPLICABLE, reason="no exposure or batch data declared")
    if spec.exposure is None or spec.batch is None:
        return _finding(rule_id, Verdict.FAIL, reason="both exposure and batch are required")
    if not spec.exposure or len(spec.exposure) != len(spec.batch):
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="exposure and batch must be non-empty vectors of equal length",
            exposure_count=len(spec.exposure),
            batch_count=len(spec.batch),
        )
    if not math.isfinite(spec.alpha) or not 0.0 < spec.alpha <= 1.0:
        return _finding(rule_id, Verdict.FAIL, reason="alpha must be finite and in (0, 1]")
    if any(bool(pd.isna(value)) for value in [*spec.exposure, *spec.batch]):
        return _finding(rule_id, Verdict.FAIL, reason="exposure and batch cannot contain missing values")
    numeric_exposure = pd.to_numeric(pd.Series(spec.exposure), errors="coerce")
    if numeric_exposure.notna().all() and not np.isfinite(numeric_exposure.to_numpy()).all():
        return _finding(rule_id, Verdict.FAIL, reason="numeric exposure must contain finite values")

    grouped = _batch_groups(spec.exposure)
    if grouped is None:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="continuous exposure could not be divided into at least two deterministic quantiles",
        )
    exposure_groups, exposure_encoding = grouped
    batch_labels = [str(value) for value in spec.batch]
    table = pd.crosstab(
        pd.Series(exposure_groups, name="exposure"),
        pd.Series(batch_labels, name="batch"),
        dropna=False,
    )
    observed = table.to_numpy(dtype=int)
    table_details = {
        "exposure_encoding": exposure_encoding,
        "exposure_levels": [str(value) for value in table.index.tolist()],
        "batch_levels": [str(value) for value in table.columns.tolist()],
        "contingency_table": observed.tolist(),
    }
    if observed.shape[0] < 2 or observed.shape[1] < 2:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="association cannot be assessed without at least two exposure and batch levels",
            **table_details,
        )

    # If each batch occurs at only one exposure level, the exposure contrast
    # is aliased with batch and cannot be estimated independently.
    if np.all(np.count_nonzero(observed, axis=0) == 1):
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="exposure is completely confounded with batch",
            empty_cells=int(np.count_nonzero(observed == 0)),
            **table_details,
        )

    chi2, chi2_pvalue, _, expected = chi2_contingency(observed, correction=False)
    denominator = observed.sum() * min(observed.shape[0] - 1, observed.shape[1] - 1)
    cramers_v = math.sqrt(float(chi2) / float(denominator)) if denominator else 0.0
    if observed.shape == (2, 2):
        fisher_result = fisher_exact(observed)
        test_name = "fisher_exact"
        pvalue = float(fisher_result.pvalue)
        fisher_statistic = float(fisher_result.statistic)
        test_statistic = fisher_statistic if math.isfinite(fisher_statistic) else None
    else:
        test_name = "chi_square"
        pvalue = float(chi2_pvalue)
        test_statistic = float(chi2)

    empty_cells = int(np.count_nonzero(observed == 0))
    sparse_cells = int(np.count_nonzero(expected < 5.0))
    details = {
        **table_details,
        "test": test_name,
        "test_statistic": test_statistic,
        "pvalue": pvalue,
        "alpha": spec.alpha,
        "cramers_v": cramers_v,
        "empty_cells": empty_cells,
        "expected_cells_below_5": sparse_cells,
    }
    if pvalue < spec.alpha:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="exposure and batch are associated; causal confounding requires review",
            **details,
        )
    if empty_cells or sparse_cells:
        return _finding(
            rule_id,
            Verdict.WARN,
            reason="contingency table contains empty or sparse cells",
            **details,
        )
    return _finding(
        rule_id,
        Verdict.PASS,
        reason="no complete confounding or observed batch association was detected",
        **details,
    )


def _parse_zero_policy(policy: str | dict[str, Any]) -> tuple[str, float] | None:
    method: Any
    value: Any
    if isinstance(policy, str):
        parts = policy.split(":", maxsplit=1)
        method = parts[0]
        value = parts[1] if len(parts) == 2 else None
    else:
        method = policy.get("method")
        value = policy.get("value", policy.get("pseudocount", policy.get("replacement")))
    normalized = str(method).strip().lower().replace("-", "_") if method is not None else ""
    if normalized not in {"additive", "multiplicative", "multiplicative_replacement", "pseudocount"}:
        return None
    try:
        replacement = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(replacement) or replacement <= 0.0:
        return None
    return normalized, replacement


def _replace_zeros(matrix: np.ndarray, method: str, replacement: float) -> np.ndarray | None:
    closed = matrix / matrix.sum(axis=1, keepdims=True)
    if method in {"additive", "pseudocount"}:
        replaced = np.where(closed == 0.0, replacement, closed)
        return replaced / replaced.sum(axis=1, keepdims=True)

    replaced = closed.copy()
    for row_index, row in enumerate(closed):
        zero_mask = row == 0.0
        zero_count = int(np.count_nonzero(zero_mask))
        remaining_mass = 1.0 - zero_count * replacement
        if remaining_mass <= 0.0:
            return None
        replaced[row_index, zero_mask] = replacement
        replaced[row_index, ~zero_mask] = (
            row[~zero_mask] * remaining_mass / row[~zero_mask].sum()
        )
    return replaced


def _helmert_basis(component_count: int) -> np.ndarray:
    basis = np.zeros((component_count - 1, component_count), dtype=float)
    for row in range(1, component_count):
        scale = math.sqrt(row * (row + 1))
        basis[row - 1, :row] = 1.0 / scale
        basis[row - 1, row] = -row / scale
    return basis


def _array_digest(array: np.ndarray) -> str:
    canonical = np.ascontiguousarray(array, dtype="<f8")
    return hashlib.sha256(canonical.tobytes()).hexdigest()


def audit_comp(spec: AnalysisSpec) -> AuditFinding:
    """Validate abundance structure and deterministically recompute log ratios."""

    rule_id = "AUDIT-COMP-001"
    if (
        spec.abundance is None
        and spec.data_scale is None
        and spec.transform is None
        and spec.zero_policy is None
    ):
        return _finding(rule_id, Verdict.NOT_APPLICABLE, reason="no compositional data declared")
    if spec.abundance is None:
        return _finding(rule_id, Verdict.FAIL, reason="abundance is required for compositional auditing")

    matrix = np.asarray(spec.abundance, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        return _finding(rule_id, Verdict.FAIL, reason="abundance must be a non-empty two-dimensional matrix")
    nonfinite = np.argwhere(~np.isfinite(matrix))
    if nonfinite.size:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="abundance contains non-finite values",
            invalid_cells=nonfinite.tolist(),
        )
    negative = np.argwhere(matrix < 0.0)
    if negative.size:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="abundance contains negative values",
            invalid_cells=negative.tolist(),
        )
    row_sums = matrix.sum(axis=1)
    if np.any(row_sums <= 0.0):
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="every abundance row must have positive total abundance",
            invalid_rows=np.flatnonzero(row_sums <= 0.0).tolist(),
        )

    scale = spec.data_scale.strip().lower() if spec.data_scale is not None else None
    if scale in _RELATIVE_SCALES:
        invalid_rows = np.flatnonzero(~np.isclose(row_sums, 1.0, rtol=0.0, atol=1e-8))
        if invalid_rows.size:
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="relative-abundance rows must sum to one",
                invalid_rows=invalid_rows.tolist(),
                row_sums=row_sums.tolist(),
                absolute_tolerance=1e-8,
            )

    zero_count = int(np.count_nonzero(matrix == 0.0))
    parsed_policy: tuple[str, float] | None = None
    if spec.zero_policy is not None:
        parsed_policy = _parse_zero_policy(spec.zero_policy)
        if parsed_policy is None:
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="zero_policy must declare a supported method and positive replacement value",
            )
    if zero_count and parsed_policy is None:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="zero abundance values require an explicit zero_policy",
            zero_count=zero_count,
        )

    working = matrix / row_sums[:, np.newaxis]
    if zero_count:
        assert parsed_policy is not None
        replaced = _replace_zeros(working, *parsed_policy)
        if replaced is None:
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="zero replacement value leaves no mass for non-zero components",
                zero_count=zero_count,
            )
        working = replaced

    transform = (spec.transform or "none").strip().lower()
    details: dict[str, Any] = {
        "shape": [int(value) for value in matrix.shape],
        "data_scale": scale,
        "transform": transform,
        "zero_count": zero_count,
        "row_sum_min": float(np.min(row_sums)),
        "row_sum_max": float(np.max(row_sums)),
    }
    if parsed_policy is not None:
        details["zero_policy"] = {"method": parsed_policy[0], "value": parsed_policy[1]}

    if transform in {"clr", "ilr"}:
        logged = np.log(working)
        clr = logged - logged.mean(axis=1, keepdims=True)
        if not np.all(np.isfinite(clr)):
            return _finding(rule_id, Verdict.FAIL, reason="declared log-ratio transform is not finite", **details)
        details.update(
            {
                "clr_digest_sha256": _array_digest(clr),
                "clr_max_abs_row_sum": float(np.max(np.abs(clr.sum(axis=1)))),
            }
        )
        if transform == "ilr":
            if matrix.shape[1] < 2:
                return _finding(rule_id, Verdict.FAIL, reason="ILR requires at least two components", **details)
            ilr = clr @ _helmert_basis(matrix.shape[1]).T
            details.update(
                {
                    "ilr_basis": "canonical_helmert",
                    "ilr_digest_sha256": _array_digest(ilr),
                    "recomputed_shape": [int(value) for value in ilr.shape],
                }
            )
            return _finding(
                rule_id,
                Verdict.REVIEW_REQUIRED,
                reason="ILR is numerically reproducible, but the scientific balance basis was not declared",
                **details,
            )
        details["recomputed_shape"] = [int(value) for value in clr.shape]
    elif transform not in {"none", "raw"}:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="declared transform is outside the deterministic MVP rule set",
            **details,
        )

    if scale is None or scale not in _RELATIVE_SCALES | _COUNT_SCALES:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="data scale is missing or outside the deterministic MVP rule set",
            **details,
        )
    return _finding(
        rule_id,
        Verdict.PASS,
        reason="abundance structure and declared transform are reproducible",
        **details,
    )


def _permutation_digest(
    n_observations: int,
    n_permutations: int,
    seed: int,
    groups: list[np.ndarray] | None,
) -> tuple[str, bool, str]:
    rng = np.random.default_rng(seed)
    digest = hashlib.sha256()
    blocks_preserved = True
    block_labels = None
    if groups is not None:
        block_labels = np.empty(n_observations, dtype=np.int64)
        for group_index, indices in enumerate(groups):
            block_labels[indices] = group_index
    for _ in range(n_permutations):
        if groups is None:
            permutation = rng.permutation(n_observations)
        else:
            permutation = np.arange(n_observations)
            for indices in groups:
                permutation[indices] = rng.permutation(indices)
            assert block_labels is not None
            blocks_preserved = blocks_preserved and bool(
                np.array_equal(block_labels, block_labels[permutation])
            )
        digest.update(np.ascontiguousarray(permutation, dtype="<i8").tobytes())
    return digest.hexdigest(), blocks_preserved, rng.bit_generator.__class__.__name__


def audit_perm(spec: AnalysisSpec) -> AuditFinding:
    """Validate permutation configuration and replay its index generation."""

    rule_id = "AUDIT-PERM-001"
    relevant = (
        spec.statistic_id,
        spec.permutation_type,
        spec.exchangeability_blocks,
        spec.n_permutations,
        spec.seed,
        spec.lib_versions,
    )
    if all(value is None for value in relevant):
        return _finding(rule_id, Verdict.NOT_APPLICABLE, reason="no permutation analysis declared")

    missing = [
        name
        for name, value in (
            ("statistic_id", spec.statistic_id),
            ("permutation_type", spec.permutation_type),
            ("n_permutations", spec.n_permutations),
            ("seed", spec.seed),
            ("lib_versions", spec.lib_versions),
        )
        if _missing(value)
    ]
    if missing:
        return _finding(rule_id, Verdict.FAIL, reason="incomplete permutation declaration", missing=missing)
    assert spec.statistic_id is not None
    assert spec.permutation_type is not None
    assert spec.n_permutations is not None
    assert spec.seed is not None
    assert spec.lib_versions is not None
    if spec.n_permutations <= 0:
        return _finding(rule_id, Verdict.FAIL, reason="n_permutations must be positive")
    if isinstance(spec.seed, bool):
        return _finding(rule_id, Verdict.FAIL, reason="seed must be an integer, not a boolean")

    normalized_versions = {key.lower(): value for key, value in spec.lib_versions.items()}
    missing_versions = [name for name in ("numpy", "scipy") if not normalized_versions.get(name)]
    if missing_versions:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="lib_versions must record NumPy and SciPy",
            missing=missing_versions,
        )

    permutation_type = spec.permutation_type.strip().lower().replace("-", "_")
    if permutation_type not in _WITHIN_BLOCK_TYPES | _UNRESTRICTED_TYPES:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="permutation type is outside the deterministic MVP rule set",
            permutation_type=spec.permutation_type,
        )

    blocks = spec.exchangeability_blocks
    if spec.sample_ids is not None:
        n_observations = len(spec.sample_ids)
    elif blocks is not None:
        n_observations = len(blocks)
    else:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="sample_ids or exchangeability_blocks are required to replay permutations",
        )
    if n_observations < 2:
        return _finding(rule_id, Verdict.FAIL, reason="at least two observations are required")
    if blocks is not None and len(blocks) != n_observations:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="exchangeability_blocks must align with observations",
            observation_count=n_observations,
            block_count=len(blocks),
        )

    groups: list[np.ndarray] | None = None
    if permutation_type in _WITHIN_BLOCK_TYPES:
        if blocks is None or any(not block.strip() for block in blocks):
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="within-block permutation requires a block label for every observation",
            )
        groups = [
            np.asarray([index for index, value in enumerate(blocks) if value == block], dtype=int)
            for block in sorted(set(blocks))
        ]
        if not any(group.size > 1 for group in groups):
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="declared exchangeability blocks permit no non-identity permutation",
            )
        if permutation_type == "paired" and any(group.size != 2 for group in groups):
            return _finding(
                rule_id,
                Verdict.FAIL,
                reason="paired permutation requires every exchangeability block to contain two rows",
                block_sizes=[int(group.size) for group in groups],
            )
    elif blocks is not None and len(set(blocks)) > 1:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="unrestricted label permutation violates declared exchangeability blocks",
            block_count=len(set(blocks)),
        )

    first_digest, first_preserved, bit_generator = _permutation_digest(
        n_observations, spec.n_permutations, spec.seed, groups
    )
    second_digest, second_preserved, second_bit_generator = _permutation_digest(
        n_observations, spec.n_permutations, spec.seed, groups
    )
    if not first_preserved or not second_preserved:
        return _finding(
            rule_id,
            Verdict.FAIL,
            reason="generated permutations cross declared exchangeability blocks",
        )
    if first_digest != second_digest or bit_generator != second_bit_generator:
        return _finding(rule_id, Verdict.FAIL, reason="fixed-seed permutation replay was not reproducible")

    actual_versions = {"numpy": np.__version__, "scipy": scipy.__version__}
    version_mismatches = {
        name: {"declared": normalized_versions[name], "runtime": actual_versions[name]}
        for name in sorted(actual_versions)
        if normalized_versions[name] != actual_versions[name]
    }
    details = {
        "statistic_id": spec.statistic_id,
        "permutation_type": permutation_type,
        "n_observations": n_observations,
        "n_permutations": spec.n_permutations,
        "seed": spec.seed,
        "bit_generator": bit_generator,
        "permutation_digest_sha256": first_digest,
        "declared_lib_versions": {name: normalized_versions[name] for name in sorted(normalized_versions)},
        "runtime_lib_versions": actual_versions,
        "exchangeability_blocks_preserved": first_preserved,
    }
    if permutation_type in _UNRESTRICTED_TYPES:
        return _finding(
            rule_id,
            Verdict.REVIEW_REQUIRED,
            reason="unrestricted exchangeability is a scientific assumption requiring review",
            **details,
        )
    if version_mismatches:
        return _finding(
            rule_id,
            Verdict.WARN,
            reason="declared library versions differ from the replay environment",
            version_mismatches=version_mismatches,
            **details,
        )
    return _finding(
        rule_id,
        Verdict.PASS,
        reason="fixed-seed replay is reproducible and respects declared blocks",
        **details,
    )


_RULES: tuple[Callable[[AnalysisSpec], AuditFinding], ...] = (
    audit_mult,
    audit_id,
    audit_batch,
    audit_comp,
    audit_perm,
)


def run_audit(spec: AnalysisSpec) -> list[AuditFinding]:
    """Run all MVP rules in stable rule order."""

    return [rule(spec) for rule in _RULES]


__all__ = [
    "audit_batch",
    "audit_comp",
    "audit_id",
    "audit_mult",
    "audit_perm",
    "run_audit",
]
