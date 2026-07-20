"""Pluggable LLM provider for the Compliance Copilot.

The rest of the platform talks to an abstract `LLMProvider`; which concrete
adapter is used is decided at runtime from the environment:

    ANTHROPIC_API_KEY set  -> ClaudeProvider (real Claude)
    otherwise              -> MockProvider (deterministic demo replies)

This mirrors the provider-adapter pattern already used for screening/data
providers: credentials live in the environment, never in the code or the DB.
"""
import os

from api.integrations.ai.base import LLMProvider, LLMResult
from api.integrations.ai.claude import ClaudeProvider
from api.integrations.ai.mock import MockProvider

# Cache the resolved provider so we don't rebuild a client per request.
_PROVIDER = None


def get_llm():
    global _PROVIDER
    if _PROVIDER is None:
        if os.getenv("ANTHROPIC_API_KEY"):
            _PROVIDER = ClaudeProvider()
        else:
            _PROVIDER = MockProvider()
    return _PROVIDER


def reset_llm():
    """Test hook: drop the cached provider so env changes take effect."""
    global _PROVIDER
    _PROVIDER = None


__all__ = ["LLMProvider", "LLMResult", "ClaudeProvider", "MockProvider",
           "get_llm", "reset_llm"]
