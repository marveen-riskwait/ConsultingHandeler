"""Australia DFAT Consolidated List.

DFAT publishes the consolidated list as a legacy binary .xls behind a redirect,
and the site is not reachable from every network. Reading .xls would mean a new
dependency for one list, so this adapter speaks CSV instead: point
`DFAT_SANCTIONS_URL` at a CSV export of the consolidated list and it ingests
live; otherwise the bundled sample of real, published DFAT entries is used.

The column names are matched case-insensitively and loosely, because DFAT has
renamed them between revisions.
"""
import csv
import io
import os

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord

_TYPES = {"individual": "INDIVIDUAL", "entity": "ENTITY"}


def _pick(row, *candidates):
    for key in candidates:
        for actual, value in row.items():
            if actual and actual.strip().lower() == key and (value or "").strip():
                return value.strip()
    return None


class DFATSource(SanctionsSource):
    code = "DFAT"
    label = "Australia DFAT Consolidated List"
    sample_file = "dfat_sample.json"

    @property
    def url(self):
        return os.getenv("DFAT_SANCTIONS_URL")   # CSV export, if you have one

    def parse(self, raw):
        text = raw.decode("utf-8-sig", errors="replace")
        records = []
        for i, row in enumerate(csv.DictReader(io.StringIO(text))):
            name = _pick(row, "name of individual or entity", "name", "full name")
            if not name:
                continue
            raw_type = (_pick(row, "type", "entity type") or "").lower()
            aliases = _pick(row, "additional information", "aka", "also known as")
            records.append(SanctionsRecord(
                source=self.code,
                external_id=_pick(row, "reference", "control date", "id") or f"row-{i}",
                name=name,
                entity_type=_TYPES.get(raw_type, "ENTITY"),
                aliases=[a.strip() for a in (aliases or "").split(";") if a.strip()],
                programs=[p for p in [_pick(row, "committees", "sanctions regime")] if p],
                country=_pick(row, "citizenship", "country"),
            ))
        return records
