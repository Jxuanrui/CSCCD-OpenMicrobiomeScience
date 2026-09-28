"""Fail-closed adapter for the local OPA policy bundle."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mra.pep.types import Decision, OpaError


_ACTIONS = {
    "read_projection",
    "execute_task",
    "promote_result",
    "export_data",
    "manage_policy",
    "approve_request",
}
_PRINCIPAL_TYPES = {"human", "agent"}
_RESOURCE_TYPES = {"projection", "task", "result", "data", "policy", "request"}


def _object(
    value: object,
    *,
    name: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OpaError(f"{name} must be an object.")
    result = dict(value)
    keys = set(result)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise OpaError(f"{name} does not match the OPA input schema.")
    return result


def _string(value: object, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise OpaError(f"{name} must be a string.")
    return value


class OpaClient:
    """Evaluate the local bundle through an OPA subprocess.

    Input is supplied over stdin so no authorization document, credential
    reference, or receipt is written to a temporary file.
    """

    def __init__(
        self,
        binary_path: str | Path = "tools/opa",
        bundle_path: str | Path = "policies/opa/bundle",
        timeout_s: float = 5.0,
    ) -> None:
        self.binary_path = Path(binary_path)
        self.bundle_path = Path(bundle_path)
        if timeout_s <= 0:
            raise ValueError("OPA timeout must be positive.")
        self.timeout_s = timeout_s

    @property
    def policy_revision(self) -> str | None:
        """Read the revision hint used when a pending approval is created."""
        data_path = self.bundle_path / "data.json"
        try:
            document = json.loads(data_path.read_text(encoding="utf-8"))
            revision = document["mra"]["meta"]["revision"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return revision if isinstance(revision, str) and revision else None

    @staticmethod
    def build_input(
        *,
        principal_type: str,
        principal_id: str,
        action: str,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build and validate exactly the input-v1 schema shape."""
        if not isinstance(principal_type, str) or principal_type not in _PRINCIPAL_TYPES:
            raise OpaError("principal.type is not recognized.")
        principal_id = _string(principal_id, "principal.id", nonempty=True)
        if not isinstance(action, str) or action not in _ACTIONS:
            raise OpaError("action is not recognized.")

        resource_doc = _object(
            resource,
            name="resource",
            required={"type"},
            optional={"project_id", "classification", "id"},
        )
        if (
            not isinstance(resource_doc["type"], str)
            or resource_doc["type"] not in _RESOURCE_TYPES
        ):
            raise OpaError("resource.type is not recognized.")
        for key in ("project_id", "classification", "id"):
            if key in resource_doc:
                resource_doc[key] = _string(resource_doc[key], f"resource.{key}")

        context_doc = _object(
            context,
            name="context",
            required=set(),
            optional={"task_id", "request_id", "subject_hash", "execution"},
        )
        for key in ("task_id", "request_id", "subject_hash"):
            if key in context_doc:
                context_doc[key] = _string(context_doc[key], f"context.{key}")
        if "execution" in context_doc:
            execution = _object(
                context_doc["execution"],
                name="context.execution",
                required=set(),
                optional={"network_mode", "credential_refs"},
            )
            if "network_mode" in execution:
                network_mode = execution["network_mode"]
                if not isinstance(network_mode, str) or network_mode not in {
                    "deny",
                    "allow",
                }:
                    raise OpaError(
                        "context.execution.network_mode is not recognized."
                    )
            if "credential_refs" in execution:
                refs = execution["credential_refs"]
                if not isinstance(refs, list) or not all(
                    isinstance(ref, str) for ref in refs
                ):
                    raise OpaError("context.execution.credential_refs must be a string array.")
                execution["credential_refs"] = list(refs)
            context_doc["execution"] = execution

        document: dict[str, Any] = {
            "principal": {"type": principal_type, "id": principal_id},
            "action": action,
            "resource": resource_doc,
            "context": context_doc,
        }
        if receipt is not None:
            receipt_doc = _object(
                receipt,
                name="approval_receipt",
                required={
                    "request_id",
                    "subject_hash",
                    "approver",
                    "status",
                    "policy_revision",
                },
            )
            for key in ("request_id", "subject_hash", "policy_revision"):
                receipt_doc[key] = _string(
                    receipt_doc[key], f"approval_receipt.{key}"
                )
            if (
                not isinstance(receipt_doc["status"], str)
                or receipt_doc["status"] not in {"approved", "rejected", "pending"}
            ):
                raise OpaError("approval_receipt.status is not recognized.")
            approver = _object(
                receipt_doc["approver"],
                name="approval_receipt.approver",
                required={"type"},
                optional={"label"},
            )
            if (
                not isinstance(approver["type"], str)
                or approver["type"] not in _PRINCIPAL_TYPES
            ):
                raise OpaError("approval_receipt.approver.type is not recognized.")
            if "label" in approver:
                approver["label"] = _string(
                    approver["label"], "approval_receipt.approver.label"
                )
            receipt_doc["approver"] = approver
            document["approval_receipt"] = receipt_doc

        try:
            json.dumps(document, allow_nan=False)
        except (TypeError, ValueError):
            raise OpaError("OPA input is not valid JSON.") from None
        return document

    def evaluate(
        self,
        *,
        principal_type: str,
        principal_id: str,
        action: str,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
    ) -> Decision:
        document = self.build_input(
            principal_type=principal_type,
            principal_id=principal_id,
            action=action,
            resource=resource,
            context=context,
            receipt=receipt,
        )
        return self._invoke(document)

    def _invoke(self, document: Mapping[str, Any]) -> Decision:
        command = [
            str(self.binary_path),
            "eval",
            "-d",
            str(self.bundle_path),
            "--format",
            "raw",
            "--stdin-input",
            "data.mra.authz.decision",
        ]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(document, sort_keys=True, separators=(",", ":")),
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise OpaError("OPA authorization failed closed.") from None
        if completed.returncode != 0:
            raise OpaError("OPA authorization failed closed.")
        return self._parse_output(completed.stdout)

    @staticmethod
    def _parse_output(output: str | bytes) -> Decision:
        if isinstance(output, bytes):
            try:
                output = output.decode("utf-8")
            except UnicodeDecodeError:
                raise OpaError("OPA returned an invalid decision.") from None
        try:
            payload = json.loads(output)
        except (TypeError, json.JSONDecodeError):
            raise OpaError("OPA returned an invalid decision.") from None

        # Raw output is the decision object. Accept the two standard JSON
        # envelopes as well so a runner can switch formats without weakening
        # validation of the decision itself.
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
            if isinstance(payload, list):
                try:
                    payload = payload[0]["expressions"][0]["value"]
                except (IndexError, KeyError, TypeError):
                    raise OpaError("OPA returned an invalid decision.") from None
        decision = _object(
            payload,
            name="OPA decision",
            required={
                "allow",
                "approval_required",
                "reason_codes",
                "constraints",
                "policy_revision",
            },
        )
        if not isinstance(decision["allow"], bool) or not isinstance(
            decision["approval_required"], bool
        ):
            raise OpaError("OPA returned an invalid decision.")
        if decision["allow"] and decision["approval_required"]:
            raise OpaError("OPA returned an inconsistent decision.")
        reason_codes = decision["reason_codes"]
        if not isinstance(reason_codes, list) or not reason_codes or not all(
            isinstance(reason, str) and reason for reason in reason_codes
        ):
            raise OpaError("OPA returned an invalid decision.")
        constraints = decision["constraints"]
        if not isinstance(constraints, dict):
            raise OpaError("OPA returned an invalid decision.")
        revision = decision["policy_revision"]
        if not isinstance(revision, str) or not revision:
            raise OpaError("OPA returned an invalid decision.")
        try:
            json.dumps(constraints, allow_nan=False)
        except (TypeError, ValueError):
            raise OpaError("OPA returned an invalid decision.") from None
        return Decision(
            decision_id=uuid.uuid4().hex,
            allow=decision["allow"],
            approval_required=decision["approval_required"],
            reason_codes=tuple(reason_codes),
            constraints=dict(constraints),
            policy_revision=revision,
        )
