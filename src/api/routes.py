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
    db, Organization, User, Role, Permission, Customer, Document, RiskAssessment,
    ComplianceEvent, ComplianceRule, Case, Task, Notification, AuditEvent,
    Party, Person, LegalEntity, Address, OwnershipRelationship,
    ScreeningRun, ScreeningMatch,
    Department, Team, OrganizationMembership, TeamMembership, AccessPolicy,
    Invitation, AssignmentRule, SLAConfiguration,
    ProfileField, RequirementDefinition, RequirementInstance,
    Provider, ProviderCredential, NormalizedComplianceResult, WebhookEvent,
    PROVIDER_TYPES, ComplianceAlert, Review, REVIEW_TYPES,
    RiskMethodology,
    utcnow, ROLES, CUSTOMER_TYPES, PARTY_KINDS, PERMISSION_CATALOG,
)
from api.utils import APIException
from api.auth import (
    hash_password, verify_password, make_token, current_user,
    login_required, role_required, permission_required, has_permission,
)
from api.engine import (risk_engine, audit, ownership, data_scope, workload,
                        sla, assignment, party_service, requirement_engine,
                        kyc_service, provider_service, alert_service,
                        review_engine)
from api.engine.screening_service import review_match
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

    role_name = body.get("role", "KYC_ANALYST")
    if role_name not in ROLES:
        role_name = "KYC_ANALYST"
    # First user of a brand-new organization is its administrator.

    org_name = (body.get("organization_name") or "").strip()
    org = None
    if org_name:
        org = Organization.query.filter_by(name=org_name).first()
    is_new_org = org is None
    if org is None:
        org = Organization(name=org_name or f"{email.split('@')[0]}'s org")
        db.session.add(org)
        db.session.flush()
    if is_new_org:
        role_name = "ORGANIZATION_ADMIN"

    from api.rbac import get_role
    role = get_role(role_name)

    user = User(
        email=email,
        password=hash_password(password),
        full_name=body.get("full_name") or email.split("@")[0],
        role=role_name,
        role_id=role.id if role else None,
        organization_id=org.id,
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(OrganizationMembership(
        organization_id=org.id, user_id=user.id, status="ACTIVE"))
    audit.record("USER_CREATED", "user", user.id, actor_label=email,
                 new_value=role_name, reason="self-registration")
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
    if not user.is_active:
        raise APIException("This account has been disabled", status_code=401)
    return jsonify({"token": make_token(user), "user": user.serialize()}), 200


@api.route("/auth/me", methods=["GET"])
@login_required
def me(user):
    team_ids = data_scope.user_team_ids(user)
    teams = Team.query.filter(Team.id.in_(team_ids or [0])).all()
    return jsonify({
        "user": user.serialize(),
        "organization": user.organization.serialize(),
        "teams": [t.serialize() for t in teams],
        "data_scope": {"case": data_scope.resolve_scope(user, "case"),
                       "customer": data_scope.resolve_scope(user, "customer")},
    }), 200


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
@api.route("/customers", methods=["GET"])
@permission_required("customer.view")
def list_customers(user):
    customers = (Customer.query
                 .filter_by(organization_id=user.organization_id)
                 .order_by(Customer.risk_score.desc(), Customer.created_at.desc())
                 .all())
    return jsonify([c.serialize() for c in customers]), 200


@api.route("/customers", methods=["POST"])
@permission_required("customer.create")
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
    # Onboarding schedules the initial KYC review.
    review_engine.schedule_initial(customer, actor=user)
    return jsonify(customer.serialize()), 201


@api.route("/customers/<int:cid>", methods=["GET"])
@permission_required("customer.view")
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

    matches = (ScreeningMatch.query.filter_by(customer_id=cid)
               .order_by(ScreeningMatch.first_detected_at.desc()).all())
    ubos = ownership.compute_ubos(customer)
    completeness = requirement_engine.summary(customer)
    reviews = (Review.query.filter_by(customer_id=cid)
               .order_by(Review.created_at.desc()).all())
    open_alerts = (ComplianceAlert.query.filter_by(customer_id=cid)
                   .filter(ComplianceAlert.status.notin_(["RESOLVED", "DISMISSED"]))
                   .all())

    return jsonify({
        "customer": customer.serialize(),
        "risk": latest.serialize() if latest else None,
        "open_cases": [c.serialize() for c in open_cases],
        "tasks": [t.serialize() for t in tasks],
        "documents": [d.serialize() for d in documents],
        "screening_matches": [m.serialize() for m in matches],
        "ubos": ubos,
        "completeness": completeness,
        "reviews": [r.serialize() for r in reviews],
        "open_alerts": [a.serialize() for a in open_alerts],
        "recent_events": [e.serialize() for e in events],
        "changes_since_review": [e.serialize() for e in changes],
        "last_review_at": since.isoformat() if since else None,
    }), 200


@api.route("/customers/<int:cid>/screen", methods=["POST"])
@permission_required("screening.run")
def screen_customer(user, cid):
    customer = _get_customer_for(user, cid)
    audit.record("SCREENING_REQUESTED", "customer", customer.id, actor=user,
                 reason="Manual screening", commit=True)
    _dispatch(run_screening, customer.id, user.id)
    return jsonify({
        "message": "Screening started",
        "async": _celery_enabled(),
        "customer_id": customer.id,
    }), 202


@api.route("/customers/<int:cid>/documents", methods=["POST"])
@permission_required("document.upload")
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
@permission_required("customer.view")
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
# Ownership / KYB — parties, graph, UBOs, directors & addresses
# ---------------------------------------------------------------------------
@api.route("/customers/<int:cid>/ownership", methods=["GET"])
@permission_required("customer.view")
def customer_ownership(user, cid):
    customer = _get_customer_for(user, cid)
    graph = ownership.build_graph(customer)
    ubos = ownership.compute_ubos(customer)
    return jsonify({"graph": graph, "ubos": ubos,
                    "directors": ownership.directors_of(customer),
                    "complex_ownership": customer.complex_ownership,
                    "root_party_id": customer.root_party_id}), 200


@api.route("/customers/<int:cid>/ownership", methods=["POST"])
@permission_required("kyb.edit")
def add_ownership(user, cid):
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    owner_name = (body.get("owner_name") or "").strip()
    if not owner_name:
        raise APIException("owner_name is required", status_code=400)
    kind = body.get("owner_kind", "PERSON")
    if kind not in PARTY_KINDS:
        kind = "PERSON"

    owner, edge, emitted = party_service.add_related_party(
        customer,
        owner_name=owner_name,
        owner_kind=kind,
        relationship_type=body.get("relationship_type", "SHAREHOLDER"),
        percentage=body.get("percentage") or 0,
        control_type=body.get("control_type"),
        country=body.get("country"),
        nationality=body.get("nationality"),
        owned_party_id=body.get("owned_party_id"),
        actor=user,
    )
    return jsonify({"owner": owner.serialize(), "edge": edge.serialize(),
                    "events": emitted}), 201


@api.route("/customers/<int:cid>/addresses", methods=["GET"])
@permission_required("customer.view")
def list_addresses(user, cid):
    customer = _get_customer_for(user, cid)
    if not customer.root_party_id:
        return jsonify([]), 200
    addrs = (Address.query.filter_by(party_id=customer.root_party_id)
             .order_by(Address.is_current.desc(), Address.valid_from.desc()).all())
    return jsonify([a.serialize() for a in addrs]), 200


@api.route("/customers/<int:cid>/addresses", methods=["POST"])
@permission_required("kyc.edit")
def create_address(user, cid):
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    line1 = (body.get("line1") or "").strip()
    if not line1:
        raise APIException("line1 is required", status_code=400)
    addr = party_service.add_address(
        customer,
        line1=line1, line2=body.get("line2"), city=body.get("city"),
        postal_code=body.get("postal_code"), country=body.get("country"),
        address_type=body.get("address_type", "RESIDENTIAL"),
        actor=user,
    )
    return jsonify(addr.serialize()), 201


@api.route("/parties/<int:pid>", methods=["GET"])
@permission_required("customer.view")
def get_party(user, pid):
    party = Party.query.get(pid)
    if party is None or party.organization_id != user.organization_id:
        raise APIException("Party not found", status_code=404)
    return jsonify(party.serialize()), 200


# ---------------------------------------------------------------------------
# KYC data (field provenance) & Requirement engine
# ---------------------------------------------------------------------------
@api.route("/customers/<int:cid>/fields", methods=["GET"])
@permission_required("kyc.view")
def list_fields(user, cid):
    customer = _get_customer_for(user, cid)
    fields = (ProfileField.query.filter_by(customer_id=cid)
              .order_by(ProfileField.category, ProfileField.field_key).all())
    return jsonify([f.serialize() for f in fields]), 200


@api.route("/customers/<int:cid>/fields", methods=["POST"])
@permission_required("kyc.edit")
def set_field(user, cid):
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    field_key = (body.get("field_key") or "").strip()
    if not field_key:
        raise APIException("field_key is required", status_code=400)
    field = kyc_service.set_field(
        customer, field_key, body.get("value"),
        category=body.get("category"), source=body.get("source", "manual"),
        confidence=body.get("confidence"), actor=user)
    return jsonify(field.serialize()), 201


@api.route("/customers/<int:cid>/fields/<int:fid>/verify", methods=["POST"])
@permission_required("kyc.approve")
def verify_field(user, cid, fid):
    customer = _get_customer_for(user, cid)
    field = ProfileField.query.get(fid)
    if field is None or field.customer_id != cid:
        raise APIException("Field not found", status_code=404)
    kyc_service.verify_field(field, user)
    return jsonify(field.serialize()), 200


@api.route("/customers/<int:cid>/requirements", methods=["GET"])
@permission_required("customer.view")
def customer_requirements(user, cid):
    customer = _get_customer_for(user, cid)
    return jsonify(requirement_engine.summary(customer)), 200


@api.route("/customers/<int:cid>/request-info", methods=["POST"])
@permission_required("kyc.review")
def request_info(user, cid):
    customer = _get_customer_for(user, cid)
    result = requirement_engine.request_missing_info(customer, actor=user)
    return jsonify(result), 202


# ---------------------------------------------------------------------------
# Screening runs & match review
# ---------------------------------------------------------------------------
@api.route("/customers/<int:cid>/screening", methods=["GET"])
@permission_required("customer.view")
def customer_screening(user, cid):
    customer = _get_customer_for(user, cid)
    runs = (ScreeningRun.query.filter_by(customer_id=cid)
            .order_by(ScreeningRun.started_at.desc()).all())
    matches = (ScreeningMatch.query.filter_by(customer_id=cid)
               .order_by(ScreeningMatch.first_detected_at.desc()).all())
    return jsonify({
        "runs": [r.serialize() for r in runs],
        "matches": [m.serialize() for m in matches],
    }), 200


@api.route("/screening/matches/<int:match_id>/review", methods=["POST"])
@permission_required("screening.review_match")
def review_screening_match(user, match_id):
    match = ScreeningMatch.query.get(match_id)
    if match is None:
        raise APIException("Match not found", status_code=404)
    _get_customer_for(user, match.customer_id)  # org scoping
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    reason = (body.get("reason") or "").strip()
    if decision not in ("FALSE_POSITIVE", "CONFIRMED", "ESCALATED"):
        raise APIException("decision must be FALSE_POSITIVE, CONFIRMED or ESCALATED",
                           status_code=400)
    if not reason:
        raise APIException("A reason is required", status_code=400)
    if decision == "CONFIRMED" and not has_permission(user, "screening.confirm_match"):
        raise APIException("Missing permission: screening.confirm_match", status_code=403)
    review_match(match, decision, reason, user)
    return jsonify(match.serialize()), 200


# ---------------------------------------------------------------------------
# Workspace — role-based action center
# ---------------------------------------------------------------------------
@api.route("/workspace", methods=["GET"])
@permission_required("workspace.view")
def workspace(user):
    # ABAC: the workspace only counts work the user is allowed to see.
    open_cases = (data_scope.visible_cases(user)
                  .filter(Case.status != "CLOSED").all())
    urgent = [c for c in open_cases if c.priority in ("CRITICAL", "HIGH")]
    open_tasks = (data_scope.visible_tasks(user)
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
@permission_required("case.view")
def list_cases(user):
    q = data_scope.visible_cases(user)   # ABAC: tenant + role/policy scope
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
@permission_required("case.view")
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
@permission_required("case.view")
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

    # Authorization is now permission-based, not role-name based.
    required_perm = {
        "CONFIRMED_MATCH": "screening.confirm_match",
        "ESCALATE": "case.escalate",
        "FALSE_POSITIVE": "screening.review_match",
        "CLEARED": "screening.review_match",
    }[decision]
    if not has_permission(user, required_perm):
        raise APIException(f"Missing permission: {required_perm}", status_code=403)

    old_status = case.status
    case.decision = decision
    case.decision_reason = reason
    case.decided_by = user.id

    # Carry the decision onto the linked screening match(es). review_match keeps
    # the match's history AND re-syncs the customer's derived flags + risk, so a
    # false positive no longer erases the fact the match ever existed.
    match_status = {
        "FALSE_POSITIVE": "FALSE_POSITIVE", "CLEARED": "FALSE_POSITIVE",
        "CONFIRMED_MATCH": "CONFIRMED", "ESCALATE": "ESCALATED",
    }[decision]
    linked = ScreeningMatch.query.filter_by(case_id=case.id).all()
    for m in linked:
        review_match(m, match_status, reason, user)

    if decision == "ESCALATE":
        case.status = "ESCALATED"
        case.priority = "CRITICAL"
    else:
        case.status = "CLOSED"
        case.closed_at = utcnow()
        for t in case.tasks:
            if t.status != "DONE":
                t.status = "DONE"

    customer.last_review_at = utcnow()
    audit.record("CASE_DECISION", "case", case.id, actor=user,
                 old_value=old_status, new_value=decision, reason=reason)
    db.session.commit()

    # If no match was linked, still refresh the risk picture.
    if not linked:
        risk_engine.recompute(customer, actor=user, reason=f"Case decision: {decision}")
    return jsonify(case.serialize(with_tasks=True)), 200


@api.route("/tasks/my-work", methods=["GET"])
@permission_required("task.view")
def my_work(user):
    tasks = (data_scope.visible_tasks(user)
             .filter(Task.status != "DONE")
             .order_by(Task.due_at.asc()).all())
    return jsonify([t.serialize() for t in tasks]), 200


@api.route("/tasks/<int:task_id>/complete", methods=["POST"])
@permission_required("task.complete")
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
@permission_required("rule.view")
def list_rules(user):
    rules = ComplianceRule.query.order_by(ComplianceRule.event_type).all()
    return jsonify([r.serialize() for r in rules]), 200


@api.route("/audit", methods=["GET"])
@permission_required("audit.view")
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


# ---------------------------------------------------------------------------
# RBAC (admin) — roles & permission catalog
# ---------------------------------------------------------------------------
@api.route("/roles", methods=["GET"])
@permission_required("role.view")
def list_roles(user):
    roles = Role.query.order_by(Role.name).all()
    return jsonify([r.serialize(with_permissions=True) for r in roles]), 200


@api.route("/permissions", methods=["GET"])
@permission_required("role.view")
def list_permissions(user):
    return jsonify([{"code": c, "label": label} for c, label in PERMISSION_CATALOG]), 200


# ---------------------------------------------------------------------------
# Organization structure (admin foundation) — departments, teams, users
# ---------------------------------------------------------------------------
@api.route("/organization", methods=["GET"])
@permission_required("organization.view")
def get_organization(user):
    org = user.organization
    depts = Department.query.filter_by(organization_id=org.id).all()
    teams = Team.query.filter_by(organization_id=org.id).all()
    members = OrganizationMembership.query.filter_by(organization_id=org.id).all()
    return jsonify({
        "organization": org.serialize(),
        "departments": [d.serialize() for d in depts],
        "teams": [t.serialize(with_members=True) for t in teams],
        "member_count": len(members),
    }), 200


@api.route("/departments", methods=["GET"])
@permission_required("department.view")
def list_departments(user):
    depts = Department.query.filter_by(organization_id=user.organization_id).all()
    return jsonify([d.serialize() for d in depts]), 200


@api.route("/departments", methods=["POST"])
@permission_required("department.create")
def create_department(user):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise APIException("name is required", status_code=400)
    dept = Department(organization_id=user.organization_id, name=name)
    db.session.add(dept)
    audit.record("DEPARTMENT_CREATED", "department", None, actor=user,
                 new_value=name, commit=True)
    return jsonify(dept.serialize()), 201


@api.route("/teams", methods=["GET"])
@permission_required("team.view")
def list_teams(user):
    teams = Team.query.filter_by(organization_id=user.organization_id).all()
    return jsonify([t.serialize(with_members=True) for t in teams]), 200


@api.route("/teams", methods=["POST"])
@permission_required("team.create")
def create_team(user):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise APIException("name is required", status_code=400)
    team = Team(organization_id=user.organization_id, name=name,
                department_id=body.get("department_id"),
                manager_id=body.get("manager_id"))
    db.session.add(team)
    audit.record("TEAM_CREATED", "team", None, actor=user, new_value=name,
                 commit=True)
    return jsonify(team.serialize()), 201


@api.route("/teams/<int:team_id>/members", methods=["POST"])
@permission_required("team.manage_members")
def add_team_member(user, team_id):
    team = Team.query.get(team_id)
    if team is None or team.organization_id != user.organization_id:
        raise APIException("Team not found", status_code=404)
    body = request.get_json(silent=True) or {}
    member = User.query.get(body.get("user_id"))
    if member is None or member.organization_id != user.organization_id:
        raise APIException("User not found in organization", status_code=404)
    if TeamMembership.query.filter_by(team_id=team_id, user_id=member.id).first():
        return jsonify({"message": "already a member"}), 200
    tm = TeamMembership(team_id=team_id, user_id=member.id,
                        role_in_team=body.get("role_in_team", "MEMBER"))
    db.session.add(tm)
    audit.record("TEAM_MEMBER_ADDED", "team", team_id, actor=user,
                 new_value=member.email, commit=True)
    return jsonify(tm.serialize()), 201


@api.route("/users", methods=["GET"])
@permission_required("user.view")
def list_users(user):
    users = User.query.filter_by(organization_id=user.organization_id).all()
    out = []
    for u in users:
        data = u.serialize(with_permissions=False)
        data["team_ids"] = data_scope.user_team_ids(u)
        out.append(data)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# Administration — invitations, user management, organization settings
# ---------------------------------------------------------------------------
@api.route("/invitations", methods=["GET"])
@permission_required("user.view")
def list_invitations(user):
    invs = (Invitation.query.filter_by(organization_id=user.organization_id)
            .order_by(Invitation.created_at.desc()).all())
    return jsonify([i.serialize() for i in invs]), 200


@api.route("/invitations", methods=["POST"])
@permission_required("user.create")
def create_invitation(user):
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise APIException("email is required", status_code=400)
    if User.query.filter_by(email=email).first():
        raise APIException("A user with this email already exists", status_code=409)
    role_name = body.get("proposed_role", "KYC_ANALYST")
    if role_name not in ROLES:
        raise APIException(f"Unknown role: {role_name}", status_code=400)
    # Only an admin can invite another admin.
    if role_name in ("ORGANIZATION_ADMIN", "PLATFORM_ADMIN", "ADMIN") \
            and not has_permission(user, "role.update"):
        raise APIException("Missing permission to invite an administrator",
                           status_code=403)
    team_id = body.get("proposed_team_id")
    if team_id:
        team = Team.query.get(team_id)
        if team is None or team.organization_id != user.organization_id:
            raise APIException("Team not found", status_code=404)

    inv = Invitation(
        organization_id=user.organization_id,
        email=email,
        proposed_role=role_name,
        proposed_team_id=team_id,
        created_by=user.id,
    )
    db.session.add(inv)
    audit.record("USER_INVITED", "invitation", None, actor=user,
                 new_value=f"{email} as {role_name}", commit=True)
    # Token returned once, to the inviter (no email channel yet — the admin
    # shares the invite link out-of-band).
    return jsonify(inv.serialize(with_token=True)), 201


@api.route("/invitations/<int:inv_id>/revoke", methods=["POST"])
@permission_required("user.create")
def revoke_invitation(user, inv_id):
    inv = Invitation.query.get(inv_id)
    if inv is None or inv.organization_id != user.organization_id:
        raise APIException("Invitation not found", status_code=404)
    if inv.status != "PENDING":
        raise APIException("Only pending invitations can be revoked", status_code=400)
    inv.status = "REVOKED"
    audit.record("INVITATION_REVOKED", "invitation", inv.id, actor=user,
                 new_value=inv.email, commit=True)
    return jsonify(inv.serialize()), 200


@api.route("/auth/accept-invitation", methods=["POST"])
def accept_invitation():
    """Public endpoint: turn a valid invitation token into an account."""
    body = request.get_json(silent=True) or {}
    token = body.get("token") or ""
    password = body.get("password") or ""
    if not token or not password:
        raise APIException("token and password are required", status_code=400)
    inv = Invitation.query.filter_by(token=token).first()
    if inv is None or not inv.is_valid():
        raise APIException("Invitation is invalid or expired", status_code=400)
    if User.query.filter_by(email=inv.email).first():
        raise APIException("A user with this email already exists", status_code=409)

    from api.rbac import get_role
    role = get_role(inv.proposed_role)
    new_user = User(
        email=inv.email,
        password=hash_password(password),
        full_name=body.get("full_name") or inv.email.split("@")[0],
        role=inv.proposed_role,
        role_id=role.id if role else None,
        organization_id=inv.organization_id,
    )
    db.session.add(new_user)
    db.session.flush()
    db.session.add(OrganizationMembership(
        organization_id=inv.organization_id, user_id=new_user.id, status="ACTIVE"))
    if inv.proposed_team_id:
        db.session.add(TeamMembership(team_id=inv.proposed_team_id,
                                      user_id=new_user.id, role_in_team="MEMBER"))
    inv.status = "ACCEPTED"
    inv.accepted_at = utcnow()
    audit.record("INVITATION_ACCEPTED", "user", new_user.id,
                 actor_label=inv.email, new_value=inv.proposed_role)
    db.session.commit()
    return jsonify({"token": make_token(new_user),
                    "user": new_user.serialize()}), 201


@api.route("/users/<int:uid>", methods=["PATCH"])
@permission_required("user.update")
def update_user(user, uid):
    target = User.query.get(uid)
    if target is None or target.organization_id != user.organization_id:
        raise APIException("User not found", status_code=404)
    body = request.get_json(silent=True) or {}

    if "role" in body:
        role_name = body["role"]
        if role_name not in ROLES:
            raise APIException(f"Unknown role: {role_name}", status_code=400)
        if role_name in ("ORGANIZATION_ADMIN", "PLATFORM_ADMIN", "ADMIN") \
                and not has_permission(user, "role.update"):
            raise APIException("Missing permission to grant an administrator role",
                               status_code=403)
        from api.rbac import get_role
        role = get_role(role_name)
        audit.record("ROLE_ASSIGNED", "user", target.id, actor=user,
                     old_value=target.role, new_value=role_name)
        target.role = role_name
        target.role_id = role.id if role else None

    if "is_active" in body:
        # Disabling/enabling accounts is its own permission (per the catalog).
        if not has_permission(user, "user.disable"):
            raise APIException("Missing permission: user.disable", status_code=403)
        if target.id == user.id and not body["is_active"]:
            raise APIException("You cannot disable your own account", status_code=400)
        old = target.is_active
        target.is_active = bool(body["is_active"])
        audit.record("USER_DISABLED" if not target.is_active else "USER_ENABLED",
                     "user", target.id, actor=user,
                     old_value=str(old), new_value=str(target.is_active))

    if "full_name" in body:
        target.full_name = (body["full_name"] or "").strip() or target.full_name

    db.session.commit()
    return jsonify(target.serialize(with_permissions=False)), 200


@api.route("/users/<int:uid>/roles", methods=["POST"])
@permission_required("user.update")
def add_user_role(user, uid):
    """Grant an ADDITIONAL role (user_roles); permissions become the union."""
    target = User.query.get(uid)
    if target is None or target.organization_id != user.organization_id:
        raise APIException("User not found", status_code=404)
    body = request.get_json(silent=True) or {}
    role_name = body.get("role")
    if role_name not in ROLES:
        raise APIException(f"Unknown role: {role_name}", status_code=400)
    if role_name in ("ORGANIZATION_ADMIN", "PLATFORM_ADMIN", "ADMIN") \
            and not has_permission(user, "role.update"):
        raise APIException("Missing permission to grant an administrator role",
                           status_code=403)
    from api.rbac import get_role
    role = get_role(role_name)
    if role in target.roles or (target.role_id and target.role_id == role.id):
        return jsonify(target.serialize()), 200
    target.roles.append(role)
    audit.record("ROLE_ASSIGNED", "user", target.id, actor=user,
                 new_value=f"+{role_name}", reason="additional role", commit=True)
    return jsonify(target.serialize()), 200


@api.route("/users/<int:uid>/roles/<role_name>", methods=["DELETE"])
@permission_required("user.update")
def remove_user_role(user, uid, role_name):
    """Remove an additional role (the primary role is changed via PATCH)."""
    target = User.query.get(uid)
    if target is None or target.organization_id != user.organization_id:
        raise APIException("User not found", status_code=404)
    role = next((r for r in target.roles if r.name == role_name), None)
    if role is None:
        raise APIException("User does not hold this additional role", status_code=404)
    target.roles.remove(role)
    audit.record("ROLE_REMOVED", "user", target.id, actor=user,
                 old_value=role_name, commit=True)
    return jsonify(target.serialize()), 200


@api.route("/teams/<int:team_id>", methods=["PATCH"])
@permission_required("team.update")
def update_team(user, team_id):
    """Rename a team, move it to a department, or configure its manager."""
    team = Team.query.get(team_id)
    if team is None or team.organization_id != user.organization_id:
        raise APIException("Team not found", status_code=404)
    body = request.get_json(silent=True) or {}
    if "name" in body:
        name = (body["name"] or "").strip()
        if name:
            team.name = name
    if "department_id" in body:
        team.department_id = body["department_id"] or None
    if "manager_id" in body:
        manager = User.query.get(body["manager_id"]) if body["manager_id"] else None
        if body["manager_id"] and (manager is None or
                                   manager.organization_id != user.organization_id):
            raise APIException("Manager not found in organization", status_code=404)
        old = team.manager_id
        team.manager_id = manager.id if manager else None
        audit.record("TEAM_MANAGER_CHANGED", "team", team.id, actor=user,
                     old_value=str(old), new_value=str(team.manager_id))
    db.session.commit()
    return jsonify(team.serialize(with_members=True)), 200


@api.route("/organization", methods=["PATCH"])
@permission_required("organization.update")
def update_organization(user):
    org = user.organization
    body = request.get_json(silent=True) or {}
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise APIException("name cannot be empty", status_code=400)
        audit.record("ORGANIZATION_UPDATED", "organization", org.id, actor=user,
                     old_value=org.name, new_value=name)
        org.name = name
    db.session.commit()
    return jsonify(org.serialize()), 200


# ---------------------------------------------------------------------------
# Management operations — dashboard, workload, queues, assignment, SLA
# ---------------------------------------------------------------------------
def _org_cases(user):
    ids = [c.id for c in Customer.query
           .filter_by(organization_id=user.organization_id).all()]
    return Case.query.filter(Case.customer_id.in_(ids or [0]))


@api.route("/management/dashboard", methods=["GET"])
@permission_required("management.view")
def management_dashboard(user):
    now = utcnow()
    all_cases = _org_cases(user).all()
    open_cases = [c for c in all_cases if c.status != "CLOSED"]
    closed_30d = [c for c in all_cases if c.status == "CLOSED" and c.closed_at
                  and (now - c.closed_at).days <= 30]
    org_customer_ids = [c.id for c in Customer.query
                        .filter_by(organization_id=user.organization_id).all()]
    open_tasks = (Task.query.filter(Task.customer_id.in_(org_customer_ids or [0]))
                  .filter(Task.status != "DONE").all())
    high_risk_customers = (Customer.query
                           .filter_by(organization_id=user.organization_id)
                           .filter(Customer.risk_level.in_(["HIGH", "CRITICAL"]))
                           .count())
    status_counts = {}
    for c in open_cases:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1
    escalated = [c for c in all_cases if c.status == "ESCALATED" or c.decision == "ESCALATE"]

    resolution_hours = [(c.closed_at - c.opened_at).total_seconds() / 3600
                        for c in closed_30d if c.opened_at and c.closed_at]

    return jsonify({
        "open_cases": len(open_cases),
        "unassigned_cases": len([c for c in open_cases if not c.assigned_to]),
        "overdue_tasks": len([t for t in open_tasks if t.due_at and t.due_at < now]),
        "cases_due_today": len([c for c in open_cases if c.due_at
                                and c.due_at <= now + timedelta(days=1)]),
        "high_risk_cases": len([c for c in open_cases
                                if c.priority in ("HIGH", "CRITICAL")]),
        "critical_alerts": len([c for c in open_cases if c.priority == "CRITICAL"]),
        "high_risk_customers": high_risk_customers,
        "cases_by_status": status_counts,
        "cases_closed_30d": len(closed_30d),
        "average_resolution_hours": (round(sum(resolution_hours) / len(resolution_hours), 1)
                                     if resolution_hours else None),
        "escalation_rate_pct": (round(100 * len(escalated) / len(all_cases))
                                if all_cases else 0),
        "team_workload": workload.org_workload(user.organization_id),
        "sla": sla.sla_summary(open_cases, user.organization_id),
    }), 200


@api.route("/management/workload", methods=["GET"])
@permission_required("management.team_view")
def management_workload(user):
    return jsonify(workload.org_workload(user.organization_id)), 200


@api.route("/management/queues", methods=["GET"])
@permission_required("management.view")
def management_queues(user):
    now = utcnow()
    unassigned = (_org_cases(user)
                  .filter(Case.status != "CLOSED")
                  .filter(Case.assigned_to.is_(None))
                  .order_by(Case.opened_at.asc()).all())
    prio = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    unassigned.sort(key=lambda c: (prio.get(c.priority, 9), c.opened_at or now))
    out = []
    for c in unassigned:
        data = c.serialize()
        cust = Customer.query.get(c.customer_id)
        data["customer_name"] = cust.name if cust else None
        data["age_hours"] = round((now - c.opened_at).total_seconds() / 3600, 1) \
            if c.opened_at else None
        out.append(data)
    return jsonify(out), 200


@api.route("/cases/<int:case_id>/assign", methods=["POST"])
@permission_required("management.assign_work", "case.assign")
def assign_case(user, case_id):
    case = Case.query.get(case_id)
    if case is None:
        raise APIException("Case not found", status_code=404)
    _get_customer_for(user, case.customer_id)
    body = request.get_json(silent=True) or {}
    target_id = body.get("user_id")
    if target_id:
        target = User.query.get(target_id)
        if target is None or target.organization_id != user.organization_id:
            raise APIException("Assignee not found in organization", status_code=404)
        old = case.assigned_to
        case.assigned_to = target.id
        audit.record("CASE_REASSIGNED" if old else "CASE_ASSIGNED", "case",
                     case.id, actor=user, old_value=str(old),
                     new_value=target.email, reason="manual", commit=True)
        return jsonify(case.serialize()), 200
    # No user_id -> automatic assignment by strategy/rules.
    assignee = assignment.auto_assign(
        case, strategy=body.get("strategy", "LEAST_LOADED"), actor=user)
    if assignee is None:
        raise APIException("No eligible assignee found", status_code=409)
    return jsonify(case.serialize()), 200


@api.route("/management/queues/bulk-assign", methods=["POST"])
@permission_required("management.assign_work")
def bulk_assign(user):
    body = request.get_json(silent=True) or {}
    strategy = body.get("strategy", "LEAST_LOADED")
    unassigned = (_org_cases(user)
                  .filter(Case.status != "CLOSED")
                  .filter(Case.assigned_to.is_(None)).all())
    assigned = 0
    for case in unassigned:
        if assignment.auto_assign(case, strategy=strategy, actor=user):
            assigned += 1
    return jsonify({"assigned": assigned, "remaining": len(unassigned) - assigned}), 200


@api.route("/management/sla", methods=["GET"])
@permission_required("management.performance_view")
def management_sla(user):
    open_cases = _org_cases(user).filter(Case.status != "CLOSED").all()
    hours_map = sla.target_hours_map(user.organization_id)
    detail = []
    for c in open_cases:
        s = sla.case_sla(c, hours_map)
        cust = Customer.query.get(c.customer_id)
        detail.append({**c.serialize(), "customer_name": cust.name if cust else None,
                       "sla_status": s["status"], "sla_deadline": s["deadline"]})
    return jsonify({
        "summary": sla.sla_summary(open_cases, user.organization_id),
        "targets": hours_map,
        "cases": detail,
    }), 200


@api.route("/assignment-rules", methods=["GET"])
@permission_required("management.view")
def list_assignment_rules(user):
    rules = (AssignmentRule.query
             .filter_by(organization_id=user.organization_id)
             .order_by(AssignmentRule.priority).all())
    return jsonify([r.serialize() for r in rules]), 200


# ---------------------------------------------------------------------------
# Provider integration layer — admin config, verification, webhooks
# ---------------------------------------------------------------------------
@api.route("/providers", methods=["GET"])
@permission_required("organization.view")
def list_providers(user):
    providers = (Provider.query.filter(
        (Provider.organization_id == user.organization_id) |
        (Provider.organization_id.is_(None))).all())
    out = []
    for p in providers:
        out.append(p.serialize(with_health=provider_service.latest_health(p)))
    return jsonify(out), 200


@api.route("/providers", methods=["POST"])
@permission_required("organization.update")
def create_provider(user):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    ptype = body.get("provider_type")
    if not name or ptype not in PROVIDER_TYPES:
        raise APIException("name and a valid provider_type are required", status_code=400)
    provider = Provider(
        organization_id=user.organization_id, name=name, provider_type=ptype,
        adapter=body.get("adapter", "mock"), enabled=bool(body.get("enabled", True)),
        config=body.get("config") or {})
    db.session.add(provider)
    audit.record("PROVIDER_CREATED", "provider", None, actor=user,
                 new_value=f"{name} ({ptype})", commit=True)
    return jsonify(provider.serialize()), 201


@api.route("/providers/<int:pid>", methods=["PATCH"])
@permission_required("organization.update")
def update_provider(user, pid):
    provider = Provider.query.get(pid)
    if provider is None or (provider.organization_id not in (None, user.organization_id)):
        raise APIException("Provider not found", status_code=404)
    body = request.get_json(silent=True) or {}
    if "enabled" in body:
        provider.enabled = bool(body["enabled"])
    if "config" in body:
        provider.config = body["config"] or {}
    db.session.commit()
    return jsonify(provider.serialize()), 200


@api.route("/providers/<int:pid>/credentials", methods=["POST"])
@permission_required("organization.update")
def set_provider_credential(user, pid):
    provider = Provider.query.get(pid)
    if provider is None or provider.organization_id != user.organization_id:
        raise APIException("Provider not found", status_code=404)
    body = request.get_json(silent=True) or {}
    key_name = (body.get("key_name") or "").strip()
    secret = body.get("secret_value")
    if not key_name or not secret:
        raise APIException("key_name and secret_value are required", status_code=400)
    cred = (ProviderCredential.query
            .filter_by(provider_id=pid, key_name=key_name).first())
    if cred is None:
        cred = ProviderCredential(provider_id=pid, key_name=key_name)
        db.session.add(cred)
    cred.secret_value = secret   # stored server-side, never returned
    audit.record("PROVIDER_CREDENTIAL_SET", "provider", pid, actor=user,
                 new_value=key_name, commit=True)
    return jsonify({"provider_id": pid, "key_name": key_name, "stored": True}), 201


@api.route("/providers/<int:pid>/health", methods=["POST"])
@permission_required("organization.view")
def provider_health(user, pid):
    provider = Provider.query.get(pid)
    if provider is None or (provider.organization_id not in (None, user.organization_id)):
        raise APIException("Provider not found", status_code=404)
    hs = provider_service.check_health(provider)
    return jsonify(hs.serialize()), 200


@api.route("/webhook-events", methods=["GET"])
@permission_required("organization.view")
def list_webhook_events(user):
    events = (WebhookEvent.query.order_by(WebhookEvent.received_at.desc())
              .limit(50).all())
    return jsonify([e.serialize() for e in events]), 200


@api.route("/customers/<int:cid>/verify", methods=["POST"])
@permission_required("kyc.review")
def verify_customer(user, cid):
    customer = _get_customer_for(user, cid)
    try:
        result = provider_service.verify_customer(customer, actor=user)
    except RuntimeError as exc:
        raise APIException(str(exc), status_code=409)
    return jsonify(result.serialize()), 200


@api.route("/webhooks/providers/<provider_name>", methods=["POST"])
def provider_webhook(provider_name):
    """Public, signature-verified, idempotent provider webhook ingestion."""
    raw_body = request.get_data() or b""
    payload = request.get_json(silent=True) or {}
    signature = request.headers.get("X-Signature")
    event_id = request.headers.get("X-Event-Id")
    status_code, body = provider_service.process_webhook(
        provider_name, raw_body, payload, signature, event_id)
    return jsonify(body), status_code


# ---------------------------------------------------------------------------
# Alert Center — first-class compliance alerts (distinct from notifications)
# ---------------------------------------------------------------------------
@api.route("/alerts", methods=["GET"])
@permission_required("case.view")
def list_alerts(user):
    q = ComplianceAlert.query.filter_by(organization_id=user.organization_id)
    status = request.args.get("status")
    if status:
        q = q.filter(ComplianceAlert.status == status)
    elif request.args.get("open") != "false":
        q = q.filter(ComplianceAlert.status.notin_(["RESOLVED", "DISMISSED"]))
    alerts = q.order_by(ComplianceAlert.created_at.desc()).limit(200).all()
    out = []
    for a in alerts:
        data = a.serialize()
        cust = Customer.query.get(a.customer_id) if a.customer_id else None
        data["customer_name"] = cust.name if cust else None
        out.append(data)
    return jsonify(out), 200


def _get_alert(user, alert_id):
    alert = ComplianceAlert.query.get(alert_id)
    if alert is None or alert.organization_id != user.organization_id:
        raise APIException("Alert not found", status_code=404)
    return alert


@api.route("/alerts/<int:alert_id>/assign", methods=["POST"])
@permission_required("case.assign")
def assign_alert(user, alert_id):
    alert = _get_alert(user, alert_id)
    body = request.get_json(silent=True) or {}
    assignee = User.query.get(body.get("user_id") or user.id)
    if assignee is None or assignee.organization_id != user.organization_id:
        raise APIException("Assignee not found", status_code=404)
    alert_service.assign(alert, user, assignee)
    return jsonify(alert.serialize()), 200


@api.route("/alerts/<int:alert_id>/resolve", methods=["POST"])
@permission_required("case.update")
def resolve_alert(user, alert_id):
    alert = _get_alert(user, alert_id)
    body = request.get_json(silent=True) or {}
    reason = (body.get("resolution") or "").strip()
    if not reason:
        raise APIException("A resolution is required", status_code=400)
    alert_service.resolve(alert, user, reason, dismiss=bool(body.get("dismiss")))
    return jsonify(alert.serialize()), 200


# ---------------------------------------------------------------------------
# Reviews — scheduled & event-driven
# ---------------------------------------------------------------------------
@api.route("/customers/<int:cid>/reviews", methods=["GET"])
@permission_required("customer.view")
def list_reviews(user, cid):
    customer = _get_customer_for(user, cid)
    reviews = (Review.query.filter_by(customer_id=cid)
               .order_by(Review.created_at.desc()).all())
    return jsonify([r.serialize() for r in reviews]), 200


@api.route("/customers/<int:cid>/reviews", methods=["POST"])
@permission_required("kyc.review")
def create_review(user, cid):
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    rtype = body.get("review_type", "PERIODIC_REVIEW")
    if rtype not in REVIEW_TYPES:
        raise APIException(f"review_type must be one of {list(REVIEW_TYPES)}",
                           status_code=400)
    review = review_engine.create_event_driven(
        customer, trigger=body.get("trigger") or "Manual", actor=user) \
        if rtype == "EVENT_DRIVEN_REVIEW" else None
    if review is None:
        review = Review(organization_id=customer.organization_id,
                        customer_id=cid, review_type=rtype, status="DUE",
                        trigger=body.get("trigger") or "Manual",
                        due_at=utcnow() + timedelta(days=14))
        db.session.add(review)
        db.session.commit()
    return jsonify(review.serialize()), 201


def _get_review(user, review_id):
    review = Review.query.get(review_id)
    if review is None or review.organization_id != user.organization_id:
        raise APIException("Review not found", status_code=404)
    return review


@api.route("/reviews/<int:review_id>/start", methods=["POST"])
@permission_required("kyc.review")
def start_review(user, review_id):
    review = _get_review(user, review_id)
    review_engine.start_review(review, actor=user)
    return jsonify(review.serialize()), 200


@api.route("/reviews/<int:review_id>/complete", methods=["POST"])
@permission_required("kyc.review")
def complete_review(user, review_id):
    review = _get_review(user, review_id)
    body = request.get_json(silent=True) or {}
    decision = body.get("decision") or "APPROVED"
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise APIException("A reason is required", status_code=400)
    review, nxt = review_engine.complete_review(review, decision, reason, actor=user)
    return jsonify({"review": review.serialize(), "next": nxt.serialize()}), 200


@api.route("/risk/methodologies", methods=["GET"])
@permission_required("risk.view")
def list_risk_methodologies(user):
    meths = (RiskMethodology.query
             .filter((RiskMethodology.organization_id == user.organization_id) |
                     (RiskMethodology.organization_id.is_(None)))
             .order_by(RiskMethodology.version).all())
    return jsonify([m.serialize(deep=True) for m in meths]), 200


@api.route("/risk/methodologies/active", methods=["GET"])
@permission_required("risk.view")
def active_risk_methodology(user):
    meth = risk_engine.active_methodology(user.organization_id)
    return jsonify(meth.serialize(deep=True) if meth else None), 200


@api.route("/monitoring/run", methods=["POST"])
@permission_required("management.view")
def run_monitoring(user):
    """Manually trigger the continuous-monitoring sweep (normally on Celery beat)."""
    return jsonify(review_engine.run_monitoring()), 200


@api.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "celery": _celery_enabled()}), 200
