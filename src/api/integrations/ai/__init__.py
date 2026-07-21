"""Pluggable LLM provider for the Compliance Copilot.

The rest of the platform talks to an abstract `LLMProvider`; which concrete
adapter is used is decided at runtime from the environment:

    AI_PROVIDER=gemini|openai|claude|mock   # explicit choice (wins)

or, when AI_PROVIDER is unset, by whichever key is present (first match):

    ANTHROPIC_API_KEY -> ClaudeProvider  (Anthropic SDK)
    GEMINI_API_KEY    -> GeminiProvider  (free key from aistudio.google.com)
    OPENAI_API_KEY    -> OpenAICompatProvider (OpenAI / Groq / OpenRouter / Ollama)
    none              -> MockProvider    (deterministic offline demo)

This mirrors the provider-adapter pattern used for screening/data providers:
credentials live in the environment, never in the code or the DB.
"""
import os

from api.integrations.ai.base import LLMProvider, LLMResult
from api.integrations.ai.claude import ClaudeProvider
from api.integrations.ai.gemini import GeminiProvider
from api.integrations.ai.openai_compat import OpenAICompatProvider
from api.integrations.ai.mock import MockProvider

# Cache the resolved provider so we don't rebuild a client per request.
_PROVIDER = None


def _resolve():
    choice = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if choice == "claude":
        return ClaudeProvider()
    if choice == "gemini":
        return GeminiProvider()
    if choice == "openai":
        return OpenAICompatProvider()
    if choice == "mock":
        return MockProvider()
    # No explicit choice: pick by available credential.
    if os.getenv("ANTHROPIC_API_KEY"):
        return ClaudeProvider()
    if os.getenv("GEMINI_API_KEY"):
        return GeminiProvider()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAICompatProvider()
    return MockProvider()


def get_llm():
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _resolve()
    return _PROVIDER


def reset_llm():
    """Test hook: drop the cached provider so env changes take effect."""
    global _PROVIDER
    _PROVIDER = None


__all__ = ["LLMProvider", "LLMResult", "ClaudeProvider", "GeminiProvider",
           "OpenAICompatProvider", "MockProvider", "get_llm", "reset_llm"]
