"""Provider registry — one place to choose the active screening provider(s)."""
import os

from api.integrations.screening.mock_provider import MockScreeningProvider
from api.integrations.screening.local_watchlist import (
    LocalWatchlistProvider, CompositeScreeningProvider,
)

_PROVIDERS = {
    "mock": MockScreeningProvider,
    "local_watchlist": LocalWatchlistProvider,
    # Default: real public watchlists (once ingested) + the demo mock.
    "composite": CompositeScreeningProvider,
}


def register_provider(name, cls):
    _PROVIDERS[name] = cls


def get_provider(name=None):
    name = name or os.getenv("SCREENING_PROVIDER", "composite")
    cls = _PROVIDERS.get(name, CompositeScreeningProvider)
    return cls()
