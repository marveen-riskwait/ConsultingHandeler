"""Claude adapter — talks to Anthropic's Messages API via the official SDK.

Configuration (all via environment, never hard-coded):
    ANTHROPIC_API_KEY   required to activate this provider
    ANTHROPIC_MODEL     optional, defaults to claude-opus-4-8
    ANTHROPIC_MAX_TOKENS optional, defaults to 3000

The Copilot reasons about compliance files, so we leave adaptive thinking on
(Claude decides how much to think per request). We keep the request
non-streaming: replies are short enough to stay well under the HTTP timeout.
"""
import os

from api.integrations.ai.base import LLMProvider, LLMResult

DEFAULT_MODEL = "claude-opus-4-8"


class ClaudeProvider(LLMProvider):
    name = "claude"
    available = True

    def __init__(self):
        # Import lazily so the app boots even when `anthropic` isn't installed
        # (e.g. no key configured -> MockProvider is used instead).
        import anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS", "3000"))

    def complete(self, system, messages):
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": m["role"], "content": m["content"]}
                      for m in messages],
        )
        # Response content is a list of blocks; keep the text ones.
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = {}
        if getattr(response, "usage", None) is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        return LLMResult(text=text.strip(), model=response.model, usage=usage)
