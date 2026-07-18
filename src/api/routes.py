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
    Party, OwnershipRelationship, ScreeningRun, ScreeningMatch,
    Department, Team, OrganizationMembership, TeamMembership, AccessPolicy,
    utcnow, ROLES, CUSTOMER_TYPES, PARTY_KINDS, PERMISSION_CATALOG,
)
from api.utils import APIException
from api.auth import (
    hash_password, verify_password, make_token, current_user,
    login_required, role_required, permission_required, has_permission,
)
from api.engine import risk_engine, audit, ownership, data_scope
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

    return jsonify({
        "customer": customer.serialize(),
        "risk": latest.serialize() if latest else None,
        "open_cases": [c.serialize() for c in open_cases],
        "tasks": [t.serialize() for t in tasks],
        "documents": [d.serialize() for d in documents],
        "screening_matches": [m.serialize() for m in matches],
        "ubos": ubos,
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
# Ownership / KYB — parties, graph & UBOs
# ---------------------------------------------------------------------------
def _ensure_root_party(customer):
    if customer.root_party_id:
        return Party.query.get(customer.root_party_id)
    kind = "ORGANIZATION" if customer.customer_type == "COMPANY" else "PERSON"
    party = Party(
        organization_id=customer.organization_id,
        kind=kind, name=customer.name, customer_id=customer.id,
        business_activity=customer.business_activity,
        country_of_incorporation=customer.country if kind == "ORGANIZATION" else None,
        country_of_residence=customer.country if kind == "PERSON" else None,
    )
    db.session.add(party)
    db.session.flush()
    customer.root_party_id = party.id
    db.session.commit()
    return party


@api.route("/customers/<int:cid>/ownership", methods=["GET"])
@permission_required("customer.view")
def customer_ownership(user, cid):
    customer = _get_customer_for(user, cid)
    graph = ownership.build_graph(customer)
    ubos = ownership.compute_ubos(customer)
    return jsonify({"graph": graph, "ubos": ubos,
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

    root = _ensure_root_party(customer)
    # The edge points at the owned party: the root by default, or another party.
    owned_id = body.get("owned_party_id") or root.id

    owner = Party(
        organization_id=customer.organization_id,
        kind=kind, name=owner_name,
        nationality=body.get("nationality"),
        country_of_residence=body.get("country") if kind == "PERSON" else None,
        country_of_incorporation=body.get("country") if kind == "ORGANIZATION" else None,
    )
    db.session.add(owner)
    db.session.flush()

    edge = OwnershipRelationship(
        organization_id=customer.organization_id,
        owner_party_id=owner.id,
        owned_party_id=owned_id,
        relationship_type=body.get("relationship_type", "SHAREHOLDER"),
        percentage=float(body.get("percentage") or 0),
        control_type=body.get("control_type"),
    )
    db.session.add(edge)
    audit.record("OWNERSHIP_ADDED", "customer", customer.id, actor=user,
                 new_value=f"{owner_name} -> {edge.percentage}%",
                 reason="KYB", commit=True)
    return jsonify({"owner": owner.serialize(), "edge": edge.serialize()}), 201


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


@api.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "celery": _celery_enabled()}), 200
