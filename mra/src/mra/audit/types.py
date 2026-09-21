"""Structured inputs and outputs for deterministic statistical audits."""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Verdict(str, Enum):
    """The possible outcomes of one deterministic audit rule."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuditFinding(BaseModel):
    """One rule result with machine-readable evidence."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    verdict: Verdict
    details: dict[str, Any] = Field(default_factory=dict)


class AnalysisSpec(BaseModel):
    """Inputs declared by an analysis for the MVP statistical audit rules.

    Scientific constraints are intentionally checked by the rules instead of
    Pydantic validators so that invalid analysis inputs produce ``FAIL``
    findings rather than preventing an audit from running.
    """

    model_config = ConfigDict(extra="forbid")

    # Multiple testing
    pvalues: list[float] | None = None
    family_id: str | list[str] | None = None
    method: str | None = None
    alpha: float = 0.05
    claimed_adjusted: list[float] | None = None

    # Identity, independence, and data partitions
    sample_ids: list[str] | None = None
    independence_unit: list[str] | None = None
    partitions: dict[str, list[str]] | None = None

    # Exposure and batch structure
    exposure: list[Any] | None = None
    batch: list[Any] | None = None

    # Compositional data declaration
    abundance: list[list[float]] | None = None
    data_scale: str | None = None
    transform: str | None = None
    zero_policy: str | dict[str, Any] | None = None

    # Permutation declaration
    statistic_id: str | None = None
    permutation_type: str | None = None
    exchangeability_blocks: list[str] | None = None
    n_permutations: int | None = None
    seed: int | None = None
    lib_versions: dict[str, str] | None = None

    @field_validator(
        "pvalues",
        "claimed_adjusted",
        "sample_ids",
        "independence_unit",
        "exposure",
        "batch",
        "abundance",
        "exchangeability_blocks",
        mode="before",
    )
    @classmethod
    def _arrays_to_lists(cls, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        return value

    @field_validator("family_id", mode="before")
    @classmethod
    def _family_array_to_list(cls, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        return value

    @field_validator("partitions", mode="before")
    @classmethod
    def _partition_arrays_to_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            key: members.tolist() if isinstance(members, np.ndarray) else members
            for key, members in value.items()
        }


__all__ = ["AnalysisSpec", "AuditFinding", "Verdict"]
