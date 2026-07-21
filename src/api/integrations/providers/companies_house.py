"""Companies House (UK) KYB adapter — real, live company-registry lookups.

Free API (rate-limited), authenticated with an API key sent as the Basic-auth
username. The key is read from the provider's stored `api_key` credential
(Administration → Integrations) or, as a platform-wide fallback, from the
COMPANIES_HOUSE_API_KEY environment variable.

Flow: search the company name -> take the best hit -> fetch the full profile
and officers -> normalize. Statuses: PASSED (found & active), MATCH (found but
dissolved/liquidation — worth a human look), FAILED (not found).
"""
import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from api.integrations.providers.base import KYBProvider, NormalizedResult

API_BASE = "https://api.company-information.service.gov.uk"
_TIMEOUT = 20


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class CompaniesHouseKYBProvider(KYBProvider):
    adapter_key = "companies_house"

    def _api_key(self):
        return (self.credentials.get("api_key")
                or os.getenv("COMPANIES_HOUSE_API_KEY"))

    def _get(self, path, params=None):
        key = self._api_key()
        if not key:
            raise RuntimeError(
                "companies_house: missing API key. Set it in Administration → "
                "Integrations (api_key) or the COMPANIES_HOUSE_API_KEY env var.")
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        token = base64.b64encode(f"{key}:".encode()).decode()
        req = urllib.request.Request(url, headers={
            "Authorization": f"Basic {token}",
            "User-Agent": "ComplianceOS/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                        context=_ssl_context()) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise RuntimeError("companies_house: API key rejected (401/403)")
            raise RuntimeError(f"companies_house: HTTP {exc.code}")

    def health_check(self):
        if not self._api_key():
            return ("DEGRADED", "API key not configured")
        return ("UP", "credentials present")

    def verify_business(self, entity):
        name = entity.get("name") or ""
        number = entity.get("registration_number")

        if number:
            profile = self._get(f"/company/{number}")
        else:
            found = self._get("/search/companies", {"q": name, "items_per_page": 5}) or {}
            items = found.get("items") or []
            if not items:
                return NormalizedResult(
                    provider="companies_house", provider_reference="",
                    result_type="KYB", status="FAILED",
                    data={"query": name, "reason": "No company found"}, raw=found)
            number = items[0].get("company_number")
            profile = self._get(f"/company/{number}")

        if not profile:
            return NormalizedResult(
                provider="companies_house", provider_reference=str(number or ""),
                result_type="KYB", status="FAILED",
                data={"query": name, "reason": "Company profile not found"}, raw={})

        officers_raw = self._get(f"/company/{number}/officers",
                                 {"items_per_page": 10}) or {}
        officers = [{
            "name": o.get("name"),
            "role": o.get("officer_role"),
            "appointed_on": o.get("appointed_on"),
            "resigned_on": o.get("resigned_on"),
            "nationality": o.get("nationality"),
        } for o in (officers_raw.get("items") or [])]

        status_raw = (profile.get("company_status") or "").lower()
        status = "PASSED" if status_raw == "active" else "MATCH"

        address = profile.get("registered_office_address") or {}
        data = {
            "company_number": profile.get("company_number"),
            "company_name": profile.get("company_name"),
            "company_status": profile.get("company_status"),
            "company_type": profile.get("type"),
            "incorporated_on": profile.get("date_of_creation"),
            "sic_codes": profile.get("sic_codes") or [],
            "registered_office": ", ".join(
                v for v in [address.get("address_line_1"), address.get("locality"),
                            address.get("postal_code"), address.get("country")] if v),
            "officers": officers,
        }
        return NormalizedResult(
            provider="companies_house",
            provider_reference=str(profile.get("company_number") or ""),
            result_type="KYB", status=status,
            data=data, raw={"profile": profile, "officers": officers_raw})

    def company_bundle(self, name=None, number=None):
        """Everything the registry knows: profile + officers + PSC (UBOs).

        Returns {company_number, profile, officers, psc} or None when the
        company can't be found. Used by the enrichment engine.
        """
        if not number:
            found = self._get("/search/companies",
                              {"q": name or "", "items_per_page": 3}) or {}
            items = found.get("items") or []
            if not items:
                return None
            number = items[0].get("company_number")
        profile = self._get(f"/company/{number}")
        if not profile:
            return None
        officers = (self._get(f"/company/{number}/officers",
                              {"items_per_page": 20}) or {}).get("items") or []
        psc = (self._get(
            f"/company/{number}/persons-with-significant-control",
            {"items_per_page": 20}) or {}).get("items") or []
        return {"company_number": number, "profile": profile,
                "officers": officers, "psc": psc}

    def normalize_webhook(self, payload):
        # Companies House has a streaming API rather than signed webhooks; the
        # generic shape below keeps the pipeline happy if one is ever pointed here.
        return NormalizedResult(
            provider="companies_house",
            provider_reference=str(payload.get("resource_id") or ""),
            result_type="KYB", status="PENDING", data=payload, raw=payload)
