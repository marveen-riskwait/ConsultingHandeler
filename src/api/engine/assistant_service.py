"""Compliance Copilot orchestration.

Assembles the prompt (persona + optional customer context + history), calls the
configured LLM provider, persists both turns, and audits the interaction. Route
code stays thin — it never talks to the provider directly.
"""
from api.models import (
    db, Conversation, Message, Customer, RiskAssessment, ScreeningMatch,
    RequirementInstance, ACTIVE_MATCH_STATUSES,
)
from api.engine import audit
from api.integrations.ai import get_llm

# The persona is deliberately advisory: the Copilot assists, the human decides.
SYSTEM_PROMPT = (
    "You are the Compliance Copilot inside an AML/KYC compliance platform used "
    "by MLROs, compliance officers and analysts. You help them work faster: "
    "drafting SAR narratives, explaining a customer's risk rating, summarising "
    "a customer file, interpreting sanctions/PEP screening results, and "
    "pointing to the relevant control or requirement.\n\n"
    "Rules:\n"
    "- You are advisory only. You never make or approve a compliance decision; "
    "you help the human make it. Always remind the user to validate your output "
    "with their MLRO before acting on anything material.\n"
    "- Be concise and practical. Prefer structured, checklist-style answers.\n"
    "- Never invent facts about a specific customer. Only use the customer "
    "context provided to you; if something isn't there, say so.\n"
    "- You cannot take actions in the platform (you cannot screen, escalate, "
    "close cases or file reports) — describe what the user should do instead."
)

# Shown in the UI empty state so users know what the Copilot is good at.
SUGGESTED_PROMPTS = [
    "Draft a SAR narrative outline for a structuring alert.",
    "Explain what makes a customer high-risk.",
    "What EDD steps apply to a PEP match?",
    "Summarise the key AML red flags to check at onboarding.",
]


def customer_context(customer):
    """Compact, model-readable snapshot of a customer file."""
    lines = [
        "CUSTOMER CONTEXT (use only this; do not invent):",
        f"- Name: {customer.name}",
        f"- Type: {customer.customer_type}",
        f"- Country: {customer.country or 'unknown'}",
        f"- Business activity: {customer.business_activity or 'n/a'}",
        f"- Status: {customer.status}",
        f"- Risk: {customer.risk_level} (score {customer.risk_score})",
        f"- PEP: {customer.is_pep} | Sanctions match: {customer.has_sanctions_match} "
        f"| Adverse media: {customer.has_adverse_media} "
        f"| Complex ownership: {customer.complex_ownership}",
    ]

    latest = (RiskAssessment.query
              .filter_by(customer_id=customer.id)
              .order_by(RiskAssessment.id.desc()).first())
    if latest and latest.factors:
        factors = ", ".join(
            (f.get("label") or f.get("code") or str(f)) if isinstance(f, dict) else str(f)
            for f in latest.factors)
        lines.append(f"- Risk factors ({latest.methodology_version}): {factors}")

    matches = (ScreeningMatch.query
               .filter(ScreeningMatch.customer_id == customer.id,
                       ScreeningMatch.status.in_(ACTIVE_MATCH_STATUSES))
               .all())
    if matches:
        lines.append("- Open screening matches:")
        for m in matches[:8]:
            lines.append(f"    * {m.match_type} — {m.matched_name or '?'} "
                         f"({m.source or 'list'}, score {m.match_score}, {m.status})")

    pending = (RequirementInstance.query
               .filter(RequirementInstance.customer_id == customer.id,
                       RequirementInstance.status != "SATISFIED")
               .all())
    if pending:
        lines.append("- Outstanding requirements: " +
                     ", ".join(f"{r.label or r.code} [{r.status}]" for r in pending[:12]))

    return "\n".join(lines)


def _history(conversation):
    """Chronological user/assistant turns for the model (skip system rows)."""
    return [{"role": m.role, "content": m.content}
            for m in conversation.messages if m.role in ("user", "assistant")]


def ask(conversation, user, text):
    """Persist the user turn, call the LLM, persist + return the reply."""
    # Append via the relationship so the in-memory collection (and therefore
    # the history we send to the model) includes this turn.
    conversation.messages.append(Message(role="user", content=text))
    # First user message becomes the conversation title.
    if conversation.title in (None, "", "New conversation"):
        conversation.title = (text[:60] + "…") if len(text) > 60 else text
    db.session.flush()

    system = SYSTEM_PROMPT
    if conversation.customer_id:
        customer = Customer.query.get(conversation.customer_id)
        if customer and customer.organization_id == user.organization_id:
            system = SYSTEM_PROMPT + "\n\n" + customer_context(customer)

    result = get_llm().complete(system, _history(conversation))

    reply = Message(role="assistant",
                    content=result.text or "(no response)",
                    meta={"model": result.model, "usage": result.usage})
    conversation.messages.append(reply)

    audit.record(
        "ASSISTANT_MESSAGE", "conversation", conversation.id,
        actor=user,
        metadata={"customer_id": conversation.customer_id,
                  "model": result.model, "usage": result.usage},
    )
    db.session.commit()
    return reply
