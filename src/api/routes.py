"""REST API for the Compliance OS vertical slice.

Everything is organization-scoped and audited. The interesting endpoint is
POST /customers/<id>/screen — it kicks off the whole spine:
screening -> event -> rules -> risk -> case/task/notification -> (human) decision.
"""
import os
from datetime import timedelta

from flask import request, jsonify, Blueprint
from flask_cors import CORS

from api.models import (
    db, Organization, User, Customer, Document, RiskAssessment,
    ComplianceEvent, ComplianceRule, Case, Task, Notification, AuditEvent,
    utcnow, ROLES, CUSTOMER_TYPES,
)
from api.utils import APIException
from api.auth import (
    hash_password, verify_password, make_token, current_user,
    login_required, role_required,
)
from api.engine import risk_engine, audit
from api.tasks import run_screening

api = Blueprint("api", __name__)
CORS(api)


def _celery_enabled():
    return bool(os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL"))


def _dispatch(task, *args):
    """Run a Celery task async if a broker is configured, else inline."""
    if _celery_enabled():
        try:
            return task.delay(*args)
        except Exception:
            pass
    return task.run(*args)


def _get_customer_for(user, customer_id):
    customer = Customer.query.get(customer_id)
    if customer is None or customer.organization_id != user.organization_id:
        raise APIException("Customer not found", status_code=404)
    return customer


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@api.route("/auth/register", methods=["POST"])
def register():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        raise APIException("email and password are required", status_code=400)
    if User.query.filter_by(email=email).first():
        raise APIException("Email already registered", status_code=409)

    role = body.get("role", "ANALYST")
    if role not in ROLES:
        role = "ANALYST"

    org_name = (body.get("organization_name") or "").strip()
    org = None
    if org_name:
        org = Organization.query.filter_by(name=org_name).first()
    if org is None:
        org = Organization(name=org_name or f"{email.split('@')[0]}'s org")
        db.session.add(org)
        db.session.flush()

    user = User(
        email=email,
        password=hash_password(password),
        full_name=body.get("full_name") or email.split("@")[0],
        role=role,
        organization_id=org.id,
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"token": make_token(user), "user": user.serialize()}), 201


@api.route("/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if user is None or not verify_password(user, password):
        raise APIException("Invalid credentials", status_code=401)
    return jsonify({"token": make_token(user), "user": user.serialize()}), 200


@api.route("/auth/me", methods=["GET"])
@login_required
def me(user):
    return jsonify({"user": user.serialize(),
                    "organization": user.organization.serialize()}), 200


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
@api.route("/customers", methods=["GET"])
@login_required
def list_customers(user):
    customers = (Customer.query
                 .filter_by(organization_id=user.organization_id)
                 .order_by(Customer.risk_score.desc(), Customer.created_at.desc())
                 .all())
    return jsonify([c.serialize() for c in customers]), 200


@api.route("/customers", methods=["POST"])
@login_required
def create_customer(user):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise APIException("name is required", status_code=400)
    ctype = body.get("customer_type", "INDIVIDUAL")
    if ctype not in CUSTOMER_TYPES:
        ctype = "INDIVIDUAL"

    customer = Customer(
        organization_id=user.organization_id,
        customer_type=ctype,
        name=name,
        country=body.get("country"),
        business_activity=body.get("business_activity"),
        complex_ownership=bool(body.get("complex_ownership", False)),
        status="ONBOARDING",
    )
    db.session.add(customer)
    db.session.flush()
    audit.record("CUSTOMER_CREATED", "customer", customer.id, actor=user,
                 new_value=name, reason="Onboarding")
    db.session.commit()
    # Baseline risk from static factors (geography / activity / ownership).
    risk_engine.recompute(customer, actor=user, reason="Initial assessment")
    return jsonify(customer.serialize()), 201


@api.route("/customers/<int:cid>", methods=["GET"])
@login_required
def customer_overview(user, cid):
    customer = _get_customer_for(user, cid)
    latest = (RiskAssessment.query.filter_by(customer_id=cid)
              .order_by(RiskAssessment.created_at.desc()).first())
    open_cases = (Case.query.filter_by(customer_id=cid)
                  .filter(Case.status != "CLOSED").all())
    tasks = (Task.query.filter_by(customer_id=cid)
             .filter(Task.status != "DONE").all())
    documents = Document.query.filter_by(customer_id=cid).all()
    events = (ComplianceEvent.query.filter_by(customer_id=cid)
              .order_by(ComplianceEvent.detected_at.desc()).limit(15).all())

    # "What changed since the last review" — the time-saving feature.
    since = customer.last_review_at or customer.created_at
    changes = (ComplianceEvent.query.filter_by(customer_id=cid)
               .filter(ComplianceEvent.detected_at >= since)
               .filter(ComplianceEvent.event_type != "SCREENING_CLEARED")
               .order_by(ComplianceEvent.detected_at.desc()).all())

    return jsonify({
        "customer": customer.serialize(),
        "risk": latest.serialize() if latest else None,
        "open_cases": [c.serialize() for c in open_cases],
        "tasks": [t.serialize() for t in tasks],
        "documents": [d.serialize() for d in documents],
        "recent_events": [e.serialize() for e in events],
        "changes_since_review": [e.serialize() for e in changes],
        "last_review_at": since.isoformat() if since else None,
    }), 200


@api.route("/customers/<int:cid>/screen", methods=["POST"])
@login_required
def screen_customer(user, cid):
    customer = _get_customer_for(user, cid)
    audit.record("SCREENING_REQUESTED", "customer", customer.id, actor=user,
                 reason="Manual screening", commit=True)
    _dispatch(run_screening, customer.id)
    return jsonify({
        "message": "Screening started",
        "async": _celery_enabled(),
        "customer_id": customer.id,
    }), 202


@api.route("/customers/<int:cid>/documents", methods=["POST"])
@login_required
def add_document(user, cid):
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    doc_type = body.get("doc_type")
    if not doc_type:
        raise APIException("doc_type is required", status_code=400)
    expiry = body.get("expiry_days")
    doc = Document(
        customer_id=customer.id,
        doc_type=doc_type,
        status=body.get("status", "PENDING"),
        expiry_date=utcnow() + timedelta(days=int(expiry)) if expiry else None,
    )
    db.session.add(doc)
    audit.record("DOCUMENT_ADDED", "document", None, actor=user,
                 new_value=doc_type, reason="Upload", commit=True)
    return jsonify(doc.serialize()), 201


@api.route("/customers/<int:cid>/timeline", methods=["GET"])
@login_required
def customer_timeline(user, cid):
    customer = _get_customer_for(user, cid)
    items = []
    for e in ComplianceEvent.query.filter_by(customer_id=cid).all():
        items.append({"kind": "event", "at": e.detected_at,
                      "severity": e.severity, "label": e.event_type,
                      "detail": e.source})
    for a in (AuditEvent.query.filter_by(entity_type="customer", entity_id=cid).all()):
        items.append({"kind": "audit", "at": a.created_at, "severity": "INFO",
                      "label": a.action,
                      "detail": f"{a.old_value or ''} -> {a.new_value or ''}"})
    for c in Case.query.filter_by(customer_id=cid).all():
        items.append({"kind": "case", "at": c.opened_at, "severity": c.priority,
                      "label": f"Case: {c.title}", "detail": c.status})
    items.sort(key=lambda x: x["at"] or utcnow(), reverse=True)
    for it in items:
        it["at"] = it["at"].isoformat() if it["at"] else None
    return jsonify(items), 200


# ---------------------------------------------------------------------------
# Workspace — role-based action center
# ---------------------------------------------------------------------------
@api.route("/workspace", methods=["GET"])
@login_required
def workspace(user):
    org_id = user.organization_id
    org_customer_ids = [c.id for c in Customer.query
                        .filter_by(organization_id=org_id).all()]

    open_cases = (Case.query.filter(Case.customer_id.in_(org_customer_ids or [0]))
                  .filter(Case.status != "CLOSED").all())
    urgent = [c for c in open_cases if c.priority in ("CRITICAL", "HIGH")]
    open_tasks = (Task.query.filter(Task.customer_id.in_(org_customer_ids or [0]))
                  .filter(Task.status != "DONE").all())
    now = utcnow()
    due_today = [t for t in open_tasks
                 if t.due_at and t.due_at <= now + timedelta(days=1)]
    unread = (Notification.query.filter_by(user_id=user.id, is_read=False).all())

    # A prioritized "my work" inbox: cases first (by priority), then tasks.
    prio = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    my_work = []
    for c in sorted(open_cases, key=lambda x: (prio.get(x.priority, 9),
                                               x.due_at or now)):
        cust = Customer.query.get(c.customer_id)
        my_work.append({
            "kind": "case", "id": c.id, "title": c.title,
            "priority": c.priority, "status": c.status,
            "customer": cust.name if cust else None,
            "customer_id": c.customer_id,
            "due_at": c.due_at.isoformat() if c.due_at else None,
        })
    for t in sorted(open_tasks, key=lambda x: (prio.get(x.priority, 9),
                                               x.due_at or now)):
        cust = Customer.query.get(t.customer_id) if t.customer_id else None
        my_work.append({
            "kind": "task", "id": t.id, "title": t.title,
            "priority": t.priority, "status": t.status,
            "customer": cust.name if cust else None,
            "customer_id": t.customer_id,
            "due_at": t.due_at.isoformat() if t.due_at else None,
        })

    return jsonify({
        "role": user.role,
        "greeting_name": user.full_name,
        "counters": {
            "urgent": len(urgent),
            "due_today": len(due_today),
            "open_cases": len(open_cases),
            "open_tasks": len(open_tasks),
            "unread_notifications": len(unread),
        },
        "my_work": my_work,
    }), 200


# ---------------------------------------------------------------------------
# Cases & Tasks
# ---------------------------------------------------------------------------
@api.route("/cases", methods=["GET"])
@login_required
def list_cases(user):
    org_customer_ids = [c.id for c in Customer.query
                        .filter_by(organization_id=user.organization_id).all()]
    q = Case.query.filter(Case.customer_id.in_(org_customer_ids or [0]))
    status = request.args.get("status")
    if status:
        q = q.filter(Case.status == status)
    cases = q.order_by(Case.opened_at.desc()).all()
    out = []
    for c in cases:
        data = c.serialize()
        cust = Customer.query.get(c.customer_id)
        data["customer_name"] = cust.name if cust else None
        out.append(data)
    return jsonify(out), 200


@api.route("/cases/<int:case_id>", methods=["GET"])
@login_required
def get_case(user, case_id):
    case = Case.query.get(case_id)
    if case is None:
        raise APIException("Case not found", status_code=404)
    customer = _get_customer_for(user, case.customer_id)
    events = (ComplianceEvent.query.filter_by(customer_id=customer.id)
              .order_by(ComplianceEvent.detected_at.desc()).limit(10).all())
    audits = (AuditEvent.query.filter_by(entity_type="case", entity_id=case.id)
              .order_by(AuditEvent.created_at.asc()).all())
    latest = (RiskAssessment.query.filter_by(customer_id=customer.id)
              .order_by(RiskAssessment.created_at.desc()).first())
    return jsonify({
        "case": case.serialize(with_tasks=True),
        "customer": customer.serialize(),
        "risk": latest.serialize() if latest else None,
        "related_events": [e.serialize() for e in events],
        "audit": [a.serialize() for a in audits],
    }), 200


DECISIONS = {"FALSE_POSITIVE", "CONFIRMED_MATCH", "ESCALATE", "CLEARED"}


@api.route("/cases/<int:case_id>/decision", methods=["POST"])
@login_required
def decide_case(user, case_id):
    case = Case.query.get(case_id)
    if case is None:
        raise APIException("Case not found", status_code=404)
    customer = _get_customer_for(user, case.customer_id)
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    reason = (body.get("reason") or "").strip()
    if decision not in DECISIONS:
        raise APIException(f"decision must be one of {sorted(DECISIONS)}",
                           status_code=400)
    if not reason:
        raise APIException("A reason is required for every decision",
                           status_code=400)

    # Confirming a match / overriding is a compliance-officer action.
    if decision == "CONFIRMED_MATCH" and user.role not in ("COMPLIANCE_OFFICER", "ADMIN"):
        raise APIException("Only a Compliance Officer can confirm a match",
                           status_code=403)

    old_status = case.status
    case.decision = decision
    case.decision_reason = reason
    case.decided_by = user.id

    if decision == "ESCALATE":
        case.status = "ESCALATED"
        case.priority = "CRITICAL"
    else:
        case.status = "CLOSED"
        case.closed_at = utcnow()
        # Clearing a false positive removes the signal that drove the case.
        if decision in ("FALSE_POSITIVE", "CLEARED"):
            if case.case_type in ("SANCTIONS_MATCH", "SANCTIONS_MATCH_FOUND"):
                customer.has_sanctions_match = False
            elif case.case_type in ("PEP", "PEP_DETECTED"):
                customer.is_pep = False
            elif case.case_type in ("ADVERSE_MEDIA", "ADVERSE_MEDIA_DETECTED"):
                customer.has_adverse_media = False
        for t in case.tasks:
            if t.status != "DONE":
                t.status = "DONE"

    customer.last_review_at = utcnow()
    audit.record("CASE_DECISION", "case", case.id, actor=user,
                 old_value=old_status, new_value=decision, reason=reason)
    db.session.commit()

    # Decisions can change the risk picture (e.g. a false positive lowers it).
    risk_engine.recompute(customer, actor=user, reason=f"Case decision: {decision}")
    return jsonify(case.serialize(with_tasks=True)), 200


@api.route("/tasks/my-work", methods=["GET"])
@login_required
def my_work(user):
    org_customer_ids = [c.id for c in Customer.query
                        .filter_by(organization_id=user.organization_id).all()]
    tasks = (Task.query.filter(Task.customer_id.in_(org_customer_ids or [0]))
             .filter(Task.status != "DONE")
             .order_by(Task.due_at.asc()).all())
    return jsonify([t.serialize() for t in tasks]), 200


@api.route("/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def complete_task(user, task_id):
    task = Task.query.get(task_id)
    if task is None:
        raise APIException("Task not found", status_code=404)
    if task.customer_id:
        _get_customer_for(user, task.customer_id)
    task.status = "DONE"
    audit.record("TASK_COMPLETED", "task", task.id, actor=user,
                 new_value="DONE", commit=True)
    return jsonify(task.serialize()), 200


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@api.route("/notifications", methods=["GET"])
@login_required
def list_notifications(user):
    notes = (Notification.query.filter_by(user_id=user.id)
             .order_by(Notification.created_at.desc()).limit(50).all())
    return jsonify([n.serialize() for n in notes]), 200


@api.route("/notifications/<int:note_id>/read", methods=["POST"])
@login_required
def read_notification(user, note_id):
    note = Notification.query.get(note_id)
    if note is None or note.user_id != user.id:
        raise APIException("Notification not found", status_code=404)
    note.is_read = True
    db.session.commit()
    return jsonify(note.serialize()), 200


# ---------------------------------------------------------------------------
# Rules & Audit (read-only, for transparency)
# ---------------------------------------------------------------------------
@api.route("/rules", methods=["GET"])
@login_required
def list_rules(user):
    rules = ComplianceRule.query.order_by(ComplianceRule.event_type).all()
    return jsonify([r.serialize() for r in rules]), 200


@api.route("/audit", methods=["GET"])
@login_required
def list_audit(user):
    q = AuditEvent.query
    et = request.args.get("entity_type")
    eid = request.args.get("entity_id")
    if et:
        q = q.filter(AuditEvent.entity_type == et)
    if eid:
        q = q.filter(AuditEvent.entity_id == int(eid))
    entries = q.order_by(AuditEvent.created_at.desc()).limit(100).all()
    return jsonify([a.serialize() for a in entries]), 200


@api.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "celery": _celery_enabled()}), 200
