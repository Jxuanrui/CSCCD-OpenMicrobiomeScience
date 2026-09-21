from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mra.knowledge import KnowledgeStore, cli
from mra.knowledge.types import CandidateEvidence, SourceDescriptor


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="fake-source",
        name="Fake Source",
        version="2026-07-27",
        source_type="rest_api",
        provides=("literature_metadata",),
        capabilities=("search",),
        endpoint="https://example.invalid",
        transport="https",
        input_schema={},
        output_schema={},
        software_license="pending",
        data_license="pending",
        upstream_license="pending",
        commercial_use="pending",
        redistribution="pending",
        provenance_fields=("pmid",),
        evidence_mapping={},
        source_authority="test",
        evidence_origin="test",
        extraction_method="api_query",
        citation_status="identifier_available",
        human_review="pending",
        default_use="candidate",
        status="candidate",
        last_verified_at=None,
    )


def _candidate() -> CandidateEvidence:
    return CandidateEvidence(
        evidence_id="fake-source:MED/1",
        source_id="fake-source",
        source_version="2026-07-27",
        source_record_id="MED/1",
        retrieved_at="2026-09-15T00:00:00Z",
        query="x",
        claim_summary="title",
        claim_type="literature_record",
        evidence_status="candidate",
        review_status="pending",
        license_status="pending",
        pmid="1",
        pmcid=None,
        doi=None,
        raw_excerpt="excerpt",
        source_url="https://example.invalid/1",
        raw_response_hash="sha256:" + "a" * 64,
    )


class _FakeAdapter:
    def describe(self) -> SourceDescriptor:
        return _descriptor()

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        del query, page_size
        return [_candidate()]


class _BoomAdapter:
    def describe(self) -> SourceDescriptor:
        return _descriptor()

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        del query, page_size
        raise RuntimeError("network down")


class _BadVersionAdapter:
    def describe(self) -> SourceDescriptor:
        return _descriptor()

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        del query, page_size
        return [replace(_candidate(), source_version="1999-01-01")]


def test_source_search_stores_candidates_and_counts_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "SOURCE_ADAPTERS", {"fake": _FakeAdapter})
    db = tmp_path / "knowledge.db"

    assert cli.main(["source-search", "--source", "fake", "--query", "x", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "retrieved=1 stored=1 duplicate=0" in out

    # Re-running the same query must not duplicate the candidate row.
    assert cli.main(["source-search", "--source", "fake", "--query", "x", "--db", str(db)]) == 0
    assert "retrieved=1 stored=0 duplicate=1" in capsys.readouterr().out

    store = KnowledgeStore(db)
    try:
        assert len(store.list_candidate_evidence(source_id="fake-source")) == 1
        assert store.list() == []
    finally:
        store.close()


def test_source_search_unknown_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        ["source-search", "--source", "nope", "--query", "x", "--db", str(tmp_path / "k.db")]
    )
    assert rc == 1
    assert "unknown source" in capsys.readouterr().out


def test_source_search_adapter_failure_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "SOURCE_ADAPTERS", {"boom": _BoomAdapter})
    rc = cli.main(
        ["source-search", "--source", "boom", "--query", "x", "--db", str(tmp_path / "k.db")]
    )
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_source_search_rejects_non_positive_page_size(tmp_path: Path) -> None:
    rc = cli.main(
        [
            "source-search",
            "--source",
            "europe-pmc",
            "--query",
            "x",
            "--page-size",
            "0",
            "--db",
            str(tmp_path / "k.db"),
        ]
    )
    assert rc == 1


def test_source_search_candidate_failure_sets_nonzero_rc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "SOURCE_ADAPTERS", {"bad": _BadVersionAdapter})
    rc = cli.main(
        ["source-search", "--source", "bad", "--query", "x", "--db", str(tmp_path / "k.db")]
    )
    assert rc == 1
    assert "failure=1" in capsys.readouterr().out


def _seed_candidate(db: Path) -> None:
    store = KnowledgeStore(db)
    store.register_source(_descriptor())
    store.save_candidate_evidence(_candidate())
    store.close()


def test_review_candidate_cli_updates_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "k.db"
    _seed_candidate(db)

    rc = cli.main(
        [
            "review-candidate",
            "--evidence-id",
            "fake-source:MED/1",
            "--status",
            "approved",
            "--db",
            str(db),
        ]
    )

    assert rc == 0
    assert "approved" in capsys.readouterr().out
    store = KnowledgeStore(db)
    try:
        assert store.get_candidate_evidence("fake-source:MED/1").review_status == "approved"
    finally:
        store.close()


def test_review_candidate_cli_unknown_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        [
            "review-candidate",
            "--evidence-id",
            "nope",
            "--status",
            "approved",
            "--db",
            str(tmp_path / "k.db"),
        ]
    )
    assert rc == 1
    assert "unknown evidence" in capsys.readouterr().out


def test_review_candidate_cli_invalid_status_exits_usage(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(
            [
                "review-candidate",
                "--evidence-id",
                "x",
                "--status",
                "bogus",
                "--db",
                str(tmp_path / "k.db"),
            ]
        )
    assert raised.value.code == 2


def test_draft_entry_unknown_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        ["draft-entry", "--evidence-id", "nope", "--package", "methods", "--db", str(tmp_path / "k.db")]
    )
    assert rc == 1
    assert "unknown evidence" in capsys.readouterr().out


def test_draft_entry_requires_approved_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "k.db"
    _seed_candidate(db)
    rc = cli.main(
        ["draft-entry", "--evidence-id", "fake-source:MED/1", "--package", "methods", "--db", str(db)]
    )
    assert rc == 1
    assert "approved first" in capsys.readouterr().out


def test_draft_entry_emits_copied_citation_and_pending_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "k.db"
    _seed_candidate(db)
    assert (
        cli.main(
            ["review-candidate", "--evidence-id", "fake-source:MED/1", "--status", "approved", "--db", str(db)]
        )
        == 0
    )
    capsys.readouterr()

    rc = cli.main(
        ["draft-entry", "--evidence-id", "fake-source:MED/1", "--package", "methods", "--db", str(db)]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "PMID:1" in out
    assert "status: pending" in out
    assert "<填写知识内容>" in out


def test_draft_entry_output_is_not_ingestable_until_approved(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    _seed_candidate(db)
    cli.main(
        ["review-candidate", "--evidence-id", "fake-source:MED/1", "--status", "approved", "--db", str(db)]
    )
    out_file = tmp_path / "draft.yaml"
    rc = cli.main(
        [
            "draft-entry",
            "--evidence-id",
            "fake-source:MED/1",
            "--package",
            "methods",
            "--out",
            str(out_file),
            "--db",
            str(db),
        ]
    )
    assert rc == 0
    assert "PMID:1" in out_file.read_text(encoding="utf-8")

    store = KnowledgeStore(":memory:")
    try:
        with pytest.raises(ValueError):
            store.ingest(out_file)  # approval.status is still "pending"
    finally:
        store.close()


def test_draft_entry_falls_back_to_doi_and_custom_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "k.db"
    store = KnowledgeStore(db)
    store.register_source(_descriptor())
    store.save_candidate_evidence(replace(_candidate(), pmid=None, doi="10.1000/xyz"))
    store.close()
    cli.main(
        ["review-candidate", "--evidence-id", "fake-source:MED/1", "--status", "approved", "--db", str(db)]
    )
    capsys.readouterr()

    rc = cli.main(
        [
            "draft-entry",
            "--evidence-id",
            "fake-source:MED/1",
            "--package",
            "methods",
            "--id",
            "my-custom-entry",
            "--db",
            str(db),
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "DOI:10.1000/xyz" in out
    assert "id: my-custom-entry" in out
