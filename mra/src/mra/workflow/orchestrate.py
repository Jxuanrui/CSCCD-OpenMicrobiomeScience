"""Deterministic single-agent orchestration inside the PEP permission envelope."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mra.audit import AnalysisSpec, AuditFinding, Verdict
from mra.demo.mock_analysis import encode_analysis_document
from mra.pep import ArtifactRef, Pep
from mra.workflow import tools


class GovernedAnalysisError(RuntimeError):
    """The governed workflow could not produce an approvable artifact."""


@dataclass(frozen=True)
class GovernedAnalysisResult:
    """Staged, audited result awaiting an explicit human approval."""

    staged_ref: ArtifactRef
    findings: list[AuditFinding]
    request_id: str


def create_governed_pep(**pep_options: Any) -> Pep:
    """Construct the workflow PEP with its trusted executable registry."""
    if "task_registry" in pep_options:
        raise ValueError("The governed workflow owns the task registry.")
    return Pep(
        task_registry={
            "simulate_association": (
                sys.executable,
                "-m",
                "mra.demo.mock_analysis",
            ),
        },
        **pep_options,
    )


def _successful_execution(artifact: ArtifactRef) -> bool:
    try:
        summary = json.loads(artifact.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return summary.get("exit_code") == 0


def _analysis_spec(document: Mapping[str, Any]) -> AnalysisSpec:
    """Translate analysis metadata into declarations understood by MVP rules."""

    data_scale = document.get("data_scale")
    if data_scale == "relative":
        data_scale = "relative_abundance"

    # The artifact reports raw Pearson p-values and explicitly makes no
    # adjusted-p-value claim. AUDIT-MULT validates claimed corrections, so its
    # four declaration fields remain unset for this analysis.
    return AnalysisSpec(
        alpha=document.get("alpha", 0.05),
        sample_ids=document.get("sample_ids"),
        exposure=document.get("exposure"),
        batch=document.get("batch"),
        abundance=document.get("abundance"),
        data_scale=data_scale,
    )


def run_governed_analysis(
    *,
    pep: Pep,
    seed: int,
    task_id: str,
    constraints: Mapping[str, Any],
    timeout_s: float,
) -> GovernedAnalysisResult:
    """Execute, verify, audit, and propose one deterministic analysis.

    The task executable comes from the PEP registry. The workflow owns the
    ``--seed`` and ``--out`` arguments so the staged subject is fixed by the
    workflow input and cannot be redirected by model-generated arguments.
    Promotion remains an explicit caller action after ``pep.approve``.
    """

    expected = encode_analysis_document(seed)
    staging_root = Path(pep.staging_root)
    subject_path = staging_root / "workflow-subjects" / f"{uuid.uuid4().hex}.json"
    execution_ref = tools.run_registered_task(
        pep,
        task_id,
        ("--seed", str(seed), "--out", str(subject_path)),
        constraints,
        timeout_s,
    )
    if not _successful_execution(execution_ref):
        raise GovernedAnalysisError(
            "The registered analysis task did not exit successfully."
        )

    try:
        actual = subject_path.read_bytes()
        document = json.loads(actual)
    except (OSError, json.JSONDecodeError):
        raise GovernedAnalysisError(
            "The registered task did not produce valid analysis JSON."
        ) from None
    if actual != expected:
        raise GovernedAnalysisError(
            "The analysis artifact does not match the seed-bound subject."
        )

    staged_ref = ArtifactRef(
        request_id=execution_ref.request_id,
        digest=hashlib.sha256(actual).hexdigest(),
        path=subject_path,
    )
    findings = tools.audit_results(_analysis_spec(document))
    failures = [
        finding.rule_id for finding in findings if finding.verdict is Verdict.FAIL
    ]
    if failures:
        joined = ", ".join(failures)
        raise GovernedAnalysisError(f"Statistical audit failed: {joined}.")

    tools.propose_promotion(pep, staged_ref.request_id, staged_ref.digest)
    return GovernedAnalysisResult(
        staged_ref=staged_ref,
        findings=findings,
        request_id=staged_ref.request_id,
    )


__all__ = [
    "GovernedAnalysisError",
    "GovernedAnalysisResult",
    "create_governed_pep",
    "run_governed_analysis",
]
