"""Backwards-compatible shim.

The provider abstraction moved to api.integrations.screening. This module keeps
`get_provider` importable from its old location.
"""
from api.integrations.screening import get_provider, ScreeningProvider, Match

__all__ = ["get_provider", "ScreeningProvider", "Match"]
