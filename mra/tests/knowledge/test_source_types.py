from dataclasses import FrozenInstanceError

import pytest

from mra.knowledge.types import CandidateEvidence, SourceDescriptor


def test_source_descriptor_can_represent_unknown_license_status() -> None:
    source = SourceDescriptor(
        source_id="europe-pmc",
        name="Europe PMC",
        version="verified-2026-09-14",
        source_type="rest_api",
        provides=("literature_metadata",),
        capabilities=("search",),
        endpoint="https://www.ebi.ac.uk/europepmc/webservices/rest/",
        transport="https",
        input_schema={},
        output_schema={},
        software_license="not_applicable",
        data_license="pending",
        upstream_license="pending",
        commercial_use="pending",
        redistribution="pending",
        provenance_fields=("pmid", "pmcid", "doi"),
        evidence_mapping={},
        source_authority="official_service",
        evidence_origin="literature_metadata",
        extraction_method="api_query",
        citation_status="identifier_available",
        human_review="approved",
        default_use="candidate",
        status="verified",
        last_verified_at="2026-09-14T00:00:00Z",
    )

    assert source.data_license == "pending"
    with pytest.raises(FrozenInstanceError):
        source.status = "deprecated"  # type: ignore[misc]


def test_candidate_evidence_distinguishes_hypothesis_from_approved_knowledge() -> None:
    evidence = CandidateEvidence(
        evidence_id="evidence-1",
        source_id="omnipath",
        source_version="pending",
        source_record_id="TRPC1",
        retrieved_at="2026-09-14T00:00:00Z",
        query="TRPC1",
        claim_summary=None,
        claim_type=None,
        evidence_status="hypothesis_only",
        review_status="pending",
        license_status="pending",
        pmid=None,
        pmcid=None,
        doi=None,
        raw_excerpt=None,
        source_url=None,
        raw_response_hash="sha256:" + "a" * 64,
    )

    assert evidence.evidence_status == "hypothesis_only"
    assert evidence.review_status == "pending"
