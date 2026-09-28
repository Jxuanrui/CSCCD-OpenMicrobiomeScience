"""Adapter protocol for external knowledge sources.

An adapter only describes a source and turns its responses into candidate
evidence. It never writes to the local store and never upgrades evidence;
promotion stays with the human-approved store path.
"""

from __future__ import annotations

from typing import Protocol

from mra.knowledge.types import CandidateEvidence, SourceDescriptor


class KnowledgeSourceAdapter(Protocol):
    """Minimal contract every external knowledge source adapter implements."""

    def describe(self) -> SourceDescriptor:
        """Return the governance metadata used to register this source."""

    def search(self, query: str, page_size: int = 25) -> list[CandidateEvidence]:
        """Return candidate evidence for a query; no local side effects."""


__all__ = ["KnowledgeSourceAdapter"]
