"""GLEIF LEI enrichment — free, global, no key.

The Global LEI Foundation indexes every legal entity holding an LEI (banks,
funds, most companies that touch financial markets): official name, legal form,
addresses, registry status. https://api.gleif.org
"""
import urllib.parse

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

API = "https://api.gleif.org/api/v1/lei-records"


class GleifSource(EnrichmentSource):
    name = "gleif"

    def applies(self, customer):
        return customer.customer_type == "COMPANY"

    def run(self, customer, context=None):
        q = urllib.parse.quote(customer.name)
        data = get_json(f"{API}?filter[fulltext]={q}&page[size]=3",
                        headers={"Accept": "application/vnd.api+json"})
        records = data.get("data") or []
        if not records:
            return result(self.name, ok=False, detail="No LEI record found.")

        attrs = (records[0].get("attributes") or {})
        entity = attrs.get("entity") or {}
        legal_name = ((entity.get("legalName") or {}).get("name") or "")
        # Fuzzy sanity check: at least one word of the customer name must
        # appear in the matched legal name (avoid wild mismatches).
        words = [w.lower() for w in customer.name.split() if len(w) > 2]
        if words and not any(w in legal_name.lower() for w in words):
            return result(self.name, ok=False,
                          detail=f"Closest LEI record '{legal_name}' did not "
                                 "match the customer name.")

        addr = entity.get("legalAddress") or {}
        fields = {
            "legal_name": legal_name,
            "lei": attrs.get("lei"),
            "legal_form": ((entity.get("legalForm") or {}).get("id")),
            "country_of_incorporation": addr.get("country"),
            "registered_office": ", ".join(
                v for v in [(addr.get("addressLines") or [None])[0],
                            addr.get("city"), addr.get("postalCode"),
                            addr.get("country")] if v),
        }
        fields = {k: {"value": v, "confidence": 0.9}
                  for k, v in fields.items() if v}
        status = ((attrs.get("registration") or {}).get("status"))
        return result(self.name,
                      detail=f"LEI {attrs.get('lei')} ({status})", fields=fields)
