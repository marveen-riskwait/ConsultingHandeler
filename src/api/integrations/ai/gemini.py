"""Google Gemini adapter — the easiest FREE option for testing the Copilot.

Get a free API key at https://aistudio.google.com (Google AI Studio) and set:

    GEMINI_API_KEY=...      # required to activate this provider
    GEMINI_MODEL=...        # optional — leave unset for auto-discovery

Google rotates model names and free-tier quotas frequently (2.0-flash keys
started 429ing with "limit: 0", 2.5-flash-lite became "no longer available to
new users"…). So instead of hardcoding a name, this adapter asks the API which
models THIS key can actually use (ListModels) and walks the best candidates,
falling back automatically when one answers 404 (retired) or 429 (no quota).
The first model that works is cached for the rest of the process lifetime.
"""
import os
import re

from api.integrations.ai.base import LLMProvider, LLMResult, get_json, post_json

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Tried first when the API listing is unavailable for some reason.
FALLBACK_CANDIDATES = ["gemini-2.5-flash", "gemini-flash-latest",
                       "gemini-2.0-flash"]


def _rank(name):
    """Prefer newest version, flash-class (free-tier friendly), short names."""
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    version = float(m.group(1)) if m else 0.0
    return (-version, 0 if "flash" in name else 1, len(name))


class GeminiProvider(LLMProvider):
    name = "gemini"
    available = True

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL")  # None -> auto-discover
        self.max_tokens = int(os.getenv("AI_MAX_TOKENS", "3000"))
        self._resolved = None  # first model that actually answered

    def _headers(self):
        return {"x-goog-api-key": self.api_key}

    def _available_models(self):
        """Model names this key can call generateContent on, best first."""
        data = get_json(f"{API_BASE}?pageSize=200", headers=self._headers())
        names = [
            (m.get("name") or "").split("/")[-1]
            for m in data.get("models", [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
        names = [n for n in names if n.startswith("gemini")
                 and "embedding" not in n]
        return sorted(names, key=_rank)

    def _candidates(self):
        if self._resolved:
            return [self._resolved]
        cands = [self.model] if self.model else []
        try:
            for n in self._available_models():
                if n not in cands:
                    cands.append(n)
        except Exception:
            for n in FALLBACK_CANDIDATES:
                if n not in cands:
                    cands.append(n)
        return cands[:6]

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

        errors = []
        for model in self._candidates():
            try:
                data = post_json(f"{API_BASE}/{model}:generateContent",
                                 payload, headers=self._headers())
            except RuntimeError as exc:
                text = str(exc)
                # Retired model (404) or zero/exhausted quota (429): try the
                # next candidate. Anything else is a real error — surface it.
                if "HTTP 404" in text or "HTTP 429" in text:
                    errors.append(f"{model}: {text[:120]}")
                    continue
                raise
            self._resolved = model  # remember what worked
            candidates = data.get("candidates") or []
            parts = ((candidates[0].get("content") or {}).get("parts")
                     if candidates else None) or []
            out = "".join(p.get("text", "") for p in parts).strip()
            meta = data.get("usageMetadata") or {}
            return LLMResult(
                text=out or "(no response)",
                model=model,
                usage={"input_tokens": meta.get("promptTokenCount"),
                       "output_tokens": meta.get("candidatesTokenCount")})

        try:
            listing = ", ".join(self._available_models()[:10]) or "none found"
        except Exception:
            listing = "could not list models"
        raise RuntimeError(
            "No Gemini model worked with this key. Tried: "
            + " | ".join(errors)
            + f". Models your key can use: {listing}. "
              "Set GEMINI_MODEL in .env to one of those, or wait if quotas "
              "are exhausted (free tier is per-minute and per-day).")
