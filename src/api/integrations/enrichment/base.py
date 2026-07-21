"""Shared shape for enrichment sources."""
# HTTP helpers shared with the AI adapters (stdlib + certifi, no new deps).
from api.integrations.ai.base import get_json, post_json  # noqa: F401


def result(source, ok=True, detail=None, fields=None, parties=None, media=None):
    """Normalized source output.

    fields:  {profile_field_key: {"value": str, "confidence": float}}
    parties: [{name, kind, relationship_type, percentage, country, nationality}]
    media:   [{title, url, date, domain, severity}]
    """
    return {"source": source, "ok": ok, "detail": detail,
            "fields": fields or {}, "parties": parties or [],
            "media": media or []}


class EnrichmentSource:
    name = "base"

    def applies(self, customer):  # pragma: no cover - interface
        return False

    def run(self, customer, context=None):  # pragma: no cover - interface
        raise NotImplementedError
