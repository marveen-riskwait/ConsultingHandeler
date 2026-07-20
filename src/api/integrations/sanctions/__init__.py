"""Public sanctions-list ingestion.

Each source (OFAC, UN, EU) is a small adapter that knows how to fetch and parse
one public list into a common `SanctionsRecord`. The registry lets the service
and CLI iterate them uniformly.
"""
from api.integrations.sanctions.base import SanctionsRecord, SanctionsSource
from api.integrations.sanctions.ofac import OFACSource
from api.integrations.sanctions.un import UNSource
from api.integrations.sanctions.eu import EUSource

_SOURCES = {
    "OFAC": OFACSource,
    "UN": UNSource,
    "EU": EUSource,
}


def get_source(code):
    cls = _SOURCES.get(code.upper())
    if cls is None:
        raise ValueError(f"Unknown sanctions source: {code}")
    return cls()


def all_sources():
    return [cls() for cls in _SOURCES.values()]


__all__ = ["SanctionsRecord", "SanctionsSource", "get_source", "all_sources",
           "OFACSource", "UNSource", "EUSource"]
