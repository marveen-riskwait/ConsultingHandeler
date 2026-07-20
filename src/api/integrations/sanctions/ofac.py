"""OFAC Specially Designated Nationals (SDN) list — US Treasury.

Public CSV, no key. sdn.csv columns (12): ent_num, SDN_Name, SDN_Type, Program,
Title, Call_Sign, Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks. The
sentinel '-0-' means an empty field. Aliases (a.k.a. names) live in a separate
file, alt.csv, keyed by the same ent_num — we merge them in so alias matching
works on the live list too.
"""
import csv
import io

from api.integrations.sanctions.base import (
    SanctionsSource, SanctionsRecord, http_get,
)

_NULL = "-0-"
_TYPE_MAP = {"individual": "INDIVIDUAL", "vessel": "VESSEL", "aircraft": "AIRCRAFT"}


def _clean(v):
    v = (v or "").strip()
    return None if v in ("", _NULL) else v


def _programs(raw):
    # Multiple programmes are packed as "UKRAINE-EO13662] [RUSSIA-EO14024".
    v = _clean(raw)
    return [p.strip("[] ") for p in v.split("] [")] if v else []


class OFACSource(SanctionsSource):
    code = "OFAC"
    label = "OFAC SDN (US Treasury)"
    url = "https://www.treasury.gov/ofac/downloads/sdn.csv"
    alt_url = "https://www.treasury.gov/ofac/downloads/alt.csv"
    sample_file = "ofac_sample.json"

    def _aliases_by_entity(self):
        """ent_num -> [alias names] from alt.csv. Best-effort: alias matching
        still works without it, just with fewer names."""
        try:
            text = http_get(self.alt_url).decode("utf-8", errors="replace")
        except Exception:
            return {}
        aliases = {}
        # alt.csv columns: ent_num, alt_num, alt_type, alt_name, alt_remarks
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 4:
                continue
            name = _clean(row[3])
            if name:
                aliases.setdefault(row[0].strip(), []).append(name)
        return aliases

    def parse(self, raw):
        text = raw.decode("utf-8", errors="replace")
        aliases_map = self._aliases_by_entity()
        records = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 12:
                continue
            name = _clean(row[1])
            if not name:
                continue
            ent_num = row[0].strip()
            sdn_type = (row[2] or "").strip().lower()
            records.append(SanctionsRecord(
                source=self.code,
                external_id=ent_num,
                name=name,
                entity_type=_TYPE_MAP.get(sdn_type, "ENTITY"),
                aliases=aliases_map.get(ent_num, [])[:20],
                programs=_programs(row[3]),
                remarks=_clean(row[11]),
            ))
        return records
