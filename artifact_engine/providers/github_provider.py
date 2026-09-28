import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 20
RETRY_DELAYS_SECONDS = (2, 5, 10)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_SEARCH_RESULTS_PER_QUERY = 10
TITLE_STOP_WORDS = {
    "about",
    "after",
    "among",
    "analysis",
    "based",
    "from",
    "into",
    "study",
    "that",
    "their",
    "through",
    "using",
    "with",
}


@dataclass(frozen=True)
class ArtifactCandidate:
    url: str
    source: str
    raw_metadata: dict


class GitHubProviderError(RuntimeError):
    pass


class GitHubNotFoundError(GitHubProviderError):
    pass


def candidate_from_url(url: str) -> ArtifactCandidate:
    owner, repository = _parse_repository_url(url)
    canonical_url = f"https://github.com/{owner}/{repository}"
    return ArtifactCandidate(
        url=canonical_url,
        source="github",
        raw_metadata={"owner": owner, "name": repository},
    )


def github_url_to_candidate(url: str) -> ArtifactCandidate:
    return candidate_from_url(url)


def find_candidates(paper) -> list[ArtifactCandidate]:
    queries = _build_search_queries(paper)
    candidates_by_url = {}

    for query in queries:
        search_result = _request_json(
            "GET",
            f"{GITHUB_API_URL}/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": MAX_SEARCH_RESULTS_PER_QUERY,
            },
        )
        for repository in search_result.get("items", []):
            try:
                candidate = candidate_from_url(repository["html_url"])
            except (KeyError, TypeError, ValueError):
                continue
            candidates_by_url.setdefault(
                candidate.url,
                ArtifactCandidate(
                    url=candidate.url,
                    source="github",
                    raw_metadata=repository,
                ),
            )

    return list(candidates_by_url.values())


def fetch_metadata(candidate: ArtifactCandidate) -> dict:
    owner, repository = _candidate_repository(candidate)
    repository_data = _request_json(
        "GET",
        f"{GITHUB_API_URL}/repos/{owner}/{repository}",
    )
    readme_data = _request_json(
        "GET",
        f"{GITHUB_API_URL}/repos/{owner}/{repository}/readme",
        allow_not_found=True,
    )
    contents_data = _request_json(
        "GET",
        f"{GITHUB_API_URL}/repos/{owner}/{repository}/contents",
        allow_not_found=True,
    )

    owner_data = repository_data.get("owner") or {}
    license_data = repository_data.get("license") or {}
    top_level_entries = contents_data if isinstance(contents_data, list) else []
    top_level_directories = [
        entry.get("name")
        for entry in top_level_entries
        if entry.get("type") == "dir" and entry.get("name")
    ]

    return {
        "url": repository_data.get("html_url")
        or f"https://github.com/{owner}/{repository}",
        "owner": owner_data.get("login") or owner,
        "name": repository_data.get("name") or repository,
        "stars": repository_data.get("stargazers_count", 0),
        "created_date": repository_data.get("created_at"),
        "license": license_data.get("spdx_id") or license_data.get("name"),
        "description": repository_data.get("description"),
        "readme": _decode_readme(readme_data),
        "top_level_directories": top_level_directories,
        "top_level_entries": top_level_entries,
        "raw_repository": repository_data,
    }


def _build_search_queries(paper) -> list[str]:
    doi = _paper_value(paper, "doi", "DOI")
    title = _paper_value(paper, "title", "Title")
    pmid = _paper_value(paper, "pmid", "PMID")
    queries = []

    if doi:
        normalized_doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(doi)).strip()
        queries.append(f'"{normalized_doi}" in:readme,description')
    if title:
        title_keywords = _title_keywords(str(title))
        if title_keywords:
            queries.append(f"{' '.join(title_keywords)} in:name,description,readme")
    if pmid:
        queries.append(f'"PMID {str(pmid).strip()}" in:readme,description')

    return list(dict.fromkeys(queries))


def _title_keywords(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", title)
    informative_words = [
        word
        for word in words
        if len(word) >= 4 and word.casefold() not in TITLE_STOP_WORDS
    ]
    return informative_words[:8]


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


def _candidate_repository(candidate: ArtifactCandidate) -> tuple[str, str]:
    raw_metadata = getattr(candidate, "raw_metadata", {}) or {}
    owner = raw_metadata.get("owner")
    if isinstance(owner, Mapping):
        owner = owner.get("login")
    repository = raw_metadata.get("name")
    if owner and repository:
        return str(owner), str(repository)
    return _parse_repository_url(candidate.url)


def _parse_repository_url(url: str) -> tuple[str, str]:
    parsed_url = urlparse(str(url).strip())
    hostname = (parsed_url.hostname or "").casefold()
    path_parts = [part for part in parsed_url.path.split("/") if part]

    if hostname in {"github.com", "www.github.com"} and len(path_parts) >= 2:
        owner, repository = path_parts[:2]
    elif (
        hostname == "api.github.com"
        and len(path_parts) >= 3
        and path_parts[0] == "repos"
    ):
        owner, repository = path_parts[1:3]
    else:
        raise ValueError("URL is not a valid GitHub repository URL")

    repository = repository.removesuffix(".git")
    if not owner or not repository:
        raise ValueError("GitHub repository owner and name are required")
    return owner, repository


def _github_headers() -> dict[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise GitHubProviderError(
            "GITHUB_TOKEN is not configured in the project .env file"
        )
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": "Cardiovascular-Database-Artifact-Engine",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request_json(method: str, url: str, allow_not_found: bool = False, **kwargs):
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=_github_headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except requests.RequestException as error:
            if attempt == len(RETRY_DELAYS_SECONDS):
                raise GitHubProviderError(
                    f"GitHub API request failed after retries: {type(error).__name__}"
                ) from error
            delay = RETRY_DELAYS_SECONDS[attempt]
            logging.warning(
                "Transient GitHub request error (%s); retrying in %s seconds",
                type(error).__name__,
                delay,
            )
            time.sleep(delay)
            continue

        if response.status_code == 404:
            if allow_not_found:
                return None
            raise GitHubNotFoundError("GitHub repository or resource was not found")

        rate_limited = response.status_code == 429 or (
            response.status_code == 403
            and response.headers.get("X-RateLimit-Remaining") == "0"
        )
        retryable = rate_limited or response.status_code in RETRYABLE_STATUS_CODES
        if retryable and attempt < len(RETRY_DELAYS_SECONDS):
            delay = _retry_delay(response, attempt)
            logging.warning(
                "GitHub API returned HTTP %s; retrying in %s seconds",
                response.status_code,
                delay,
            )
            time.sleep(delay)
            continue
        if rate_limited:
            raise GitHubProviderError("GitHub API rate limit was exceeded")
        if response.status_code >= 400:
            raise GitHubProviderError(
                f"GitHub API request failed with HTTP {response.status_code}"
            )

        try:
            return response.json()
        except requests.JSONDecodeError as error:
            raise GitHubProviderError("GitHub API returned invalid JSON") from error

    raise GitHubProviderError("GitHub API request failed")


def _retry_delay(response: requests.Response, attempt: int) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return max(int(retry_after), 1)
    return RETRY_DELAYS_SECONDS[attempt]


def _decode_readme(readme_data: Any) -> str:
    if not isinstance(readme_data, Mapping):
        return ""
    content = readme_data.get("content")
    if not content:
        return ""
    try:
        return base64.b64decode(content).decode("utf-8", errors="replace")
    except (TypeError, ValueError):
        return ""
