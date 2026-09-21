from __future__ import annotations

import numpy as np

from mra.demo.synthetic import generate_synthetic_data


def test_same_seed_is_fully_reproducible() -> None:
    first = generate_synthetic_data(seed=1234)
    second = generate_synthetic_data(seed=1234)

    assert np.array_equal(first.abundance, second.abundance)
    assert np.array_equal(first.sample_ids, second.sample_ids)
    assert np.array_equal(first.exposure, second.exposure)
    assert np.array_equal(first.batch, second.batch)
    assert first.taxa == second.taxa
    assert first.true_associated_taxa == second.true_associated_taxa


def test_abundance_is_nonnegative_and_compositional() -> None:
    data = generate_synthetic_data()

    assert data.abundance.shape == (200, 40)
    assert np.all(data.abundance >= 0)
    np.testing.assert_allclose(data.abundance.sum(axis=1), 1.0, rtol=0, atol=1e-12)


def test_true_associations_are_stronger_than_noise() -> None:
    data = generate_synthetic_data(seed=77)
    exposure_correlations = np.array(
        [
            np.corrcoef(data.exposure, data.abundance[:, index])[0, 1]
            for index in range(40)
        ]
    )
    associated_count = len(data.true_associated_taxa)
    associated = np.abs(exposure_correlations[:associated_count])
    noise = np.abs(exposure_correlations[associated_count:])

    assert np.all(associated > 0.5)
    assert associated.mean() > noise.mean() + 0.4


def test_batch_confounding_switch_controls_exposure_batch_association() -> None:
    unconfounded = generate_synthetic_data(seed=9, confounded=False)
    confounded = generate_synthetic_data(seed=9, confounded=True)

    unconfounded_means = np.array(
        [
            unconfounded.exposure[unconfounded.batch == label].mean()
            for label in sorted(set(unconfounded.batch))
        ]
    )
    confounded_means = np.array(
        [
            confounded.exposure[confounded.batch == label].mean()
            for label in sorted(set(confounded.batch))
        ]
    )

    assert np.ptp(unconfounded_means) < 1e-10
    assert np.ptp(confounded_means) > 15.0
