"""Command line tools for ingesting and querying the local knowledge store."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence

import yaml

from mra.knowledge import KnowledgeStore
from mra.knowledge.sources.europepmc import EuropePmcAdapter
from mra.knowledge.store import DuplicateEvidenceError
from mra.knowledge.types import PACKAGE_VALUES, REVIEW_STATUS_VALUES


DEFAULT_DB_PATH = Path("var/knowledge/knowledge.db")

# Only sources with a working adapter are queryable from the CLI.
SOURCE_ADAPTERS: dict[str, type] = {"europe-pmc": EuropePmcAdapter}


def _add_db_argument(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=argparse.SUPPRESS if suppress else DEFAULT_DB_PATH,
        help="SQLite database path (default: var/knowledge/knowledge.db)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_db_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest all YAML files under a directory")
    ingest.add_argument("yaml_dir", type=Path)
    _add_db_argument(ingest, suppress=True)

    search = commands.add_parser("search", help="search indexed knowledge entries")
    search.add_argument("query")
    _add_db_argument(search, suppress=True)

    list_entries = commands.add_parser("list", help="list knowledge entries")
    list_entries.add_argument("--package", choices=sorted(PACKAGE_VALUES))
    _add_db_argument(list_entries, suppress=True)

    source_search = commands.add_parser(
        "source-search",
        help="query an external source into the candidate evidence inbox",
    )
    source_search.add_argument("--source", required=True)
    source_search.add_argument("--query", required=True)
    source_search.add_argument("--page-size", type=int, default=5)
    _add_db_argument(source_search, suppress=True)

    review = commands.add_parser(
        "review-candidate",
        help="record a human review decision for a candidate evidence row",
    )
    review.add_argument("--evidence-id", required=True)
    review.add_argument("--status", required=True, choices=sorted(REVIEW_STATUS_VALUES))
    _add_db_argument(review, suppress=True)

    draft = commands.add_parser(
        "draft-entry",
        help="write a knowledge entry draft from an approved candidate evidence row",
    )
    draft.add_argument("--evidence-id", required=True)
    draft.add_argument("--package", required=True, choices=sorted(PACKAGE_VALUES))
    draft.add_argument("--id", dest="entry_id", default=None)
    draft.add_argument("--out", type=Path, default=None)
    _add_db_argument(draft, suppress=True)
    return parser


def _ingest(yaml_dir: Path, db_path: Path) -> int:
    if not yaml_dir.is_dir():
        print(f"FAILED {yaml_dir}: directory does not exist")
        print("success=0 failure=1")
        return 1

    success = 0
    failure = 0
    store = KnowledgeStore(db_path)
    try:
        for path in sorted(yaml_dir.rglob("*.yaml")):
            try:
                entry = store.ingest(path)
            except Exception as exc:  # Continue ingesting independent packages.
                failure += 1
                print(f"FAILED {path}: {exc}")
                continue
            success += 1
            print(f"{entry.id}/{entry.version}/{entry.package}")
    finally:
        store.close()
    print(f"success={success} failure={failure}")
    return 0 if failure == 0 else 1


def _search(query: str, db_path: Path) -> int:
    store = KnowledgeStore(db_path)
    try:
        for hit in store.search(query):
            print(
                "|".join(
                    (
                        hit.entry.title,
                        hit.evidence_level,
                        hit.applicability,
                        hit.source_summary,
                    )
                )
            )
    finally:
        store.close()
    return 0


def _list_entries(package: str | None, db_path: Path) -> int:
    store = KnowledgeStore(db_path)
    try:
        for entry in store.list(package=package):
            print(f"{entry.id}/{entry.version}/{entry.package}")
    finally:
        store.close()
    return 0


def _source_search(source: str, query: str, page_size: int, db_path: Path) -> int:
    adapter_type = SOURCE_ADAPTERS.get(source)
    if adapter_type is None:
        print(f"FAILED unknown source: {source}")
        return 1
    if page_size < 1:
        print("FAILED page-size must be >= 1")
        return 1

    adapter = adapter_type()
    store = KnowledgeStore(db_path)
    try:
        descriptor = adapter.describe()
        if store.get_source(descriptor.source_id) is None:
            store.register_source(descriptor)
        try:
            candidates = adapter.search(query, page_size=page_size)
        except Exception as exc:  # Network/parse failures must stay visible.
            print(f"FAILED {source}: {exc}")
            return 1
        stored = 0
        duplicates = 0
        failures = 0
        for candidate in candidates:
            try:
                saved = store.save_candidate_evidence(candidate)
            except DuplicateEvidenceError:
                duplicates += 1
                continue
            except ValueError as exc:
                failures += 1
                print(f"FAILED {candidate.evidence_id}: {exc}")
                continue
            stored += 1
            print(
                "|".join(
                    (
                        saved.source_record_id,
                        saved.pmid or "",
                        saved.doi or "",
                        saved.evidence_status,
                    )
                )
            )
        print(
            f"retrieved={len(candidates)} stored={stored} "
            f"duplicate={duplicates} failure={failures}"
        )
        return 0 if failures == 0 else 1
    finally:
        store.close()


def _review_candidate(evidence_id: str, status: str, db_path: Path) -> int:
    store = KnowledgeStore(db_path)
    try:
        try:
            updated = store.set_candidate_review_status(evidence_id, status)
        except KeyError:
            print(f"FAILED unknown evidence: {evidence_id}")
            return 1
        except ValueError as exc:
            print(f"FAILED {exc}")
            return 1
        print(f"{updated.evidence_id}|{updated.review_status}")
        return 0
    finally:
        store.close()


def _draft_entry(
    evidence_id: str,
    package: str,
    entry_id: str | None,
    out_path: Path | None,
    db_path: Path,
) -> int:
    """Emit a YAML draft skeleton; the citation is copied, never invented."""

    store = KnowledgeStore(db_path)
    try:
        candidate = store.get_candidate_evidence(evidence_id)
    finally:
        store.close()
    if candidate is None:
        print(f"FAILED unknown evidence: {evidence_id}")
        return 1
    if candidate.review_status != "approved":
        print(
            "FAILED evidence must be reviewed and approved first "
            f"(current: {candidate.review_status})"
        )
        return 1

    if candidate.pmid:
        reference = f"PMID:{candidate.pmid}"
    elif candidate.doi:
        reference = f"DOI:{candidate.doi}"
    else:
        reference = candidate.source_record_id
    slug = re.sub(r"[^a-z0-9]+", "-", candidate.source_record_id.lower()).strip("-")
    document = {
        "id": entry_id or f"draft-{slug}",
        "package": package,
        "title": candidate.claim_summary or "<填写标题>",
        "version": 1,
        "evidence_level": "other",
        "applicability": "<填写适用范围>",
        "content": "<填写知识内容>",
        "source": [
            {
                "kind": "literature",
                "ref": reference,
                "date": candidate.retrieved_at[:10],
            }
        ],
        "approval": {"status": "pending", "by": "<填写批准人>", "revision": "draft"},
    }
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    if out_path is None:
        print(text, end="")
        return 0
    try:
        out_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"FAILED cannot write draft: {exc}")
        return 1
    print(f"wrote {out_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "ingest":
        return _ingest(arguments.yaml_dir, arguments.db)
    if arguments.command == "search":
        return _search(arguments.query, arguments.db)
    if arguments.command == "source-search":
        return _source_search(
            arguments.source, arguments.query, arguments.page_size, arguments.db
        )
    if arguments.command == "review-candidate":
        return _review_candidate(arguments.evidence_id, arguments.status, arguments.db)
    if arguments.command == "draft-entry":
        return _draft_entry(
            arguments.evidence_id,
            arguments.package,
            arguments.entry_id,
            arguments.out,
            arguments.db,
        )
    return _list_entries(arguments.package, arguments.db)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
