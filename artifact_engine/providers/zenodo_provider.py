import logging
import re
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

import requests


ZENODO_API_URL = "https://zenodo.org/api"
REQUEST_TIMEOUT_SECONDS = 20
RETRY_DELAYS_SECONDS = (2, 5, 10)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_SEARCH_RESULTS = 25
ZENODO_DOI_PATTERN = re.compile(r"10\.5281/zenodo\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ArtifactCandidate:
    url: str
    source: str
    raw_metadata: dict


class ZenodoProviderError(RuntimeError):
    pass


class ZenodoNotFoundError(ZenodoProviderError):
    pass


def candidate_from_url(url: str) -> ArtifactCandidate:
    record_id = _record_id_from_url(url)
    return ArtifactCandidate(
        url=f"https://zenodo.org/records/{record_id}",
        source="zenodo",
        raw_metadata={"id": record_id},
    )


def find_candidates(paper) -> list[ArtifactCandidate]:
    doi = _paper_value(paper, "doi", "DOI")
    if not doi:
        return []

    normalized_doi = re.sub(
        r"^https?://(?:dx\.)?doi\.org/", "", str(doi), flags=re.IGNORECASE
    ).strip()
    search_queries = (
        f'metadata.related_identifiers.identifier:"{normalized_doi}"',
        f'related.identifier:"{normalized_doi}"',
    )
    candidates_by_url = {}

    for query in search_queries:
        search_result = _request_json(
            "GET",
            f"{ZENODO_API_URL}/records",
            params={"q": query, "size": MAX_SEARCH_RESULTS},
        )
        for record in search_result.get("hits", {}).get("hits", []):
            record_id = record.get("id")
            if record_id is None:
                continue
            candidate = ArtifactCandidate(
                url=f"https://zenodo.org/records/{record_id}",
                source="zenodo",
                raw_metadata=record,
            )
            candidates_by_url.setdefault(candidate.url, candidate)

    return list(candidates_by_url.values())


def fetch_metadata(candidate: ArtifactCandidate) -> dict:
    record_id = _candidate_record_id(candidate)
    record = _request_json("GET", f"{ZENODO_API_URL}/records/{record_id}")
    metadata = record.get("metadata") or {}

    return {
        "url": (record.get("links") or {}).get("self_html")
        or f"https://zenodo.org/records/{record_id}",
        "record_id": str(record_id),
        "doi": record.get("doi") or metadata.get("doi"),
        "title": metadata.get("title"),
        "description": metadata.get("description"),
        "creators": metadata.get("creators") or [],
        "related_identifiers": metadata.get("related_identifiers") or [],
        "license": metadata.get("license"),
        "raw_record": record,
    }


def _paper_value(paper, *names: str):
    if isinstance(paper, Mapping):
        for name in names:
            value = paper.get(name)
            if value:
                return value
        return None

    for name in names:
        value = getattr(paper, name, None)
        if value:
            return value
    return None


def _candidate_record_id(candidate: ArtifactCandidate) -> str:
    raw_metadata = getattr(candidate, "raw_metadata", {}) or {}
    record_id = raw_metadata.get("id")
    if record_id is not None:
        return str(record_id)
    return _record_id_from_url(candidate.url)


def _record_id_from_url(url: str) -> str:
    value = str(url).strip()
    doi_match = ZENODO_DOI_PATTERN.search(value)
    if doi_match:
        return doi_match.group(1)

    parsed_url = urlparse(value)
    hostname = (parsed_url.hostname or "").casefold()
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if hostname not in {"zenodo.org", "www.zenodo.org"}:
        raise ValueError("URL is not a valid Zenodo record URL")
    if len(path_parts) < 2 or path_parts[0] not in {"record", "records"}:
        raise ValueError("URL is not a valid Zenodo record URL")
    if not path_parts[1].isdigit():
        raise ValueError("Zenodo record ID must be numeric")
    return path_parts[1]


def _request_json(method: str, url: str, **kwargs):
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            response = requests.request(
                method,
                url,
                headers={"User-Agent": "Cardiovascular-Database-Artifact-Engine"},
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except requests.RequestException as error:
            if attempt == len(RETRY_DELAYS_SECONDS):
                raise ZenodoProviderError(
                    f"Zenodo API request failed after retries: {type(error).__name__}"
                ) from error
            delay = RETRY_DELAYS_SECONDS[attempt]
            logging.warning(
                "Transient Zenodo request error (%s); retrying in %s seconds",
                type(error).__name__,
                delay,
            )
            time.sleep(delay)
            continue

        if response.status_code == 404:
            raise ZenodoNotFoundError("Zenodo record or resource was not found")

        if (
            response.status_code in RETRYABLE_STATUS_CODES
            and attempt < len(RETRY_DELAYS_SECONDS)
        ):
            delay = _retry_delay(response, attempt)
            logging.warning(
                "Zenodo API returned HTTP %s; retrying in %s seconds",
                response.status_code,
                delay,
            )
            time.sleep(delay)
            continue
        if response.status_code == 429:
            raise ZenodoProviderError("Zenodo API rate limit was exceeded")
        if response.status_code >= 400:
            raise ZenodoProviderError(
                f"Zenodo API request failed with HTTP {response.status_code}"
            )

        try:
            return response.json()
        except requests.JSONDecodeError as error:
            raise ZenodoProviderError("Zenodo API returned invalid JSON") from error

    raise ZenodoProviderError("Zenodo API request failed")


def _retry_delay(response: requests.Response, attempt: int) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return max(int(retry_after), 1)
    return RETRY_DELAYS_SECONDS[attempt]
