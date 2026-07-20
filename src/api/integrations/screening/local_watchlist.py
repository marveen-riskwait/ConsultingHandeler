"""Screening against the locally-ingested public sanctions lists.

`LocalWatchlistProvider` matches a subject against SanctionedEntity (OFAC / UN /
EU rows loaded by the watchlist service). `CompositeScreeningProvider` runs the
local watchlist AND the deterministic mock, so real sanctioned names hit real
list entries while the demo names keep firing the full chain.
"""
from api.integrations.screening.base import ScreeningProvider, Match
from api.integrations.screening.mock_provider import MockScreeningProvider

_SOURCE_LABELS = {
    "OFAC": "OFAC SDN (US Treasury)",
    "UN": "UN Consolidated List",
    "EU": "EU Consolidated List",
}


class LocalWatchlistProvider(ScreeningProvider):
    name = "local_watchlist"

    def _screen(self, name, country=None):
        # Imported lazily: this provider is only usable inside the app context.
        from api.engine import watchlist_service

        matches = []
        for entity, score in watchlist_service.search(name, limit=10):
            matches.append(Match(
                match_type="SANCTIONS",
                source=_SOURCE_LABELS.get(entity.source, entity.source),
                matched_name=entity.name,
                match_score=score,
                data={
                    "list_source": entity.source,
                    "external_id": entity.external_id,
                    "entity_type": entity.entity_type,
                    "programs": entity.programs or [],
                    "aliases": entity.aliases or [],
                    "country": entity.country,
                    "remarks": entity.remarks,
                },
            ))
        return matches

    def screen_person(self, name, country=None, dob=None):
        return self._screen(name, country)

    def screen_company(self, name, country=None, registration_number=None):
        return self._screen(name, country)


class CompositeScreeningProvider(ScreeningProvider):
    """Local public watchlists first, then the demo mock; de-duped."""
    name = "composite"

    def __init__(self):
        self._providers = [LocalWatchlistProvider(), MockScreeningProvider()]

    def _screen(self, method, *args, **kwargs):
        seen, merged = set(), []
        for p in self._providers:
            for m in getattr(p, method)(*args, **kwargs):
                key = (m.match_type, m.source, m.matched_name)
                if key not in seen:
                    seen.add(key)
                    merged.append(m)
        return merged

    def screen_person(self, name, country=None, dob=None):
        return self._screen("screen_person", name, country=country, dob=dob)

    def screen_company(self, name, country=None, registration_number=None):
        return self._screen("screen_company", name, country=country,
                            registration_number=registration_number)
