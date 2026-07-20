"""UN Security Council Consolidated Sanctions List.

Public XML, no key: individuals under <INDIVIDUALS>, entities under <ENTITIES>.
"""
import xml.etree.ElementTree as ET

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord


def _txt(el, tag):
    v = el.findtext(tag)
    return v.strip() if v and v.strip() else None


def _join_names(el, tags):
    parts = [_txt(el, t) for t in tags]
    return " ".join(p for p in parts if p)


class UNSource(SanctionsSource):
    code = "UN"
    label = "UN Consolidated List"
    url = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    sample_file = "un_sample.json"

    def parse(self, raw):
        root = ET.fromstring(raw)
        records = []

        for ind in root.findall(".//INDIVIDUALS/INDIVIDUAL"):
            name = _join_names(ind, ["FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"])
            if not name:
                continue
            aliases = [a.strip() for a in
                       (al.findtext("ALIAS_NAME") or "" for al in ind.findall("INDIVIDUAL_ALIAS"))
                       if a and a.strip()]
            records.append(SanctionsRecord(
                source=self.code,
                external_id=_txt(ind, "DATAID") or _txt(ind, "REFERENCE_NUMBER") or name,
                name=name,
                entity_type="INDIVIDUAL",
                aliases=aliases,
                programs=[t for t in [_txt(ind, "UN_LIST_TYPE")] if t],
                country=(ind.findtext(".//NATIONALITY/VALUE") or "").strip() or None,
                remarks=_txt(ind, "COMMENTS1"),
            ))

        for ent in root.findall(".//ENTITIES/ENTITY"):
            name = _txt(ent, "FIRST_NAME")
            if not name:
                continue
            aliases = [a.strip() for a in
                       (al.findtext("ALIAS_NAME") or "" for al in ent.findall("ENTITY_ALIAS"))
                       if a and a.strip()]
            records.append(SanctionsRecord(
                source=self.code,
                external_id=_txt(ent, "DATAID") or _txt(ent, "REFERENCE_NUMBER") or name,
                name=name,
                entity_type="ENTITY",
                aliases=aliases,
                programs=[t for t in [_txt(ent, "UN_LIST_TYPE")] if t],
                remarks=_txt(ent, "COMMENTS1"),
            ))

        return records
