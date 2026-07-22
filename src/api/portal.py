"""The customer portal — everything a client may see, and nothing else.

Separate blueprint on purpose. The staff API answers "what is this customer?"
with the firm's analysis attached: risk score, screening matches, PEP and
adverse-media flags, cases, alerts, reviews. None of that may reach the client,
and not only for privacy: telling a customer they are flagged can amount to
unlawful disclosure ("tipping off") under the AML directives.

Relying on portal accounts merely lacking permissions did not work — they
legitimately need kyc.view to fill their own questionnaire, and that opened the
whole customer file. So this is an allowlist: portal accounts may call this
blueprint (plus messaging), every route here is scoped to their own customer by
construction rather than by a parameter, and responses are built by
`portal_customer()`, which cannot emit a risk field because it never reads one.

What the client gets: their own declared answers, the documents expected from
them, what they have sent and its state, their submission progress, and the
conversation with the firm. What they never get: the assessment.
"""
from flask import Blueprint, request, jsonify

from api.models import db, Customer, Document, ProfileField, User, utcnow
from api.utils import APIException
from api.auth import login_required
from api.engine import audit, requirement_engine, kyc_service

portal = Blueprint("portal", __name__)

# Reasons an analyst may return a document with. Closed list first, because a
# free-text reason is where an accidental disclosure would happen; the analyst
# may still write one when none of these fit (their call, they know the file).
REJECTION_REASONS = {
    "UNREADABLE": "The document is not readable — please send a clearer copy.",
    "EXPIRED": "The document has expired — please send a current one.",
    "INCOMPLETE": "Some pages are missing — please send the complete document.",
    "WRONG_TYPE": "This is not the type of document we asked for.",
    "NAME_MISMATCH": "The name on the document does not match your file.",
    "TOO_OLD": "The document is older than we can accept — please send a recent one.",
}


def notify_customer(customer, what="something"):
    """Email every portal account attached to this customer. Best effort."""
    from api.integrations import mailer
    org = customer.organization.name if customer.organization else None
    results = []
    for account in User.query.filter_by(customer_id=customer.id,
                                        is_active=True).all():
        results.append(mailer.notify_action_needed(account, org, what))
    return results


def portal_user():
    """The signed-in customer account, or 403. Never trusts a customer id from
    the request: the file is the one attached to the account."""
    from api.auth import current_user
    user = current_user()
    if not user.is_portal_user() or not user.customer_id:
        raise APIException("Customer portal accounts only", status_code=403)
    customer = Customer.query.get(user.customer_id)
    if customer is None:
        raise APIException("No customer file attached to this account",
                           status_code=404)
    return user, customer


def portal_customer(customer):
    """The client's view of their own file — declared identity only.

    Deliberately built from an explicit list. A serializer that filtered a
    dict would leak every field added later; this one can only ever emit what
    is written here.
    """
    return {
        "id": customer.id,
        "name": customer.name,
        "customer_type": customer.customer_type,
        "country": customer.country,
        # Where their *submission* stands — not where the review stands.
        "submitted": customer.status == "SUBMITTED",
    }


def portal_document(doc):
    return {
        "id": doc.id,
        "doc_type": doc.doc_type,
        "file_name": doc.file_name,
        "file_size": doc.file_size,
        "media_type": doc.media_type,
        "description": doc.description,
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        # ACCEPTED / RECEIVED / RETURNED — never the internal VERIFIED wording,
        # and never why a document mattered.
        "state": ("RETURNED" if doc.rejection_reason
                  else "ACCEPTED" if doc.status == "VERIFIED" else "RECEIVED"),
        "returned_reason": doc.rejection_reason,
    }


def _progress(customer):
    """How much of what we asked for has arrived. Counts requested items, not
    compliance completeness — the client sees their own to-do list.

    Each outstanding document carries the `doc_type` the requirement engine
    actually matches on. Sending the requirement *code* instead files the
    document under a type nothing looks at: it never satisfies the requirement
    and never appears under the right row in the analyst's view. The two are
    not the same string — IDENTITY_DOCUMENT is satisfied by a PASSPORT.
    """
    from api.models import RequirementDefinition

    summary = requirement_engine.summary(customer)
    items = summary.get("requirements", [])
    outstanding = [r for r in items if r.get("status") == "MISSING"]
    definitions = {d.id: d for d in RequirementDefinition.query.filter(
        RequirementDefinition.id.in_([r["definition_id"] for r in outstanding
                                      if r.get("definition_id")] or [0])).all()}
    rows = []
    for r in outstanding:
        definition = definitions.get(r.get("definition_id"))
        rows.append({"code": r["code"], "label": r["label"],
                     "kind": r.get("kind"),
                     "doc_type": (definition.doc_type if definition else None)
                                 or r["code"]})
    return {
        "requested": len(items),
        "provided": len(items) - len(outstanding),
        "outstanding": rows,
    }


@portal.route("/me", methods=["GET"])
@login_required
def portal_me(_user):
    user, customer = portal_user()
    return jsonify({
        "user": {"id": user.id, "full_name": user.full_name, "email": user.email},
        "organization": (user.organization.name if user.organization else None),
        "customer": portal_customer(customer),
        "progress": _progress(customer),
    }), 200


@portal.route("/kyc-form", methods=["GET"])
@login_required
def portal_get_form(_user):
    """Their questionnaire: the questions, their own answers, their documents."""
    from api import kyc_form
    from api.models import RISK_RANK
    _u, customer = portal_user()
    # The schema widens with risk (EDD sections). Asking those questions is
    # legitimate enhanced due diligence; exposing the rating that triggered
    # them is not — so the rank drives the schema and never leaves the server.
    rank = RISK_RANK.get(customer.risk_level, 0)
    schema = kyc_form.schema_for(customer.customer_type, rank)
    fields = ProfileField.query.filter_by(customer_id=customer.id).all()
    docs = Document.query.filter_by(customer_id=customer.id).all()
    return jsonify({
        **schema,
        "customer": portal_customer(customer),
        # The client is dealing with the firm, so the firm is named.
        "organization": (_u.organization.name if _u.organization else None),
        # `verified` is the firm's judgement on the answer — not shown.
        "values": {f.field_key: {"value": f.value} for f in fields},
        "documents": [portal_document(d) for d in docs],
        "progress": _progress(customer),
    }), 200


@portal.route("/kyc-form", methods=["POST"])
@login_required
def portal_save_form(_user):
    from api import kyc_form
    user, customer = portal_user()
    body = request.get_json(silent=True) or {}
    answers = body.get("fields") or {}
    index = kyc_form.field_index()
    saved = 0
    for key, value in answers.items():
        if key not in index:
            continue
        if kyc_service.set_field(customer, key, value, source="portal",
                                 actor=user):
            saved += 1
    db.session.commit()
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify({"saved": saved, "progress": _progress(customer)}), 200


@portal.route("/documents", methods=["GET"])
@login_required
def portal_documents(_user):
    _u, customer = portal_user()
    docs = Document.query.filter_by(customer_id=customer.id).all()
    return jsonify([portal_document(d) for d in docs]), 200


@portal.route("/documents", methods=["POST"])
@login_required
def portal_upload_document(_user):
    """Send a document and say what it is, in the client's own words."""
    from api.integrations import media
    user, customer = portal_user()
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise APIException("A file is required", status_code=400)
    doc_type = (request.form.get("doc_type") or "OTHER").strip()
    description = (request.form.get("description") or "").strip()[:500]

    stored = media.store(upload)
    doc = Document(customer_id=customer.id, doc_type=doc_type,
                   status="PENDING", uploaded_by_id=user.id,
                   file_url=stored["url"], file_name=upload.filename[:255],
                   media_type=stored["media_type"], description=description or None)
    try:
        doc.file_size = upload.stream.tell() or None
    except Exception:
        doc.file_size = None
    db.session.add(doc)
    audit.record("DOCUMENT_ADDED", "document", None, actor=user,
                 new_value=f"{doc_type} · {doc.file_name}",
                 reason="Uploaded by the customer through the portal")
    db.session.commit()
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify(portal_document(doc)), 201


@portal.route("/documents/<int:did>", methods=["DELETE"])
@login_required
def portal_delete_document(_user, did):
    """Withdraw a document sent by mistake — only while it is still pending."""
    user, customer = portal_user()
    doc = Document.query.filter_by(id=did, customer_id=customer.id).first()
    if doc is None:
        raise APIException("Document not found", status_code=404)
    if doc.status == "VERIFIED":
        raise APIException("This document has already been accepted and cannot "
                           "be withdrawn.", status_code=409)
    audit.record("DOCUMENT_REMOVED", "document", doc.id, actor=user,
                 old_value=f"{doc.doc_type} · {doc.file_name or 'no file'}",
                 reason="Withdrawn by the customer")
    db.session.delete(doc)
    db.session.commit()
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify({"deleted": True}), 200


@portal.route("/rejection-reasons", methods=["GET"])
@login_required
def portal_rejection_reasons(_user):
    """Shared with the staff UI so both sides use the same wording."""
    return jsonify([{"code": c, "message": m}
                    for c, m in REJECTION_REASONS.items()]), 200


# ---------------------------------------------------------------------------
# Assistant — one thread, about their own outstanding items, nothing else.
# ---------------------------------------------------------------------------
def _portal_conversation(user, customer, create=True):
    from api.models import Conversation
    convo = (Conversation.query
             .filter_by(user_id=user.id, customer_id=customer.id)
             .order_by(Conversation.id.desc()).first())
    if convo is None and create:
        convo = Conversation(organization_id=user.organization_id,
                             user_id=user.id, customer_id=customer.id,
                             title="Help with my file")
        db.session.add(convo)
        db.session.flush()
    return convo


@portal.route("/assistant", methods=["GET"])
@login_required
def portal_assistant(_user):
    """A single ongoing thread — a customer has no use for many."""
    from api.engine import assistant_service
    from api.integrations.ai import get_llm
    user, customer = portal_user()
    convo = _portal_conversation(user, customer)
    db.session.commit()
    return jsonify({
        "provider": get_llm().name,
        "suggested": assistant_service.PORTAL_SUGGESTED_PROMPTS,
        "messages": [m.serialize() for m in convo.messages],
    }), 200


@portal.route("/assistant", methods=["POST"])
@login_required
def portal_assistant_send(_user):
    from api.engine import assistant_service
    user, customer = portal_user()
    text = ((request.get_json(silent=True) or {}).get("message") or "").strip()
    if not text:
        raise APIException("A message is required", status_code=400)
    convo = _portal_conversation(user, customer)
    reply = assistant_service.ask(convo, user, text, portal=True)
    return jsonify(reply.serialize()), 201


# ---------------------------------------------------------------------------
# Submitting — and taking it back while nobody has started reviewing.
# ---------------------------------------------------------------------------
def _open_review_task(customer):
    from api.models import Task
    return (Task.query
            .filter_by(customer_id=customer.id, task_type="KYC_REVIEW")
            .filter(Task.status == "OPEN").first())


def _started_review_task(customer):
    from api.models import Task
    return (Task.query
            .filter_by(customer_id=customer.id, task_type="KYC_REVIEW")
            .filter(Task.status != "OPEN").first())


@portal.route("/kyc-form/submit", methods=["POST"])
@login_required
def portal_submit_form(_user):
    """Hand the file to the team. The questionnaire becomes read-only."""
    from api.engine.events import emit_event
    user, customer = portal_user()
    if customer.status == "SUBMITTED":
        raise APIException("Your file has already been submitted.",
                           status_code=409)
    summary = requirement_engine.summary(customer)
    customer.status = "SUBMITTED"
    audit.record("KYC_FORM_SUBMITTED", "customer", customer.id, actor=user,
                 new_value=f"completeness={summary['completeness_pct']}%",
                 reason="Submitted by the customer through the portal",
                 commit=True)
    emit_event("KYC_FORM_SUBMITTED", customer_id=customer.id, severity="INFO",
               source="portal", actor=user,
               payload={"completeness_pct": summary["completeness_pct"]})
    db.session.commit()
    return jsonify({"submitted": True,
                    "customer": portal_customer(customer)}), 200


@portal.route("/kyc-form/reopen", methods=["POST"])
@login_required
def portal_reopen_form(_user):
    """Take the submission back to correct something.

    Allowed only while nobody has started reviewing: pulling the file out from
    under an analyst mid-review would be worse than making the customer ask.
    The refusal points them at the messages tab rather than leaving them stuck.
    """
    user, customer = portal_user()
    if customer.status != "SUBMITTED":
        raise APIException("Your file is not submitted.", status_code=409)
    if _started_review_task(customer) is not None:
        raise APIException(
            "Our team has already started reviewing your file, so it can no "
            "longer be reopened from here. Send us a message and we will "
            "help you correct it.", status_code=409)

    customer.status = "ONBOARDING"
    audit.record("KYC_FORM_REOPENED", "customer", customer.id, actor=user,
                 new_value="ONBOARDING",
                 reason="Reopened by the customer before review started",
                 commit=True)
    return jsonify({"submitted": False,
                    "customer": portal_customer(customer)}), 200
