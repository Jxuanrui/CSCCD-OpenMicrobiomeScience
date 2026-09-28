from __future__ import annotations

from pathlib import Path

import pytest

from mra.knowledge import KnowledgeStore
from mra.knowledge.types import CandidateEvidence, SourceDescriptor


def _descriptor(**overrides: object) -> SourceDescriptor:
    values: dict[str, object] = {
        "source_id": "europe-pmc",
        "name": "Europe PMC",
        "version": "verified-2026-09-14",
        "source_type": "rest_api",
        "provides": ("literature_metadata",),
        "capabilities": ("search", "fetch"),
        "endpoint": "https://www.ebi.ac.uk/europepmc/webservices/rest/",
        "transport": "https",
        "input_schema": {},
        "output_schema": {},
        "software_license": "not_applicable",
        "data_license": "pending",
        "upstream_license": "pending",
        "commercial_use": "pending",
        "redistribution": "pending",
        "provenance_fields": ("pmid", "pmcid", "doi"),
        "evidence_mapping": {},
        "source_authority": "official_service",
        "evidence_origin": "literature_metadata",
        "extraction_method": "api_query",
        "citation_status": "identifier_available",
        "human_review": "approved",
        "default_use": "evidence",
        "status": "verified",
        "last_verified_at": "2026-09-14T00:00:00Z",
    }
    values.update(overrides)
    return SourceDescriptor(**values)  # type: ignore[arg-type]


def _omnipath(**overrides: object) -> SourceDescriptor:
    values: dict[str, object] = {
        "source_id": "omnipath",
        "name": "OmniPath",
        "provides": ("interactions", "annotations"),
        "endpoint": "https://omnipathdb.org/",
        "data_license": "unknown",
        "provenance_fields": ("source", "target", "references"),
        "evidence_origin": "pathway_database",
        "default_use": "hypothesis_only",
    }
    values.update(overrides)
    return _descriptor(**values)


def _evidence(**overrides: object) -> CandidateEvidence:
    values: dict[str, object] = {
        "evidence_id": "ev-1",
        "source_id": "europe-pmc",
        "source_version": "verified-2026-09-14",
        "source_record_id": "rec-1",
        "retrieved_at": "2026-09-14T00:00:00Z",
        "query": "microbiome",
        "claim_summary": "a claim",
        "claim_type": "association",
        "evidence_status": "evidence",
        "review_status": "pending",
        "license_status": "allowed",
        "pmid": "12345",
        "pmcid": None,
        "doi": None,
        "raw_excerpt": "excerpt",
        "source_url": "https://example.com/1",
        "raw_response_hash": "sha256:" + "a" * 64,
    }
    values.update(overrides)
    return CandidateEvidence(**values)  # type: ignore[arg-type]


def test_save_roundtrip_with_pmid() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    ev = _evidence()
    saved = store.save_candidate_evidence(ev)
    assert saved == ev
    assert store.list_candidate_evidence() == [ev]


def test_missing_references_downgrade_to_candidate() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    ev = _evidence(pmid=None, pmcid=None, doi=None)
    saved = store.save_candidate_evidence(ev)
    assert saved.evidence_status == "candidate"
    assert store.list_candidate_evidence()[0].evidence_status == "candidate"


def test_candidate_status_is_not_auto_upgraded() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    ev = _evidence(evidence_status="candidate")
    saved = store.save_candidate_evidence(ev)
    assert saved.evidence_status == "candidate"
    assert store.list_candidate_evidence()[0].evidence_status == "candidate"


def test_hypothesis_only_source_forces_hypothesis_only() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_omnipath())
    ev = _evidence(
        source_id="omnipath",
        source_version="verified-2026-09-14",
        evidence_status="evidence",
    )
    saved = store.save_candidate_evidence(ev)
    assert saved.evidence_status == "hypothesis_only"
    assert store.list_candidate_evidence()[0].evidence_status == "hypothesis_only"


@pytest.mark.parametrize(
    "default_use,requested,has_citation,expected",
    [
        # evidence-grade source: ceiling is the source default_use.
        ("evidence", "hypothesis_only", True, "hypothesis_only"),
        ("evidence", "hypothesis_only", False, "hypothesis_only"),
        ("evidence", "candidate", True, "candidate"),
        ("evidence", "candidate", False, "candidate"),
        ("evidence", "evidence", True, "evidence"),
        ("evidence", "evidence", False, "candidate"),
        # candidate-grade source: evidence requests downgrade to candidate.
        ("candidate", "hypothesis_only", True, "hypothesis_only"),
        ("candidate", "hypothesis_only", False, "hypothesis_only"),
        ("candidate", "candidate", True, "candidate"),
        ("candidate", "candidate", False, "candidate"),
        ("candidate", "evidence", True, "candidate"),
        ("candidate", "evidence", False, "candidate"),
        # hypothesis_only source: everything stays hypothesis_only.
        ("hypothesis_only", "hypothesis_only", True, "hypothesis_only"),
        ("hypothesis_only", "hypothesis_only", False, "hypothesis_only"),
        ("hypothesis_only", "candidate", True, "hypothesis_only"),
        ("hypothesis_only", "candidate", False, "hypothesis_only"),
        ("hypothesis_only", "evidence", True, "hypothesis_only"),
        ("hypothesis_only", "evidence", False, "hypothesis_only"),
    ],
)
def test_status_only_downgrades_matrix(
    default_use: str, requested: str, has_citation: bool, expected: str
) -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor(default_use=default_use))
    ev = _evidence(
        evidence_status=requested,
        pmid="12345" if has_citation else None,
        pmcid=None,
        doi=None,
    )
    saved = store.save_candidate_evidence(ev)
    assert saved.evidence_status == expected
    assert store.list_candidate_evidence()[0].evidence_status == expected


def test_candidate_status_source_caps_at_candidate() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor(status="candidate", default_use="candidate"))
    saved = store.save_candidate_evidence(_evidence(evidence_status="evidence"))
    assert saved.evidence_status == "candidate"
    assert store.list_candidate_evidence()[0].evidence_status == "candidate"


def test_deprecated_source_rejects_new_evidence() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor(status="deprecated", default_use="candidate"))
    with pytest.raises(ValueError, match="deprecated"):
        store.save_candidate_evidence(_evidence())


def test_source_version_mismatch_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match="version mismatch"):
        store.save_candidate_evidence(_evidence(source_version="verified-1999-01-01"))


def test_whitespace_references_are_treated_as_missing() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    saved = store.save_candidate_evidence(_evidence(pmid="   ", pmcid="\t\n ", doi=" "))
    assert saved.evidence_status == "candidate"
    assert saved.pmid is None
    assert saved.pmcid is None
    assert saved.doi is None
    stored = store.list_candidate_evidence()[0]
    assert stored.evidence_status == "candidate"
    assert stored.pmid is None
    assert stored.pmcid is None
    assert stored.doi is None


@pytest.mark.parametrize("field", ["pmid", "pmcid", "doi"])
@pytest.mark.parametrize("value", [12345, 0, True, ["12345"]])
def test_non_string_reference_is_rejected(field: str, value: object) -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match=field):
        store.save_candidate_evidence(_evidence(**{field: value}))


def test_unknown_source_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="not registered"):
        store.save_candidate_evidence(_evidence(source_id="does-not-exist"))


def test_invalid_hash_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match="raw_response_hash"):
        store.save_candidate_evidence(_evidence(raw_response_hash="abc"))
    with pytest.raises(ValueError, match="raw_response_hash"):
        store.save_candidate_evidence(_evidence(raw_response_hash="sha256:" + "a" * 63))


@pytest.mark.parametrize("value", ["approved_knowledge", "bogus"])
def test_invalid_evidence_status_is_rejected(value: str) -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match="evidence_status"):
        store.save_candidate_evidence(_evidence(evidence_status=value))


def test_invalid_review_status_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match="review_status"):
        store.save_candidate_evidence(_evidence(review_status="bogus"))


def test_duplicate_evidence_id_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_evidence())
    with pytest.raises(ValueError, match="already exists"):
        store.save_candidate_evidence(_evidence())


def test_list_filters_by_source_and_review_status() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.register_source(_omnipath())
    store.save_candidate_evidence(
        _evidence(evidence_id="ev-1", source_id="europe-pmc", review_status="pending")
    )
    store.save_candidate_evidence(
        _evidence(evidence_id="ev-2", source_id="europe-pmc", review_status="approved")
    )
    store.save_candidate_evidence(
        _evidence(
            evidence_id="ev-3",
            source_id="omnipath",
            source_version="verified-2026-09-14",
            review_status="pending",
            pmid=None,
            doi=None,
        )
    )

    assert [e.evidence_id for e in store.list_candidate_evidence(source_id="europe-pmc")] == [
        "ev-1",
        "ev-2",
    ]
    assert [e.evidence_id for e in store.list_candidate_evidence(review_status="pending")] == [
        "ev-1",
        "ev-3",
    ]
    assert [
        e.evidence_id
        for e in store.list_candidate_evidence(
            source_id="europe-pmc", review_status="pending"
        )
    ] == ["ev-1"]


def test_unknown_review_status_filter_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="Unknown review status"):
        store.list_candidate_evidence(review_status="bogus")


def test_candidate_evidence_does_not_change_entries(tmp_path: Path) -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    before = store.list()
    store.save_candidate_evidence(_evidence())
    assert store.list() == before
    assert store.list_candidate_evidence() == [_evidence()]


def test_set_candidate_review_status_updates_and_filters() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_evidence(evidence_id="ev-1"))

    updated = store.set_candidate_review_status("ev-1", "approved")

    assert updated.review_status == "approved"
    assert store.get_candidate_evidence("ev-1") == updated
    assert [
        evidence.evidence_id
        for evidence in store.list_candidate_evidence(review_status="approved")
    ] == ["ev-1"]
    assert store.list_candidate_evidence(review_status="pending") == []
    # A review decision must never change the evidence level.
    assert updated.evidence_status == "evidence"


def test_set_candidate_review_status_unknown_id_raises() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(KeyError):
        store.set_candidate_review_status("missing", "approved")


def test_set_candidate_review_status_invalid_value_raises() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="review_status"):
        store.set_candidate_review_status("ev-1", "bogus")


def test_set_candidate_review_status_is_idempotent() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_evidence(evidence_id="ev-1"))

    first = store.set_candidate_review_status("ev-1", "rejected")
    second = store.set_candidate_review_status("ev-1", "rejected")

    assert first == second
    assert store.get_candidate_evidence("ev-1").review_status == "rejected"


def test_get_candidate_evidence_rejects_non_string_id() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError):
        store.get_candidate_evidence(123)  # type: ignore[arg-type]
