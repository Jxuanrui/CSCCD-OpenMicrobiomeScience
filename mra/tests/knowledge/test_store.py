from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mra.knowledge import KnowledgeStore


def _write_entry(path: Path, **overrides: object) -> Path:
    values: dict[str, object] = {
        "id": "methods-1",
        "package": "methods",
        "title": "组成数据方法",
        "version": 1,
        "evidence_level": "confirmed_rule",
        "applicability": "肠道菌群关联分析",
        "content": "microbiome compositional data 方法 and batch audit",
        "source": [{"kind": "literature", "ref": "PMID:123", "date": "2026-09-12"}],
        "approval": {"status": "approved", "by": "pi", "revision": "r1"},
    }
    values.update(overrides)
    path.write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_ingest_and_search_approved_entry(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    entry = store.ingest(_write_entry(tmp_path / "entry.yaml"))
    assert entry.id == "methods-1"
    assert store.get(entry.id) == entry
    assert store.search("microbiome")[0].entry == entry
    assert store.search("菌群")[0].entry == entry
    assert "microbiome" in store.search("microbiome")[0].snippet


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("title", None, "missing required field"),
        ("package", "invalid", "package must be one of"),
        ("evidence_level", "invalid", "evidence_level must be one of"),
        ("approval", {"status": "pending", "by": "pi", "revision": "r1"}, "approval.status"),
    ],
)
def test_invalid_entries_are_rejected(
    tmp_path: Path, field: str, value: object, reason: str
) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    values: dict[str, object] = {
        "id": "bad",
        "package": "methods",
        "title": "title",
        "version": 1,
        "evidence_level": "confirmed_rule",
        "applicability": "scope",
        "content": "content",
        "source": [{"kind": "internal", "ref": "memo", "date": "2026-09-12"}],
        "approval": {"status": "approved", "by": "pi", "revision": "r1"},
    }
    values.pop(field, None)
    if value is not None:
        values[field] = value
    path = tmp_path / f"{field}.yaml"
    path.write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        store.ingest(path)


def test_retired_entries_are_excluded_by_default(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.ingest(_write_entry(tmp_path / "entry.yaml"))
    store.mark_retired("methods-1")
    assert store.search("microbiome") == []
    assert [entry.id for entry in store.list()] == []
    assert store.search("microbiome", include_retired=True)[0].entry.retired is True
    assert [entry.id for entry in store.list(include_retired=True)] == ["methods-1"]


def test_ingest_same_id_replaces_version_and_fts(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.ingest(_write_entry(tmp_path / "one.yaml"))
    updated = store.ingest(
        _write_entry(
            tmp_path / "two.yaml",
            version=2,
            title="Updated title",
            content="new English evidence",
        )
    )
    assert store.get("methods-1") == updated
    assert store.search("new")[0].entry.version == 2
    assert store.search("microbiome") == []


def test_jieba_search_matches_multi_character_chinese_terms(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    entry = store.ingest(
        _write_entry(
            tmp_path / "cjk.yaml",
            id="methods-cjk",
            title="批次混杂与目标队列样本",
            applicability="队列A",
            content="Goldberg 方法说明批次混杂风险。",
        )
    )

    harbin_hits = store.search("队列A")
    assert [hit.entry.id for hit in harbin_hits] == [entry.id]
    assert "队列A" in harbin_hits[0].snippet
    assert "队列A" in harbin_hits[0].entry.applicability
    assert store.search("批次混杂")[0].entry.id == entry.id
    assert store.search("Goldberg")[0].entry.id == entry.id
    assert store.search("哈") == []
