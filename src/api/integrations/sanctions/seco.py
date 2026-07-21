"""Swiss SECO consolidated sanctions list (SESAM).

SECO publishes through a JSF search application rather than a stable file URL:
the documented download link answers with an HTML page unless it is called
inside a session. So there is no working default here — set
`SECO_SANCTIONS_URL` to whatever direct XML link SECO is serving, and until
then the bundled sample of real, published SECO entries is used. Same
arrangement as the EU list, and for the same reason.

The parser matches on local tag names because the SESAM XML is namespaced, and
tolerates both the <individual>/<entity> and flat <target> shapes SECO has
used across format revisions.
"""
import os
import xml.etree.ElementTree as ET

from api.integrations.sanctions.base import SanctionsSource, SanctionsRecord


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _name_from(node):
    """SECO spells a name as <name part-type="..."> fragments under <name>."""
    parts = []
    for child in node.iter():
        if _local(child.tag) in ("name", "wholename", "whole-name") and (child.text or "").strip():
            parts.append(child.text.strip())
    return " ".join(dict.fromkeys(parts))


class SECOSource(SanctionsSource):
    code = "SECO"
    label = "Switzerland SECO List"
    sample_file = "seco_sample.json"

    @property
    def url(self):
        return os.getenv("SECO_SANCTIONS_URL")   # no stable public default

    def parse(self, raw):
        root = ET.fromstring(raw)
        records = []
        for target in root.iter():
            if _local(target.tag) != "target":
                continue
            ssid = target.get("ssid") or target.get("id")
            etype, names = "ENTITY", []
            for child in target:
                kind = _local(child.tag)
                if kind in ("individual", "entity", "object"):
                    etype = {"individual": "INDIVIDUAL", "object": "VESSEL"}.get(kind, "ENTITY")
                    for sub in child:
                        if _local(sub.tag) == "name":
                            n = _name_from(sub)
                            if n:
                                names.append(n)
            if not names:
                continue
            records.append(SanctionsRecord(
                source=self.code,
                external_id=str(ssid or names[0]),
                name=names[0],
                entity_type=etype,
                aliases=sorted(set(names[1:])),
            ))
        return records
