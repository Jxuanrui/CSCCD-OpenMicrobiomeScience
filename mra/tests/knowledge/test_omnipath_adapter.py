from __future__ import annotations

import json
import re

import pytest

from mra.knowledge import KnowledgeStore
from mra.knowledge.sources.omnipath import OmniPathAdapter, OmniPathError

_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")

_FIXTURE = [
    {
        "source": "EGFR",
        "target": "ARAF",
        "is_directed": True,
        "is_stimulation": True,
        "is_inhibition": False,
        "sources": ["Reactome"],
        "references": ["Reactome:6814671"],
        "curation_effort": 1,
    },
    {
        "source": "TP53",
        "target": "MDM2",
        "is_directed": True,
        "is_stimulation": False,
        "is_inhibition": True,
        "sources": ["BioGRID"],
        "references": ["BioGRID:123", "PubMed:987654"],
        "curation_effort": 2,
    },
]


def _adapter(payload: object, **kwargs: object) -> OmniPathAdapter:
    body = json.dumps(payload).encode("utf-8")

    def fetcher(_url: str) -> bytes:
        return body

    return OmniPathAdapter(fetcher=fetcher, **kwargs)


def test_describe_is_hypothesis_grade_and_registers() -> None:
    descriptor = OmniPathAdapter().describe()
    assert descriptor.source_id == "omnipath"
    assert descriptor.status == "candidate"
    assert descriptor.default_use == "hypothesis_only"
    assert descriptor.human_review == "pending"

    store = KnowledgeStore(":memory:")
    assert store.register_source(descriptor) == descriptor


def test_search_maps_fields_without_side_effects() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(OmniPathAdapter().describe())

    hits = _adapter(_FIXTURE).search("EGFR", page_size=2)

    assert len(hits) == 2
    first = hits[0]
    assert first.source_id == "omnipath"
    assert first.source_record_id == "EGFR->ARAF"
    assert first.evidence_id == "omnipath:EGFR->ARAF#0"
    assert first.pmid is None
    assert first.pmcid is None
    assert first.doi is None
    assert first.claim_type == "molecular_interaction"
    assert first.evidence_status == "candidate"
    assert first.review_status == "pending"
    assert _HASH_RE.fullmatch(first.raw_response_hash)
    assert store.list() == []
    assert store.list_candidate_evidence() == []


def test_excerpt_contains_flags_sources_and_references() -> None:
    hits = _adapter(_FIXTURE).search("TP53")
    second = hits[1]
    assert "is_inhibition=True" in (second.raw_excerpt or "")
    assert "sources=BioGRID" in (second.raw_excerpt or "")
    assert "refs=BioGRID:123,PubMed:987654" in (second.raw_excerpt or "")


def test_build_url_encodes_gene() -> None:
    url = OmniPathAdapter().build_url("EGFR;TP53", 5)
    assert url.startswith("https://omnipathdb.org/interactions?")
    assert "proteins=EGFR%3BTP53" in url
    assert "limit=5" in url
    assert "fields=sources%2Creferences%2Ccuration_effort" in url


@pytest.mark.parametrize("page_size", [0, 101, True, "5"])
def test_page_size_bounds(page_size: object) -> None:
    with pytest.raises(ValueError):
        _adapter(_FIXTURE).search("EGFR", page_size=page_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_query_rejected(query: object) -> None:
    with pytest.raises(ValueError):
        _adapter(_FIXTURE).search(query)  # type: ignore[arg-type]


def test_invalid_json_raises() -> None:
    adapter = OmniPathAdapter(fetcher=lambda _url: b"not json")
    with pytest.raises(OmniPathError):
        adapter.search("EGFR")


def test_non_list_payload_raises() -> None:
    with pytest.raises(OmniPathError):
        _adapter({"error": "boom"}).search("EGFR")


def test_non_bytes_fetcher_raises() -> None:
    adapter = OmniPathAdapter(fetcher=lambda _url: "str")  # type: ignore[arg-type,return-value]
    with pytest.raises(OmniPathError):
        adapter.search("EGFR")


def test_empty_list_returns_empty() -> None:
    assert _adapter([]).search("EGFR") == []


def test_candidates_roundtrip_through_store_forced_hypothesis() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(OmniPathAdapter().describe())

    for candidate in _adapter(_FIXTURE).search("EGFR", page_size=2):
        saved = store.save_candidate_evidence(candidate)
        # hypothesis_only source forces evidence level down; never upgrade.
        assert saved.evidence_status == "hypothesis_only"

    stored = store.list_candidate_evidence(source_id="omnipath")
    assert {evidence.source_record_id for evidence in stored} == {
        "EGFR->ARAF",
        "TP53->MDM2",
    }
    assert store.list() == []
