"""Screening provider interface + a normalized Match shape."""
from dataclasses import dataclass, field


@dataclass
class Match:
    """Normalized screening hit — providers translate their payloads into this."""
    match_type: str            # SANCTIONS | PEP | ADVERSE_MEDIA
    source: str                # list / provider label
    matched_name: str
    match_score: int           # 0-100
    data: dict = field(default_factory=dict)  # dob, nationality, aliases, article, ...

    def as_dict(self):
        return {
            "match_type": self.match_type,
            "source": self.source,
            "matched_name": self.matched_name,
            "match_score": self.match_score,
            "data": self.data,
        }


class ScreeningProvider:
    name = "abstract"

    def screen_person(self, name, country=None, dob=None):
        raise NotImplementedError

    def screen_company(self, name, country=None, registration_number=None):
        raise NotImplementedError

    # Convenience used by the service; providers may override.
    def screen(self, name, country=None, kind="PERSON"):
        if kind == "ORGANIZATION":
            return self.screen_company(name, country=country)
        return self.screen_person(name, country=country)
