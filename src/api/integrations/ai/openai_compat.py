"""OpenAI-compatible adapter (chat/completions wire format).

Works with ChatGPT's API (paid) and, more usefully for free testing, with any
OpenAI-compatible endpoint by overriding the base URL:

    OPENAI_API_KEY=...                       # required to activate
    OPENAI_MODEL=gpt-4o-mini                 # optional
    OPENAI_BASE_URL=https://api.openai.com/v1   # optional; e.g.
        https://api.groq.com/openai/v1       (Groq — free tier)
        https://openrouter.ai/api/v1         (OpenRouter — free models)
        http://localhost:11434/v1            (Ollama — local, key can be "ollama")
"""
import os

from api.integrations.ai.base import LLMProvider, LLMResult, post_json

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAICompatProvider(LLMProvider):
    name = "openai"
    available = True

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.base_url = (os.getenv("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "3000"))

    def complete(self, system, messages):
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env (any OpenAI-"
                "compatible backend key works — OpenAI, Groq, OpenRouter, "
                "Ollama) and restart the backend.")
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system},
                         *[{"role": m["role"], "content": m["content"]}
                           for m in messages]],
        }
        data = post_json(f"{self.base_url}/chat/completions", payload,
                         headers={"Authorization": f"Bearer {self.api_key}"})

        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content")
                if choices else "") or ""
        usage = data.get("usage") or {}
        return LLMResult(
            text=text.strip() or "(no response)",
            model=data.get("model") or self.model,
            usage={"input_tokens": usage.get("prompt_tokens"),
                   "output_tokens": usage.get("completion_tokens")})
