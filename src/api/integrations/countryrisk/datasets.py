"""Official country-risk lists used by AML geography scoring.

Why this is a dated snapshot rather than a live feed: neither the FATF nor the
European Commission publishes these as a machine-readable file at a stable URL.
FATF revises its lists at each plenary (three times a year) and the EU amends
its delegated regulation occasionally — so the honest design is a snapshot that
*declares its own age*, plus a way to refresh it.

Each dataset therefore carries `as_of` and `source_url`, the API exposes them,
and `is_stale()` flags a snapshot older than a plenary cycle. A compliance
officer can see at a glance whether the geography scoring is current, which is
what an auditor will ask. Point `FATF_LISTS_URL` / `EU_HIGH_RISK_URL` at a JSON
feed of the same shape to refresh automatically.

Shape: {"as_of": "YYYY-MM-DD", "countries": ["..."]}.
"""
import json
import os
from datetime import date, datetime

from api.integrations.sanctions.base import http_get

# Refresh cycle: FATF plenaries are roughly every four months.
STALE_AFTER_DAYS = 150

FATF_CALL_FOR_ACTION = {
    "as_of": "2025-06-13",
    "source_url": "https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/Call-for-action-june-2025.html",
    "label": "FATF Call for Action (black list)",
    "countries": ["Iran", "North Korea", "Myanmar"],
}

FATF_INCREASED_MONITORING = {
    "as_of": "2025-06-13",
    "source_url": "https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/Increased-monitoring-june-2025.html",
    "label": "FATF Increased Monitoring (grey list)",
    "countries": [
        "Algeria", "Angola", "Bolivia", "Bulgaria", "Burkina Faso", "Cameroon",
        "Côte d'Ivoire", "Democratic Republic of the Congo", "Haiti", "Kenya",
        "Laos", "Lebanon", "Monaco", "Mozambique", "Namibia", "Nepal",
        "Nigeria", "South Africa", "South Sudan", "Syria", "Tanzania",
        "Venezuela", "Vietnam", "Virgin Islands (UK)", "Yemen",
    ],
}

EU_HIGH_RISK_THIRD_COUNTRIES = {
    "as_of": "2025-06-10",
    "source_url": "https://finance.ec.europa.eu/financial-crime/high-risk-third-countries_en",
    "label": "EU high-risk third countries",
    "countries": [
        "Afghanistan", "Algeria", "Angola", "Barbados", "Bolivia", "Burkina Faso",
        "Cameroon", "Côte d'Ivoire", "Democratic Republic of the Congo", "Haiti",
        "Iran", "Kenya", "Laos", "Lebanon", "Monaco", "Mozambique", "Myanmar",
        "Namibia", "Nepal", "Nigeria", "North Korea", "South Africa",
        "South Sudan", "Syria", "Tanzania", "Venezuela", "Vietnam", "Yemen",
    ],
}

_ENV_OVERRIDE = {
    "FATF_CALL_FOR_ACTION": "FATF_CALL_FOR_ACTION_URL",
    "FATF_INCREASED_MONITORING": "FATF_INCREASED_MONITORING_URL",
    "EU_HIGH_RISK": "EU_HIGH_RISK_URL",
}

_BUNDLED = {
    "FATF_CALL_FOR_ACTION": FATF_CALL_FOR_ACTION,
    "FATF_INCREASED_MONITORING": FATF_INCREASED_MONITORING,
    "EU_HIGH_RISK": EU_HIGH_RISK_THIRD_COUNTRIES,
}


def is_stale(as_of, today=None):
    try:
        d = datetime.strptime(as_of, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    return ((today or date.today()) - d).days > STALE_AFTER_DAYS


def get(code, prefer_live=True):
    """Return one dataset dict, live from its env URL when configured."""
    bundled = _BUNDLED[code]
    url = os.getenv(_ENV_OVERRIDE[code]) if prefer_live else None
    if url:
        try:
            fetched = json.loads(http_get(url).decode("utf-8"))
            countries = [c for c in fetched.get("countries", []) if c]
            if countries:
                return {**bundled, "as_of": fetched.get("as_of", bundled["as_of"]),
                        "countries": countries, "live": True}
        except Exception:
            pass          # a broken feed must not disable geography scoring
    return {**bundled, "live": False}


def all_lists(prefer_live=True):
    out = {}
    for code in _BUNDLED:
        data = get(code, prefer_live=prefer_live)
        out[code] = {**data, "stale": is_stale(data["as_of"])}
    return out
