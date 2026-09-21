"""Thin Europe PMC REST adapter that yields candidate evidence only.

Reuses the documented Europe PMC API behaviour (see
``paper-lookup/references/europepmc.md``, verified 2026-07-27):

- errors arrive with HTTP 200 and an ``errCode`` but no ``resultList``;
- boolean-ish fields are the strings ``"Y"`` / ``"N"``;
- ``pmcid`` is absent (not null) when the article is not in PMC.

The adapter never writes to the store and never assigns an evidence level
above ``candidate``; the store applies the real downgrade rules.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mra.knowledge.types import CandidateEvidence, SourceDescriptor

_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_SOURCE_ID = "europe-pmc"
# Matches the API verification date recorded in the paper-lookup reference.
_SOURCE_VERSION = "2026-07-27"
_MAX_PAGE_SIZE = 1000


class EuropePmcError(RuntimeError):
    """Raised when Europe PMC cannot return a usable result set."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class EuropePmcAdapter:
    """Call the Europe PMC search endpoint and map results to candidate evidence."""

    def __init__(
        self,
        fetcher: Callable[[str], bytes] | None = None,
        timeout_s: float = 15.0,
        max_excerpt: int = 2000,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else self._default_fetcher
        self.timeout_s = float(timeout_s)
        self.max_excerpt = int(max_excerpt)

    def _default_fetcher(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EuropePmcError(f"Europe PMC request failed: {exc}") from None

    def describe(self) -> SourceDescriptor:
        """Describe Europe PMC as an unverified candidate-grade source."""

        return SourceDescriptor(
            source_id=_SOURCE_ID,
            name="Europe PMC",
            version=_SOURCE_VERSION,
            source_type="rest_api",
            provides=("literature_metadata",),
            capabilities=("search",),
            endpoint=_BASE_URL,
            transport="https",
            input_schema={},
            output_schema={},
            software_license="pending",
            data_license="pending",
            upstream_license="pending",
            commercial_use="pending",
            redistribution="pending",
            provenance_fields=("pmid", "pmcid", "doi"),
            evidence_mapping={},
            source_authority="official_service",
            evidence_origin="literature_metadata",
            extraction_method="api_query",
            citation_status="identifier_available",
            human_review="pending",
            default_use="candidate",
            status="candidate",
            last_verified_at=None,
        )

    def build_url(self, query: str, page_size: int) -> str:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": page_size,
            }
        )
        return f"{_BASE_URL}/search?{params}"

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        """Return candidate evidence for a query; never touches the store."""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= _MAX_PAGE_SIZE
        ):
            raise ValueError("page_size must be an integer between 1 and 1000")

        payload = self._fetch_payload(self.build_url(query, page_size))
        results = payload["resultList"].get("result", [])
        if not isinstance(results, list):
            raise EuropePmcError("Europe PMC resultList.result was not a list")
        retrieved_at = _now()
        candidates: list[CandidateEvidence] = []
        for result in results:
            if not isinstance(result, dict):
                # Fail visible rather than silently dropping a malformed record.
                raise EuropePmcError("Europe PMC returned a non-object result")
            candidates.append(self._to_candidate(result, query, retrieved_at))
        return candidates

    def _fetch_payload(self, url: str) -> dict[str, Any]:
        raw = self._fetcher(url)
        if not isinstance(raw, (bytes, bytearray)):
            raise EuropePmcError("fetcher must return bytes")
        try:
            payload = json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EuropePmcError("Europe PMC returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise EuropePmcError("Europe PMC returned an unexpected payload")
        # Europe PMC reports failures inside HTTP 200: errCode and no resultList.
        if "resultList" not in payload:
            code = payload.get("errCode", "unknown")
            message = payload.get("errMsg", "missing resultList")
            raise EuropePmcError(f"Europe PMC error {code}: {message}")
        if not isinstance(payload["resultList"], dict):
            raise EuropePmcError("Europe PMC resultList was not an object")
        return payload

    def _to_candidate(
        self, result: dict[str, Any], query: str, retrieved_at: str
    ) -> CandidateEvidence:
        source = str(result.get("source") or "MED")
        record_id = _optional(result.get("id"))
        if record_id is None:
            # Without an id the evidence key would collide; fail visible.
            raise EuropePmcError("Europe PMC record is missing an id")
        doi = _optional(result.get("doi"))
        excerpt = _optional(result.get("abstractText"))
        if excerpt is not None and self.max_excerpt > 0:
            excerpt = excerpt[: self.max_excerpt]
        source_url = (
            f"https://doi.org/{doi}"
            if doi is not None
            else f"https://europepmc.org/article/{source}/{record_id}"
        )
        try:
            canonical = json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError):
            raise EuropePmcError("Europe PMC record is not JSON-serializable") from None
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return CandidateEvidence(
            evidence_id=f"{_SOURCE_ID}:{source}/{record_id}",
            source_id=_SOURCE_ID,
            source_version=_SOURCE_VERSION,
            source_record_id=f"{source}/{record_id}",
            retrieved_at=retrieved_at,
            query=query,
            claim_summary=_optional(result.get("title")),
            claim_type="literature_record",
            evidence_status="candidate",
            review_status="pending",
            license_status="pending",
            pmid=_optional(result.get("pmid")),
            pmcid=_optional(result.get("pmcid")),
            doi=doi,
            raw_excerpt=excerpt,
            source_url=source_url,
            raw_response_hash="sha256:" + digest,
        )


__all__ = ["EuropePmcAdapter", "EuropePmcError"]
