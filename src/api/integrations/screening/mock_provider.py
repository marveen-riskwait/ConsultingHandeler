"""Deterministic mock screening provider.

Keyed on the subject's name so the full event -> rule -> risk -> case chain is
reproducible in demos. Swap for real sanctions / PEP / adverse-media providers
later without touching the rest of the platform.
"""
from api.integrations.screening.base import ScreeningProvider, Match


class MockScreeningProvider(ScreeningProvider):
    name = "mock"

    def _screen(self, name, country=None):
        n = (name or "").lower()
        matches = []
        if "smith" in n or "ivanov" in n:
            matches.append(Match(
                match_type="SANCTIONS",
                source="EU Consolidated Sanctions",
                matched_name=name,
                match_score=87,
                data={"dob": "1962-04-11", "nationality": "Unknown",
                      "aliases": ["J. Smith"], "programme": "EU restrictive measures"},
            ))
        if "pep" in n or "minister" in n or "ivanov" in n:
            matches.append(Match(
                match_type="PEP",
                source="Global PEP Database",
                matched_name=name,
                match_score=91,
                data={"pep_type": "CURRENT", "position": "Regional official",
                      "nationality": country},
            ))
        if "media" in n or "corruption" in n:
            matches.append(Match(
                match_type="ADVERSE_MEDIA",
                source="Adverse Media",
                matched_name=name,
                match_score=74,
                data={"article": "Former director investigated for corruption",
                      "category": "financial crime"},
            ))
        return matches

    def screen_person(self, name, country=None, dob=None):
        return self._screen(name, country)

    def screen_company(self, name, country=None, registration_number=None):
        return self._screen(name, country)
