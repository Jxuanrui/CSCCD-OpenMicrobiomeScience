import http.client
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import URLError

from Bio import Entrez
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENTREZ_EMAIL = "cardio_artifact_engine@example.com"
REQUEST_INTERVAL_SECONDS = 0.11
RETRY_DELAYS_SECONDS = (1, 3, 6)
RETRYABLE_ENTREZ_ERRORS = (
    http.client.HTTPException,
    URLError,
    ConnectionError,
    TimeoutError,
)
RETRYABLE_NCBI_RUNTIME_ERROR_MARKERS = (
    "backend failed",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "temporarily unavailable",
    "temporary failure",
    "timed out",
    "timeout",
)
RETRYABLE_NCBI_HTTP_STATUS_PATTERN = re.compile(
    r"(?:\bhttp\b[^.\n]{0,80}\b5\d{2}\b|\b5\d{2}\s+status\b)",
    re.IGNORECASE,
)
_PMCID_PATTERN = re.compile(r"^PMC\d+$", re.IGNORECASE)


def search_papers(
    query: str,
    max_results: int = 100,
    filters: dict | None = None,
) -> list[dict]:
    """Search the PMC Open Access subset and return Paper-schema records."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if max_results < 0:
        raise ValueError("max_results must be non-negative")
    if max_results == 0:
        return []

    _configure_entrez()
    search_record = _entrez_call(
        lambda: _read_entrez_record(
            Entrez.esearch(
                db="pmc",
                term=_build_search_query(query, filters),
                retmax=max_results,
                retmode="xml",
            )
        ),
        "PMC search",
    )
    pmcids = [
        normalized
        for pmcid in search_record.get("IdList", [])
        if (normalized := _normalize_search_pmcid(pmcid)) is not None
    ]
    if not pmcids:
        return []
    return _fetch_pmc_metadata(pmcids)


def fetch_fulltext(pmcid: str) -> str | None:
    """Fetch OA full text XML for a PMCID, returning None when unavailable."""
    normalized_pmcid = _normalize_pmcid(pmcid)
    if normalized_pmcid is None:
        return None

    _configure_entrez()
    try:
        content = _entrez_call(
            lambda: _read_entrez_text(
                Entrez.efetch(
                    db="pmc",
                    id=normalized_pmcid,
                    rettype="full",
                    retmode="xml",
                )
            ),
            f"PMC full text {normalized_pmcid}",
        )
    except Exception as error:
        logging.warning(
            "PMC full text unavailable for %s (%s)",
            normalized_pmcid,
            type(error).__name__,
        )
        return None
    return content or None


def fetch_paper_metadata(pmid: str) -> dict:
    """Fetch detailed PubMed metadata for one PMID."""
    normalized_pmid = str(pmid).strip()
    if not normalized_pmid or not normalized_pmid.isdigit():
        raise ValueError("pmid must be a numeric string")

    _configure_entrez()
    return _entrez_call(
        lambda: _parse_article_xml(
            _read_entrez_text(
                Entrez.efetch(
                    db="pubmed",
                    id=normalized_pmid,
                    retmode="xml",
                )
            )
        ),
        f"PubMed metadata {normalized_pmid}",
    )


def _configure_entrez() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("NCBI_API_KEY")
    # 吸收适配（2026-09-21）：无 key 时降速到 NCBI 公共限速（3 req/s）而非硬拒绝，
    # 与 KG 侧 fetch_pubtator 的无 key 用法对齐；配置 key 后自动恢复原 0.11s 间隔。
    global REQUEST_INTERVAL_SECONDS
    Entrez.email = os.getenv("NCBI_EMAIL") or ENTREZ_EMAIL
    if api_key:
        Entrez.api_key = api_key
    else:
        REQUEST_INTERVAL_SECONDS = 0.4
        logging.getLogger(__name__).warning(
            "NCBI_API_KEY 未配置，按公共限速 3 req/s 运行（建议申请 key 提速至 10 req/s）"
        )


def _build_search_query(query: str, filters: dict | None) -> str:
    query = query.strip()
    if "[" in query:
        query_term = f"({query})"
    else:
        keywords = [keyword.strip() for keyword in query.split() if keyword.strip()]
        query_term = " AND ".join(f'"{keyword}"[tiab]' for keyword in keywords)

    terms = [query_term, "open access[filter]"]
    filters = filters or {}

    journals = filters.get("journals", filters.get("journal"))
    if isinstance(journals, str):
        journals = [journals]
    if journals:
        journal_terms = [f'"{str(journal).strip()}"[Journal]' for journal in journals]
        terms.append("(" + " OR ".join(journal_terms) + ")")

    year_start = filters.get("year_start", filters.get("min_year"))
    year_end = filters.get("year_end", filters.get("max_year"))
    year_range = filters.get("year_range")
    if year_range and (year_start is None and year_end is None):
        year_start, year_end = year_range
    if year_start is not None or year_end is not None:
        start = str(year_start) if year_start is not None else "1800"
        end = str(year_end) if year_end is not None else "3000"
        terms.append(f"{start}:{end}[dp]")
    return " AND ".join(terms)


def _fetch_pmc_metadata(pmcids: list[str]) -> list[dict]:
    content = _entrez_call(
        lambda: _read_entrez_text(
            Entrez.efetch(
                db="pmc",
                id=",".join(pmcids),
                rettype="full",
                retmode="xml",
            )
        ),
        "PMC metadata",
    )
    records = _parse_article_collection(content)
    records_by_pmcid = {record.get("pmcid"): record for record in records}
    return [
        records_by_pmcid[pmcid]
        for pmcid in pmcids
        if pmcid in records_by_pmcid
    ]


def _entrez_call(operation, description: str):
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            result = operation()
            time.sleep(REQUEST_INTERVAL_SECONDS)
            return result
        except Exception as error:
            if not _is_retryable_entrez_error(error):
                raise
            if attempt == len(RETRY_DELAYS_SECONDS):
                raise
            delay = RETRY_DELAYS_SECONDS[attempt]
            logging.warning(
                "Transient NCBI request error for %s (%s); retrying in %s seconds",
                description,
                type(error).__name__,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(f"{description} failed")


def _is_retryable_entrez_error(error: Exception) -> bool:
    if isinstance(error, RETRYABLE_ENTREZ_ERRORS):
        return True
    if not isinstance(error, RuntimeError):
        return False
    message = " ".join(str(error).split()).casefold()
    return bool(RETRYABLE_NCBI_HTTP_STATUS_PATTERN.search(message)) or any(
        marker in message for marker in RETRYABLE_NCBI_RUNTIME_ERROR_MARKERS
    )


def _read_entrez_record(handle):
    try:
        with handle:
            return Entrez.read(handle)
    except TypeError:
        return Entrez.read(handle)


def _read_entrez_text(handle) -> str:
    try:
        with handle:
            content = handle.read()
    except TypeError:
        content = handle.read()
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _parse_article_collection(content: str) -> list[dict]:
    root = ET.fromstring(content)
    articles = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"article", "PubmedArticle"}
    ]
    return [_parse_article(article) for article in articles]


def _parse_article_xml(content: str) -> dict:
    articles = _parse_article_collection(content)
    if not articles:
        raise ValueError("NCBI returned no PubMed article metadata")
    return articles[0]


def _parse_article(article: ET.Element) -> dict:
    identifier_tags = ("article-id", "ArticleId")
    pmid = _first_text(article, "PMID") or _first_text(
        article, identifier_tags, "pmid"
    )
    pmcid = _first_text(article, identifier_tags, "pmc") or _first_text(
        article, identifier_tags, "pmcid"
    )
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    doi = _first_text(article, identifier_tags, "doi")
    title = _first_text(article, ("article-title", "ArticleTitle"))
    journal = _first_text(article, ("journal-title", "Title"))
    year = _first_year(article)
    paper_id = doi or pmid or pmcid
    return {
        "paper_id": paper_id,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "title": title,
        "journal": journal,
        "year": year,
    }


def _first_text(
    element: ET.Element,
    tag_name: str | tuple[str, ...],
    attribute_value: str | None = None,
):
    tag_names = {tag_name} if isinstance(tag_name, str) else set(tag_name)
    for child in element.iter():
        if _local_name(child.tag) not in tag_names:
            continue
        if attribute_value:
            child_attribute = child.attrib.get("pub-id-type") or child.attrib.get(
                "IdType"
            )
            if not child_attribute or (
                child_attribute.casefold() != attribute_value.casefold()
            ):
                continue
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return None


def _first_year(article: ET.Element):
    for tag_name in ("pub-date", "article-date", "PubDate", "ArticleDate"):
        for element in article.iter():
            if _local_name(element.tag) != tag_name:
                continue
            year = _first_text(element, ("year", "Year"))
            if year and year.isdigit():
                return int(year)
            medline_date = _first_text(element, "MedlineDate")
            if medline_date:
                match = re.search(r"\b(?:18|19|20)\d{2}\b", medline_date)
                if match:
                    return int(match.group(0))
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_pmcid(pmcid: str) -> str | None:
    normalized = str(pmcid).strip().upper()
    return normalized if _PMCID_PATTERN.fullmatch(normalized) else None


def _normalize_search_pmcid(pmcid: str) -> str | None:
    normalized = str(pmcid).strip().upper()
    if normalized.isdigit():
        normalized = f"PMC{normalized}"
    return normalized if _PMCID_PATTERN.fullmatch(normalized) else None
