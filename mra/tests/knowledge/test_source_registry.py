from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mra.knowledge import KnowledgeStore
from mra.knowledge.types import SourceDescriptor


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
        "default_use": "candidate",
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


def test_register_and_get_europe_pmc_roundtrip() -> None:
    store = KnowledgeStore(":memory:")
    desc = _descriptor()
    assert store.register_source(desc) == desc
    assert store.get_source("europe-pmc") == desc
    assert store.get_source("missing") is None


def test_provides_filter_selects_matching_sources() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.register_source(_omnipath())

    all_sources = store.list_sources()
    assert [s.source_id for s in all_sources] == ["europe-pmc", "omnipath"]
    literature = store.list_sources(provides="literature_metadata")
    assert [s.source_id for s in literature] == ["europe-pmc"]
    interactions = store.list_sources(provides="interactions")
    assert [s.source_id for s in interactions] == ["omnipath"]
    assert store.list_sources(provides="sequences") == []


def test_deprecated_sources_hidden_by_default() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.register_source(
        _descriptor(
            source_id="old-service",
            status="deprecated",
            human_review="rejected",
            last_verified_at="2026-01-01T00:00:00Z",
        )
    )

    assert [s.source_id for s in store.list_sources()] == ["europe-pmc"]
    assert [s.source_id for s in store.list_sources(status="deprecated")] == ["old-service"]
    assert [s.source_id for s in store.list_sources(status=None)] == [
        "europe-pmc",
        "old-service",
    ]


@pytest.mark.parametrize(
    "field",
    [
        "software_license",
        "data_license",
        "upstream_license",
        "commercial_use",
        "redistribution",
    ],
)
@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_license_fields_are_rejected(field: str, value: object) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match=field):
        store.register_source(_descriptor(**{field: value}))


@pytest.mark.parametrize("field", ["source_id", "name", "version", "source_type"])
def test_empty_identity_fields_are_rejected(field: str) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match=field):
        store.register_source(_descriptor(**{field: "  "}))


@pytest.mark.parametrize(
    "field",
    [
        "transport",
        "source_authority",
        "evidence_origin",
        "extraction_method",
        "citation_status",
    ],
)
@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_metadata_fields_are_rejected(field: str, value: object) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match=field):
        store.register_source(_descriptor(**{field: value}))


@pytest.mark.parametrize("field", ["provides", "capabilities", "provenance_fields"])
def test_tuple_fields_reject_non_tuple(field: str) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match=field):
        store.register_source(_descriptor(**{field: "literature_metadata"}))


@pytest.mark.parametrize(
    ("status", "default_use"),
    [
        ("candidate", "evidence"),
        ("deprecated", "evidence"),
    ],
)
def test_candidate_or_deprecated_cannot_default_to_evidence(
    status: str, default_use: str
) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="default_use"):
        store.register_source(
            _descriptor(
                status=status,
                default_use=default_use,
                human_review="pending",
                last_verified_at=None,
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "archived"),
        ("default_use", "production"),
        ("human_review", "unknown"),
    ],
)
def test_invalid_enum_values_are_rejected(field: str, value: str) -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match=field):
        store.register_source(_descriptor(**{field: value}))


def test_verified_requires_human_approval() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="human_review"):
        store.register_source(_descriptor(human_review="pending"))


def test_verified_requires_last_verified_at() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="last_verified_at"):
        store.register_source(_descriptor(last_verified_at=None))
    with pytest.raises(ValueError, match="last_verified_at"):
        store.register_source(_descriptor(last_verified_at=" "))


def test_duplicate_source_id_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    with pytest.raises(ValueError, match="already registered"):
        store.register_source(_descriptor(name="Europe PMC Mirror"))


def test_candidate_source_registers_but_is_not_default_listed() -> None:
    store = KnowledgeStore(":memory:")
    candidate = _descriptor(
        source_id="new-api",
        status="candidate",
        human_review="pending",
        last_verified_at=None,
    )
    store.register_source(candidate)
    store.register_source(_descriptor())

    assert store.get_source("new-api") == candidate
    assert [s.source_id for s in store.list_sources()] == ["europe-pmc"]
    assert [s.source_id for s in store.list_sources(status="candidate")] == ["new-api"]
    assert [s.source_id for s in store.list_sources(status=None)] == [
        "europe-pmc",
        "new-api",
    ]


def test_unknown_status_filter_is_rejected() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(ValueError, match="Unknown source status"):
        store.list_sources(status="archived")


def test_knowledge_entries_still_work_alongside_registry(tmp_path: Path) -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    entry_yaml = tmp_path / "entry.yaml"
    entry_yaml.write_text(
        yaml.safe_dump(
            {
                "id": "methods-1",
                "package": "methods",
                "title": "组成数据方法",
                "version": 1,
                "evidence_level": "confirmed_rule",
                "applicability": "肠道菌群关联分析",
                "content": "microbiome compositional data methods",
                "source": [{"kind": "literature", "ref": "PMID:123", "date": "2026-09-12"}],
                "approval": {"status": "approved", "by": "pi", "revision": "r1"},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    entry = store.ingest(entry_yaml)
    assert store.get(entry.id) == entry
    assert store.search("microbiome")[0].entry == entry
    assert store.get_source("europe-pmc") is not None


def test_candidate_to_verified_requires_approval_and_timestamp() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(
        _descriptor(
            source_id="new-api",
            status="candidate",
            human_review="pending",
            last_verified_at=None,
        )
    )

    with pytest.raises(ValueError, match="human_review"):
        store.update_source_status(
            "new-api",
            status="verified",
            human_review="pending",
            default_use="evidence",
            last_verified_at="2026-09-15T00:00:00Z",
        )
    with pytest.raises(ValueError, match="last_verified_at"):
        store.update_source_status(
            "new-api",
            status="verified",
            human_review="approved",
            default_use="evidence",
            last_verified_at=None,
        )

    updated = store.update_source_status(
        "new-api",
        status="verified",
        human_review="approved",
        default_use="evidence",
        last_verified_at="2026-09-15T00:00:00Z",
    )
    assert updated.status == "verified"
    assert updated.human_review == "approved"
    assert updated.last_verified_at == "2026-09-15T00:00:00Z"
    assert store.get_source("new-api") == updated


def test_verified_to_deprecated_transition() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    updated = store.update_source_status(
        "europe-pmc",
        status="deprecated",
        human_review="rejected",
        default_use="candidate",
        last_verified_at="2026-09-14T00:00:00Z",
    )
    assert updated.status == "deprecated"
    assert store.get_source("europe-pmc") == updated
    assert [s.source_id for s in store.list_sources()] == []
    assert [s.source_id for s in store.list_sources(status="deprecated")] == ["europe-pmc"]


def test_illegal_status_transitions_are_rejected() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(_descriptor())
    store.register_source(
        _descriptor(
            source_id="old-service",
            status="deprecated",
            human_review="rejected",
            last_verified_at="2026-01-01T00:00:00Z",
        )
    )

    with pytest.raises(ValueError, match="transition"):
        store.update_source_status(
            "europe-pmc",
            status="candidate",
            human_review="pending",
            default_use="candidate",
            last_verified_at=None,
        )
    with pytest.raises(ValueError, match="transition"):
        store.update_source_status(
            "old-service",
            status="candidate",
            human_review="pending",
            default_use="candidate",
            last_verified_at=None,
        )
    with pytest.raises(ValueError, match="transition"):
        store.update_source_status(
            "old-service",
            status="verified",
            human_review="approved",
            default_use="evidence",
            last_verified_at="2026-09-15T00:00:00Z",
        )


def test_update_source_status_missing_source_raises_key_error() -> None:
    store = KnowledgeStore(":memory:")
    with pytest.raises(KeyError):
        store.update_source_status(
            "missing",
            status="verified",
            human_review="approved",
            default_use="evidence",
            last_verified_at="2026-09-15T00:00:00Z",
        )


def test_update_source_status_reflects_in_default_listing() -> None:
    store = KnowledgeStore(":memory:")
    store.register_source(
        _descriptor(
            source_id="new-api",
            status="candidate",
            human_review="pending",
            last_verified_at=None,
        )
    )
    assert [s.source_id for s in store.list_sources()] == []

    store.update_source_status(
        "new-api",
        status="verified",
        human_review="approved",
        default_use="evidence",
        last_verified_at="2026-09-15T00:00:00Z",
    )
    assert [s.source_id for s in store.list_sources()] == ["new-api"]
