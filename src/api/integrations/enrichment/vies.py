"""EU VAT validation (VIES) — free, no key, every member state.

The Commission's VIES service answers whether a VAT number is registered and,
for most member states, returns the registered name and address. That is a
genuine KYB signal in the 25 EU countries where there is no free company
registry: it proves the entity exists and is trading under that identity.

Runs when the customer has a VAT number recorded (profile field `vat_number`),
because VIES answers on the number, not on a name.
"""
import re

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

API = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{cc}/vat/{num}"
_VAT = re.compile(r"^\s*([A-Z]{2})\s*([0-9A-Za-z\s.\-]{4,20})\s*$")
# Only real VAT prefixes: without this, "notavat" parses as country NO. Greece
# files under EL (not GR) and Northern Ireland under XI, per the VIES scheme.
_VAT_PREFIXES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK", "XI",
}


def split_vat(raw):
    """'LU 26375245' -> ('LU', '26375245'); None when it isn't a VAT number."""
    m = _VAT.match((raw or "").upper())
    if not m:
        return None
    country, number = m.group(1), re.sub(r"[^0-9A-Z]", "", m.group(2))
    if country not in _VAT_PREFIXES or not number:
        return None
    return country, number


class ViesSource(EnrichmentSource):
    name = "vies"

    def _vat_of(self, customer, context):
        values = (context or {}).get("fields") or {}
        return (values.get("vat_number") or values.get("tax_id")
                or getattr(customer, "vat_number", None))

    def applies(self, customer):
        return customer.customer_type == "COMPANY"

    def run(self, customer, context=None):
        raw = self._vat_of(customer, context)
        parsed = split_vat(raw)
        if not parsed:
            return result(self.name, ok=False,
                          detail="No EU VAT number on file to validate "
                                 "(add `vat_number`, e.g. LU26375245).")
        country, number = parsed
        data = get_json(API.format(cc=country, num=number))

        if not data.get("isValid"):
            # A registered-looking number that VIES rejects is a finding, not a
            # failed lookup: it contradicts what the customer declared.
            return result(self.name, ok=True,
                          detail=f"VAT {country}{number} is NOT registered in VIES.",
                          fields={"vat_valid": {"value": "No", "confidence": 0.99}})

        name = (data.get("name") or "").strip()
        address = " ".join((data.get("address") or "").split())
        fields = {"vat_valid": {"value": "Yes", "confidence": 0.99}}
        if name and name not in ("---", "-"):
            fields["legal_name"] = {"value": name, "confidence": 0.95}
        if address and address not in ("---", "-"):
            fields["registered_office"] = {"value": address, "confidence": 0.9}
        return result(self.name, ok=True,
                      detail=f"VAT {country}{number} valid — {name or 'name not disclosed'}.",
                      fields=fields)
