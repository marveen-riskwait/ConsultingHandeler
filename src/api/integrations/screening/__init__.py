"""Screening provider abstraction + registry.

Swap or add a provider by implementing ScreeningProvider and registering it —
the rest of the platform is untouched.
"""
from api.integrations.screening.base import ScreeningProvider, Match
from api.integrations.screening.registry import get_provider, register_provider

__all__ = ["ScreeningProvider", "Match", "get_provider", "register_provider"]
