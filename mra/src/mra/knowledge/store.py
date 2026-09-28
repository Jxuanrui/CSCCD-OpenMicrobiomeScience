"""SQLite and FTS5 storage for manually approved knowledge packages."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import jieba
import yaml

from mra.knowledge.types import (
    DEFAULT_USE_VALUES,
    EVIDENCE_LEVEL_VALUES,
    EVIDENCE_STATUS_VALUES,
    HUMAN_REVIEW_VALUES,
    PACKAGE_VALUES,
    REVIEW_STATUS_VALUES,
    SOURCE_KIND_VALUES,
    SOURCE_STATUS_VALUES,
    CandidateEvidence,
    Entry,
    SearchHit,
    SourceDescriptor,
)


_REQUIRED_FIELDS = {
    "id",
    "package",
    "title",
    "version",
    "evidence_level",
    "applicability",
    "content",
    "source",
    "approval",
}
_OPTIONAL_FIELDS = {"retired"}
_SOURCE_FIELDS = {"kind", "ref", "date"}
_APPROVAL_FIELDS = {"status", "by", "revision"}


def _error(message: str) -> ValueError:
    return ValueError(f"Invalid knowledge entry: {message}")


def _string(value: Any, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise _error(f"{field} must be a non-empty string")


_SOURCE_IDENTITY_FIELDS = ("source_id", "name", "version", "source_type")
_SOURCE_LICENSE_FIELDS = (
    "software_license",
    "data_license",
    "upstream_license",
    "commercial_use",
    "redistribution",
)
_SOURCE_METADATA_FIELDS = (
    "transport",
    "source_authority",
    "evidence_origin",
    "extraction_method",
    "citation_status",
)
_SOURCE_TUPLE_FIELDS = ("provides", "capabilities", "provenance_fields")
_SOURCE_MAPPING_FIELDS = ("input_schema", "output_schema", "evidence_mapping")
_ALLOWED_SOURCE_TRANSITIONS = frozenset(
    {
        ("candidate", "verified"),
        ("candidate", "deprecated"),
        ("verified", "deprecated"),
    }
)


def _source_error(message: str) -> ValueError:
    return ValueError(f"Invalid source descriptor: {message}")


def _validate_source_descriptor(desc: Any) -> SourceDescriptor:
    if not isinstance(desc, SourceDescriptor):
        raise _source_error("must be a SourceDescriptor")
    for field in _SOURCE_IDENTITY_FIELDS + _SOURCE_LICENSE_FIELDS + _SOURCE_METADATA_FIELDS:
        value = getattr(desc, field)
        if not isinstance(value, str) or not value.strip():
            raise _source_error(f"{field} must be a non-empty string")
    for field in _SOURCE_TUPLE_FIELDS:
        value = getattr(desc, field)
        if not isinstance(value, tuple):
            raise _source_error(f"{field} must be a tuple")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise _source_error(f"{field} must contain only non-empty strings")
    for field in _SOURCE_MAPPING_FIELDS:
        if not isinstance(getattr(desc, field), dict):
            raise _source_error(f"{field} must be a mapping")
    if desc.status not in SOURCE_STATUS_VALUES:
        raise _source_error(
            "status must be one of: " + ", ".join(sorted(SOURCE_STATUS_VALUES))
        )
    if desc.default_use not in DEFAULT_USE_VALUES:
        raise _source_error(
            "default_use must be one of: " + ", ".join(sorted(DEFAULT_USE_VALUES))
        )
    if desc.human_review not in HUMAN_REVIEW_VALUES:
        raise _source_error(
            "human_review must be one of: " + ", ".join(sorted(HUMAN_REVIEW_VALUES))
        )
    if desc.status in ("candidate", "deprecated") and desc.default_use == "evidence":
        raise _source_error(
            f'status "{desc.status}" cannot set default_use to "evidence"'
        )
    if desc.status == "verified":
        if desc.human_review != "approved":
            raise _source_error('verified sources require human_review "approved"')
        if not isinstance(desc.last_verified_at, str) or not desc.last_verified_at.strip():
            raise _source_error("verified sources require a non-empty last_verified_at")
    return desc


def _source_from_row(row: sqlite3.Row) -> SourceDescriptor:
    return SourceDescriptor(
        source_id=row["source_id"],
        name=row["name"],
        version=row["version"],
        source_type=row["source_type"],
        provides=tuple(json.loads(row["provides_json"])),
        capabilities=tuple(json.loads(row["capabilities_json"])),
        endpoint=row["endpoint"],
        transport=row["transport"],
        input_schema=json.loads(row["input_schema_json"]),
        output_schema=json.loads(row["output_schema_json"]),
        software_license=row["software_license"],
        data_license=row["data_license"],
        upstream_license=row["upstream_license"],
        commercial_use=row["commercial_use"],
        redistribution=row["redistribution"],
        provenance_fields=tuple(json.loads(row["provenance_fields_json"])),
        evidence_mapping=json.loads(row["evidence_mapping_json"]),
        source_authority=row["source_authority"],
        evidence_origin=row["evidence_origin"],
        extraction_method=row["extraction_method"],
        citation_status=row["citation_status"],
        human_review=row["human_review"],
        default_use=row["default_use"],
        status=row["status"],
        last_verified_at=row["last_verified_at"],
    )


_CANDIDATE_REQUIRED_FIELDS = (
    "evidence_id",
    "source_id",
    "source_version",
    "source_record_id",
    "retrieved_at",
    "license_status",
)
_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _candidate_error(message: str) -> ValueError:
    return ValueError(f"Invalid candidate evidence: {message}")


class DuplicateEvidenceError(ValueError):
    """Raised when candidate evidence with the same id already exists.

    A distinct type keeps callers off error-message substring matching.
    """


class SourceNotRegisteredError(ValueError):
    """Raised when evidence references a source that was never registered."""


_CANDIDATE_REFERENCE_FIELDS = ("pmid", "pmcid", "doi")
_EVIDENCE_STATUS_RANK = {
    status: rank
    for rank, status in enumerate(("hypothesis_only", "candidate", "evidence"))
}


def _lower_status(first: str, second: str) -> str:
    """Return the lower of two evidence levels; levels only ever downgrade."""
    if _EVIDENCE_STATUS_RANK[first] <= _EVIDENCE_STATUS_RANK[second]:
        return first
    return second


def _normalize_reference(value: Any, field: str) -> str | None:
    """Normalize pmid/pmcid/doi to None or a non-empty string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _candidate_error(f"{field} must be a non-empty string or None")
    value = value.strip()
    return value if value else None


def _validate_candidate_evidence(ev: Any) -> CandidateEvidence:
    if not isinstance(ev, CandidateEvidence):
        raise _candidate_error("must be a CandidateEvidence")
    for field in _CANDIDATE_REQUIRED_FIELDS:
        value = getattr(ev, field)
        if not isinstance(value, str) or not value.strip():
            raise _candidate_error(f"{field} must be a non-empty string")
    if ev.review_status not in REVIEW_STATUS_VALUES:
        raise _candidate_error(
            "review_status must be one of: " + ", ".join(sorted(REVIEW_STATUS_VALUES))
        )
    if ev.evidence_status == "approved_knowledge":
        raise _candidate_error('evidence_status "approved_knowledge" is rejected')
    if ev.evidence_status not in EVIDENCE_STATUS_VALUES:
        raise _candidate_error(
            "evidence_status must be one of: " + ", ".join(sorted(EVIDENCE_STATUS_VALUES))
        )
    if not isinstance(ev.raw_response_hash, str) or _HASH_PATTERN.fullmatch(
        ev.raw_response_hash
    ) is None:
        raise _candidate_error('raw_response_hash must match "sha256:[0-9a-f]{64}"')
    normalized_refs = {
        field: _normalize_reference(getattr(ev, field), field)
        for field in _CANDIDATE_REFERENCE_FIELDS
    }
    if any(
        normalized_refs[field] != getattr(ev, field)
        for field in _CANDIDATE_REFERENCE_FIELDS
    ):
        ev = replace(ev, **normalized_refs)
    return ev


def _candidate_evidence_from_row(row: sqlite3.Row) -> CandidateEvidence:
    return CandidateEvidence(
        evidence_id=row["evidence_id"],
        source_id=row["source_id"],
        source_version=row["source_version"],
        source_record_id=row["source_record_id"],
        retrieved_at=row["retrieved_at"],
        query=row["query"],
        claim_summary=row["claim_summary"],
        claim_type=row["claim_type"],
        evidence_status=row["evidence_status"],
        review_status=row["review_status"],
        license_status=row["license_status"],
        pmid=row["pmid"],
        pmcid=row["pmcid"],
        doi=row["doi"],
        raw_excerpt=row["raw_excerpt"],
        source_url=row["source_url"],
        raw_response_hash=row["raw_response_hash"],
    )


def _index_text(value: str) -> str:
    """Pre-segment text so SQLite FTS5 can search Chinese terms."""
    return " ".join(jieba.cut_for_search(value))


def _query_expression(query: str) -> str:
    terms: list[str] = []
    for term in jieba.cut_for_search(query):
        if term.isspace():
            continue
        terms.append(f'"{term.replace(chr(34), chr(34) * 2)}"')
    return " ".join(terms)


def _entry_from_row(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        package=row["package"],
        title=row["title"],
        version=int(row["version"]),
        evidence_level=row["evidence_level"],
        applicability=row["applicability"],
        content=row["content"],
        source=json.loads(row["sources_json"]),
        approval={
            "status": row["approval_status"],
            "by": row["approval_by"],
            "revision": row["approval_revision"],
        },
        retired=bool(row["retired"]),
        created_at=row["created_at"],
    )


def _validate_document(document: Any) -> Entry:
    if not isinstance(document, dict):
        raise _error("top level must be a mapping")
    missing = sorted(_REQUIRED_FIELDS - set(document))
    if missing:
        raise _error(f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(document) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS)
    if unknown:
        raise _error(f"unknown field(s): {', '.join(unknown)}")

    entry_id = _string(document["id"], "id")
    package = _string(document["package"], "package")
    if package not in PACKAGE_VALUES:
        raise _error(f"package must be one of: {', '.join(sorted(PACKAGE_VALUES))}")
    title = _string(document["title"], "title")
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _error("version must be a positive integer")
    evidence_level = _string(document["evidence_level"], "evidence_level")
    if evidence_level not in EVIDENCE_LEVEL_VALUES:
        raise _error(
            "evidence_level must be one of: "
            + ", ".join(sorted(EVIDENCE_LEVEL_VALUES))
        )
    applicability = _string(document["applicability"], "applicability")
    content = _string(document["content"], "content")

    source = document["source"]
    if not isinstance(source, list) or not source:
        raise _error("source must be a non-empty list")
    normalized_source: list[dict[str, str]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            raise _error(f"source[{index}] must be a mapping")
        missing_source = sorted(_SOURCE_FIELDS - set(item))
        if missing_source:
            raise _error(
                f"source[{index}] missing required field(s): {', '.join(missing_source)}"
            )
        unknown_source = sorted(set(item) - _SOURCE_FIELDS)
        if unknown_source:
            raise _error(f"source[{index}] has unknown field(s): {', '.join(unknown_source)}")
        kind = _string(item["kind"], f"source[{index}].kind")
        if kind not in SOURCE_KIND_VALUES:
            raise _error(f"source[{index}].kind must be literature or internal")
        normalized_source.append(
            {
                "kind": kind,
                "ref": _string(item["ref"], f"source[{index}].ref"),
                "date": _string(item["date"], f"source[{index}].date"),
            }
        )

    approval = document["approval"]
    if not isinstance(approval, dict):
        raise _error("approval must be a mapping")
    missing_approval = sorted(_APPROVAL_FIELDS - set(approval))
    if missing_approval:
        raise _error(f"approval missing required field(s): {', '.join(missing_approval)}")
    unknown_approval = sorted(set(approval) - _APPROVAL_FIELDS)
    if unknown_approval:
        raise _error(f"approval has unknown field(s): {', '.join(unknown_approval)}")
    status = _string(approval["status"], "approval.status")
    if status != "approved":
        raise _error('approval.status must be "approved"')
    normalized_approval = {
        "status": status,
        "by": _string(approval["by"], "approval.by"),
        "revision": _string(approval["revision"], "approval.revision"),
    }

    retired = document.get("retired", False)
    if not isinstance(retired, bool):
        raise _error("retired must be boolean")
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return Entry(
        id=entry_id,
        package=package,
        title=title,
        version=version,
        evidence_level=evidence_level,
        applicability=applicability,
        content=content,
        source=normalized_source,
        approval=normalized_approval,
        retired=retired,
        created_at=created_at,
    )


class KnowledgeStore:
    """A small, local store for approved and auditable knowledge entries."""

    def __init__(self, db_path: str | Path = "var/knowledge/knowledge.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            try:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise ValueError("Knowledge database directory is unavailable") from None
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                self.db_path, timeout=5.0, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY,
                    package TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    evidence_level TEXT NOT NULL,
                    applicability TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    approval_by TEXT NOT NULL,
                    approval_revision TEXT NOT NULL,
                    retired INTEGER NOT NULL CHECK (retired IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_entries USING fts5(
                    id UNINDEXED, title, applicability, content
                );
                CREATE TABLE IF NOT EXISTS source_registry (
                    source_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    provides_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    endpoint TEXT,
                    transport TEXT NOT NULL,
                    input_schema_json TEXT NOT NULL,
                    output_schema_json TEXT NOT NULL,
                    software_license TEXT NOT NULL,
                    data_license TEXT NOT NULL,
                    upstream_license TEXT NOT NULL,
                    commercial_use TEXT NOT NULL,
                    redistribution TEXT NOT NULL,
                    provenance_fields_json TEXT NOT NULL,
                    evidence_mapping_json TEXT NOT NULL,
                    source_authority TEXT NOT NULL,
                    evidence_origin TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    citation_status TEXT NOT NULL,
                    human_review TEXT NOT NULL,
                    default_use TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_verified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS candidate_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    query TEXT,
                    claim_summary TEXT,
                    claim_type TEXT,
                    evidence_status TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    license_status TEXT NOT NULL,
                    pmid TEXT,
                    pmcid TEXT,
                    doi TEXT,
                    raw_excerpt TEXT,
                    source_url TEXT,
                    raw_response_hash TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
                );
                """
            )
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.commit()
        except sqlite3.Error:
            raise ValueError("Knowledge database initialization failed") from None

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def ingest(self, path: str | Path) -> Entry:
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                document = yaml.safe_load(handle)
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Knowledge YAML could not be read: {exc}") from None
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid knowledge YAML: {exc}") from None
        entry = _validate_document(document)
        sources_json = json.dumps(entry.source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute("DELETE FROM fts_entries WHERE id = ?", (entry.id,))
                    self._connection.execute(
                        """
                        INSERT INTO entries (
                            id, package, title, version, evidence_level, applicability,
                            content, sources_json, approval_status, approval_by,
                            approval_revision, retired, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            package=excluded.package, title=excluded.title,
                            version=excluded.version, evidence_level=excluded.evidence_level,
                            applicability=excluded.applicability, content=excluded.content,
                            sources_json=excluded.sources_json, approval_status=excluded.approval_status,
                            approval_by=excluded.approval_by, approval_revision=excluded.approval_revision,
                            retired=excluded.retired, created_at=excluded.created_at
                        """,
                        (
                            entry.id,
                            entry.package,
                            entry.title,
                            entry.version,
                            entry.evidence_level,
                            entry.applicability,
                            entry.content,
                            sources_json,
                            entry.approval["status"],
                            entry.approval["by"],
                            entry.approval["revision"],
                            int(entry.retired),
                            entry.created_at,
                        ),
                    )
                    self._connection.execute(
                        "INSERT INTO fts_entries (id, title, applicability, content) VALUES (?, ?, ?, ?)",
                        (entry.id, _index_text(entry.title), _index_text(entry.applicability), _index_text(entry.content)),
                    )
            except sqlite3.Error as exc:
                raise ValueError(f"Knowledge entry could not be stored: {exc}") from None
        return entry

    def get(self, entry_id: str) -> Entry | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return _entry_from_row(row) if row is not None else None

    def list(self, package: str | None = None, include_retired: bool = False) -> list[Entry]:
        query = "SELECT * FROM entries"
        params: list[Any] = []
        clauses: list[str] = []
        if package is not None:
            if package not in PACKAGE_VALUES:
                raise ValueError(f"Unknown knowledge package: {package}")
            clauses.append("package = ?")
            params.append(package)
        if not include_retired:
            clauses.append("retired = 0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_entry_from_row(row) for row in rows]

    def search(
        self,
        query: str,
        package: str | None = None,
        include_retired: bool = False,
    ) -> list[SearchHit]:
        if not isinstance(query, str) or not query.strip():
            return []
        if package is not None and package not in PACKAGE_VALUES:
            raise ValueError(f"Unknown knowledge package: {package}")
        expression = _query_expression(query)
        if not expression:
            return []
        clauses = ["fts_entries MATCH ?"]
        params: list[Any] = [expression]
        if package is not None:
            clauses.append("e.package = ?")
            params.append(package)
        if not include_retired:
            clauses.append("e.retired = 0")
        sql = (
            "SELECT e.*, snippet(fts_entries, -1, '[', ']', '...', 24) AS match_snippet "
            "FROM fts_entries AS f JOIN entries AS e ON e.id = f.id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY e.id"
        )
        with self._lock:
            try:
                rows = self._connection.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise ValueError(f"Knowledge search failed: {exc}") from None
        return [SearchHit(_entry_from_row(row), row["match_snippet"]) for row in rows]

    def mark_retired(self, entry_id: str) -> None:
        with self._lock:
            with self._connection:
                cursor = self._connection.execute(
                    "UPDATE entries SET retired = 1 WHERE id = ?", (entry_id,)
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Knowledge entry does not exist: {entry_id}")

    def register_source(self, desc: SourceDescriptor) -> SourceDescriptor:
        desc = _validate_source_descriptor(desc)
        params = (
            desc.source_id,
            desc.name,
            desc.version,
            desc.source_type,
            json.dumps(list(desc.provides), ensure_ascii=False, sort_keys=True),
            json.dumps(list(desc.capabilities), ensure_ascii=False, sort_keys=True),
            desc.endpoint,
            desc.transport,
            json.dumps(desc.input_schema, ensure_ascii=False, sort_keys=True),
            json.dumps(desc.output_schema, ensure_ascii=False, sort_keys=True),
            desc.software_license,
            desc.data_license,
            desc.upstream_license,
            desc.commercial_use,
            desc.redistribution,
            json.dumps(list(desc.provenance_fields), ensure_ascii=False, sort_keys=True),
            json.dumps(desc.evidence_mapping, ensure_ascii=False, sort_keys=True),
            desc.source_authority,
            desc.evidence_origin,
            desc.extraction_method,
            desc.citation_status,
            desc.human_review,
            desc.default_use,
            desc.status,
            desc.last_verified_at,
        )
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO source_registry (
                            source_id, name, version, source_type,
                            provides_json, capabilities_json, endpoint, transport,
                            input_schema_json, output_schema_json,
                            software_license, data_license, upstream_license,
                            commercial_use, redistribution,
                            provenance_fields_json, evidence_mapping_json,
                            source_authority, evidence_origin, extraction_method,
                            citation_status, human_review, default_use, status,
                            last_verified_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        params,
                    )
            except sqlite3.IntegrityError:
                raise ValueError(f"Source is already registered: {desc.source_id}") from None
            except sqlite3.Error as exc:
                raise ValueError(f"Source could not be registered: {exc}") from None
        return desc

    def get_source(self, source_id: str) -> SourceDescriptor | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM source_registry WHERE source_id = ?", (source_id,)
            ).fetchone()
        return _source_from_row(row) if row is not None else None

    def list_sources(
        self, provides: str | None = None, status: str | None = "verified"
    ) -> list[SourceDescriptor]:
        if status is not None and status not in SOURCE_STATUS_VALUES:
            raise ValueError(
                "Unknown source status: "
                + status
                + " (expected one of: "
                + ", ".join(sorted(SOURCE_STATUS_VALUES))
                + ")"
            )
        query = "SELECT * FROM source_registry"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY source_id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        sources = [_source_from_row(row) for row in rows]
        if provides is not None:
            sources = [source for source in sources if provides in source.provides]
        return sources

    def update_source_status(
        self,
        source_id: str,
        *,
        status: str,
        human_review: str,
        default_use: str,
        last_verified_at: str | None,
    ) -> SourceDescriptor:
        existing = self.get_source(source_id)
        if existing is None:
            raise KeyError(f"Source is not registered: {source_id}")
        updated = _validate_source_descriptor(
            replace(
                existing,
                status=status,
                human_review=human_review,
                default_use=default_use,
                last_verified_at=last_verified_at,
            )
        )
        if existing.status != updated.status and (
            existing.status,
            updated.status,
        ) not in _ALLOWED_SOURCE_TRANSITIONS:
            raise ValueError(
                f"Invalid source status transition: "
                f"{existing.status} -> {updated.status}"
            )
        with self._lock:
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """
                        UPDATE source_registry SET
                            human_review = ?, default_use = ?, status = ?,
                            last_verified_at = ?
                        WHERE source_id = ?
                        """,
                        (
                            updated.human_review,
                            updated.default_use,
                            updated.status,
                            updated.last_verified_at,
                            updated.source_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise KeyError(f"Source is not registered: {source_id}")
            except sqlite3.Error as exc:
                raise ValueError(f"Source status could not be updated: {exc}") from None
        return updated

    def save_candidate_evidence(self, ev: CandidateEvidence) -> CandidateEvidence:
        ev = _validate_candidate_evidence(ev)
        source = self.get_source(ev.source_id)
        if source is None:
            raise SourceNotRegisteredError(f"Source is not registered: {ev.source_id}")
        if source.status == "deprecated":
            raise ValueError(f"Source is deprecated: {ev.source_id}")
        if ev.source_version != source.version:
            raise ValueError(
                f"Source version mismatch for {ev.source_id}: "
                f"registered {source.version}, got {ev.source_version}"
            )
        ceiling = source.default_use
        if source.status == "candidate":
            ceiling = _lower_status(ceiling, "candidate")
        if not any((ev.pmid, ev.pmcid, ev.doi)):
            ceiling = _lower_status(ceiling, "candidate")
        normalized_status = _lower_status(ev.evidence_status, ceiling)
        if normalized_status != ev.evidence_status:
            ev = replace(ev, evidence_status=normalized_status)
        params = (
            ev.evidence_id,
            ev.source_id,
            ev.source_version,
            ev.source_record_id,
            ev.retrieved_at,
            ev.query,
            ev.claim_summary,
            ev.claim_type,
            ev.evidence_status,
            ev.review_status,
            ev.license_status,
            ev.pmid,
            ev.pmcid,
            ev.doi,
            ev.raw_excerpt,
            ev.source_url,
            ev.raw_response_hash,
        )
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO candidate_evidence (
                            evidence_id, source_id, source_version, source_record_id,
                            retrieved_at, query, claim_summary, claim_type,
                            evidence_status, review_status, license_status,
                            pmid, pmcid, doi, raw_excerpt, source_url,
                            raw_response_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        params,
                    )
            except sqlite3.IntegrityError:
                raise DuplicateEvidenceError(
                    f"Candidate evidence already exists: {ev.evidence_id}"
                ) from None
            except sqlite3.Error as exc:
                raise ValueError(f"Candidate evidence could not be stored: {exc}") from None
        return ev

    def list_candidate_evidence(
        self, source_id: str | None = None, review_status: str | None = None
    ) -> list[CandidateEvidence]:
        if review_status is not None and review_status not in REVIEW_STATUS_VALUES:
            raise ValueError(
                "Unknown review status: "
                + review_status
                + " (expected one of: "
                + ", ".join(sorted(REVIEW_STATUS_VALUES))
                + ")"
            )
        query = "SELECT * FROM candidate_evidence"
        params: list[Any] = []
        clauses: list[str] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if review_status is not None:
            clauses.append("review_status = ?")
            params.append(review_status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY evidence_id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_candidate_evidence_from_row(row) for row in rows]

    def get_candidate_evidence(self, evidence_id: str) -> CandidateEvidence | None:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id must be a non-empty string")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM candidate_evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        return _candidate_evidence_from_row(row) if row is not None else None

    def set_candidate_review_status(
        self, evidence_id: str, review_status: str
    ) -> CandidateEvidence:
        """Record a human review decision; evidence level is never touched."""

        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id must be a non-empty string")
        if review_status not in REVIEW_STATUS_VALUES:
            raise ValueError(
                "review_status must be one of: "
                + ", ".join(sorted(REVIEW_STATUS_VALUES))
            )
        with self._lock:
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        "UPDATE candidate_evidence SET review_status = ? "
                        "WHERE evidence_id = ?",
                        (review_status, evidence_id),
                    )
                    row = (
                        self._connection.execute(
                            "SELECT * FROM candidate_evidence WHERE evidence_id = ?",
                            (evidence_id,),
                        ).fetchone()
                        if cursor.rowcount == 1
                        else None
                    )
            except sqlite3.Error as exc:
                raise ValueError(
                    f"Candidate review status could not be updated: {exc}"
                ) from None
        if row is None:
            raise KeyError(f"Candidate evidence does not exist: {evidence_id}")
        return _candidate_evidence_from_row(row)


__all__ = ["KnowledgeStore"]
