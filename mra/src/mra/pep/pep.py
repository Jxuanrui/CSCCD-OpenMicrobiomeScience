"""Trusted Python policy enforcement point for governed side effects."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mra.pep.ledger import AuditLedger
from mra.pep.opa import OpaClient
from mra.pep.types import (
    ApprovalReceipt,
    ArtifactRef,
    AuditEvent,
    CommandResult,
    Decision,
    PepError,
)


RunCommand = Callable[[str, Sequence[str], Mapping[str, Any], float], object]

_ACTION_RESOURCE_TYPES = {
    "read_projection": "projection",
    "execute_task": "task",
    "promote_result": "result",
    "export_data": "data",
    "manage_policy": "policy",
    "approve_request": "request",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest_json(document: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise PepError("Authorization input is not valid JSON.") from None
    return hashlib.sha256(encoded).hexdigest()


class Pep:
    """The minimum trusted enforcement surface for the MVP.

    ``execute_task`` carries ``constraints.network_mode`` into the OPA input
    and only runs decisions allowed with ``network_mode=deny``. The MVP runner
    is a direct subprocess for hermetic simulation commands; OS-level network
    isolation belongs to the later systemd/Apptainer integration layer.

    Principal IDs and types are selected by these trusted methods. Resource,
    context, command arguments, and model output can never add or replace the
    principal object sent to OPA.
    """

    def __init__(
        self,
        *,
        opa_client: OpaClient | Any | None = None,
        ledger: AuditLedger | None = None,
        ledger_path: str | Path = "var/audit/audit.db",
        staging_root: str | Path = "var/staging",
        results_root: str | Path = "var/results",
        run_command: RunCommand | None = None,
        task_registry: Mapping[str, Sequence[str]] | None = None,
        agent_principal_id: str = "mra-agent",
        human_principal_id: str = "local-human",
    ) -> None:
        if not agent_principal_id or not human_principal_id:
            raise ValueError("Principal IDs must be non-empty.")
        self.opa = opa_client if opa_client is not None else OpaClient()
        self.ledger = ledger if ledger is not None else AuditLedger(ledger_path)
        self.staging_root = Path(staging_root)
        self.results_root = Path(results_root)
        self._run_command = run_command or self._subprocess_runner
        registry = {} if task_registry is None else dict(task_registry)
        normalized_registry: dict[str, tuple[str, ...]] = {}
        for task_id, command_argv in registry.items():
            if not isinstance(task_id, str) or not task_id:
                raise ValueError("Task registry IDs must be non-empty strings.")
            if not isinstance(command_argv, Sequence) or isinstance(
                command_argv, (str, bytes)
            ):
                raise ValueError("Task registry commands must be string sequences.")
            try:
                argv = tuple(command_argv)
            except TypeError:
                raise ValueError(
                    "Task registry commands must be string sequences."
                ) from None
            if not argv or not all(isinstance(part, str) and part for part in argv):
                raise ValueError(
                    "Task registry commands must be non-empty string sequences."
                )
            normalized_registry[task_id] = argv
        self._task_registry = normalized_registry
        self._principal_ids = {
            "agent": agent_principal_id,
            "human": human_principal_id,
        }
        revision = getattr(self.opa, "policy_revision", None)
        self._policy_revision = revision if isinstance(revision, str) else None

    @staticmethod
    def _subprocess_runner(
        command: str,
        args: Sequence[str],
        constraints: Mapping[str, Any],
        timeout_s: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run a registered simulation command without a shell.

        The environment is reduced to non-secret process settings. The
        ``constraints`` argument is intentionally present for a future runner
        that enforces memory and CPU limits; this MVP only enforces timeout.
        """
        del constraints
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        }
        return subprocess.run(
            [command, *args],
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            shell=False,
            env=environment,
        )

    def _principal_id(self, principal_type: str) -> str:
        try:
            return self._principal_ids[principal_type]
        except KeyError:
            raise PepError("Principal type is not recognized.") from None

    def _trusted_receipt(
        self,
        action: str,
        context: Mapping[str, Any],
        supplied: Mapping[str, Any] | ApprovalReceipt | None,
    ) -> dict[str, Any] | None:
        request_id = context.get("request_id")
        if not isinstance(request_id, str):
            return None
        approval = self.ledger.get_approval_request(request_id)
        if (
            approval is None
            or approval["status"] != "approved"
            or approval["action"] != action
        ):
            return None
        stored = self.ledger.get_receipt(request_id)
        if stored is None:
            return None
        trusted = stored.as_opa_input()
        if supplied is None:
            return trusted
        if isinstance(supplied, ApprovalReceipt):
            candidate = supplied.as_opa_input()
        elif isinstance(supplied, Mapping):
            candidate = dict(supplied)
        else:
            raise PepError("Approval receipt is invalid.")
        return trusted if candidate == trusted else None

    def _input_and_decision(
        self,
        *,
        principal_type: str,
        action: str,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
        receipt: Mapping[str, Any] | ApprovalReceipt | None,
    ) -> tuple[dict[str, Any], Decision]:
        principal_id = self._principal_id(principal_type)
        trusted_receipt = self._trusted_receipt(action, context, receipt)
        document = OpaClient.build_input(
            principal_type=principal_type,
            principal_id=principal_id,
            action=action,
            resource=resource,
            context=context,
            receipt=trusted_receipt,
        )
        decision = self.opa.evaluate(
            principal_type=principal_type,
            principal_id=principal_id,
            action=action,
            resource=resource,
            context=context,
            receipt=trusted_receipt,
        )
        if not isinstance(decision, Decision):
            raise PepError("OPA client returned an invalid decision.")
        return document, decision

    def _event(
        self,
        *,
        decision: Decision,
        principal_type: str,
        action: str,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
        input_digest: str,
        output_digest: str | None = None,
    ) -> AuditEvent:
        if decision.allow:
            outcome = "allow"
        elif decision.approval_required:
            outcome = "approval_required"
        else:
            outcome = "deny"
        resource_id = resource.get("id")
        request_id = context.get("request_id")
        subject_hash = context.get("subject_hash")
        return AuditEvent(
            id=decision.decision_id,
            ts=_now(),
            principal_type=principal_type,
            principal_id=self._principal_id(principal_type),
            action=action,
            resource_kind=str(resource.get("type", "unknown")),
            resource_id=resource_id if isinstance(resource_id, str) else None,
            request_id=request_id if isinstance(request_id, str) else None,
            subject_hash=subject_hash if isinstance(subject_hash, str) else None,
            decision=outcome,
            approval_required=decision.approval_required,
            reason_codes=decision.reason_codes,
            constraints=decision.constraints,
            policy_revision=decision.policy_revision,
            input_digest=input_digest,
            output_digest=output_digest,
        )

    def evaluate(
        self,
        principal_type: str,
        action: str,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
        receipt: Mapping[str, Any] | ApprovalReceipt | None = None,
    ) -> Decision:
        """Evaluate and durably audit an authorization decision.

        This is a trusted internal primitive for tests and read-only queries;
        it is not exposed to agents as a side-effect entry point.
        """
        document, decision = self._input_and_decision(
            principal_type=principal_type,
            action=action,
            resource=resource,
            context=context,
            receipt=receipt,
        )
        self.ledger.record_event(
            self._event(
                decision=decision,
                principal_type=principal_type,
                action=action,
                resource=resource,
                context=context,
                input_digest=_digest_json(document),
            )
        )
        self._policy_revision = decision.policy_revision
        return decision

    def request_approval(self, request_id: str, action: str, subject_hash: str) -> None:
        """Record an agent-proposed request bound to an action, hash, and policy.

        The proposing principal is fixed internally to ``agent``; callers
        cannot supply or replace an identity. Agents can propose promotion
        here but cannot invoke :meth:`promote_result` directly.
        """
        if not request_id or not action or not subject_hash:
            raise PepError("Approval request fields must be non-empty.")
        resource_type = _ACTION_RESOURCE_TYPES.get(action)
        if resource_type is None:
            raise PepError("Approval action is not recognized.")
        decision = self.evaluate(
            "agent",
            action,
            {"type": resource_type, "id": request_id},
            {"request_id": request_id, "subject_hash": subject_hash},
        )
        if decision.allow or not decision.approval_required:
            raise PepError("Approval request was denied by policy.")
        self.ledger.create_approval_request(
            request_id=request_id,
            action=action,
            subject_hash=subject_hash,
            policy_revision=decision.policy_revision,
        )

    def approve(self, request_id: str, label: str) -> None:
        """Authorize one pending request as the fixed local human principal."""
        if not request_id or not isinstance(label, str) or not label:
            raise PepError("Approval ID and label must be non-empty.")
        pending = self.ledger.get_approval_request(request_id)
        if pending is None or pending["status"] != "pending":
            raise PepError("Approval request is not pending.")
        resource = {"type": "request", "id": request_id}
        context = {
            "request_id": request_id,
            "subject_hash": pending["subject_hash"],
        }
        document, decision = self._input_and_decision(
            principal_type="human",
            action="approve_request",
            resource=resource,
            context=context,
            receipt=None,
        )
        if not decision.allow or decision.approval_required:
            raise PepError("Approval was denied by policy.")
        event = self._event(
            decision=decision,
            principal_type="human",
            action="approve_request",
            resource=resource,
            context=context,
            input_digest=_digest_json(document),
        )
        self.ledger.approve_request(
            request_id=request_id,
            approver_id=self._principal_id("human"),
            label=label,
            approved_at=event.ts,
            policy_revision=decision.policy_revision,
            event=event,
        )
        self._policy_revision = decision.policy_revision

    def query_audit(self, **filters: object) -> list[dict[str, Any]]:
        """Read normalized audit rows using an allowlist of columns."""
        return self.ledger.query_audit(**filters)

    @staticmethod
    def _command_result(result: object) -> CommandResult:
        if isinstance(result, CommandResult):
            return result
        if isinstance(result, Mapping):
            stdout = result.get("stdout", "")
            exit_code = result.get("exit_code", result.get("returncode"))
        else:
            stdout = getattr(result, "stdout", "")
            exit_code = getattr(result, "returncode", None)
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if stdout is None:
            stdout = ""
        if not isinstance(stdout, str) or not isinstance(exit_code, int):
            raise PepError("Command runner returned an invalid result.")
        return CommandResult(stdout=stdout, exit_code=exit_code)

    def execute_task(
        self,
        task_id: str,
        extra_args: Sequence[str] = (),
        constraints: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ArtifactRef:
        """Authorize, run, and stage one registered simulation task."""
        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or timeout_s <= 0
        ):
            raise PepError("Task execution arguments are invalid.")
        if not isinstance(extra_args, Sequence) or isinstance(extra_args, (str, bytes)):
            raise PepError("Task arguments must be a string sequence.")
        try:
            command_args = tuple(extra_args)
        except TypeError:
            raise PepError("Task arguments must be a string sequence.") from None
        if not all(isinstance(argument, str) for argument in command_args):
            raise PepError("Task arguments must be a string sequence.")
        if not isinstance(constraints, Mapping):
            raise PepError("Task constraints must be an object.")
        allowed_constraint_keys = {"network_mode", "credential_refs"}
        if set(constraints) - allowed_constraint_keys:
            raise PepError("Task constraints do not match the execution schema.")
        credential_refs = constraints.get("credential_refs", [])
        if not isinstance(credential_refs, list):
            raise PepError("Task credential references must be a string array.")
        execution = {
            "network_mode": constraints.get("network_mode", "deny"),
            "credential_refs": list(credential_refs),
        }
        request_id = uuid.uuid4().hex
        context = {
            "task_id": task_id,
            "request_id": request_id,
            "execution": execution,
        }
        if task_id not in self._task_registry:
            self.evaluate(
                "agent",
                "execute_task",
                {"type": "task", "id": task_id},
                context,
            )
            raise PepError("Task is not registered.")
        decision = self.evaluate(
            "agent",
            "execute_task",
            {"type": "task", "id": task_id},
            context,
        )
        if not decision.allow or decision.approval_required:
            raise PepError("Task execution was denied by policy.")
        policy_timeout = decision.constraints.get("timeout_s", timeout_s)
        if (
            not isinstance(policy_timeout, (int, float))
            or isinstance(policy_timeout, bool)
            or policy_timeout <= 0
        ):
            raise PepError("OPA returned an invalid task timeout constraint.")
        effective_timeout = min(float(timeout_s), float(policy_timeout))
        effective_constraints = dict(decision.constraints)
        effective_constraints.update(execution)
        command_argv = self._task_registry[task_id]
        command = command_argv[0]
        fixed_args = (*command_argv[1:], *command_args)
        try:
            raw_result = self._run_command(
                command, fixed_args, effective_constraints, effective_timeout
            )
        except Exception:
            raise PepError("Task execution failed.") from None
        result = self._command_result(raw_result)
        payload = {
            "stdout_summary": result.stdout[:4096],
            "exit_code": result.exit_code,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.ledger.set_output_digest(decision.decision_id, digest)
        output_directory = self.staging_root / request_id
        output_path = output_directory / "result.json"
        try:
            output_directory.mkdir(parents=True, exist_ok=False)
            with output_path.open("xb") as output:
                output.write(encoded)
        except OSError:
            raise PepError("Task artifact could not be staged.") from None
        return ArtifactRef(request_id=request_id, digest=digest, path=output_path)

    @staticmethod
    def _safe_request_component(request_id: str) -> str:
        if (
            not request_id
            or request_id in {".", ".."}
            or Path(request_id).name != request_id
        ):
            raise PepError("Request ID is not a safe path component.")
        return request_id

    def _record_enforcement_deny(
        self,
        *,
        principal_type: str,
        request_id: str,
        subject_hash: str,
        resource: Mapping[str, Any],
        reason: str,
        policy_revision: str,
    ) -> None:
        decision = Decision(
            decision_id=uuid.uuid4().hex,
            allow=False,
            approval_required=False,
            reason_codes=(reason,),
            constraints={},
            policy_revision=policy_revision,
        )
        context = {"request_id": request_id, "subject_hash": subject_hash}
        document = OpaClient.build_input(
            principal_type=principal_type,
            principal_id=self._principal_id(principal_type),
            action="promote_result",
            resource=resource,
            context=context,
        )
        self.ledger.record_event(
            self._event(
                decision=decision,
                principal_type=principal_type,
                action="promote_result",
                resource=resource,
                context=context,
                input_digest=_digest_json(document),
            )
        )

    def promote_result(
        self,
        request_id: str,
        subject_hash: str,
        staged_path: str | Path,
    ) -> ArtifactRef:
        """Authorize and copy one approved staged file into formal results.

        This entry point is fixed to the local human principal; an agent may
        only call :meth:`request_approval` to propose promotion. The
        deterministic scientific audit is an orchestration prerequisite.
        """
        request_id = self._safe_request_component(request_id)
        resource = {"type": "result", "id": request_id}
        context = {"request_id": request_id, "subject_hash": subject_hash}
        approval = self.ledger.get_approval_request(request_id)
        candidate_revision = getattr(self.opa, "policy_revision", None)
        expected_revision = (
            candidate_revision
            if isinstance(candidate_revision, str) and candidate_revision
            else self._policy_revision
        )
        if (
            approval is None
            or approval["status"] != "approved"
            or approval["action"] != "promote_result"
            or approval["subject_hash"] != subject_hash
            or not expected_revision
            or approval["policy_revision"] != expected_revision
        ):
            if not expected_revision:
                raise PepError("Policy revision is unavailable for promotion.")
            self._record_enforcement_deny(
                principal_type="human",
                request_id=request_id,
                subject_hash=subject_hash,
                resource=resource,
                reason="DENY_APPROVAL_INVALID",
                policy_revision=expected_revision,
            )
            raise PepError("Result promotion was denied by policy.")
        decision = self.evaluate(
            "human",
            "promote_result",
            resource,
            context,
        )
        if not decision.allow or decision.approval_required:
            raise PepError("Result promotion was denied by policy.")

        try:
            source = Path(staged_path).resolve(strict=True)
            source.relative_to(self.staging_root.resolve())
        except (OSError, ValueError):
            self._record_enforcement_deny(
                principal_type="human",
                request_id=request_id,
                subject_hash=subject_hash,
                resource=resource,
                reason="DENY_STAGED_PATH_OUT_OF_SCOPE",
                policy_revision=decision.policy_revision,
            )
            raise PepError("Staged artifact is outside the staging boundary.") from None
        destination_directory = self.results_root / request_id
        destination = destination_directory / source.name
        if destination_directory.exists():
            raise PepError("A result already exists for this request.")
        try:
            with source.open("rb") as input_file:
                if not stat.S_ISREG(os.fstat(input_file.fileno()).st_mode):
                    raise PepError("Staged artifact must be a regular file.")
                digest = hashlib.sha256()
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    digest.update(block)
                actual_digest = digest.hexdigest()
                if actual_digest != subject_hash:
                    self._record_enforcement_deny(
                        principal_type="human",
                        request_id=request_id,
                        subject_hash=subject_hash,
                        resource=resource,
                        reason="DENY_SUBJECT_HASH_MISMATCH",
                        policy_revision=decision.policy_revision,
                    )
                    raise PepError(
                        "Staged artifact hash does not match the approved subject."
                    )
                self.ledger.set_output_digest(decision.decision_id, actual_digest)
                input_file.seek(0)
                destination_directory.mkdir(parents=True, exist_ok=False)
                with destination.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
        except PepError:
            raise
        except OSError:
            raise PepError("Result artifact could not be promoted.") from None
        return ArtifactRef(
            request_id=request_id,
            digest=actual_digest,
            path=destination,
        )
