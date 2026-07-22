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

# The customer-facing persona. Separate constant, not a variation of the staff
# one, because the difference is not tone — it is what the assistant is allowed
# to know. Telling a customer they are flagged can be unlawful disclosure
# ("tipping off") under the AML directives, so the model is never handed the
# assessment in the first place: nothing here can be prompted out of it.
PORTAL_SYSTEM_PROMPT = (
    "You are the onboarding assistant of a compliance firm, talking directly "
    "to one of its customers inside their secure portal. Your only job is to "
    "help this person complete what the firm has asked them for.\n\n"
    "Rules:\n"
    "- Speak on behalf of the firm, never as a named individual.\n"
    "- Help with exactly one thing: what is still outstanding on this "
    "customer's own file, what each requested item means, and how to provide "
    "it. Be concrete and encouraging.\n"
    "- You have no information about any other customer and must never "
    "speculate about one.\n"
    "- You do not know, and must never discuss, this customer's risk rating, "
    "screening results, checks being performed on them, internal reviews, or "
    "any assessment the firm has made. If asked, say plainly that you can only "
    "help with their outstanding items and that their contact at the firm will "
    "reach out about anything else — then offer to help with what is missing.\n"
    "- Never promise an outcome, a decision, or a timeline for approval.\n"
    "- Only use the context provided below. If something is not there, say you "
    "don't have it and suggest sending a message to the team."
)

PORTAL_SUGGESTED_PROMPTS = [
    "What do you still need from me?",
    "What counts as a proof of address?",
    "I don't have the document you asked for — what are my options?",
    "How do I send a document?",
]


def portal_customer_context(customer):
    """What the assistant may know when it is talking to the customer.

    Built from scratch rather than trimming `customer_context()`: this function
    must be unable to emit a risk field, and the way to guarantee that is for
    it never to read one.
    """
    from api.models import Document
    from api.engine import requirement_engine

    lines = [
        "CUSTOMER CONTEXT (use only this; do not invent):",
        f"- You are speaking with: {customer.name}",
        f"- File type: {customer.customer_type}",
    ]
    summary = requirement_engine.summary(customer)
    outstanding = [r for r in summary.get("requirements", [])
                   if r.get("status") == "MISSING"]
    if outstanding:
        lines.append("- Still outstanding (this is what you help with):")
        for r in outstanding:
            kind = "document" if r.get("kind") == "DOCUMENT" else "information"
            lines.append(f"    - {r['label']} ({kind})")
    else:
        lines.append("- Nothing is outstanding: everything asked for has been "
                     "provided. Thank them and say the team will be in touch.")

    docs = [d for d in Document.query.filter_by(customer_id=customer.id).all()
            if d.file_url]
    if docs:
        lines.append("- Documents they have already sent:")
        for d in docs[:15]:
            note = " (we asked them to send it again)" if d.rejection_reason else ""
            lines.append(f"    - {d.doc_type}: {d.file_name}{note}")
    return "\n".join(lines)


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


def ask(conversation, user, text, portal=False):
    """Persist the user turn, call the LLM, persist + return the reply.

    `portal=True` switches both the persona and the context to the
    customer-facing pair — the assessment is never assembled at all.
    """
    # Append via the relationship so the in-memory collection (and therefore
    # the history we send to the model) includes this turn.
    conversation.messages.append(Message(role="user", content=text))
    # First user message becomes the conversation title.
    if conversation.title in (None, "", "New conversation"):
        conversation.title = (text[:60] + "…") if len(text) > 60 else text
    db.session.flush()

    base = PORTAL_SYSTEM_PROMPT if portal else SYSTEM_PROMPT
    build_context = portal_customer_context if portal else customer_context
    system = base
    if conversation.customer_id:
        customer = Customer.query.get(conversation.customer_id)
        if customer and customer.organization_id == user.organization_id:
            system = base + "\n\n" + build_context(customer)

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
