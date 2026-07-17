"""Screening provider abstraction.

The rest of the platform must never depend on a concrete screening vendor. It
depends on this interface; swapping providers later means adding one class.
For the slice we ship a deterministic mock so the flow is reproducible.
"""


class ScreeningProvider:
    def screen(self, subject_name, country=None):
        """Return a list of normalized match dicts:
            {list, match_type, matched_name, match_score, dob, nationality}
        match_type in SANCTIONS | PEP | ADVERSE_MEDIA
        """
        raise NotImplementedError


class MockScreeningProvider(ScreeningProvider):
    """Deterministic mock keyed on the subject's name — good enough to drive the
    whole event -> rule -> risk -> case chain in a demo."""

    def screen(self, subject_name, country=None):
        name = (subject_name or "").lower()
        matches = []

        if "smith" in name or "ivanov" in name:
            matches.append({
                "list": "EU Consolidated Sanctions",
                "match_type": "SANCTIONS",
                "matched_name": subject_name,
                "match_score": 87,
                "dob": "1962-04-11",
                "nationality": "Unknown",
            })
        if "pep" in name or "minister" in name or "ivanov" in name:
            matches.append({
                "list": "Global PEP Database",
                "match_type": "PEP",
                "matched_name": subject_name,
                "match_score": 91,
                "dob": None,
                "nationality": country,
            })
        if "media" in name or "corruption" in name:
            matches.append({
                "list": "Adverse Media",
                "match_type": "ADVERSE_MEDIA",
                "matched_name": subject_name,
                "match_score": 74,
                "article": "Former director investigated for corruption",
            })
        return matches


def get_provider():
    return MockScreeningProvider()
