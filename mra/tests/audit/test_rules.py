from __future__ import annotations

import numpy as np
import scipy
from statsmodels.stats.multitest import multipletests

from mra.audit import (
    AnalysisSpec,
    Verdict,
    audit_batch,
    audit_comp,
    audit_id,
    audit_mult,
    audit_perm,
    run_audit,
)
from mra.demo.synthetic import generate_synthetic_data


def _versions() -> dict[str, str]:
    return {"numpy": np.__version__, "scipy": scipy.__version__}


def test_mult_fails_when_unadjusted_values_are_claimed_as_bh() -> None:
    pvalues = [0.001, 0.02, 0.2, 0.8]

    finding = audit_mult(
        AnalysisSpec(
            pvalues=pvalues,
            family_id="primary",
            method="BH",
            claimed_adjusted=pvalues,
        )
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["mismatch_indices"]


def test_mult_fails_for_out_of_range_pvalue() -> None:
    finding = audit_mult(
        AnalysisSpec(
            pvalues=[0.01, 1.01],
            family_id="primary",
            method="bonferroni",
            claimed_adjusted=[0.02, 1.0],
        )
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["invalid_indices"] == [1]


def test_mult_passes_for_correct_bh_values() -> None:
    pvalues = [0.001, 0.02, 0.2, 0.8]
    adjusted = multipletests(pvalues, method="fdr_bh")[1].tolist()

    finding = audit_mult(
        AnalysisSpec(
            pvalues=pvalues,
            family_id="primary",
            method="BH",
            claimed_adjusted=adjusted,
        )
    )

    assert finding.verdict is Verdict.PASS


def test_mult_passes_for_correct_by_values_with_two_families() -> None:
    pvalues = [0.01, 0.02, 0.1, 0.4]
    families = ["a", "a", "b", "b"]
    adjusted = [
        *multipletests(pvalues[:2], method="fdr_by")[1].tolist(),
        *multipletests(pvalues[2:], method="fdr_by")[1].tolist(),
    ]

    finding = audit_mult(
        AnalysisSpec(
            pvalues=pvalues,
            family_id=families,
            method="BY",
            claimed_adjusted=adjusted,
        )
    )

    assert finding.verdict is Verdict.PASS
    assert finding.details["family_sizes"] == {"a": 2, "b": 2}


def test_mult_requires_review_for_unknown_method() -> None:
    finding = audit_mult(
        AnalysisSpec(
            pvalues=[0.01, 0.2],
            family_id="primary",
            method="custom_stepdown",
            claimed_adjusted=[0.02, 0.2],
        )
    )

    assert finding.verdict is Verdict.REVIEW_REQUIRED


def test_id_fails_for_train_test_overlap() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3"],
            independence_unit=["u1", "u2", "u3"],
            partitions={"train": ["s1", "s2"], "test": ["s2", "s3"]},
        )
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["partition_overlaps"][0]["sample_ids"] == ["s2"]


def test_id_fails_when_one_unit_crosses_partitions() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3"],
            independence_unit=["person_1", "person_1", "person_2"],
            partitions={"train": ["s1"], "test": ["s2", "s3"]},
        )
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["cross_partition_independence_units"][0][
        "independence_units"
    ] == ["person_1"]


def test_id_passes_for_unique_rows_without_partitions() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3"],
            independence_unit=["u1", "u2", "u3"],
        )
    )

    assert finding.verdict is Verdict.PASS


def test_id_passes_for_disjoint_partitions() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            independence_unit=["u1", "u2", "u3", "u4"],
            partitions={"train": ["s1", "s2"], "test": ["s3"], "val": ["s4"]},
        )
    )

    assert finding.verdict is Verdict.PASS


def test_id_repeated_rows_require_review_instead_of_proving_pseudoreplication() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s1", "s2"],
            independence_unit=["u1", "u1", "u2"],
        )
    )

    assert finding.verdict is Verdict.REVIEW_REQUIRED
    assert finding.details["duplicate_sample_ids"] == ["s1"]


def test_id_empty_partition_warns() -> None:
    finding = audit_id(
        AnalysisSpec(
            sample_ids=["s1", "s2"],
            independence_unit=["u1", "u2"],
            partitions={"train": ["s1", "s2"], "test": []},
        )
    )

    assert finding.verdict is Verdict.WARN
    assert finding.details["empty_partitions"] == ["test"]


def test_batch_complete_confounding_fails() -> None:
    finding = audit_batch(
        AnalysisSpec(
            exposure=["control"] * 20 + ["case"] * 20,
            batch=["batch_1"] * 20 + ["batch_2"] * 20,
        )
    )

    assert finding.verdict is Verdict.FAIL


def test_batch_misaligned_vectors_fail() -> None:
    finding = audit_batch(AnalysisSpec(exposure=[0, 1, 0], batch=["a", "b"]))

    assert finding.verdict is Verdict.FAIL


def test_batch_nonfinite_numeric_exposure_fails_cleanly() -> None:
    finding = audit_batch(
        AnalysisSpec(exposure=[0.0, 1.0, float("inf")], batch=["a", "b", "a"])
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["reason"] == "numeric exposure must contain finite values"


def test_batch_balanced_categorical_table_passes() -> None:
    exposure = ["control", "case"] * 40
    batch = (["b1", "b2"] * 20) + (["b2", "b1"] * 20)

    finding = audit_batch(AnalysisSpec(exposure=exposure, batch=batch))

    assert finding.verdict is Verdict.PASS
    assert finding.details["test"] == "fisher_exact"


def test_batch_unconfounded_synthetic_data_passes() -> None:
    data = generate_synthetic_data(seed=19, confounded=False)

    finding = audit_batch(AnalysisSpec(exposure=data.exposure, batch=data.batch))

    assert finding.verdict is Verdict.PASS


def test_batch_confounded_synthetic_data_requires_review() -> None:
    data = generate_synthetic_data(seed=19, confounded=True)

    finding = audit_batch(AnalysisSpec(exposure=data.exposure, batch=data.batch))

    assert finding.verdict is Verdict.REVIEW_REQUIRED
    assert finding.details["pvalue"] < 0.05


def test_batch_sparse_nonsignificant_table_warns() -> None:
    finding = audit_batch(
        AnalysisSpec(exposure=["a", "a", "b"], batch=["x", "y", "y"])
    )

    assert finding.verdict is Verdict.WARN
    assert finding.details["expected_cells_below_5"] == 4


def test_comp_negative_abundance_fails() -> None:
    finding = audit_comp(
        AnalysisSpec(abundance=[[0.8, 0.2], [1.1, -0.1]], data_scale="relative_abundance")
    )

    assert finding.verdict is Verdict.FAIL


def test_comp_nonunit_relative_rows_fail() -> None:
    finding = audit_comp(
        AnalysisSpec(abundance=[[0.4, 0.4], [0.3, 0.7]], data_scale="relative_abundance")
    )

    assert finding.verdict is Verdict.FAIL


def test_comp_zeros_without_policy_fail() -> None:
    finding = audit_comp(
        AnalysisSpec(
            abundance=[[0.0, 0.25, 0.75], [0.2, 0.3, 0.5]],
            data_scale="relative_abundance",
            transform="clr",
        )
    )

    assert finding.verdict is Verdict.FAIL


def test_comp_synthetic_relative_abundance_passes() -> None:
    data = generate_synthetic_data(seed=20)

    finding = audit_comp(
        AnalysisSpec(abundance=data.abundance, data_scale="relative_abundance")
    )

    assert finding.verdict is Verdict.PASS


def test_comp_clr_with_explicit_zero_policy_passes_and_is_reproducible() -> None:
    spec = AnalysisSpec(
        abundance=[[0.0, 0.25, 0.75], [0.2, 0.3, 0.5]],
        data_scale="relative_abundance",
        transform="clr",
        zero_policy={"method": "multiplicative_replacement", "value": 1e-4},
    )

    first = audit_comp(spec)
    second = audit_comp(spec)

    assert first.verdict is Verdict.PASS
    assert first.details["clr_digest_sha256"] == second.details["clr_digest_sha256"]
    assert first.details["clr_max_abs_row_sum"] < 1e-12


def test_comp_ilr_requires_review_for_undeclared_scientific_basis() -> None:
    finding = audit_comp(
        AnalysisSpec(
            abundance=[[0.2, 0.3, 0.5], [0.1, 0.4, 0.5]],
            data_scale="relative_abundance",
            transform="ilr",
        )
    )

    assert finding.verdict is Verdict.REVIEW_REQUIRED
    assert finding.details["recomputed_shape"] == [2, 2]


def test_perm_missing_seed_and_versions_fail() -> None:
    finding = audit_perm(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            statistic_id="diet_association",
            permutation_type="within_blocks",
            exchangeability_blocks=["a", "a", "b", "b"],
            n_permutations=99,
        )
    )

    assert finding.verdict is Verdict.FAIL
    assert finding.details["missing"] == ["seed", "lib_versions"]


def test_perm_unrestricted_shuffle_across_declared_blocks_fails() -> None:
    finding = audit_perm(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            statistic_id="diet_association",
            permutation_type="unrestricted",
            exchangeability_blocks=["a", "a", "b", "b"],
            n_permutations=99,
            seed=42,
            lib_versions=_versions(),
        )
    )

    assert finding.verdict is Verdict.FAIL


def test_perm_within_blocks_passes_and_two_runs_match() -> None:
    data = generate_synthetic_data(n_samples=12, seed=21)
    spec = AnalysisSpec(
        sample_ids=data.sample_ids,
        statistic_id="diet_association",
        permutation_type="within_blocks",
        exchangeability_blocks=data.batch,
        n_permutations=101,
        seed=42,
        lib_versions=_versions(),
    )

    first = audit_perm(spec)
    second = audit_perm(spec)

    assert first.verdict is Verdict.PASS
    assert first.details["exchangeability_blocks_preserved"] is True
    assert first.details["permutation_digest_sha256"] == second.details[
        "permutation_digest_sha256"
    ]


def test_perm_paired_blocks_pass() -> None:
    finding = audit_perm(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            statistic_id="paired_difference",
            permutation_type="paired",
            exchangeability_blocks=["p1", "p1", "p2", "p2"],
            n_permutations=32,
            seed=7,
            lib_versions=_versions(),
        )
    )

    assert finding.verdict is Verdict.PASS


def test_perm_unrestricted_exchangeability_requires_review() -> None:
    finding = audit_perm(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            statistic_id="group_difference",
            permutation_type="unrestricted",
            n_permutations=32,
            seed=7,
            lib_versions=_versions(),
        )
    )

    assert finding.verdict is Verdict.REVIEW_REQUIRED


def test_perm_runtime_version_mismatch_warns() -> None:
    finding = audit_perm(
        AnalysisSpec(
            sample_ids=["s1", "s2", "s3", "s4"],
            statistic_id="paired_difference",
            permutation_type="paired",
            exchangeability_blocks=["p1", "p1", "p2", "p2"],
            n_permutations=32,
            seed=7,
            lib_versions={"numpy": "0.0", "scipy": "0.0"},
        )
    )

    assert finding.verdict is Verdict.WARN
    assert sorted(finding.details["version_mismatches"]) == ["numpy", "scipy"]


def test_run_audit_has_stable_rule_order() -> None:
    findings = run_audit(AnalysisSpec())

    assert [finding.rule_id for finding in findings] == [
        "AUDIT-MULT-001",
        "AUDIT-ID-001",
        "AUDIT-BATCH-001",
        "AUDIT-COMP-001",
        "AUDIT-PERM-001",
    ]
    assert all(finding.verdict is Verdict.NOT_APPLICABLE for finding in findings)
