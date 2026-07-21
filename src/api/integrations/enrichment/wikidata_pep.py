"""A free PEP *lead* from Wikidata — explicitly not a PEP determination.

There is no reliable free PEP database. OpenSanctions is the serious one and it
needs a commercial licence. Wikidata is the only credible free angle: it records
"position held" (P39) and political party membership (P102) for public figures.

What this is honest about, because getting it wrong is worse than not having it:

- It is a LEAD, not a decision. The result is written as a low-confidence
  observation for a human to confirm; it never sets `is_pep`, never raises the
  risk score on its own, and never closes the question either way.
- Coverage is skewed. Heads of state are all there; a municipal councillor or a
  state-owned enterprise director very often is not. A silent answer means
  "Wikidata does not know", never "this person is not a PEP".
- The regulatory definition is wider than the data. Family members and close
  associates (RCAs) are PEPs under the AML directives; Wikidata models none of
  that relationship graph usefully.

So it saves an analyst a search. It does not replace one.
"""
import urllib.parse

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

API = "https://www.wikidata.org/w/api.php"
_UA = {"User-Agent": "ComplianceOS/1.0", "Accept": "application/json"}
# P39 position held, P102 member of political party, P106 occupation.
_POSITION, _PARTY = "P39", "P102"


def _search(name):
    q = urllib.parse.quote(name)
    data = get_json(f"{API}?action=wbsearchentities&search={q}&language=en"
                    "&type=item&format=json&limit=1", headers=_UA)
    hits = (data or {}).get("search") or []
    return hits[0] if hits else None


def _claims(qid):
    data = get_json(f"{API}?action=wbgetentities&ids={qid}&props=claims"
                    "&format=json", headers=_UA)
    return ((data or {}).get("entities") or {}).get(qid, {}).get("claims", {})


def _ids(claims, prop):
    out = []
    for claim in claims.get(prop, []):
        value = (((claim.get("mainsnak") or {}).get("datavalue") or {})
                 .get("value") or {})
        if value.get("id"):
            out.append(value["id"])
    return out


def _labels(qids):
    if not qids:
        return {}
    data = get_json(f"{API}?action=wbgetentities&ids={'|'.join(qids[:20])}"
                    "&props=labels&languages=en&format=json", headers=_UA)
    entities = (data or {}).get("entities") or {}
    return {qid: (((e.get("labels") or {}).get("en") or {}).get("value") or qid)
            for qid, e in entities.items()}


class WikidataPepSource(EnrichmentSource):
    name = "wikidata_pep"

    def applies(self, customer):
        return customer.customer_type == "INDIVIDUAL"

    def run(self, customer, context=None):
        hit = _search(customer.name)
        if not hit:
            return result(self.name, ok=False,
                          detail="No Wikidata entry — this says nothing about "
                                 "PEP status, only that Wikidata has no record.")
        qid, label = hit["id"], hit.get("label") or customer.name
        claims = _claims(qid)
        positions = _ids(claims, _POSITION)
        parties = _ids(claims, _PARTY)
        if not positions and not parties:
            return result(self.name, ok=False,
                          detail=f"Wikidata entry {qid} ({label}) holds no public "
                                 "office or party membership. Not a clearance.")

        labels = _labels(positions + parties)
        held = [labels.get(q, q) for q in positions]
        affiliations = [labels.get(q, q) for q in parties]
        summary = "; ".join(held[:3]) or "; ".join(affiliations[:2])
        detail = (f"Possible PEP — Wikidata {qid} ({label}) records "
                  f"{len(held)} public position(s)"
                  + (f" and {len(affiliations)} party affiliation(s)" if affiliations else "")
                  + f": {summary}. Confirm before acting.")
        return result(
            self.name, ok=True, detail=detail,
            # Confidence stays low on purpose: the engine auto-verifies only
            # trusted registry sources at >= 0.9, so this always lands as an
            # observation a human has to look at.
            fields={"pep_signal": {"value": f"Possible — {summary}",
                                   "confidence": 0.5},
                    "wikidata_id": {"value": qid, "confidence": 0.8}})
