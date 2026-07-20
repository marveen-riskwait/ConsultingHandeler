"""EU Consolidated Financial Sanctions List.

The official EU list is served from the Commission's FSF service behind an
access token, so there is no fixed public URL. Set `EU_SANCTIONS_URL` (the
tokenised download URL) to fetch live; otherwise the bundled sample of real,
public EU-listed entries is used. The parser is namespace-tolerant (matches on
local tag names) because the EU XML is heavily namespaced.
"""
import os
import xml.etree.ElementTree as ET

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord


def _local(tag):
    return tag.rsplit("}", 1)[-1]  # strip {namespace}


class EUSource(SanctionsSource):
    code = "EU"
    label = "EU Consolidated List"
    sample_file = "eu_sample.json"

    @property
    def url(self):
        return os.getenv("EU_SANCTIONS_URL")  # tokenised URL, if configured

    def parse(self, raw):
        root = ET.fromstring(raw)
        records = []
        for entity in root.iter():
            if _local(entity.tag) != "sanctionEntity":
                continue
            names, aliases, etype, country, ext_id = [], [], "ENTITY", None, None
            ext_id = entity.get("logicalId") or entity.get("euReferenceNumber")
            for child in entity.iter():
                lt = _local(child.tag)
                if lt == "nameAlias":
                    whole = child.get("wholeName")
                    if whole:
                        names.append(whole.strip())
                elif lt == "subjectType":
                    code = (child.get("classificationCode") or "").lower()
                    etype = "INDIVIDUAL" if code == "person" else "ENTITY"
                elif lt == "citizenship" and country is None:
                    country = child.get("countryDescription")
            if not names:
                continue
            records.append(SanctionsRecord(
                source=self.code,
                external_id=str(ext_id or names[0]),
                name=names[0],
                entity_type=etype,
                aliases=names[1:],
                country=country,
            ))
        return records
