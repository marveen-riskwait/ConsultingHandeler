"""US company registration via SEC EDGAR — free, no key.

The American counterpart to the Companies House and SIRENE adapters: official
name, CIK, industry (SIC) and state of incorporation for any company that files
with the SEC. Public companies and funds, not every US LLC — but for the
entities a compliance team actually onboards it covers the ones that matter.

SEC asks API users to identify themselves; SEC_EDGAR_USER_AGENT sets the
contact string they request.
"""
import os

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _headers():
    return {"User-Agent": os.getenv("SEC_EDGAR_USER_AGENT",
                                    "ComplianceOS/1.0 (compliance@example.com)"),
            "Accept": "application/json"}


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == " ").strip()


class SecEdgarSource(EnrichmentSource):
    name = "sec_edgar"

    def applies(self, customer):
        return customer.customer_type == "COMPANY"

    def _find_cik(self, name):
        data = get_json(TICKERS, headers=_headers())
        target = _norm(name)
        if not target:
            return None, None
        best = None
        for row in (data or {}).values():
            title = _norm(row.get("title"))
            if not title:
                continue
            if title == target:
                return row.get("cik_str"), row.get("title")
            if best is None and (target in title or title in target):
                best = (row.get("cik_str"), row.get("title"))
        return best or (None, None)

    def run(self, customer, context=None):
        cik, matched = self._find_cik(customer.name)
        if not cik:
            return result(self.name, ok=False,
                          detail="No SEC filer matches this name.")
        data = get_json(SUBMISSIONS.format(cik=int(cik)), headers=_headers())
        addresses = (data.get("addresses") or {}).get("business") or {}
        office = ", ".join(p for p in [addresses.get("street1"),
                                       addresses.get("city"),
                                       addresses.get("stateOrCountry"),
                                       addresses.get("zipCode")] if p)
        fields = {
            "legal_name": {"value": data.get("name") or matched, "confidence": 0.95},
            "sec_cik": {"value": str(cik), "confidence": 0.99},
        }
        if data.get("sicDescription"):
            fields["business_activity"] = {"value": data["sicDescription"],
                                           "confidence": 0.8}
        if data.get("stateOfIncorporation"):
            fields["country_of_incorporation"] = {
                "value": f"US-{data['stateOfIncorporation']}", "confidence": 0.9}
        if office:
            fields["registered_office"] = {"value": office, "confidence": 0.85}
        return result(self.name, ok=True,
                      detail=f"SEC filer {data.get('name') or matched} (CIK {cik}).",
                      fields=fields)
