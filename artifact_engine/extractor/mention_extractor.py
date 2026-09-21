"""Extract repository and DOI mentions from paper text."""

from __future__ import annotations

import re
from collections.abc import Iterator


_GITHUB_PATTERN = re.compile(
    r"(?<![\w./-])(?:https?://)?(?:www\.)?github\.com/[^\s<>\"']+",
    re.IGNORECASE,
)
_ZENODO_URL_PATTERN = re.compile(
    r"(?<![\w./-])(?:https?://)?(?:www\.)?zenodo\.org/(?:record|records)/\d+[^\s<>\"']*",
    re.IGNORECASE,
)
_ZENODO_DOI_PATTERN = re.compile(
    r"(?<![\w./-])(?:https?://)?(?:(?:dx|www)\.)?doi\.org/10\.5281/zenodo\.\d+"
    r"|(?<![\w./-])10\.5281/zenodo\.\d+",
    re.IGNORECASE,
)
_GENERIC_DOI_PATTERN = re.compile(
    r"(?<![\w./-])10\.\d{4,9}/\S+",
    re.IGNORECASE,
)

_TRAILING_PUNCTUATION = ".,;:!?\"'`"
_CLOSING_TO_OPENING = {
    ")": "(",
    "]": "[",
    "}": "{",
}


def extract_mentions(text: str) -> list[dict]:
    """Return unique GitHub, Zenodo, and DOI mentions in text order.

    The returned ``raw_match`` is the cleaned text fragment that was matched;
    ``normalized_url`` is suitable for passing to the provider layer.
    """
    if not isinstance(text, str) or not text:
        return []

    matches = list(_find_matches(text))
    matches.sort(key=lambda match: (match[0], match[1]))

    accepted: list[tuple[int, int, str, str, str]] = []
    occupied_until = -1
    seen_urls: set[str] = set()
    for start, priority, end, url_type, raw_match, normalized_url in matches:
        if start < occupied_until:
            continue
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        accepted.append((start, priority, raw_match, url_type, normalized_url))
        occupied_until = end

    accepted.sort(key=lambda match: match[0])
    return [
        {
            "raw_match": raw_match,
            "url_type": url_type,
            "normalized_url": normalized_url,
        }
        for _, _, raw_match, url_type, normalized_url in accepted
    ]


def _find_matches(text: str) -> Iterator[tuple[int, int, int, str, str, str]]:
    patterns = (
        (0, "github", _GITHUB_PATTERN, _normalize_github),
        (1, "zenodo", _ZENODO_URL_PATTERN, _normalize_zenodo_url),
        (2, "zenodo_doi", _ZENODO_DOI_PATTERN, _normalize_zenodo_doi),
        (3, "generic_doi", _GENERIC_DOI_PATTERN, _normalize_generic_doi),
    )
    for priority, url_type, pattern, normalizer in patterns:
        for match in pattern.finditer(text):
            raw_match = _trim_trailing_noise(match.group(0))
            if not raw_match:
                continue
            normalized_url = normalizer(raw_match)
            if normalized_url is None:
                continue
            end = match.start() + len(raw_match)
            yield (
                match.start(),
                priority,
                end,
                url_type,
                raw_match,
                normalized_url,
            )


def _normalize_github(raw_match: str) -> str | None:
    value = _without_scheme(raw_match)
    if not value.casefold().startswith("github.com/"):
        return None
    path = value.split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[0].casefold() != "github.com":
        return None
    owner, repository = parts[1], parts[2]
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        return None
    return f"https://github.com/{owner.casefold()}/{repository.casefold()}"


def _normalize_zenodo_url(raw_match: str) -> str | None:
    value = _without_scheme(raw_match)
    if not value.casefold().startswith(("zenodo.org/", "www.zenodo.org/")):
        return None
    path = value.split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[0].casefold() not in {"zenodo.org", "www.zenodo.org"}:
        return None
    if parts[1].casefold() not in {"record", "records"} or not parts[2].isdigit():
        return None
    return f"https://zenodo.org/records/{parts[2]}"


def _normalize_zenodo_doi(raw_match: str) -> str | None:
    doi = _doi_from_match(raw_match)
    if not re.fullmatch(r"10\.5281/zenodo\.\d+", doi, re.IGNORECASE):
        return None
    return f"https://doi.org/{doi.lower()}"


def _normalize_generic_doi(raw_match: str) -> str | None:
    doi = _doi_from_match(raw_match)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.IGNORECASE):
        return None
    if re.fullmatch(r"10\.5281/zenodo\.\d+", doi, re.IGNORECASE):
        return None
    return f"https://doi.org/{doi}"


def _doi_from_match(raw_match: str) -> str:
    value = raw_match.strip()
    value = re.sub(
        r"^(?:https?://)?(?:(?:dx|www)\.)?doi\.org/",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value


def _without_scheme(value: str) -> str:
    return re.sub(r"^https?://", "", value, flags=re.IGNORECASE)


def _trim_trailing_noise(value: str) -> str:
    value = value.rstrip()
    while value and (
        value[-1] in _TRAILING_PUNCTUATION or _has_unbalanced_closing(value)
    ):
        if value[-1] in _CLOSING_TO_OPENING and not _has_matching_opening(value, value[-1]):
            value = value[:-1]
            continue
        if value[-1] in _TRAILING_PUNCTUATION:
            value = value[:-1]
            continue
        break
    return value


def _has_unbalanced_closing(value: str) -> bool:
    return value[-1] in _CLOSING_TO_OPENING and not _has_matching_opening(
        value, value[-1]
    )


def _has_matching_opening(value: str, closing: str) -> bool:
    return value.count(_CLOSING_TO_OPENING[closing]) >= value.count(closing)
