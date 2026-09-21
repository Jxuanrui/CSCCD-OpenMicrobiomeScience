"""Types shared by the policy enforcement point."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PepError(RuntimeError):
    """A fail-closed PEP error with a credential-free message."""


class OpaError(PepError):
    """OPA could not produce a valid authorization decision."""


class LedgerError(PepError):
    """The audit ledger could not complete an atomic operation."""


@dataclass(frozen=True)
class Decision:
    """Normalized result returned by the OPA policy decision point."""

    decision_id: str
    allow: bool
    approval_required: bool
    reason_codes: tuple[str, ...]
    constraints: dict[str, Any]
    policy_revision: str


@dataclass(frozen=True)
class ArtifactRef:
    """A content-addressed artifact created or promoted by the PEP."""

    request_id: str
    digest: str
    path: Path


@dataclass(frozen=True)
class AuditEvent:
    """One append-only authorization or enforcement event."""

    id: str
    ts: str
    principal_type: str
    principal_id: str
    action: str
    resource_kind: str
    resource_id: str | None
    request_id: str | None
    subject_hash: str | None
    decision: str
    approval_required: bool
    reason_codes: tuple[str, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)
    policy_revision: str = ""
    input_digest: str = ""
    output_digest: str | None = None


@dataclass(frozen=True)
class ApprovalReceipt:
    """Trusted receipt reconstructed from the SQLite approval table."""

    request_id: str
    subject_hash: str
    status: str
    policy_revision: str
    approver_type: str = "human"
    label: str | None = None

    def as_opa_input(self) -> dict[str, Any]:
        approver: dict[str, str] = {"type": self.approver_type}
        if self.label is not None:
            approver["label"] = self.label
        return {
            "request_id": self.request_id,
            "subject_hash": self.subject_hash,
            "approver": approver,
            "status": self.status,
            "policy_revision": self.policy_revision,
        }


@dataclass(frozen=True)
class CommandResult:
    """Minimal normalized result from an injected command runner."""

    stdout: str
    exit_code: int
