"""Generate a small microbiome--diet study with an explicit truth structure.

The default fixture contains 200 samples, 40 taxa, a continuous nutrient
exposure, and three balanced batches.  Five taxa have a real exposure effect;
the remaining taxa are generated independently of exposure.  The effect
coefficients sum to zero, so the compositional closure does not create a
direct exposure signal in the noise taxa.  Setting ``confounded=True`` adds a
batch-specific shift to exposure, providing a controlled batch--exposure
confound for audit demonstrations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticMicrobiomeData:
    """A reproducible synthetic microbiome--diet study.

    Attributes:
        abundance: Non-negative relative-abundance matrix with shape
            ``(n_samples, n_taxa)``.  Columns correspond to ``taxa`` and each
            row sums to one.
        sample_ids: String identifiers with one entry per sample.
        exposure: Continuous nutrient-intake-like exposure, one value per
            sample.  Its mean is approximately 50 in the default fixture.
        batch: Categorical batch labels (``batch_1`` through ``batch_3``).
        taxa: Column names for ``abundance``.
        true_associated_taxa: Taxa with a deliberately non-zero exposure
            effect.  This is ground truth for downstream audit checks; it is
            not inferred from the generated matrix.
        confounded: Whether the batch-specific exposure shift was enabled.
    """

    abundance: np.ndarray
    sample_ids: np.ndarray
    exposure: np.ndarray
    batch: np.ndarray
    taxa: tuple[str, ...]
    true_associated_taxa: tuple[str, ...]
    confounded: bool


# A shorter name is useful in small demo scripts while keeping the descriptive
# class name available to callers that want to be explicit.
SyntheticStudy = SyntheticMicrobiomeData


def _balanced_batches(
    n_samples: int, batch_count: int, rng: np.random.Generator
) -> np.ndarray:
    """Return shuffled, as-balanced-as-possible categorical batch labels."""

    indices = np.tile(
        np.arange(batch_count), (n_samples + batch_count - 1) // batch_count
    )
    indices = indices[:n_samples]
    rng.shuffle(indices)
    return np.asarray([f"batch_{index + 1}" for index in indices], dtype="U16")


def _standardized_within_batch(
    n_samples: int, batch_indices: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Generate a continuous covariate with exactly matched batch means."""

    values = rng.normal(size=n_samples)
    for index in np.unique(batch_indices):
        members = batch_indices == index
        values[members] -= values[members].mean()
    standard_deviation = values.std(ddof=0)
    if standard_deviation == 0:  # Defensive for very small custom fixtures.
        return np.zeros(n_samples)
    return values / standard_deviation


def generate_synthetic_data(
    n_samples: int = 200,
    n_taxa: int = 40,
    n_associated: int = 5,
    seed: int = 20260911,
    effect_size: float = 1.0,
    confounded: bool = False,
    *,
    batch_confounding: bool | None = None,
) -> SyntheticMicrobiomeData:
    """Generate a deterministic microbiome--diet association fixture.

    Args:
        n_samples: Number of samples.  The default is approximately the MVP
            fixture size requested by the project plan.
        n_taxa: Number of taxa (abundance columns).
        n_associated: Number of taxa with a true exposure effect.  The first
            ``n_associated`` names in ``true_associated_taxa`` identify them.
        seed: Seed for a local ``numpy`` generator.  The same arguments and
            seed produce byte-for-byte identical arrays.
        effect_size: Non-negative multiplier controlling the exposure effect.
            Zero removes the injected signal; one gives a strong, easy-to-see
            signal for the default fixture.
        confounded: If true, add a fixed exposure offset by batch.  Batch
            means are exactly matched when false.
        batch_confounding: Alias for ``confounded`` for callers that prefer to
            name the source of the confound explicitly.  When supplied, it
            takes precedence over ``confounded``.

    Returns:
        A :class:`SyntheticMicrobiomeData` object containing the matrix,
        covariates, column labels, and the known associated taxa.

    Raises:
        ValueError: If dimensions or ``effect_size`` are invalid.
    """

    if n_samples < 6:
        raise ValueError("n_samples must be at least 6")
    if n_taxa < 2:
        raise ValueError("n_taxa must be at least 2")
    if not 2 <= n_associated < n_taxa:
        raise ValueError("n_associated must be between 2 and n_taxa - 1")
    if n_associated * 0.06 >= 1.0:
        raise ValueError("n_associated must leave positive mass for noise taxa")
    if not np.isfinite(effect_size) or effect_size < 0:
        raise ValueError("effect_size must be finite and non-negative")
    if batch_confounding is not None:
        confounded = batch_confounding

    rng = np.random.default_rng(seed)
    batch_count = 3
    batch_labels = _balanced_batches(n_samples, batch_count, rng)
    batch_indices = np.array(
        [int(label.rsplit("_", 1)[1]) - 1 for label in batch_labels], dtype=int
    )

    # Centering within each batch makes the no-confound condition exact rather
    # than relying on a chance non-significant difference in random means.
    latent_exposure = _standardized_within_batch(n_samples, batch_indices, rng)
    if confounded:
        batch_offsets = np.array((-12.0, 0.0, 12.0))
        exposure = 50.0 + 6.0 * latent_exposure + batch_offsets[batch_indices]
    else:
        exposure = 50.0 + 6.0 * latent_exposure

    exposure_standard_deviation = exposure.std(ddof=0)
    if exposure_standard_deviation == 0:
        exposure_driver = np.zeros(n_samples)
    else:
        exposure_driver = (exposure - exposure.mean()) / exposure_standard_deviation
    exposure_driver = np.tanh(effect_size * exposure_driver)

    # The coefficients sum to zero.  Four taxa increase with exposure and the
    # final associated taxon decreases; this keeps the total pre-normalization
    # mass independent of exposure while retaining five known associations.
    signal_baseline = 0.06
    positive_coefficient = min(
        0.012, 0.75 * signal_baseline / (n_associated - 1)
    )
    signal_coefficients = np.full(n_associated, positive_coefficient)
    signal_coefficients[-1] = -positive_coefficient * (n_associated - 1)
    signal_baselines = np.full(n_associated, signal_baseline)
    signal_noise = rng.normal(0.0, 0.004, size=(n_samples, n_associated))
    signal = (
        signal_baselines
        + exposure_driver[:, np.newaxis]
        * (effect_size > 0)
        * signal_coefficients
        + signal_noise
    )
    signal = np.maximum(signal, np.finfo(float).eps)

    noise_count = n_taxa - n_associated
    noise_mass = 1.0 - float(signal_baselines.sum())
    noise_baseline = noise_mass / noise_count
    noise = noise_baseline * rng.lognormal(
        mean=0.0, sigma=0.35, size=(n_samples, noise_count)
    )

    raw_abundance = np.concatenate((signal, noise), axis=1)
    abundance = raw_abundance / raw_abundance.sum(axis=1, keepdims=True)
    abundance = np.asarray(abundance, dtype=float)

    taxa = tuple(f"taxon_{index + 1:03d}" for index in range(n_taxa))
    true_associated_taxa = taxa[:n_associated]
    sample_ids = np.asarray(
        [f"sample_{index + 1:03d}" for index in range(n_samples)], dtype="U32"
    )

    return SyntheticMicrobiomeData(
        abundance=abundance,
        sample_ids=sample_ids,
        exposure=np.asarray(exposure, dtype=float),
        batch=batch_labels,
        taxa=taxa,
        true_associated_taxa=true_associated_taxa,
        confounded=bool(confounded),
    )


def generate_synthetic_microbiome_data(
    *args: object, **kwargs: object
) -> SyntheticMicrobiomeData:
    """Descriptive alias for :func:`generate_synthetic_data`."""

    return generate_synthetic_data(*args, **kwargs)


__all__ = [
    "SyntheticMicrobiomeData",
    "SyntheticStudy",
    "generate_synthetic_data",
    "generate_synthetic_microbiome_data",
]
