"""UK registry enrichment: profile + officers + PSC (real UBOs).

Uses the existing Companies House KYB adapter (per-org api_key credential or
COMPANIES_HOUSE_API_KEY env). Skips cleanly when no key is configured.
"""
from api.integrations.enrichment.base import EnrichmentSource, result

# PSC natures_of_control -> conservative ownership percentage (lower bound).
_PSC_PCT = {
    "ownership-of-shares-25-to-50-percent": 25.0,
    "ownership-of-shares-50-to-75-percent": 50.0,
    "ownership-of-shares-75-to-100-percent": 75.0,
    "voting-rights-25-to-50-percent": 25.0,
    "voting-rights-50-to-75-percent": 50.0,
    "voting-rights-75-to-100-percent": 75.0,
}


def _adapter_for(customer):
    """Adapter with the org's stored credential when available, env otherwise."""
    from api.engine.provider_service import find_provider
    from api.integrations.providers.registry import get_adapter
    from api.integrations.providers.companies_house import CompaniesHouseKYBProvider
    row = find_provider(customer.organization_id, name="Companies House",
                        provider_type="KYB")
    if row is not None:
        adapter = get_adapter(row)
        if isinstance(adapter, CompaniesHouseKYBProvider):
            return adapter
    return CompaniesHouseKYBProvider()


_UK_NAMES = ("united kingdom", "uk", "gb", "great britain", "england",
             "scotland", "wales", "northern ireland")


def _anchored(customer, reg_field):
    """A UK-register import must be anchored: either someone OTHER than this
    source recorded a registration number (the bundle was fetched by it), or
    the customer is a UK company. A bare name match is NOT enough — it once
    imported the director of a dissolved UK homonym into a Luxembourg
    insurer's UBOs. A number this source wrote itself is no anchor: that
    would let the homonym vouch for the homonym."""
    if reg_field is not None and (reg_field.value or "").strip() \
            and (reg_field.source or "") != "registry:companies_house":
        return True
    return (customer.country or "").strip().lower() in _UK_NAMES


class CompaniesHouseSource(EnrichmentSource):
    name = "companies_house"

    def applies(self, customer):
        return customer.customer_type == "COMPANY"

    def run(self, customer, context=None):
        adapter = _adapter_for(customer)
        if not adapter._api_key():
            return result(self.name, ok=False,
                          detail="No Companies House API key configured — skipped.")

        from api.models import ProfileField
        reg = (ProfileField.query
               .filter_by(customer_id=customer.id, field_key="registration_number")
               .first())
        anchored = _anchored(customer, reg)
        # A self-written number must not even drive the query — the homonym
        # would keep fetching itself by its own id.
        number = reg.value if (reg and anchored) else None
        bundle = adapter.company_bundle(name=customer.name, number=number)
        if bundle is None:
            return result(self.name, ok=False,
                          detail="Company not found in the UK register.")

        if not anchored:
            # Report the finding, import nothing: name-only matches on
            # non-UK customers are homonyms until a human anchors them.
            profile = bundle["profile"]
            return result(
                self.name, ok=False,
                detail=f"Name-only match in the UK register: "
                       f"{profile.get('company_name')} "
                       f"#{bundle['company_number']} "
                       f"({profile.get('company_status')}). Not imported — "
                       f"set registration_number if this really is the "
                       f"same company.")

        profile = bundle["profile"]
        address = profile.get("registered_office_address") or {}
        fields = {
            "legal_name": profile.get("company_name"),
            "registration_number": profile.get("company_number"),
            "legal_form": profile.get("type"),
            "date_of_incorporation": profile.get("date_of_creation"),
            "country_of_incorporation": "United Kingdom",
            "registered_office": ", ".join(
                v for v in [address.get("address_line_1"), address.get("locality"),
                            address.get("postal_code"), address.get("country")] if v),
            "nace_code": ", ".join(profile.get("sic_codes") or []),
        }
        fields = {k: {"value": v, "confidence": 0.95}
                  for k, v in fields.items() if v}

        parties = []
        for o in bundle["officers"]:
            if o.get("resigned_on"):
                continue
            parties.append({"name": o.get("name"), "kind": "PERSON",
                            "relationship_type": "DIRECTOR", "percentage": 0.0,
                            "nationality": o.get("nationality"),
                            "country": (o.get("address") or {}).get("country")})
        for p in bundle["psc"]:
            if p.get("ceased_on"):
                continue
            pct = max([_PSC_PCT.get(n, 0.0)
                       for n in (p.get("natures_of_control") or [])] or [25.0])
            kind = ("PERSON" if (p.get("kind") or "").startswith("individual")
                    else "ORGANIZATION")
            parties.append({"name": p.get("name"), "kind": kind,
                            "relationship_type": "SHAREHOLDER",
                            "percentage": pct,
                            "nationality": p.get("nationality"),
                            "country": p.get("country_of_residence")})

        return result(self.name, detail=f"UK register match "
                      f"#{bundle['company_number']} "
                      f"({profile.get('company_status')})",
                      fields=fields, parties=parties)
