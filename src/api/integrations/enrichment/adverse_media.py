"""Adverse-media search over GDELT (free, no key, global press coverage).

Searches the customer name together with a financial-crime lexicon; when the
Copilot has a real AI provider configured, the headlines are additionally
classified by the LLM (relevant / severity) — otherwise a keyword heuristic
scores them. Results feed an ADVERSE_MEDIA_DETECTED event, never a silent flag.
"""
import json
import urllib.parse

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

API = "https://api.gdeltproject.org/api/v2/doc/doc"

CRIME_TERMS = ["money laundering", "fraud", "sanctions", "corruption",
               "bribery", "embezzlement", "terrorist financing", "tax evasion"]

_SEVERE = ("launder", "terror", "sanction", "indict", "convict")


class AdverseMediaSource(EnrichmentSource):
    name = "adverse_media"

    def applies(self, customer):
        return True  # individuals and companies alike

    def _classify_with_ai(self, customer, articles):
        """Best-effort LLM triage of the headlines; None on any failure."""
        from api.integrations.ai import get_llm
        llm = get_llm()
        if llm.name == "mock":
            return None
        headlines = "\n".join(f"{i+1}. {a['title']} ({a['domain']})"
                              for i, a in enumerate(articles))
        prompt = (
            f'Subject under AML review: "{customer.name}" '
            f"({customer.customer_type.lower()}, {customer.country or 'country unknown'}).\n"
            f"Candidate headlines:\n{headlines}\n\n"
            "For each number, answer with STRICT JSON only — a list of objects "
            '{"n": <number>, "relevant": true|false, "severity": "LOW"|"MEDIUM"|"HIGH"} '
            "— relevant means the headline plausibly concerns THIS subject and "
            "a financial-crime topic. No other text.")
        try:
            out = llm.complete("You are a precise AML adverse-media triage "
                               "assistant. Reply with strict JSON only.",
                               [{"role": "user", "content": prompt}])
            text = out.text.strip()
            start, end = text.find("["), text.rfind("]")
            verdicts = json.loads(text[start:end + 1])
            by_n = {int(v["n"]): v for v in verdicts if "n" in v}
            kept = []
            for i, a in enumerate(articles):
                v = by_n.get(i + 1)
                if v and v.get("relevant"):
                    kept.append({**a, "severity": v.get("severity", "MEDIUM"),
                                 "triage": "ai"})
            return kept
        except Exception:
            return None

    def run(self, customer, context=None):
        crime = " OR ".join(f'"{t}"' for t in CRIME_TERMS)
        query = f'"{customer.name}" ({crime})'
        url = (f"{API}?query={urllib.parse.quote(query)}"
               "&mode=artlist&maxrecords=10&format=json&timespan=5y")
        data = get_json(url) or {}
        raw = data.get("articles") or []
        articles = [{"title": a.get("title") or "", "url": a.get("url"),
                     "date": a.get("seendate"), "domain": a.get("domain")}
                    for a in raw if a.get("title")]
        if not articles:
            return result(self.name, detail="No adverse media found.")

        kept = self._classify_with_ai(customer, articles)
        if kept is None:  # keyword heuristic fallback
            kept = []
            for a in articles:
                t = a["title"].lower()
                if any(w.lower() in t for w in customer.name.split() if len(w) > 3):
                    severity = ("HIGH" if any(s in t for s in _SEVERE)
                                else "MEDIUM")
                    kept.append({**a, "severity": severity, "triage": "keywords"})

        detail = (f"{len(articles)} candidate article(s), "
                  f"{len(kept)} retained after triage.")
        return result(self.name, detail=detail, media=kept[:5])
