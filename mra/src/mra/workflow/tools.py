"""Controlled tool surface available to the research agent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mra.audit import AnalysisSpec, AuditFinding
from mra.audit.rules import run_audit
from mra.pep import ArtifactRef, Pep


class ExecutionConstraints(BaseModel):
    """Caller-controlled execution fields accepted by the current PEP."""

    model_config = ConfigDict(extra="forbid")

    network_mode: Literal["deny", "allow"] = "deny"
    credential_refs: list[str] = Field(default_factory=list)


class RunRegisteredTaskParameters(BaseModel):
    """Model-visible arguments for ``run_registered_task``."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    extra_args: list[str] = Field(default_factory=list)
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    timeout_s: float = Field(gt=0)


class AuditResultsParameters(BaseModel):
    """Model-visible arguments for ``audit_results``."""

    model_config = ConfigDict(extra="forbid")

    analysis_spec: AnalysisSpec


class ProposePromotionParameters(BaseModel):
    """Model-visible arguments for ``propose_promotion``."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


RUN_REGISTERED_TASK_SCHEMA: dict[str, Any] = {
    "name": "run_registered_task",
    "description": (
        "Run one policy-registered task through the PEP and stage its "
        "execution artifact."
    ),
    "parameters": RunRegisteredTaskParameters.model_json_schema(),
}
AUDIT_RESULTS_SCHEMA: dict[str, Any] = {
    "name": "audit_results",
    "description": "Run the deterministic statistical audit rules without side effects.",
    "parameters": AuditResultsParameters.model_json_schema(),
}
PROPOSE_PROMOTION_SCHEMA: dict[str, Any] = {
    "name": "propose_promotion",
    "description": ("Create a pending promotion approval bound to an artifact digest."),
    "parameters": ProposePromotionParameters.model_json_schema(),
}
TOOL_SCHEMAS = (
    RUN_REGISTERED_TASK_SCHEMA,
    AUDIT_RESULTS_SCHEMA,
    PROPOSE_PROMOTION_SCHEMA,
)


def run_registered_task(
    pep: Pep,
    task_id: str,
    extra_args: Sequence[str],
    constraints: Mapping[str, Any],
    timeout_s: float,
) -> ArtifactRef:
    """Run a registered task through the sole side-effecting execution gate."""

    return pep.execute_task(task_id, extra_args, constraints, timeout_s)


def audit_results(analysis_spec: AnalysisSpec) -> list[AuditFinding]:
    """Run all deterministic audit rules in their stable order."""

    return run_audit(analysis_spec)


def propose_promotion(pep: Pep, request_id: str, subject_hash: str) -> None:
    """Create a pending approval request for an immutable staged artifact."""

    pep.request_approval(request_id, "promote_result", subject_hash)


__all__ = [
    "AUDIT_RESULTS_SCHEMA",
    "PROPOSE_PROMOTION_SCHEMA",
    "RUN_REGISTERED_TASK_SCHEMA",
    "TOOL_SCHEMAS",
    "AuditResultsParameters",
    "ExecutionConstraints",
    "ProposePromotionParameters",
    "RunRegisteredTaskParameters",
    "audit_results",
    "propose_promotion",
    "run_registered_task",
]
