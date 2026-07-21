"""Deterministic fallback provider — used when no ANTHROPIC_API_KEY is set.

It lets the whole Copilot flow (create conversation -> send -> reply -> audit)
work end-to-end in local dev and demos without any credentials or network. The
replies are canned but shaped like the real thing, including the MLRO
disclaimer, so the UI can be exercised faithfully.
"""
from api.integrations.ai.base import LLMProvider, LLMResult

_DISCLAIMER = ("\n\n_(Demo mode — no ANTHROPIC_API_KEY configured. Set one to "
               "enable Claude. As always, validate any AI output with your "
               "MLRO before acting.)_")


class MockProvider(LLMProvider):
    name = "mock"
    available = True

    def check(self):
        return True, ("Demo mode — no AI provider configured. Set "
                      "GEMINI_API_KEY (free) or another provider key in .env.")

    def complete(self, system, messages):
        last_user = next((m["content"] for m in reversed(messages)
                          if m["role"] == "user"), "")
        has_customer = "CUSTOMER CONTEXT" in (system or "")

        lowered = last_user.lower()
        if "sar" in lowered or "suspicious" in lowered:
            body = ("Here is a draft SAR narrative outline you can adapt:\n\n"
                    "1. **Subject** — identify the customer and account(s).\n"
                    "2. **Activity** — describe the transactions, dates and "
                    "amounts that triggered the alert.\n"
                    "3. **Why suspicious** — tie the pattern to the risk "
                    "indicators (e.g. structuring, high-risk jurisdiction).\n"
                    "4. **Action taken** — screening, EDD, escalation.\n\n"
                    "Fill in the specifics from the customer file before filing.")
        elif "high-risk" in lowered or "high risk" in lowered or "why" in lowered:
            body = ("A customer is typically rated high-risk when several "
                    "factors compound: exposure to a high-risk jurisdiction, "
                    "a PEP or sanctions match, opaque ownership, or a "
                    "cash-intensive activity. Review the risk factors on the "
                    "file to see which drove this score.")
        elif has_customer and "summ" in lowered:
            body = ("Summary: this customer's file shows the risk rating, open "
                    "screening matches and outstanding KYC requirements listed "
                    "in the context above. Prioritise clearing any confirmed "
                    "matches and completing verification before onboarding.")
        else:
            body = ("I'm the Compliance Copilot. I can help you draft SAR "
                    "narratives, explain a customer's risk rating, summarise a "
                    "file, or point you to the right control. Ask me about a "
                    "specific customer or task.")

        return LLMResult(text=body + _DISCLAIMER, model="mock",
                         usage={"input_tokens": 0, "output_tokens": 0})
