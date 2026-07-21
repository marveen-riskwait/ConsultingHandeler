"""Consolidated Canadian Autonomous Sanctions List (SEMA), Global Affairs Canada.

Public XML, no key. One <record> per listed party: individuals carry
LastName/GivenName, entities and vessels carry EntityOrShip. Country labels are
bilingual ("Belarus / Bélarus") — the English side is kept.
"""
import os
import xml.etree.ElementTree as ET

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord

_ALIAS_SPLIT = (";", "|")


def _txt(rec, tag):
    v = rec.findtext(tag)
    return v.strip() if v and v.strip() else None


def _english(value):
    """'Belarus / Bélarus' -> 'Belarus'."""
    return value.split("/")[0].strip() if value else None


def _aliases(value):
    if not value:
        return []
    parts = [value]
    for sep in _ALIAS_SPLIT:
        parts = [p for chunk in parts for p in chunk.split(sep)]
    return [p.strip() for p in parts if p.strip()]


class CanadaSource(SanctionsSource):
    code = "CANADA"
    label = "Canada Consolidated List (SEMA)"
    sample_file = "canada_sample.json"

    @property
    def url(self):
        return os.getenv(
            "CANADA_SANCTIONS_URL",
            "https://www.international.gc.ca/world-monde/assets/office_docs/"
            "international_relations-relations_internationales/sanctions/sema-lmes.xml")

    def parse(self, raw):
        root = ET.fromstring(raw)
        records = []
        for i, rec in enumerate(root.findall(".//record")):
            entity = _txt(rec, "EntityOrShip")
            last, given = _txt(rec, "LastName"), _txt(rec, "GivenName")
            if entity:
                name, etype = entity, ("VESSEL" if _txt(rec, "ShipIMONumber") else "ENTITY")
            else:
                name = " ".join(p for p in [given, last] if p)
                etype = "INDIVIDUAL"
            if not name:
                continue
            schedule, item = _txt(rec, "Schedule"), _txt(rec, "Item")
            records.append(SanctionsRecord(
                source=self.code,
                external_id="-".join(p for p in [schedule, item] if p) or f"row-{i}",
                name=name,
                entity_type=etype,
                aliases=_aliases(_txt(rec, "Aliases")),
                programs=[p for p in [_english(_txt(rec, "Country"))] if p],
                country=_english(_txt(rec, "Country")),
                remarks=_txt(rec, "DateOfListing"),
            ))
        return records
