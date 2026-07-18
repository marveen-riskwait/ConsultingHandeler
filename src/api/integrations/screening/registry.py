"""Provider registry — one place to choose the active screening provider(s)."""
import os

from api.integrations.screening.mock_provider import MockScreeningProvider

_PROVIDERS = {
    "mock": MockScreeningProvider,
}


def register_provider(name, cls):
    _PROVIDERS[name] = cls


def get_provider(name=None):
    name = name or os.getenv("SCREENING_PROVIDER", "mock")
    cls = _PROVIDERS.get(name, MockScreeningProvider)
    return cls()
