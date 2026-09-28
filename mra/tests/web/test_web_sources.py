from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mra.knowledge import KnowledgeStore
from mra.knowledge.types import CandidateEvidence, SourceDescriptor
from mra.pep import AuditLedger, Pep
from mra.web import create_app


def _pep(tmp_path: Path) -> tuple[Pep, AuditLedger]:
    ledger = AuditLedger(tmp_path / "audit.db")
    instance = Pep(
        ledger=ledger,
        staging_root=tmp_path / "staging",
        results_root=tmp_path / "results",
    )
    return instance, ledger


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="europe-pmc",
        name="Europe PMC",
        version="2026-07-27",
        source_type="rest_api",
        provides=("literature_metadata",),
        capabilities=("search",),
        endpoint="https://www.ebi.ac.uk/europepmc/webservices/rest",
        transport="https",
        input_schema={},
        output_schema={},
        software_license="pending",
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
        human_review="pending",
        default_use="candidate",
        status="candidate",
        last_verified_at=None,
    )


def _candidate() -> CandidateEvidence:
    return CandidateEvidence(
        evidence_id="europe-pmc:MED/1",
        source_id="europe-pmc",
        source_version="2026-07-27",
        source_record_id="MED/1",
        retrieved_at="2026-09-15T00:00:00Z",
        query="microbiome",
        claim_summary="candidate title",
        claim_type="literature_record",
        evidence_status="candidate",
        review_status="pending",
        license_status="pending",
        pmid="1",
        pmcid=None,
        doi=None,
        raw_excerpt="candidate excerpt",
        source_url="https://europepmc.org/article/MED/1",
        raw_response_hash="sha256:" + "a" * 64,
    )


def test_sources_without_knowledge_shows_empty_state(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    try:
        with TestClient(create_app(pep)) as client:
            response = client.get("/sources")
    finally:
        ledger.close()
    assert response.status_code == 200
    assert "未启用" in response.text


def test_sources_lists_registry_and_candidates_with_filter(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_candidate())
    try:
        with TestClient(create_app(pep, knowledge=store)) as client:
            listing = client.get("/sources")
            filtered = client.get("/sources", params={"review_status": "approved"})
            invalid = client.get("/sources", params={"review_status": "bogus"})
    finally:
        store.close()
        ledger.close()

    assert listing.status_code == 200
    assert "europe-pmc" in listing.text
    assert "MED/1" in listing.text
    assert "candidate excerpt" in listing.text

    assert filtered.status_code == 200
    assert "MED/1" not in filtered.text

    assert invalid.status_code == 400


def test_sources_review_post_updates_status(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    store = KnowledgeStore(tmp_path / "k.db")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_candidate())
    try:
        with TestClient(create_app(pep, knowledge=store)) as client:
            resp = client.post(
                "/sources/review",
                data={"evidence_id": "europe-pmc:MED/1", "status": "approved"},
                follow_redirects=False,
            )
    finally:
        store.close()
        ledger.close()

    assert resp.status_code == 303
    store2 = KnowledgeStore(tmp_path / "k.db")
    try:
        assert store2.get_candidate_evidence("europe-pmc:MED/1").review_status == "approved"
    finally:
        store2.close()


def test_sources_review_post_invalid_status(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    store = KnowledgeStore(tmp_path / "k.db")
    store.register_source(_descriptor())
    store.save_candidate_evidence(_candidate())
    try:
        with TestClient(create_app(pep, knowledge=store)) as client:
            resp = client.post(
                "/sources/review",
                data={"evidence_id": "europe-pmc:MED/1", "status": "bogus"},
            )
    finally:
        store.close()
        ledger.close()
    assert resp.status_code == 400


def test_sources_review_post_missing_evidence_id(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    store = KnowledgeStore(tmp_path / "k.db")
    store.register_source(_descriptor())
    try:
        with TestClient(create_app(pep, knowledge=store)) as client:
            resp = client.post("/sources/review", data={"status": "approved"})
    finally:
        store.close()
        ledger.close()
    assert resp.status_code == 400


def test_sources_review_post_unknown_evidence(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    store = KnowledgeStore(tmp_path / "k.db")
    store.register_source(_descriptor())
    try:
        with TestClient(create_app(pep, knowledge=store)) as client:
            resp = client.post(
                "/sources/review",
                data={"evidence_id": "nope", "status": "approved"},
            )
    finally:
        store.close()
        ledger.close()
    assert resp.status_code == 404


def test_sources_review_without_knowledge_returns_404(tmp_path: Path) -> None:
    pep, ledger = _pep(tmp_path)
    try:
        with TestClient(create_app(pep)) as client:
            resp = client.post(
                "/sources/review",
                data={"evidence_id": "x", "status": "approved"},
            )
    finally:
        ledger.close()
    assert resp.status_code == 404
