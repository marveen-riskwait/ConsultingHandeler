"""Google Gemini adapter — the easiest FREE option for testing the Copilot.

Get a free API key at https://aistudio.google.com (Google AI Studio) and set:

    GEMINI_API_KEY=...        # required to activate this provider
    GEMINI_MODEL=gemini-2.0-flash   # optional (free-tier friendly default)

Plain REST via the generativelanguage API — no SDK dependency.
"""
import os

from api.integrations.ai.base import LLMProvider, LLMResult, post_json

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"
    available = True

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "3000"))

    def complete(self, system, messages):
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com and add it to .env "
                "(then restart the backend).")
        # Gemini calls the assistant role "model".
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]}
                    for m in messages]
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self.max_tokens},
        }
        data = post_json(f"{API_BASE}/{self.model}:generateContent",
                         payload, headers={"x-goog-api-key": self.api_key})

        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts")
                 if candidates else None) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        meta = data.get("usageMetadata") or {}
        return LLMResult(
            text=text or "(no response)",
            model=self.model,
            usage={"input_tokens": meta.get("promptTokenCount"),
                   "output_tokens": meta.get("candidatesTokenCount")})
