"""Thin OmniPath REST adapter that yields candidate evidence only.

OmniPath (https://omnipathdb.org/) exposes molecular interaction networks.
Each interaction is a database-integrated record whose ``references`` are
database/paper ids (e.g. ``Reactome:...``), not PMIDs. We therefore register
it as a hypothesis-grade source: results carry no PMID/DOI and must stay
hypothesis_only until a human validates and authors a knowledge entry.

The adapter never writes to the store and never assigns an evidence level;
the store applies the real downgrade rules.
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

_BASE_URL = "https://omnipathdb.org"
_SOURCE_ID = "omnipath"
_SOURCE_VERSION = "2026-09-15"


class OmniPathError(RuntimeError):
    """Raised when OmniPath cannot return a usable result set.

    ``status_hint``（P2 统一失败分类法）：timeout / rate_limited / malformed /
    unavailable——区分"系统状态"与"知识结果"。
    """

    def __init__(self, message: str, status_hint: str = "unavailable") -> None:
        super().__init__(message)
        self.status_hint = status_hint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class OmniPathAdapter:
    """Call the OmniPath interactions endpoint and map results to candidates."""

    def __init__(
        self,
        fetcher: Callable[[str], bytes] | None = None,
        timeout_s: float = 15.0,
        max_excerpt: int = 1200,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else self._default_fetcher
        self.timeout_s = float(timeout_s)
        self.max_excerpt = int(max_excerpt)

    def _default_fetcher(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or "Too Many Requests" in str(exc):
                raise OmniPathError(f"OmniPath rate limited: {exc}",
                                    status_hint="rate_limited") from None
            raise OmniPathError(f"OmniPath HTTP {exc.code}: {exc}",
                                status_hint="unavailable") from None
        except TimeoutError as exc:
            raise OmniPathError(f"OmniPath timeout: {exc}",
                                status_hint="timeout") from None
        except (urllib.error.URLError, OSError) as exc:
            raise OmniPathError(f"OmniPath request failed: {exc}",
                                status_hint="unavailable") from None

    def describe(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id=_SOURCE_ID,
            name="OmniPath",
            version=_SOURCE_VERSION,
            source_type="rest_api",
            provides=("molecular_interactions",),
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
            provenance_fields=("source", "target", "references"),
            evidence_mapping={},
            source_authority="official_service",
            evidence_origin="pathway_database",
            extraction_method="api_query",
            citation_status="database_reference_only",
            human_review="pending",
            default_use="hypothesis_only",
            status="candidate",
            last_verified_at=None,
        )

    def build_url(self, query: str, page_size: int) -> str:
        params = urllib.parse.urlencode(
            {
                "format": "json",
                "proteins": query,
                "fields": "sources,references,curation_effort",
                "limit": page_size,
            }
        )
        return f"{_BASE_URL}/interactions?{params}"

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size must be an integer between 1 and 100")

        payload = self._fetch_payload(self.build_url(query, page_size))
        if not isinstance(payload, list):
            raise OmniPathError("OmniPath returned an unexpected payload",
                               status_hint="malformed")
        retrieved_at = _now()
        candidates: list[CandidateEvidence] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise OmniPathError("OmniPath returned a non-object result")
            candidates.append(self._to_candidate(item, query, index, retrieved_at))
        return candidates

    def _fetch_payload(self, url: str) -> Any:
        raw = self._fetcher(url)
        if not isinstance(raw, (bytes, bytearray)):
            raise OmniPathError("fetcher must return bytes")
        try:
            return json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OmniPathError("OmniPath returned invalid JSON",
                               status_hint="malformed") from None

    def _to_candidate(
        self,
        item: dict[str, Any],
        query: str,
        index: int,
        retrieved_at: str,
    ) -> CandidateEvidence:
        source = _optional(item.get("source")) or "unknown"
        target = _optional(item.get("target")) or "unknown"
        references = item.get("references")
        if isinstance(references, list):
            references = [ref for ref in references if isinstance(ref, str)]
        else:
            references = []
        canonical = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record_id = f"{source}->{target}"
        excerpt_parts = []
        for key in ("is_directed", "is_stimulation", "is_inhibition"):
            value = item.get(key)
            if isinstance(value, bool):
                excerpt_parts.append(f"{key}={value}")
        if isinstance(item.get("sources"), list) and item["sources"]:
            excerpt_parts.append("sources=" + ",".join(map(str, item["sources"][:5])))
        if references:
            excerpt_parts.append("refs=" + ",".join(references[:5]))
        excerpt = "; ".join(excerpt_parts)
        if excerpt and self.max_excerpt > 0:
            excerpt = excerpt[: self.max_excerpt]
        return CandidateEvidence(
            evidence_id=f"omnipath:{record_id}#{index}",
            source_id=_SOURCE_ID,
            source_version=_SOURCE_VERSION,
            source_record_id=record_id,
            retrieved_at=retrieved_at,
            query=query,
            claim_summary=record_id,
            claim_type="molecular_interaction",
            evidence_status="candidate",
            review_status="pending",
            license_status="pending",
            pmid=None,
            pmcid=None,
            doi=None,
            raw_excerpt=excerpt or None,
            source_url=f"{_BASE_URL}/",
            raw_response_hash="sha256:" + digest,
        )


__all__ = ["OmniPathAdapter", "OmniPathError"]
