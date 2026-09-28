"""SQLite audit and approval ledger for the PEP."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mra.pep.types import ApprovalReceipt, AuditEvent, LedgerError


_AUDIT_COLUMNS = {
    "id",
    "ts",
    "principal_type",
    "principal_id",
    "action",
    "resource_kind",
    "resource_id",
    "request_id",
    "subject_hash",
    "decision",
    "approval_required",
    "reason_codes",
    "constraints",
    "policy_revision",
    "input_digest",
    "output_digest",
}


def _json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise LedgerError("Audit value is not valid JSON.") from None


class AuditLedger:
    """Single-file WAL ledger with atomic approval transitions."""

    def __init__(self, path: str | Path = "var/audit/audit.db") -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(self.path) != ":memory:":
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise LedgerError("Audit ledger directory is unavailable.") from None
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                str(self.path), timeout=5.0, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    principal_type TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT,
                    request_id TEXT,
                    subject_hash TEXT,
                    decision TEXT NOT NULL,
                    approval_required INTEGER NOT NULL CHECK (approval_required IN (0, 1)),
                    reason_codes TEXT NOT NULL,
                    constraints TEXT NOT NULL,
                    policy_revision TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    output_digest TEXT
                );

                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    subject_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
                    approver_id TEXT,
                    label TEXT,
                    approved_at TEXT,
                    policy_revision TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_request_id
                    ON audit_events(request_id);
                CREATE INDEX IF NOT EXISTS idx_audit_action
                    ON audit_events(action);
                """
            )
            self._connection.commit()
        except sqlite3.Error:
            raise LedgerError("Audit ledger initialization failed.") from None

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                raise LedgerError("Audit ledger close failed.") from None

    @staticmethod
    def _event_values(event: AuditEvent) -> tuple[object, ...]:
        return (
            event.id,
            event.ts,
            event.principal_type,
            event.principal_id,
            event.action,
            event.resource_kind,
            event.resource_id,
            event.request_id,
            event.subject_hash,
            event.decision,
            int(event.approval_required),
            _json(event.reason_codes),
            _json(event.constraints),
            event.policy_revision,
            event.input_digest,
            event.output_digest,
        )

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: AuditEvent) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                id, ts, principal_type, principal_id, action, resource_kind,
                resource_id, request_id, subject_hash, decision,
                approval_required, reason_codes, constraints, policy_revision,
                input_digest, output_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            AuditLedger._event_values(event),
        )

    def record_event(self, event: AuditEvent) -> None:
        with self._lock:
            try:
                with self._connection:
                    self._insert_event(self._connection, event)
            except sqlite3.Error:
                raise LedgerError("Audit event could not be recorded.") from None

    def set_output_digest(self, event_id: str, output_digest: str) -> None:
        """Attach a known digest before the corresponding file write."""
        with self._lock:
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        "UPDATE audit_events SET output_digest = ? WHERE id = ?",
                        (output_digest, event_id),
                    )
                    if cursor.rowcount != 1:
                        raise LedgerError("Audit event is missing.")
            except LedgerError:
                raise
            except sqlite3.Error:
                raise LedgerError("Audit output digest could not be recorded.") from None

    def create_approval_request(
        self,
        *,
        request_id: str,
        action: str,
        subject_hash: str,
        policy_revision: str,
    ) -> None:
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO approval_requests (
                            id, request_id, action, subject_hash, status,
                            approver_id, label, approved_at, policy_revision
                        ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            request_id,
                            action,
                            subject_hash,
                            policy_revision,
                        ),
                    )
            except sqlite3.Error:
                raise LedgerError("Approval request could not be recorded.") from None

    def get_approval_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT * FROM approval_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
            except sqlite3.Error:
                raise LedgerError("Approval request could not be read.") from None
        return dict(row) if row is not None else None

    def get_receipt(self, request_id: str) -> ApprovalReceipt | None:
        row = self.get_approval_request(request_id)
        if row is None or row["status"] != "approved":
            return None
        return ApprovalReceipt(
            request_id=row["request_id"],
            subject_hash=row["subject_hash"],
            status=row["status"],
            policy_revision=row["policy_revision"],
            approver_type="human",
            label=row["label"],
        )

    def approve_request(
        self,
        *,
        request_id: str,
        approver_id: str,
        label: str,
        approved_at: str,
        policy_revision: str,
        event: AuditEvent,
    ) -> None:
        """Atomically transition pending -> approved and append its audit event."""
        with self._lock:
            try:
                with self._connection:
                    row = self._connection.execute(
                        """
                        SELECT status, policy_revision
                        FROM approval_requests WHERE request_id = ?
                        """,
                        (request_id,),
                    ).fetchone()
                    if row is None:
                        raise LedgerError("Approval request does not exist.")
                    if row["status"] != "pending":
                        raise LedgerError("Approval request is not pending.")
                    if row["policy_revision"] != policy_revision:
                        raise LedgerError("Approval request policy revision is stale.")
                    cursor = self._connection.execute(
                        """
                        UPDATE approval_requests
                        SET status = 'approved', approver_id = ?, label = ?, approved_at = ?
                        WHERE request_id = ? AND status = 'pending'
                        """,
                        (approver_id, label, approved_at, request_id),
                    )
                    if cursor.rowcount != 1:
                        raise LedgerError("Approval request is not pending.")
                    self._insert_event(self._connection, event)
            except LedgerError:
                raise
            except sqlite3.Error:
                raise LedgerError("Approval request could not be approved.") from None

    def query_audit(self, **filters: object) -> list[dict[str, Any]]:
        unknown = set(filters) - _AUDIT_COLUMNS
        if unknown:
            raise LedgerError("Unsupported audit filter.")
        clauses: list[str] = []
        values: list[object] = []
        for key, value in filters.items():
            if value is None:
                clauses.append(f"{key} IS NULL")
            else:
                clauses.append(f"{key} = ?")
                if key == "approval_required" and isinstance(value, bool):
                    value = int(value)
                elif key in {"reason_codes", "constraints"} and not isinstance(
                    value, str
                ):
                    value = _json(value)
                values.append(value)
        query = "SELECT * FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts ASC, id ASC"
        with self._lock:
            try:
                rows = self._connection.execute(query, values).fetchall()
            except sqlite3.Error:
                raise LedgerError("Audit events could not be read.") from None
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["approval_required"] = bool(item["approval_required"])
            item["reason_codes"] = json.loads(item["reason_codes"])
            item["constraints"] = json.loads(item["constraints"])
            results.append(item)
        return results
