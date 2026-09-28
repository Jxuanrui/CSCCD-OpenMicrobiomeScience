"""Run the small-sample artifact discovery batch and independent verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


ENGINE_ROOT = Path(__file__).resolve().parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from classifier import evidence as evidence_checks  # noqa: E402
from classifier import score  # noqa: E402
from extractor.mention_extractor import extract_mentions  # noqa: E402
from providers import github_provider, pmc_provider, zenodo_provider  # noqa: E402


DEFAULT_DB_PATH = ENGINE_ROOT / "data" / "artifact_engine.db"
DOMAIN_SAMPLE_SIZE = 10
DOMAIN_SEARCH_SIZE = 12
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_embedding_model = None


def _get_embedding_model():
    """Lazily load the sentence-transformers model (already cached locally).

    Short READMEs with different wording than the paper title (e.g. "A package
    for processing 3C/Hi-C data" vs. "map3C: a computational tool for
    processing multiomic single-cell Hi-C data") fail the lexical keyword
    overlap check in evidence.check_readme_contains_title but are still
    semantically close; embedding similarity catches those cases.

    吸收适配（2026-09-21）：未安装 sentence_transformers 时优雅降级——只走词法
    证据，个别语义匹配项可能落入 review_queue 而非 confirmed（偏保守，可接受）。
    """
    global _embedding_model
    if _embedding_model is None:
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except ImportError:
            _embedding_model = False
    return _embedding_model or None
SUPPORTED_SOURCES = {"github", "zenodo"}
STATUS_ORDER = {
    "low_confidence_rejected": 0,
    "review_queue": 1,
    "confirmed": 2,
}
DDL = """
CREATE TABLE IF NOT EXISTS Paper (
    paper_id TEXT PRIMARY KEY,
    doi TEXT,
    pmid TEXT,
    title TEXT NOT NULL,
    journal TEXT,
    year INTEGER
);

CREATE TABLE IF NOT EXISTS Repository (
    repo_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    owner TEXT,
    name TEXT,
    stars INTEGER,
    created_date TEXT,
    license TEXT
);

CREATE TABLE IF NOT EXISTS Artifact (
    artifact_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES Paper(paper_id),
    repo_id TEXT REFERENCES Repository(repo_id),
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN
        ('analysis_code','workflow','software','dataset','notebook','documentation')),
    language TEXT,
    description TEXT,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('confirmed','review_queue','low_confidence_rejected'))
);
"""


def init_database(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the three design-schema tables and return an open connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(DDL)
    connection.commit()
    return connection


def _memory_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(DDL)
    return connection


def _value(record: Mapping | None, *names: str, default=None):
    if isinstance(record, Mapping):
        for name in names:
            if record.get(name) is not None:
                return record[name]
    return default


def _paper_key(paper: Mapping) -> str:
    return str(_value(paper, "pmcid", "paper_id", "doi", "pmid") or "").strip()


def _paper_authors(paper: Mapping) -> list[str]:
    authors = _value(paper, "authors", default=[])
    if isinstance(authors, list):
        return [str(author) for author in authors if str(author).strip()]
    return []


def _paper_text(paper: Mapping) -> str:
    return " ".join(
        str(_value(paper, key, default="") or "")
        for key in ("title", "abstract", "abstract_text", "fulltext")
    )


def _extract_fulltext_context(fulltext: str | None) -> dict[str, Any]:
    """Extract plain text, abstract, and author names from PMC XML/text."""
    if not fulltext:
        return {"text": "", "abstract": "", "authors": []}
    try:
        root = ET.fromstring(fulltext)
        text = " ".join(" ".join(root.itertext()).split())
        abstract_nodes = [
            node for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].casefold() == "abstract"
        ]
        abstract = " ".join(
            " ".join(" ".join(node.itertext()).split())
            for node in abstract_nodes
        )
        authors = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].casefold() not in {"contrib", "author"}:
                continue
            name = " ".join(node.itertext()).split()
            if name:
                candidate = " ".join(name)
                # A <contrib> node's itertext() naturally includes the ORCID
                # ID and full affiliation address alongside the name (Phase 5
                # batch-005 review follow-up: PMC13069690's SpNeigh paper has
                # three authors whose contrib text -- name + ORCID + a
                # two-line institute address -- all land at 238/303/575
                # characters), routinely exceeding a couple hundred
                # characters. The old <200-char cutoff silently dropped every
                # author on papers like this, leaving owner_matches_author
                # permanently unreachable ("paper author list is
                # unavailable") even when the artifact owner's GitHub
                # username is a literal match for the author's name. The
                # design already relies on these affiliation-bearing strings
                # for matching (see check_owner_matches_author's
                # institutional-word ignore list for the scFoundation case),
                # so there is no length-based signal here worth gating on;
                # only de-duplicate.
                if candidate not in authors:
                    authors.append(candidate)
        return {"text": text, "abstract": abstract, "authors": authors}
    except ET.ParseError:
        return {"text": fulltext, "abstract": "", "authors": []}


def _paper_with_context(paper: Mapping, context: Mapping) -> dict:
    enriched = dict(paper)
    if context.get("abstract"):
        enriched["abstract"] = context["abstract"]
    if context.get("authors"):
        enriched["authors"] = context["authors"]
    if context.get("text"):
        enriched["fulltext"] = context["text"]
    return enriched


CODE_HINT_TERMS = (
    "github", "zenodo", "code availability", "source code",
    "software availability", "data availability",
)


def _domain_phrase_query(domain: str) -> str:
    """Build a bracket-syntax phrase query so PMC matches the domain as a phrase,
    not as independently-scattered single words (Phase 2 lesson: bare multi-word
    queries get loosely term-mapped by PMC and return irrelevant noise)."""
    hint_clause = " OR ".join(f'"{term}"[tiab]' for term in CODE_HINT_TERMS)
    return f'"{domain}"[tiab] AND ({hint_clause})'


def sample_papers() -> list[dict]:
    """Search each configured evidence domain, prioritize link hints, and dedupe."""
    selected: dict[str, dict] = {}
    for domain, patterns in evidence_checks.DOMAIN_KEYWORDS.items():
        query = _domain_phrase_query(domain)
        try:
            records = pmc_provider.search_papers(
                query, max_results=DOMAIN_SEARCH_SIZE
            )
        except Exception as error:
            logging.warning("PMC search failed for %s: %s", domain, error)
            continue
        records = sorted(
            records,
            key=lambda record: (bool(re.search(
                r"github\.com|zenodo\.org|10\.5281/zenodo",
                _paper_text(record),
                re.IGNORECASE,
            )), str(_value(record, "title", default=""))),
            reverse=True,
        )
        for paper in records[:DOMAIN_SAMPLE_SIZE]:
            paper_id = _paper_key(paper)
            if paper_id:
                selected.setdefault(paper_id, dict(paper))
    return list(selected.values())


def _candidate_source(mention: Mapping) -> str | None:
    normalized_url = str(mention.get("normalized_url") or "")
    url_type = str(mention.get("url_type") or "")
    if url_type == "github" or "github.com/" in normalized_url.casefold():
        return "github"
    if url_type.startswith("zenodo") or "zenodo.org/" in normalized_url.casefold():
        return "zenodo"
    return None


def _candidate_from_mention(mention: Mapping):
    source = _candidate_source(mention)
    url = mention.get("normalized_url")
    if source == "github":
        return github_provider.candidate_from_url(str(url))
    if source == "zenodo":
        return zenodo_provider.candidate_from_url(str(url))
    raise ValueError(f"unsupported candidate source: {url}")


def _repository_id(source: str, url: str) -> str:
    return hashlib.sha256(f"{source}:{url}".encode()).hexdigest()[:32]


def _artifact_id(paper_id: str, source: str, url: str) -> str:
    return hashlib.sha256(f"{paper_id}:{source}:{url}".encode()).hexdigest()[:32]


def _metadata_text(metadata: Mapping) -> str:
    return " ".join(
        str(metadata.get(key) or "")
        for key in ("title", "description", "readme")
    )


def build_evidence(paper: Mapping, metadata: Mapping, paper_text: str) -> list[dict]:
    """Run every Phase 3 evidence check with normalized provider metadata."""
    authors = _paper_authors(paper)
    paper_title = str(_value(paper, "title", default="") or "")
    evidence = [
        evidence_checks.check_doi_cited_in_text(paper_text, dict(metadata)),
        evidence_checks.check_explicit_deposit_statement(paper_text, dict(metadata)),
        evidence_checks.check_owner_matches_author(dict(metadata), authors),
        evidence_checks.check_readme_contains_title(
            _metadata_text(metadata), paper_title, embedding_model=_get_embedding_model()
        ),
        evidence_checks.check_domain_keyword_hit(
            f"{paper_title} {paper_text} {_metadata_text(metadata)}"
        ),
        evidence_checks.check_tool_keyword_hit(_metadata_text(metadata)),
    ]
    evidence.extend(
        evidence_checks.check_dir_structure(
            metadata.get("top_level_directories") or []
        )
    )
    return evidence


def _upsert_paper(connection: sqlite3.Connection, paper: Mapping) -> None:
    connection.execute(
        """INSERT INTO Paper(paper_id, doi, pmid, title, journal, year)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(paper_id) DO UPDATE SET
             doi=excluded.doi, pmid=excluded.pmid, title=excluded.title,
             journal=excluded.journal, year=excluded.year""",
        (
            _paper_key(paper),
            _value(paper, "doi"),
            _value(paper, "pmid"),
            str(_value(paper, "title", default="Untitled") or "Untitled"),
            _value(paper, "journal"),
            _value(paper, "year"),
        ),
    )


def _sqlite_scalar(value: Any) -> str | int | float | None:
    """Coerce provider metadata values (dicts/lists) to a SQLite-bindable scalar.

    Zenodo's ``license``/``creators`` fields and similar provider metadata can be
    dicts or lists (e.g. ``{"id": "mit-license"}``); sqlite3 only accepts
    str/int/float/bytes/None as bind parameters.
    """
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, Mapping):
        return str(
            value.get("id") or value.get("name") or value.get("spdx_id") or value
        )
    return str(value)


def _upsert_repository(
    connection: sqlite3.Connection, source: str, url: str, metadata: Mapping
) -> str:
    repo_id = _repository_id(source, url)
    owner = metadata.get("owner")
    if not owner and metadata.get("creators"):
        creators = metadata.get("creators")
        owner = ", ".join(
            str(item.get("name") or item.get("creator") or "")
            for item in creators if isinstance(item, Mapping)
        ) or None
    owner = _sqlite_scalar(owner)
    connection.execute(
        """INSERT INTO Repository(repo_id, provider, owner, name, stars, created_date, license)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(repo_id) DO UPDATE SET provider=excluded.provider,
             owner=excluded.owner, name=excluded.name, stars=excluded.stars,
             created_date=excluded.created_date, license=excluded.license""",
        (
            repo_id, source, owner,
            _sqlite_scalar(metadata.get("name") or metadata.get("title")),
            _sqlite_scalar(metadata.get("stars")),
            _sqlite_scalar(metadata.get("created_date")),
            _sqlite_scalar(metadata.get("license")),
        ),
    )
    return repo_id


def _write_artifact(
    connection: sqlite3.Connection, paper: Mapping, source: str, url: str,
    metadata: Mapping, classification: Mapping, repo_id: str,
) -> None:
    connection.execute(
        """INSERT INTO Artifact(artifact_id, paper_id, repo_id, url, source,
           artifact_type, language, description, confidence, evidence, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(artifact_id) DO UPDATE SET repo_id=excluded.repo_id,
             artifact_type=excluded.artifact_type, language=excluded.language,
             description=excluded.description, confidence=excluded.confidence,
             evidence=excluded.evidence, status=excluded.status""",
        (
            _artifact_id(_paper_key(paper), source, url), _paper_key(paper), repo_id,
            url, source, classification["artifact_type"],
            _sqlite_scalar(metadata.get("language")),
            _sqlite_scalar(metadata.get("description")), classification["confidence"],
            json.dumps(classification["evidence"], ensure_ascii=False, sort_keys=True),
            classification["status"],
        ),
    )


def process_paper(connection: sqlite3.Connection | None, paper: Mapping) -> dict[str, int]:
    """Process one paper; individual candidate failures are isolated."""
    stats = Counter(
        papers=1, papers_failed=0, candidates=0, artifacts=0, failed=0, skipped=0
    )
    paper_id = _paper_key(paper)
    if not paper_id or not _value(paper, "title", default=""):
        stats["papers_failed"] += 1
        return dict(stats)
    try:
        fulltext = pmc_provider.fetch_fulltext(_value(paper, "pmcid", default=""))
        context = _extract_fulltext_context(fulltext)
        enriched_paper = _paper_with_context(paper, context)
        paper_text = context["text"] or _paper_text(enriched_paper)
        mentions = extract_mentions(paper_text)
        stats["candidates"] = len(mentions)
        if connection is not None:
            _upsert_paper(connection, enriched_paper)
        for mention in mentions:
            try:
                source = _candidate_source(mention)
                if source not in SUPPORTED_SOURCES:
                    # Not a real failure: generic literature-citation DOIs are
                    # expected and out of scope for this MVP's providers
                    # (GitHub/Zenodo only; see design.md Open follow-ups).
                    stats["skipped"] += 1
                    continue
                candidate = _candidate_from_mention(mention)
                metadata = (
                    github_provider.fetch_metadata(candidate)
                    if source == "github"
                    else zenodo_provider.fetch_metadata(candidate)
                )
                # A GitHub repo can be renamed/transferred between the paper's
                # publication and when this pipeline runs (Phase 5 batch-004
                # review follow-up: PMC11058068's pyaging repo moved from
                # rsinghlab/pyaging, the URL actually cited in the paper text,
                # to lucascamillomd/pyaging, the owner's personal account --
                # itself the strongest possible owner_matches_author signal).
                # github_provider.fetch_metadata always reports the *current*
                # canonical html_url, so without this the identifier the paper
                # text actually cites is lost entirely, making
                # doi_cited_in_text/explicit_deposit_statement silently
                # unreachable even though the citation is right there. Same
                # class of provider-current-state-vs-paper-cited-state drift
                # as the Zenodo record_id fix, just on the GitHub side.
                metadata = {**metadata, "cited_url": str(mention.get("normalized_url") or "")}
                evidence = build_evidence(enriched_paper, metadata, paper_text)
                classification = score.classify_artifact(
                    enriched_paper, metadata, evidence
                )
                if connection is not None:
                    repo_id = _upsert_repository(
                        connection, source, str(mention["normalized_url"]), metadata
                    )
                    _write_artifact(
                        connection, enriched_paper, source,
                        str(mention["normalized_url"]), metadata, classification, repo_id,
                    )
                stats["artifacts"] += 1
            except Exception as error:
                stats["failed"] += 1
                logging.warning(
                    "candidate failed for paper %s (%s): %s",
                    paper_id, type(error).__name__, error,
                )
        if connection is not None:
            connection.commit()
    except Exception as error:
        stats["papers_failed"] += 1
        logging.warning("paper failed for %s (%s): %s", paper_id, type(error).__name__, error)
    return dict(stats)


def run_batch(
    limit: int | None = None,
    dry_run: bool = False,
    db_path: str | Path = DEFAULT_DB_PATH,
    papers_file: str | Path | None = None,
    offset: int = 0,
    count: int | None = None,
) -> dict[str, int]:
    """Run sampling (or a cached paper slice) and first-round classification."""
    if papers_file is not None:
        with open(papers_file, encoding="utf-8") as handle:
            papers = json.load(handle)
        papers = papers[offset:]
        if count is not None:
            papers = papers[: max(count, 0)]
    else:
        papers = sample_papers()
    if limit is not None:
        papers = papers[: max(limit, 0)]
    connection = _memory_database() if dry_run else init_database(db_path)
    total = Counter(papers_selected=len(papers))
    for paper in papers:
        total.update(process_paper(connection, paper))
    if not dry_run:
        connection.commit()
    connection.close()
    result = dict(total)
    print(json.dumps({"mode": "dry-run" if dry_run else "write", **result}, ensure_ascii=False, sort_keys=True))
    return result


def _parse_verification_output(response_text: str) -> dict:
    parsed = score._parse_gemini_output(response_text)
    status = parsed.get("status")
    confidence = float(parsed.get("confidence"))
    if status not in STATUS_ORDER:
        raise ValueError("Gemini returned an invalid status")
    if not 0 <= confidence <= 1:
        raise ValueError("Gemini confidence must be in [0, 1]")
    return {
        "status": status,
        "confidence": round(confidence, 3),
        "reason": str(parsed.get("reason") or ""),
    }


def independent_gemini_verification(paper: Mapping, artifact: Mapping) -> dict:
    """Ask Gemini for an independent status without exposing round-one status."""
    load_dotenv(score.PROJECT_ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    base_url = os.getenv(
        "GOOGLE_GEMINI_BASE_URL", score.GEMINI_DEFAULT_BASE_URL
    ).rstrip("/")
    if not base_url.endswith("/v1beta"):
        base_url = f"{base_url}/v1beta"
    evidence = json.loads(str(artifact["evidence"]))
    payload = {
        "paper": {
            "title": paper["title"],
            "abstract": paper.get("abstract") or paper.get("abstract_text") or "",
        },
        "artifact": {
            "paper_id": artifact["paper_id"], "url": artifact["url"],
            "source": artifact["source"], "artifact_type": artifact["artifact_type"],
        },
        "evidence": evidence,
    }
    prompt = (
        "Independently classify whether this research artifact is attributable to "
        "the paper authors. Do not assume any prior classification. Use only the "
        "structured evidence and paper information. Return JSON only as "
        '{"status":"confirmed|review_queue|low_confidence_rejected",'
        '"confidence":0.0,"reason":"brief reason"}.\n\n'
        + json.dumps(payload, ensure_ascii=False, default=str)
    )
    response = requests.post(
        f"{base_url}/models/{score.GEMINI_MODEL}:generateContent",
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        },
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        timeout=score.GEMINI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw = response.json()
    text = raw["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_verification_output(text)


def is_disagreement(first_status: str, first_confidence: float, second_status: str, second_confidence: float) -> bool:
    """Apply the Phase 4 extreme-status and adjacent-confidence rules."""
    if first_status == second_status:
        return False
    if abs(STATUS_ORDER[first_status] - STATUS_ORDER[second_status]) == 2:
        return True
    return abs(float(first_confidence) - float(second_confidence)) >= 0.15


def _paper_for_verification(connection: sqlite3.Connection, paper_id: str) -> dict:
    row = connection.execute(
        "SELECT paper_id, doi, pmid, title, journal, year FROM Paper WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"paper {paper_id} not found")
    paper = dict(zip(("paper_id", "doi", "pmid", "title", "journal", "year"), row))
    fulltext_id = paper.get("paper_id") if str(paper.get("paper_id", "")).upper().startswith("PMC") else None
    if fulltext_id:
        fulltext = pmc_provider.fetch_fulltext(fulltext_id)
        context = _extract_fulltext_context(fulltext)
        paper.update({"abstract": context.get("abstract", ""), "authors": context.get("authors", [])})
    return paper


def cross_verify(limit: int | None = None, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """Independently verify stored artifacts and print only disagreement records."""
    connection = init_database(db_path)
    query = "SELECT artifact_id, paper_id, url, source, artifact_type, confidence, evidence, status FROM Artifact ORDER BY artifact_id"
    rows = connection.execute(query).fetchall()
    if limit is not None:
        rows = rows[: max(limit, 0)]
    disagreements = []
    failures = 0
    for row in rows:
        keys = ("artifact_id", "paper_id", "url", "source", "artifact_type", "confidence", "evidence", "status")
        artifact = dict(zip(keys, row))
        try:
            paper = _paper_for_verification(connection, artifact["paper_id"])
            second = independent_gemini_verification(paper, artifact)
            if is_disagreement(artifact["status"], artifact["confidence"], second["status"], second["confidence"]):
                disagreements.append({
                    "paper_id": artifact["paper_id"],
                    "first_round": {"status": artifact["status"], "confidence": artifact["confidence"]},
                    "second_round": second,
                })
        except Exception as error:
            failures += 1
            logging.warning("cross verification failed for artifact %s: %s", artifact["artifact_id"], error)
    connection.close()
    print(json.dumps({"checked": len(rows), "failed": failures, "disagreements": disagreements}, ensure_ascii=False, indent=2))
    return disagreements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "cross-verify"), default="run")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--papers-file", default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.command == "cross-verify":
        if args.dry_run:
            parser.error("--dry-run is only supported for the batch run")
        cross_verify(args.limit, args.db)
    else:
        run_batch(args.limit, args.dry_run, args.db, args.papers_file, args.offset, args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
