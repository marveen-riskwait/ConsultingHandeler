"""Shared plumbing for sanctions sources: HTTP fetch + record shape.

Fetching uses the stdlib (urllib) with certifi's CA bundle when available, so
we don't add a heavy HTTP dependency. Every source degrades to a bundled
offline sample when the live fetch fails (no network / blocked egress), so the
feature is always demonstrable and the DB always gets real public entries.
"""
import json
import os
import ssl
import urllib.request
from dataclasses import dataclass, field

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
_TIMEOUT = 30


@dataclass
class SanctionsRecord:
    source: str
    external_id: str
    name: str
    entity_type: str = "ENTITY"          # INDIVIDUAL | ENTITY | VESSEL | AIRCRAFT | OTHER
    aliases: list = field(default_factory=list)
    programs: list = field(default_factory=list)
    country: str = None
    remarks: str = None
    # [{"asset": "XBT", "address": "1A1zP..."}] — sanctioned wallets, when the
    # source publishes them (OFAC does, inside the remarks field).
    wallets: list = field(default_factory=list)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get(url, timeout=_TIMEOUT):
    """GET a URL as bytes. Raises on any failure (caller falls back to sample)."""
    req = urllib.request.Request(url, headers={"User-Agent": "ComplianceOS/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


def load_sample(filename):
    """Load a bundled sample list of dicts (real, public entries)."""
    path = os.path.join(SAMPLES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class SanctionsSource:
    code = "ABSTRACT"
    label = "Abstract"
    url = None
    sample_file = None

    def parse(self, raw):
        """Parse raw bytes from `url` into a list[SanctionsRecord]."""
        raise NotImplementedError

    def parse_sample(self, rows):
        """Turn bundled sample dicts into records (default: direct mapping)."""
        return [SanctionsRecord(source=self.code, **row) for row in rows]

    def records(self, prefer_live=True, limit=None):
        """Return (records, is_live). Try live fetch; fall back to sample."""
        recs, is_live = [], False
        if prefer_live and self.url:
            try:
                recs = self.parse(http_get(self.url))
                is_live = True
            except Exception:
                recs, is_live = [], False
        if not recs and self.sample_file:
            recs = self.parse_sample(load_sample(self.sample_file))
            is_live = False
        if limit:
            recs = recs[:limit]
        return recs, is_live
