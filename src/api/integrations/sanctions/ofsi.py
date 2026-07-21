"""UK OFSI Consolidated List of Financial Sanctions Targets (HM Treasury).

Public XML, no key. The file is large (~50 MB), so it is streamed with
iterparse and elements are cleared as they are consumed rather than building a
whole DOM.

Shape worth knowing: OFSI emits one <FinancialSanctionsTarget> *per name*, not
per person. Every spelling of the same target shares a GroupID, and AliasType
says whether that row is the primary name or a variation. So rows are grouped
into one record whose aliases are the other spellings — which is exactly the
entity/aliases shape the matcher expects.
"""
import io
import os
import xml.etree.ElementTree as ET

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord

_TYPES = {"individual": "INDIVIDUAL", "entity": "ENTITY", "ship": "VESSEL"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _full_name(fields):
    """OFSI splits a name across name1..name5 (given names) and Name6 (family)."""
    parts = [fields.get(f"name{i}") for i in range(1, 6)] + [fields.get("Name6")]
    return " ".join(p.strip() for p in parts if p and p.strip())


class OFSISource(SanctionsSource):
    code = "OFSI"
    label = "UK OFSI Consolidated List"
    sample_file = "ofsi_sample.json"

    @property
    def url(self):
        return os.getenv(
            "OFSI_SANCTIONS_URL",
            "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml")

    def parse(self, raw):
        groups = {}
        order = []
        for _, el in ET.iterparse(io.BytesIO(raw), events=("end",)):
            if _local(el.tag) != "FinancialSanctionsTarget":
                continue
            fields = {_local(c.tag): (c.text or "") for c in el}
            el.clear()

            name = _full_name(fields)
            if not name:
                continue
            gid = (fields.get("GroupID") or "").strip() or name
            is_primary = "primary name" == (fields.get("AliasType") or "").strip().lower()

            g = groups.get(gid)
            if g is None:
                g = groups[gid] = {"name": None, "aliases": [], "fields": fields}
                order.append(gid)
            if is_primary and not g["name"]:
                g["name"] = name
                g["fields"] = fields          # keep the primary row's metadata
            elif name != g["name"]:
                g["aliases"].append(name)

        records = []
        for gid in order:
            g = groups[gid]
            f = g["fields"]
            primary = g["name"]
            aliases = g["aliases"]
            if not primary:                   # no row flagged primary: promote one
                if not aliases:
                    continue
                primary, aliases = aliases[0], aliases[1:]
            gtype = (f.get("GroupTypeDescription") or "").strip().lower()
            records.append(SanctionsRecord(
                source=self.code,
                external_id=str(gid),
                name=primary,
                entity_type=_TYPES.get(gtype, "ENTITY"),
                aliases=sorted(set(aliases)),
                programs=[p for p in [(f.get("RegimeName") or "").strip()] if p],
                country=(f.get("Individual_Nationality") or f.get("Country") or "").strip() or None,
                remarks=(f.get("UKStatementOfReasons") or "").strip() or None,
            ))
        return records
