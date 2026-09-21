"""Typed records used by the approved knowledge package store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PACKAGE_VALUES = frozenset({"governance", "methods", "counterevidence", "context"})
EVIDENCE_LEVEL_VALUES = frozenset(
    {"confirmed_rule", "confirmed_counterevidence", "contextual_fact", "other"}
)
SOURCE_KIND_VALUES = frozenset({"literature", "internal"})
SOURCE_STATUS_VALUES = frozenset({"candidate", "verified", "deprecated"})
DEFAULT_USE_VALUES = frozenset({"evidence", "candidate", "hypothesis_only"})
HUMAN_REVIEW_VALUES = frozenset({"pending", "approved", "rejected"})
EVIDENCE_STATUS_VALUES = frozenset({"candidate", "evidence", "hypothesis_only"})
REVIEW_STATUS_VALUES = frozenset({"pending", "approved", "rejected"})


@dataclass(frozen=True)
class Entry:
    """One approved, versioned knowledge package entry."""

    id: str
    package: str
    title: str
    version: int
    evidence_level: str
    applicability: str
    content: str
    source: list[dict[str, str]]
    approval: dict[str, str]
    retired: bool = False
    created_at: str = ""


@dataclass(frozen=True)
class SearchHit:
    """A knowledge entry and the FTS-generated matching excerpt."""

    entry: Entry
    snippet: str

    @property
    def evidence_level(self) -> str:
        return self.entry.evidence_level

    @property
    def applicability(self) -> str:
        return self.entry.applicability

    @property
    def source(self) -> list[dict[str, str]]:
        return self.entry.source

    @property
    def source_summary(self) -> str:
        """Compact source references suitable for a model-facing result."""
        return ", ".join(item["ref"] for item in self.entry.source)


@dataclass(frozen=True)
class SourceDescriptor:
    """A registered external knowledge source and its governance metadata."""

    source_id: str
    name: str
    version: str
    source_type: str
    provides: tuple[str, ...]
    capabilities: tuple[str, ...]
    endpoint: str | None
    transport: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    software_license: str
    data_license: str
    upstream_license: str
    commercial_use: str
    redistribution: str
    provenance_fields: tuple[str, ...]
    evidence_mapping: dict[str, Any]
    source_authority: str
    evidence_origin: str
    extraction_method: str
    citation_status: str
    human_review: str
    default_use: str
    status: str
    last_verified_at: str | None


@dataclass(frozen=True)
class CandidateEvidence:
    """Evidence returned by a source that has not become approved knowledge."""

    evidence_id: str
    source_id: str
    source_version: str
    source_record_id: str
    retrieved_at: str
    query: str | None
    claim_summary: str | None
    claim_type: str | None
    evidence_status: str
    review_status: str
    license_status: str
    pmid: str | None
    pmcid: str | None
    doi: str | None
    raw_excerpt: str | None
    source_url: str | None
    raw_response_hash: str


__all__ = [
    "CandidateEvidence",
    "DEFAULT_USE_VALUES",
    "EVIDENCE_LEVEL_VALUES",
    "EVIDENCE_STATUS_VALUES",
    "Entry",
    "HUMAN_REVIEW_VALUES",
    "PACKAGE_VALUES",
    "REVIEW_STATUS_VALUES",
    "SOURCE_KIND_VALUES",
    "SOURCE_STATUS_VALUES",
    "SearchHit",
    "SourceDescriptor",
]
