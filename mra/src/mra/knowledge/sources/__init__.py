"""External knowledge source adapters (thin, candidate-evidence only)."""

from mra.knowledge.sources.base import KnowledgeSourceAdapter
from mra.knowledge.sources.europepmc import EuropePmcAdapter, EuropePmcError
from mra.knowledge.sources.omnipath import OmniPathAdapter, OmniPathError

__all__ = [
    "EuropePmcAdapter",
    "EuropePmcError",
    "KnowledgeSourceAdapter",
    "OmniPathAdapter",
    "OmniPathError",
]
