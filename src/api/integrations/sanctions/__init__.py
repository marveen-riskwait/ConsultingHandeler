"""Public sanctions-list ingestion.

Each source is a small adapter that knows how to fetch and parse one public
list into a common `SanctionsRecord`. The registry lets the service and CLI
iterate them uniformly.

Live by default: OFAC (US), UN, OFSI (UK), Canada (SEMA). The EU, Swiss SECO
and Australian DFAT lists are not served from a stable public file URL — set
EU_SANCTIONS_URL / SECO_SANCTIONS_URL / DFAT_SANCTIONS_URL to ingest them live;
until then each falls back to a bundled sample of real published entries.
"""
from api.integrations.sanctions.base import SanctionsRecord, SanctionsSource
from api.integrations.sanctions.ofac import OFACSource
from api.integrations.sanctions.un import UNSource
from api.integrations.sanctions.eu import EUSource
from api.integrations.sanctions.ofsi import OFSISource
from api.integrations.sanctions.canada import CanadaSource
from api.integrations.sanctions.seco import SECOSource
from api.integrations.sanctions.dfat import DFATSource

_SOURCES = {
    "OFAC": OFACSource,
    "UN": UNSource,
    "EU": EUSource,
    "OFSI": OFSISource,
    "CANADA": CanadaSource,
    "SECO": SECOSource,
    "DFAT": DFATSource,
}


def get_source(code):
    cls = _SOURCES.get(code.upper())
    if cls is None:
        raise ValueError(f"Unknown sanctions source: {code}")
    return cls()


def all_sources():
    return [cls() for cls in _SOURCES.values()]


__all__ = ["SanctionsRecord", "SanctionsSource", "get_source", "all_sources",
           "OFACSource", "UNSource", "EUSource", "OFSISource", "CanadaSource",
           "SECOSource", "DFATSource"]
