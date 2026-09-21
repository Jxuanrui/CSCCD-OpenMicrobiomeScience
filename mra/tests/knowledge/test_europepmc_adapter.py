from __future__ import annotations

import json
import re

import pytest

from mra.knowledge import KnowledgeStore
from mra.knowledge.sources.europepmc import EuropePmcAdapter, EuropePmcError

_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}")

_FIXTURE = {
    "version": "6.9",
    "hitCount": 2,
    "resultList": {
        "result": [
            {
                "id": "37917583",
                "source": "MED",
                "pmid": "37917583",
                "pmcid": "PMC10680139",
                "doi": "10.1016/j.celrep.2023.113339",
                "title": "Microbiome diet association",
                "abstractText": "A study of diet and microbiome composition.",
                "isOpenAccess": "Y",
                "inEPMC": "Y",
            },
            {
                "id": "PPR1283561",
                "source": "PPR",
                "doi": "10.1101/2024.01.01.123456",
                "title": "Preprint on gut microbiome",
                "abstractText": "Preprint abstract text.",
            },
        ]
    },
}

_ERROR_FIXTURE = {
    "errCode": 404,
    "errMsg": "Invalid page size provided. Valid size is between 1 and 1000",
}


def _adapter(payload: object, **kwargs: object) -> EuropePmcAdapter:
    body = json.dumps(payload).encode("utf-8")

    def fetcher(_url: str) -> bytes:
        return body

    return EuropePmcAdapter(fetcher=fetcher, **kwargs)


def test_describe_is_candidate_grade_and_registers_with_store() -> None:
    descriptor = EuropePmcAdapter().describe()
    assert descriptor.source_id == "europe-pmc"
    assert descriptor.status == "candidate"
    assert descriptor.human_review == "pending"
    assert descriptor.default_use == "candidate"
    assert descriptor.last_verified_at is None

    store = KnowledgeStore(":memory:")
    assert store.register_source(descriptor) == descriptor
    assert store.get_source("europe-pmc") == descriptor


def test_search_maps_fields_without_side_effects() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(EuropePmcAdapter().describe())

    hits = _adapter(_FIXTURE).search("microbiome diet", page_size=2)

    assert len(hits) == 2
    first = hits[0]
    assert first.source_id == "europe-pmc"
    assert first.source_record_id == "MED/37917583"
    assert first.evidence_id == "europe-pmc:MED/37917583"
    assert first.source_version == "2026-07-27"
    assert first.pmid == "37917583"
    assert first.pmcid == "PMC10680139"
    assert first.doi == "10.1016/j.celrep.2023.113339"
    assert first.claim_summary == "Microbiome diet association"
    assert first.claim_type == "literature_record"
    assert first.source_url == "https://doi.org/10.1016/j.celrep.2023.113339"
    assert first.evidence_status == "candidate"
    assert first.review_status == "pending"
    assert first.retrieved_at.endswith("Z")
    assert _HASH_RE.fullmatch(first.raw_response_hash)
    # The store still has no approved entries; search must not write.
    assert store.list() == []
    assert store.list_candidate_evidence() == []


def test_missing_pmcid_and_pmid_are_none() -> None:
    hits = _adapter(_FIXTURE).search("microbiome")
    preprint = hits[1]
    assert preprint.pmid is None
    assert preprint.pmcid is None
    assert preprint.doi == "10.1101/2024.01.01.123456"
    assert preprint.source_record_id == "PPR/PPR1283561"
    assert preprint.source_url == "https://doi.org/10.1101/2024.01.01.123456"


def test_err_code_inside_http_200_raises() -> None:
    with pytest.raises(EuropePmcError) as raised:
        _adapter(_ERROR_FIXTURE).search("microbiome", page_size=5)
    assert "404" in str(raised.value)


def test_invalid_json_raises() -> None:
    adapter = EuropePmcAdapter(fetcher=lambda _url: b"not json")
    with pytest.raises(EuropePmcError):
        adapter.search("microbiome")


def test_missing_result_list_raises() -> None:
    with pytest.raises(EuropePmcError):
        _adapter({"version": "6.9", "hitCount": 0}).search("microbiome")


def test_non_bytes_fetcher_result_raises() -> None:
    adapter = EuropePmcAdapter(fetcher=lambda _url: "json-string")  # type: ignore[arg-type,return-value]
    with pytest.raises(EuropePmcError):
        adapter.search("microbiome")


@pytest.mark.parametrize("page_size", [0, -1, 1001, True, "25", 1.5])
def test_page_size_bounds(page_size: object) -> None:
    with pytest.raises(ValueError):
        _adapter(_FIXTURE).search("microbiome", page_size=page_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_query_rejected(query: object) -> None:
    with pytest.raises(ValueError):
        _adapter(_FIXTURE).search(query)  # type: ignore[arg-type]


def test_empty_result_list_returns_empty() -> None:
    payload = {"version": "6.9", "hitCount": 0, "resultList": {"result": []}}
    assert _adapter(payload).search("nothing") == []


def test_excerpt_is_truncated_to_max_excerpt() -> None:
    adapter = _adapter(_FIXTURE, max_excerpt=10)
    hits = adapter.search("microbiome")
    assert hits[0].raw_excerpt == "A study of"


def test_build_url_encodes_query() -> None:
    url = EuropePmcAdapter().build_url('gut AND "microbiome"', 5)
    assert url.startswith("https://www.ebi.ac.uk/europepmc/webservices/rest/search?")
    assert "pageSize=5" in url
    assert "resultType=core" in url
    assert "%22microbiome%22" in url


def test_record_missing_id_raises() -> None:
    payload = {
        "version": "6.9",
        "hitCount": 1,
        "resultList": {"result": [{"source": "MED", "title": "no id here"}]},
    }
    with pytest.raises(EuropePmcError, match="id"):
        _adapter(payload).search("microbiome")


def test_non_object_result_raises() -> None:
    payload = {"version": "6.9", "hitCount": 1, "resultList": {"result": ["nope"]}}
    with pytest.raises(EuropePmcError):
        _adapter(payload).search("microbiome")


def test_hash_is_deterministic_for_same_record() -> None:
    first = _adapter(_FIXTURE).search("microbiome")[0].raw_response_hash
    second = _adapter(_FIXTURE).search("microbiome")[0].raw_response_hash
    assert first == second
    assert _HASH_RE.fullmatch(first)


def test_candidates_roundtrip_through_store_downgrade_rules() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(EuropePmcAdapter().describe())

    for candidate in _adapter(_FIXTURE).search("microbiome", page_size=2):
        saved = store.save_candidate_evidence(candidate)
        # Candidate-grade source caps at candidate; no upgrade path exists.
        assert saved.evidence_status == "candidate"

    stored = store.list_candidate_evidence(source_id="europe-pmc")
    assert {evidence.evidence_id for evidence in stored} == {
        "europe-pmc:MED/37917583",
        "europe-pmc:PPR/PPR1283561",
    }
    assert store.list() == []
