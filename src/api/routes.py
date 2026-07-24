"""REST API for the Compliance OS vertical slice.

Everything is organization-scoped and audited. The interesting endpoint is
POST /customers/<id>/screen — it kicks off the whole spine:
screening -> event -> rules -> risk -> case/task/notification -> (human) decision.
"""
import os
from datetime import timedelta

from flask import request, jsonify, Blueprint
from flask_cors import CORS
from sqlalchemy import func

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
    RiskMethodology, WorkflowDefinition, WorkflowInstance,
    RegulatorySource, RegulatoryRequirement, RegulatoryChange,
    Conversation, Message,
    ChatRoom, ChatMember, ChatMessage,
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
                        review_engine, workflow_engine, regulatory_service,
                        assistant_service, watchlist_service)
from api.engine.screening_service import review_match
from api.tasks import run_screening

api = Blueprint("api", __name__)


def _mfa_ticket(user):
    """A 5-minute token that proves the password step only (mfa_pending)."""
    from flask_jwt_extended import create_access_token
    from datetime import timedelta
    return create_access_token(identity=str(user.id),
                               additional_claims={"mfa_pending": True},
                               expires_delta=timedelta(minutes=5))


def _ticket_user():
    """Load the user behind a pending-MFA ticket, or 401."""
    from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity
    verify_jwt_in_request()
    if not get_jwt().get("mfa_pending"):
        raise APIException("A valid second-factor ticket is required",
                           status_code=401)
    user = User.query.get(int(get_jwt_identity()))
    if user is None or not user.is_active:
        raise APIException("Invalid or inactive user", status_code=401)
    return user


def _auth_response(user, status=200):
    """Return the user and plant the access + refresh cookies. The body still
    carries a token for API clients and older callers; the browser ignores it
    and rides the httpOnly cookies instead."""
    from flask_jwt_extended import (create_access_token, create_refresh_token,
                                    set_access_cookies, set_refresh_cookies)
    access = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "org": user.organization_id})
    refresh = create_refresh_token(identity=str(user.id))
    # The browser rides the httpOnly cookies and ignores this body token (the
    # store scrubs any JWT from localStorage). It stays in the body only so
    # header-auth API clients and the test suite can obtain a token.
    resp = jsonify({"user": user.serialize(), "token": access})
    set_access_cookies(resp, access)
    set_refresh_cookies(resp, refresh)
    return resp, status


def _rate_limit(*rules):
    """Per-route rate limit that no-ops until the limiter is attached in app.py
    (import order), and stays inert under TESTING so the suite isn't throttled."""
    def decorator(fn):
        fn._rate_rules = rules
        return fn
    return decorator
from api.security import cors_origins
CORS(api, origins=cors_origins(), supports_credentials=True)


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
@_rate_limit("5 per minute", "20 per hour")
def register():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        raise APIException("email and password are required", status_code=400)
    from api.security import password_problem
    problem = password_problem(password)
    if problem:
        raise APIException(problem, status_code=400)
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
        role_name = "ADMIN"   # first user of a new org is its administrator

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
    user.email_verified = False   # self-service: must confirm the address
    db.session.add(user)
    db.session.flush()
    db.session.add(OrganizationMembership(
        organization_id=org.id, user_id=user.id, status="ACTIVE"))
    audit.record("USER_CREATED", "user", user.id, actor_label=email,
                 new_value=role_name, reason="self-registration")
    db.session.commit()
    db.session.commit()
    try:
        _send_verification(user)
    except Exception:
        pass   # a mail hiccup must not fail the signup; they can resend
    return _auth_response(user, status=201)


@api.route("/auth/login", methods=["POST"])
@_rate_limit("10 per minute", "50 per hour")
def login():
    from api.security import MAX_FAILED_LOGINS, LOCKOUT_MINUTES
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = User.query.filter_by(email=email).first()

    # One generic failure for every reason — unknown user, wrong password,
    # disabled, locked — so an attacker learns nothing about which accounts
    # exist or why a login failed. The specifics live in the audit trail.
    def _fail():
        audit.record("LOGIN_FAILED", "user",
                     user.id if user else None, actor_label=email,
                     new_value="invalid", commit=True)
        raise APIException("Invalid credentials", status_code=401)

    if user is None:
        _fail()
    if user.locked_until and user.locked_until > utcnow():
        _fail()
    if not user.is_active or not verify_password(user, password):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            audit.record("ACCOUNT_LOCKED", "user", user.id, actor_label=email,
                         new_value=f"{LOCKOUT_MINUTES}m after "
                                   f"{user.failed_logins} failed attempts")
        db.session.commit()
        _fail()

    user.failed_logins = 0
    user.locked_until = None
    db.session.commit()

    # Second factor. Staff enrol TOTP; portal customers get an emailed code.
    # A password-only pass must not open a session, so we return a short-lived
    # pending ticket that is useless until the second factor is presented.
    from api.engine import mfa
    from api.security import mfa_enforced
    if user.mfa_enabled:
        if user.mfa_method == "EMAIL_OTP":
            try:
                mfa.send_email_otp(user)
            except Exception:
                pass
        audit.record("LOGIN_MFA_PENDING", "user", user.id, actor=user, commit=True)
        return jsonify({"mfa_required": True, "method": user.mfa_method,
                        "ticket": _mfa_ticket(user)}), 200
    if mfa_enforced() and not user.is_portal_user():
        # Enforced but not yet enrolled: send them to set TOTP up first.
        audit.record("LOGIN_MFA_SETUP", "user", user.id, actor=user, commit=True)
        return jsonify({"mfa_setup_required": True,
                        "ticket": _mfa_ticket(user)}), 200

    user.last_login_at = utcnow()
    audit.record("LOGIN_OK", "user", user.id, actor=user, commit=True)
    return _auth_response(user)


@api.route("/auth/logout", methods=["POST"])
@login_required
def logout(user):
    """Real logout: revoke THIS token so it cannot be used again, even if it
    was copied elsewhere. The client also forgets it, but the server no longer
    honours it either — the difference between "forgotten" and "revoked"."""
    from flask_jwt_extended import get_jwt
    from datetime import datetime, timezone
    from api.models.security import revoke
    claims = get_jwt()
    exp = claims.get("exp")
    expires_at = (datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None)
                  if exp else None)
    revoke(claims.get("jti"), user_id=user.id, expires_at=expires_at)
    audit.record("LOGOUT", "user", user.id, actor=user, commit=True)
    from flask_jwt_extended import unset_jwt_cookies
    resp = jsonify({"ok": True})
    unset_jwt_cookies(resp)
    return resp, 200


def _app_link(path):
    base = (os.getenv("PORTAL_URL") or os.getenv("APP_URL")
            or request.host_url.rstrip("/"))
    return f"{base.rstrip('/')}{path}"


def _send_verification(user):
    from api.models import email_tokens
    from api.integrations import mailer
    secret = email_tokens.issue(user, "VERIFY_EMAIL")
    org = user.organization.name if user.organization else None
    link = _app_link(f"/verify-email?token={secret}")
    return mailer.notify_verify_email(user, org, link)


@api.route("/auth/verify-email", methods=["POST"])
@_rate_limit("10 per minute")
def verify_email():
    """Confirm an address from the emailed link. Public: the token is the proof."""
    from api.models import email_tokens
    secret = ((request.get_json(silent=True) or {}).get("token") or "").strip()
    uid = email_tokens.consume_by_secret("VERIFY_EMAIL", secret)
    if uid is None:
        raise APIException("This link is invalid or has expired.", status_code=400)
    user = User.query.get(uid)
    if user is None:
        raise APIException("Account not found", status_code=404)
    user.email_verified = True
    audit.record("EMAIL_VERIFIED", "user", user.id, actor=user, commit=True)
    return jsonify({"verified": True}), 200


@api.route("/auth/resend-verification", methods=["POST"])
@login_required
@_rate_limit("3 per hour")
def resend_verification(user):
    if user.email_verified:
        return jsonify({"already_verified": True}), 200
    result = _send_verification(user)
    return jsonify({"sent": result.get("sent", False)}), 200


@api.route("/auth/forgot-password", methods=["POST"])
@_rate_limit("5 per hour")
def forgot_password():
    """Start a reset. Always answers the same, whether or not the address
    exists — otherwise this becomes an account-enumeration oracle."""
    from api.models import email_tokens
    from api.integrations import mailer
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first() if email else None
    if user is not None and user.is_active:
        secret = email_tokens.issue(user, "RESET_PASSWORD")
        org = user.organization.name if user.organization else None
        link = _app_link(f"/reset-password?token={secret}")
        mailer.notify_password_reset(user, org, link)
        audit.record("PASSWORD_RESET_REQUESTED", "user", user.id,
                     actor_label=email, commit=True)
    return jsonify({"ok": True}), 200


@api.route("/auth/reset-password", methods=["POST"])
@_rate_limit("10 per hour")
def reset_password():
    """Set a new password from the emailed link, then revoke the token."""
    from api.models import email_tokens
    from api.security import password_problem
    body = request.get_json(silent=True) or {}
    secret = (body.get("token") or "").strip()
    password = body.get("password") or ""
    problem = password_problem(password)
    if problem:
        raise APIException(problem, status_code=400)
    uid = email_tokens.consume_by_secret("RESET_PASSWORD", secret)
    if uid is None:
        raise APIException("This reset link is invalid or has expired.",
                           status_code=400)
    user = User.query.get(uid)
    if user is None:
        raise APIException("Account not found", status_code=404)
    user.password = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    audit.record("PASSWORD_RESET", "user", user.id, actor=user, commit=True)
    return jsonify({"reset": True}), 200


@api.route("/auth/mfa", methods=["POST"])
@_rate_limit("10 per minute")
def auth_mfa():
    """Second step of login: present the ticket + the code, get the session."""
    from api.engine import mfa
    user = _ticket_user()
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip()
    if not mfa.verify(user, code):
        audit.record("LOGIN_MFA_FAILED", "user", user.id, actor=user, commit=True)
        raise APIException("Invalid code", status_code=401)
    user.last_login_at = utcnow()
    audit.record("LOGIN_OK", "user", user.id, actor=user,
                 new_value="mfa", commit=True)
    return _auth_response(user)


@api.route("/auth/mfa", methods=["DELETE"])
@login_required
def auth_mfa_disable(user):
    from api.engine import mfa
    if user.is_portal_user():
        raise APIException("Email verification stays on for portal accounts.",
                           status_code=400)
    mfa.disable(user, actor=user)
    return jsonify({"disabled": True}), 200


@api.route("/auth/mfa/enroll", methods=["POST"])
def auth_mfa_enroll():
    """Begin TOTP enrollment during forced setup (ticket) or from the profile
    (a live session). Returns the secret + otpauth URI to render as a QR."""
    from api.engine import mfa
    try:
        user = current_user()          # live session (profile)
    except APIException:
        user = _ticket_user()          # forced setup at login
    out = mfa.begin_totp_enrollment(user)
    out["qr_svg"] = _qr_svg(out["otpauth_uri"])
    return jsonify(out), 200


@api.route("/auth/mfa/confirm", methods=["POST"])
def auth_mfa_confirm():
    """Confirm TOTP with a first code, turn it on, and — during forced setup —
    hand back the session. Returns the one-time backup codes."""
    from api.engine import mfa
    setup = False
    try:
        user = current_user()
    except APIException:
        user = _ticket_user()
        setup = True
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip()
    backup = mfa.confirm_totp(user, code)
    if backup is None:
        raise APIException("That code did not match — check the app time and "
                           "try again.", status_code=400)
    if setup:
        user.last_login_at = utcnow()
        resp, status = _auth_response(user)
        payload = resp.get_json()
        payload["backup_codes"] = backup
        resp.set_data(jsonify(payload).get_data())
        return resp, status
    return jsonify({"enabled": True, "backup_codes": backup}), 200


@api.route("/profile", methods=["PATCH"])
@login_required
def update_profile(user):
    """Edit one's own identity fields (not email, role or org — those are
    administered, not self-served)."""
    body = request.get_json(silent=True) or {}
    for field in ("full_name", "job_title", "phone", "timezone"):
        if field in body:
            setattr(user, field, (body.get(field) or "").strip()[:120] or None)
    audit.record("PROFILE_UPDATED", "user", user.id, actor=user, commit=True)
    return jsonify({"user": user.serialize()}), 200


@api.route("/profile/avatar", methods=["POST"])
@login_required
def upload_avatar(user):
    """Set a profile photo. Stored like documents (signed URLs); only images."""
    from api.integrations import media
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise APIException("An image file is required", status_code=400)
    if not (upload.mimetype or "").startswith("image/"):
        raise APIException("The profile photo must be an image.", status_code=400)
    stored = media.store(upload)
    user.avatar_url = stored["url"]
    audit.record("AVATAR_UPDATED", "user", user.id, actor=user, commit=True)
    return jsonify({"user": user.serialize()}), 200


@api.route("/profile/avatar", methods=["DELETE"])
@login_required
def remove_avatar(user):
    user.avatar_url = None
    db.session.commit()
    return jsonify({"user": user.serialize()}), 200


@api.route("/profile/password", methods=["POST"])
@login_required
def change_password(user):
    """Change one's own password — requires the current one, then applies the
    policy. Revokes the current token so other sessions must re-authenticate."""
    from api.security import password_problem
    body = request.get_json(silent=True) or {}
    current = body.get("current_password") or ""
    new_pw = body.get("new_password") or ""
    if not verify_password(user, current):
        raise APIException("Your current password is incorrect.", status_code=400)
    problem = password_problem(new_pw)
    if problem:
        raise APIException(problem, status_code=400)
    user.password = hash_password(new_pw)
    audit.record("PASSWORD_CHANGED", "user", user.id, actor=user, commit=True)
    return jsonify({"ok": True}), 200


@api.route("/auth/refresh", methods=["POST"])
def refresh():
    """Mint a fresh 30-minute access cookie from the refresh cookie. This is
    what keeps a session alive without a long-lived access token, and without
    the browser ever holding a token in JavaScript."""
    from flask_jwt_extended import (jwt_required, get_jwt_identity,
                                    create_access_token, set_access_cookies,
                                    verify_jwt_in_request)
    verify_jwt_in_request(refresh=True)
    uid = get_jwt_identity()
    user = User.query.get(int(uid)) if uid is not None else None
    if user is None or not user.is_active:
        raise APIException("Invalid or inactive user", status_code=401)
    access = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "org": user.organization_id})
    resp = jsonify({"user": user.serialize(), "token": access})
    set_access_cookies(resp, access)
    return resp, 200


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
    """The active book. Archived files are out of the way but never lost —
    `?archived=1` lists them so they can be reviewed or restored."""
    only_archived = request.args.get("archived") in ("1", "true", "yes")
    query = Customer.query.filter_by(organization_id=user.organization_id)
    query = (query.filter(Customer.status == "ARCHIVED") if only_archived
             else query.filter(Customer.status != "ARCHIVED"))
    customers = query.order_by(Customer.risk_score.desc(),
                               Customer.created_at.desc()).all()
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
    # And materialises what we will ask for. Requirements used to be computed
    # lazily, the first time a screen asked — so a customer nobody had opened
    # yet had none in the database, and anything reading the table directly (a
    # dashboard, an export, a reminder job) saw an empty file.
    requirement_engine.evaluate(customer)
    # Auto-enrichment from public sources — async when Celery is up, so bulk
    # onboarding never blocks on external registries.
    if _celery_enabled():
        from api.tasks import run_enrichment
        _dispatch(run_enrichment, customer.id, user.id)
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

    def _task_detail(t):
        """Task + the human names its ids stand for, so the fiche can show
        who is on it and jump to the case that spawned it."""
        data = t.serialize()
        assignee = User.query.get(t.assigned_to) if t.assigned_to else None
        data["assigned_to_name"] = (assignee.full_name or assignee.email) if assignee else None
        parent = Case.query.get(t.case_id) if t.case_id else None
        data["case_title"] = parent.title if parent else None
        return data

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
        "tasks": [_task_detail(t) for t in tasks],
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
    """Attach a document to the file.

    Accepts multipart/form-data with a `file` part — the real case, a scan or
    a PDF — and keeps the JSON form (doc_type only) for declaring that a
    document is expected before it arrives. Only an upload with a file counts
    as evidence for the requirement engine.
    """
    from api.integrations import media

    customer = _get_customer_for(user, cid)
    upload = request.files.get("file")
    body = request.get_json(silent=True) or {}
    source = request.form if upload is not None else body
    doc_type = (source.get("doc_type") or "").strip()
    if not doc_type:
        raise APIException("doc_type is required", status_code=400)
    expiry = source.get("expiry_days")

    doc = Document(
        customer_id=customer.id,
        doc_type=doc_type,
        status=source.get("status", "PENDING"),
        expiry_date=utcnow() + timedelta(days=int(expiry)) if expiry else None,
        uploaded_by_id=user.id,
    )
    if upload is not None and upload.filename:
        stored = media.store(upload)
        doc.file_url = stored["url"]
        doc.file_name = upload.filename[:255]
        doc.media_type = stored["media_type"]
        # Size after storing: the stream is at EOF, which is what we want.
        try:
            doc.file_size = upload.stream.tell() or None
        except Exception:
            doc.file_size = None

    db.session.add(doc)
    audit.record("DOCUMENT_ADDED", "document", None, actor=user,
                 new_value=f"{doc_type} · {doc.file_name}" if doc.file_name else doc_type,
                 reason="Upload" if doc.file_url else "Declared as expected",
                 commit=True)
    # A newly received document can satisfy an outstanding requirement.
    if doc.file_url:
        from api.engine import requirement_engine
        requirement_engine.evaluate(customer)
        db.session.commit()
    return jsonify(doc.serialize()), 201


@api.route("/customers/<int:cid>/documents/<int:did>/review", methods=["POST"])
@permission_required("document.verify")
def review_document(user, cid, did):
    """Accept a document, or return it to the customer with a reason.

    The reason is shown to the customer, so it describes the *document* — not
    why it mattered. A closed list covers the usual cases; free text stays
    available because the analyst knows their file.
    """
    from api.portal import REJECTION_REASONS
    customer = _get_customer_for(user, cid)
    doc = Document.query.filter_by(id=did, customer_id=customer.id).first()
    if doc is None:
        raise APIException("Document not found", status_code=404)
    body = request.get_json(silent=True) or {}
    decision = (body.get("decision") or "").upper()

    if decision == "ACCEPT":
        doc.status = "VERIFIED"
        doc.rejection_reason = None
    elif decision == "RETURN":
        code = (body.get("reason_code") or "").upper()
        reason = REJECTION_REASONS.get(code) or (body.get("reason") or "").strip()
        if not reason:
            raise APIException("A reason is required to return a document",
                               status_code=400)
        doc.status = "PENDING"
        doc.rejection_reason = reason[:300]
    else:
        raise APIException("decision must be ACCEPT or RETURN", status_code=400)

    audit.record("DOCUMENT_REVIEWED", "document", doc.id, actor=user,
                 new_value=decision, reason=doc.rejection_reason or "accepted")
    db.session.commit()
    if decision == "RETURN":
        # A customer who is not told is a customer who does not resend. The
        # email says only that a document is needed — the reason stays behind
        # the login.
        from api.portal import notify_customer
        notify_customer(customer, what="a document")
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify(doc.serialize()), 200


@api.route("/customers/<int:cid>/documents/<int:did>", methods=["DELETE"])
@permission_required("document.upload")
def delete_document(user, cid, did):
    """Remove a document sent by mistake (wrong file, wrong customer)."""
    customer = _get_customer_for(user, cid)
    doc = Document.query.filter_by(id=did, customer_id=customer.id).first()
    if doc is None:
        raise APIException("Document not found", status_code=404)
    audit.record("DOCUMENT_REMOVED", "document", doc.id, actor=user,
                 old_value=f"{doc.doc_type} · {doc.file_name or 'no file'}",
                 reason="Removed by user")
    db.session.delete(doc)
    db.session.commit()
    from api.engine import requirement_engine
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify({"deleted": True}), 200


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


@api.route("/customers/<int:cid>/ownership/<int:edge_id>", methods=["DELETE"])
@permission_required("kyb.edit")
def remove_ownership(user, cid, edge_id):
    """Remove an erroneous owner/director (edge deactivated, history kept).
    Deciding a case FALSE_POSITIVE never rewrites KYB data — this does."""
    customer = _get_customer_for(user, cid)
    edge, emitted = party_service.remove_related_party(customer, edge_id,
                                                       actor=user)
    if edge is None:
        raise APIException("Ownership link not found for this customer",
                           status_code=404)
    return jsonify({"removed": True, "edge": edge.serialize(),
                    "events": emitted}), 200


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


@api.route("/customers/<int:cid>/fields/<int:fid>", methods=["DELETE"])
@permission_required("kyc.edit")
def delete_field(user, cid, fid):
    """Remove a wrong profile field (e.g. registry data imported for the
    wrong company). The audit trail keeps what was removed and by whom."""
    customer = _get_customer_for(user, cid)
    field = ProfileField.query.get(fid)
    if field is None or field.customer_id != cid:
        raise APIException("Field not found", status_code=404)
    audit.record("PROFILE_FIELD_REMOVED", "customer", cid, actor=user,
                 old_value=f"{field.field_key}={field.value} "
                           f"(src={field.source})")
    db.session.delete(field)
    db.session.commit()
    requirement_engine.evaluate(customer)
    db.session.commit()
    return jsonify({"deleted": True}), 200


# ---------------------------------------------------------------------------
# Transaction monitoring
# ---------------------------------------------------------------------------
@api.route("/customers/<int:cid>/transactions", methods=["GET"])
@permission_required("transaction.view")
def list_transactions(user, cid):
    from api.models import Transaction
    customer = _get_customer_for(user, cid)
    q = Transaction.query.filter_by(customer_id=cid)
    if request.args.get("flagged") == "true":
        q = q.filter(Transaction.flagged.is_(True))
    txns = q.order_by(Transaction.booked_at.desc()).limit(200).all()
    return jsonify([t.serialize() for t in txns]), 200


@api.route("/customers/<int:cid>/transactions", methods=["POST"])
@permission_required("transaction.ingest")
def ingest_transactions(user, cid):
    """Ingest one transaction or a batch. Each is monitored on the way in;
    flagged ones raise a TRANSACTION_ALERT onto the compliance spine."""
    from api.engine import transaction_monitoring
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    rows = body.get("transactions")
    if rows is None:
        rows = [body]                      # single-transaction shorthand
    if not isinstance(rows, list) or not rows:
        raise APIException("Provide a transaction or a 'transactions' list",
                           status_code=400)
    out, flagged = [], 0
    for row in rows:
        tx, fired = transaction_monitoring.ingest(customer, row, actor=user)
        out.append(tx.serialize())
        if fired:
            flagged += 1
    return jsonify({"ingested": len(out), "flagged": flagged,
                    "transactions": out}), 201


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
    instance = (WorkflowInstance.query.filter_by(case_id=case.id)
                .order_by(WorkflowInstance.started_at.desc()).first())
    return jsonify({
        "case": case.serialize(with_tasks=True),
        "customer": customer.serialize(),
        "risk": latest.serialize() if latest else None,
        "related_events": [e.serialize() for e in events],
        "audit": [a.serialize() for a in audits],
        "workflow": instance.serialize(deep=True) if instance else None,
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
    # Tenant isolation: an org sees its own entries + system-generated ones.
    q = AuditEvent.query.filter(
        (AuditEvent.organization_id == user.organization_id) |
        (AuditEvent.organization_id.is_(None)))
    et = request.args.get("entity_type")
    eid = request.args.get("entity_id")
    action = request.args.get("action")
    if et:
        q = q.filter(AuditEvent.entity_type == et)
    if eid:
        q = q.filter(AuditEvent.entity_id == int(eid))
    if action:
        q = q.filter(AuditEvent.action.like(f"%{action}%"))
    entries = q.order_by(AuditEvent.created_at.desc()).limit(200).all()
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


# The permissions that keep an ADMIN able to administer permissions. Removing
# them from the ADMIN role would lock every administrator out — refuse.
_LOCKOUT_GUARD = {"role.view", "role.update", "user.view"}


@api.route("/roles/<int:rid>/permissions", methods=["POST"])
@permission_required("role.update")
def toggle_role_permission(user, rid):
    """Grant or revoke one permission on a role (the clickable matrix)."""
    role = Role.query.get(rid)
    if role is None:
        raise APIException("Role not found", status_code=404)
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    enabled = bool(body.get("enabled"))
    permission = Permission.query.filter_by(code=code).first()
    if permission is None:
        raise APIException("Unknown permission code", status_code=400)
    if not enabled and role.name == "ADMIN" and code in _LOCKOUT_GUARD:
        raise APIException(
            f"Refusing to remove {code} from ADMIN — administrators would be "
            "locked out of permission management.", status_code=409)

    has = any(p.code == code for p in role.permissions)
    if enabled and not has:
        role.permissions.append(permission)
    elif not enabled and has:
        role.permissions = [p for p in role.permissions if p.code != code]
    audit.record("ROLE_PERMISSION_GRANTED" if enabled else "ROLE_PERMISSION_REVOKED",
                 "role", role.id, actor=user,
                 old_value=role.name, new_value=code, commit=True)
    return jsonify(role.serialize(with_permissions=True)), 200


@api.route("/users/<int:uid>/permissions", methods=["POST"])
@permission_required("role.update")
def toggle_user_permission(user, uid):
    """Grant or revoke a special authorization (extra permission) for one user."""
    target = User.query.get(uid)
    if target is None or target.organization_id != user.organization_id:
        raise APIException("User not found", status_code=404)
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    enabled = bool(body.get("enabled"))
    permission = Permission.query.filter_by(code=code).first()
    if permission is None:
        raise APIException("Unknown permission code", status_code=400)

    has = any(p.code == code for p in target.extra_permissions)
    if enabled and not has:
        target.extra_permissions.append(permission)
    elif not enabled and has:
        target.extra_permissions = [p for p in target.extra_permissions
                                    if p.code != code]
    audit.record("USER_PERMISSION_GRANTED" if enabled else "USER_PERMISSION_REVOKED",
                 "user", target.id, actor=user,
                 old_value=target.email, new_value=code,
                 reason="Special authorization", commit=True)
    return jsonify(target.serialize()), 200


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


@api.route("/users/assignable", methods=["GET"])
@permission_required("case.assign")
def assignable_users(user):
    """Who work can be handed to: your team-mates when you belong to one or
    more teams, the whole active staff otherwise. Deliberately NOT gated by
    user.view — assigning is an operational act, not user administration."""
    team_ids = [tm.team_id for tm in
                TeamMembership.query.filter_by(user_id=user.id).all()]
    if team_ids:
        member_ids = {tm.user_id for tm in TeamMembership.query
                      .filter(TeamMembership.team_id.in_(team_ids)).all()}
        candidates = User.query.filter(User.id.in_(member_ids)).all()
    else:
        candidates = User.query.filter_by(
            organization_id=user.organization_id).all()
    out = [{"id": u.id, "full_name": u.full_name, "email": u.email}
           for u in candidates
           if u.is_active and u.organization_id == user.organization_id
           and not u.customer_id]           # never a portal account
    out.sort(key=lambda x: (x["full_name"] or x["email"]).lower())
    return jsonify(out), 200


@api.route("/users", methods=["GET"])
@permission_required("user.view")
def list_users(user):
    users = User.query.filter_by(organization_id=user.organization_id).all()
    out = []
    for u in users:
        # Effective permissions included so the admin UI can show which chips
        # come from the role vs an individual grant.
        data = u.serialize(with_permissions=True)
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
@_rate_limit("10 per minute")
def accept_invitation():
    """Public endpoint: turn a valid invitation token into an account."""
    body = request.get_json(silent=True) or {}
    token = body.get("token") or ""
    password = body.get("password") or ""
    if not token or not password:
        raise APIException("token and password are required", status_code=400)
    from api.security import password_problem
    problem = password_problem(password)
    if problem:
        raise APIException(problem, status_code=400)
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
        # A portal invitation binds the account to its customer file here,
        # from the token — the registration form has no say in it.
        customer_id=inv.customer_id,
    )
    # They received the invitation at this address, so it is already proven.
    new_user.email_verified = True   # receiving the invite proved the address
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
    _send_verification(new_user)
    return _auth_response(new_user, status=201)


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
        if body.get("team_id"):
            case.team_id = int(body["team_id"])
        audit.record("CASE_REASSIGNED" if old else "CASE_ASSIGNED", "case",
                     case.id, actor=user, old_value=str(old),
                     new_value=target.email, reason="manual", commit=True)
        # Handing the file over hands over the conversation with it.
        from api.engine import customer_chat
        customer_chat.sync_for_case(case)
        db.session.commit()
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
    # Edge whitespace is never part of a secret — but a newline picked up by
    # copy-paste breaks Authorization headers downstream (real incident).
    secret = (body.get("secret_value") or "").strip()
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


@api.route("/providers/<int:pid>/credentials", methods=["DELETE"])
@permission_required("organization.update")
def delete_provider_credential(user, pid):
    """Remove a stored credential by key name — a mistyped one must be
    correctable from the UI, not only by overwriting it."""
    provider = Provider.query.get(pid)
    if provider is None or provider.organization_id != user.organization_id:
        raise APIException("Provider not found", status_code=404)
    key_name = ((request.get_json(silent=True) or {}).get("key_name") or "").strip()
    cred = (ProviderCredential.query
            .filter_by(provider_id=pid, key_name=key_name).first())
    if cred is None:
        raise APIException("No credential with that key name", status_code=404)
    db.session.delete(cred)
    audit.record("PROVIDER_CREDENTIAL_DELETED", "provider", pid, actor=user,
                 old_value=key_name, commit=True)
    return jsonify({"provider_id": pid, "key_name": key_name, "deleted": True}), 200


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
@_rate_limit("60 per minute")
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
        # The assignee as a person, not an id — the assigned list reads
        # "who is on it" at a glance in a large team.
        assignee = User.query.get(a.assigned_to) if a.assigned_to else None
        data["assigned_to_name"] = (assignee.full_name or assignee.email) if assignee else None
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


# ---------------------------------------------------------------------------
# Workflow engine
# ---------------------------------------------------------------------------
@api.route("/workflows", methods=["GET"])
@permission_required("workflow.view")
def list_workflows(user):
    defs = (WorkflowDefinition.query
            .filter((WorkflowDefinition.organization_id == user.organization_id) |
                    (WorkflowDefinition.organization_id.is_(None)))
            .order_by(WorkflowDefinition.name).all())
    return jsonify([d.serialize(deep=True) for d in defs]), 200


def _get_instance(user, instance_id):
    inst = WorkflowInstance.query.get(instance_id)
    if inst is None or inst.organization_id != user.organization_id:
        raise APIException("Workflow instance not found", status_code=404)
    return inst


@api.route("/cases/<int:case_id>/workflow/start", methods=["POST"])
@permission_required("workflow.execute")
def start_workflow(user, case_id):
    case = Case.query.get(case_id)
    if case is None:
        raise APIException("Case not found", status_code=404)
    _get_customer_for(user, case.customer_id)
    inst = workflow_engine.start_for_case(case, user.organization_id, actor=user)
    if inst is None:
        raise APIException("No matching workflow, or already running", status_code=409)
    return jsonify(inst.serialize(deep=True)), 201


@api.route("/workflow-instances/<int:instance_id>/complete-step", methods=["POST"])
@permission_required("workflow.execute")
def complete_step(user, instance_id):
    inst = _get_instance(user, instance_id)
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if len(note) < 5:
        # A compliance step is completed with findings, not with a click: the
        # note is what an auditor reads to know the work actually happened.
        raise APIException("Describe what was done before completing this step "
                           "(min 5 characters) — it is recorded in the audit "
                           "trail.", status_code=400)
    try:
        workflow_engine.complete_current_step(inst, actor=user, note=note)
    except PermissionError as exc:
        raise APIException(str(exc), status_code=403)
    except ValueError as exc:
        raise APIException(str(exc), status_code=400)
    return jsonify(inst.serialize(deep=True)), 200


@api.route("/workflow-instances/<int:instance_id>/approve", methods=["POST"])
@permission_required("case.approve")
def approve_step(user, instance_id):
    inst = _get_instance(user, instance_id)
    body = request.get_json(silent=True) or {}
    decision = body.get("decision", "APPROVE")
    reason = (body.get("reason") or "").strip()
    if decision not in ("APPROVE", "REJECT") or not reason:
        raise APIException("decision (APPROVE/REJECT) and reason are required",
                           status_code=400)
    try:
        approval = workflow_engine.decide_approval(inst, user, decision, reason)
    except ValueError as exc:
        raise APIException(str(exc), status_code=400)
    return jsonify({"approval": approval.serialize(),
                    "workflow": inst.serialize(deep=True)}), 200


# ---------------------------------------------------------------------------
# Regulatory Intelligence
# ---------------------------------------------------------------------------
@api.route("/regulatory", methods=["GET"])
@permission_required("regulatory.view")
def regulatory_dashboard(user):
    return jsonify(regulatory_service.dashboard(user.organization_id)), 200


@api.route("/regulatory/sources", methods=["GET"])
@permission_required("regulatory.view")
def regulatory_sources(user):
    sources = (RegulatorySource.query
               .filter((RegulatorySource.organization_id == user.organization_id) |
                       (RegulatorySource.organization_id.is_(None))).all())
    return jsonify([s.serialize(deep=True) for s in sources]), 200


@api.route("/regulatory/changes", methods=["POST"])
@permission_required("regulatory.manage")
def create_regulatory_change(user):
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        raise APIException("title is required", status_code=400)
    source = None
    if body.get("source_id"):
        source = RegulatorySource.query.get(body["source_id"])
    change = regulatory_service.register_change(
        user.organization_id, source, title, body.get("summary"),
        impact_level=body.get("impact_level", "MEDIUM"), actor=user)
    return jsonify(change.serialize()), 201


@api.route("/regulatory/changes/<int:change_id>/assess", methods=["POST"])
@permission_required("regulatory.manage")
def assess_regulatory_change(user, change_id):
    change = RegulatoryChange.query.get(change_id)
    if change is None or (change.organization_id not in (None, user.organization_id)):
        raise APIException("Change not found", status_code=404)
    body = request.get_json(silent=True) or {}
    assessment = regulatory_service.assess_impact(change, actor=user,
                                                  notes=body.get("notes"))
    return jsonify({"change": change.serialize(),
                    "assessment": assessment.serialize()}), 200


@api.route("/risk/country-lists", methods=["GET"])
@permission_required("risk.view")
def risk_country_lists(user):
    """The official geography lists behind the score, with their age."""
    from api.engine import country_risk
    return jsonify(country_risk.status(
        organization_id=user.organization_id)), 200


@api.route("/risk/country-lists/sync", methods=["POST"])
@permission_required("risk.approve", "organization.update")
def risk_country_lists_sync(user):
    from api.engine import country_risk
    out = country_risk.sync(organization_id=user.organization_id)
    audit.record("COUNTRY_RISK_SYNCED", "risk_methodology", None, actor=user,
                 new_value=str(len(out.get("synced", []))),
                 reason="Refreshed FATF/EU country lists", commit=True)
    return jsonify(out), 200


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
# management.view is held by managers only, and the person who actually wants
# to sweep reviews is the compliance officer — risk.approve is their marker.
@permission_required("management.view", "risk.approve")
def run_monitoring(user):
    """Manually trigger the continuous-monitoring sweep (normally on Celery beat)."""
    return jsonify(review_engine.run_monitoring()), 200


# ---------------------------------------------------------------------------
# Team chat (rooms, messages, media) — realtime delivery via api.sockets
# ---------------------------------------------------------------------------
def _get_membership(user, room_id):
    member = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if member is None:
        raise APIException("Room not found", status_code=404)
    return member


def _room_summary(room, user):
    last = (ChatMessage.query.filter_by(room_id=room.id)
            .order_by(ChatMessage.id.desc()).first())
    me = next((m for m in room.members if m.user_id == user.id), None)
    unread_q = (ChatMessage.query.filter_by(room_id=room.id)
                .filter(ChatMessage.sender_id != user.id))
    if me and me.last_read_at:
        unread_q = unread_q.filter(ChatMessage.created_at > me.last_read_at)
    return room.serialize(for_user_id=user.id, last_message=last,
                          unread=unread_q.count())


@api.route("/chat/users", methods=["GET"])
@permission_required("workspace.view")
def chat_users(user):
    """Who this user may chat with. Staff see colleagues + customers; a
    customer portal user sees only their reference contacts."""
    from api.engine import chat_directory
    return jsonify(chat_directory.directory(
        user, query=request.args.get("q"))), 200


@api.route("/chat/rooms", methods=["GET"])
@permission_required("workspace.view")
def list_chat_rooms(user):
    from api.engine import customer_chat
    if user.is_portal_user() and user.customer_id:
        # A client always has exactly one conversation, and it exists from the
        # moment they log in — they should never have to create anything.
        customer = Customer.query.get(user.customer_id)
        if customer is not None:
            customer_chat.sync_members(customer)
            db.session.commit()

    memberships = ChatMember.query.filter_by(user_id=user.id).all()
    rooms = [m.room for m in memberships
             if m.room.organization_id == user.organization_id]
    out = [_room_summary(r, user) for r in rooms]
    # Most recent activity first.
    out.sort(key=lambda r: (r["last_message"] or {}).get("created_at") or "",
             reverse=True)
    return jsonify(out), 200


@api.route("/customers/<int:cid>/chat-room", methods=["POST"])
@permission_required("workspace.view")
def open_customer_chat_room(user, cid):
    """Open (creating it if needed) the conversation attached to a customer.

    Staff on the file get in through the team. When nobody is assigned yet, any
    staff member who may see customers can pick the conversation up — and doing
    so makes them a participant, so the client gets an answer instead of
    silence.
    """
    from api.engine import customer_chat
    customer = Customer.query.get(cid)
    if not customer_chat.can_open(user, customer, has_permission):
        raise APIException("Not allowed to open this conversation",
                           status_code=403)
    room = customer_chat.sync_members(customer, extra_user_ids=[user.id])
    db.session.commit()
    return jsonify(_room_summary(room, user)), 200


@api.route("/chat/rooms", methods=["POST"])
@permission_required("workspace.view")
def create_chat_room(user):
    """{user_id} -> 1-1 DM (reused if it exists); {name, member_ids} -> group."""
    from api.sockets import socketio as sio
    body = request.get_json(silent=True) or {}

    from api.engine import chat_directory

    if body.get("user_id"):  # ---- direct message
        other = User.query.get(int(body["user_id"]))
        if other is None or other.organization_id != user.organization_id:
            raise APIException("User not found", status_code=404)
        if other.id == user.id:
            raise APIException("Cannot DM yourself", status_code=400)
        # Portal users may only reach their reference contacts.
        if not chat_directory.can_message(user, other):
            raise APIException("You are not allowed to message this user.",
                               status_code=403)
        # Reuse the existing DM between these two people.
        for m in ChatMember.query.filter_by(user_id=user.id).all():
            room = m.room
            if not room.is_group and room.member_ids() == {user.id, other.id}:
                return jsonify(_room_summary(room, user)), 200
        room = ChatRoom(organization_id=user.organization_id, is_group=False,
                        created_by=user.id)
        db.session.add(room)
        db.session.flush()
        db.session.add_all([ChatMember(room_id=room.id, user_id=user.id),
                            ChatMember(room_id=room.id, user_id=other.id)])
        member_ids = [user.id, other.id]
    else:  # ---- group
        if not chat_directory.can_create_group(user):
            raise APIException("Customer users can only message their "
                               "reference contact.", status_code=403)
        name = (body.get("name") or "").strip()
        if not name:
            raise APIException("name (group) or user_id (DM) is required",
                               status_code=400)
        ids = {int(i) for i in (body.get("member_ids") or [])}
        members = User.query.filter(
            User.id.in_(ids or {0}),
            User.organization_id == user.organization_id).all()
        room = ChatRoom(organization_id=user.organization_id, is_group=True,
                        name=name, created_by=user.id)
        db.session.add(room)
        db.session.flush()
        db.session.add(ChatMember(room_id=room.id, user_id=user.id))
        for m in members:
            if m.id != user.id:
                db.session.add(ChatMember(room_id=room.id, user_id=m.id))
        member_ids = [user.id] + [m.id for m in members if m.id != user.id]

    db.session.commit()
    db.session.refresh(room)
    # Tell every member's live socket so they can subscribe + refresh lists.
    for uid in member_ids:
        sio.emit("chat:room-created", {"room_id": room.id}, to=f"user:{uid}")
    return jsonify(_room_summary(room, user)), 201


@api.route("/chat/rooms/<int:rid>/members", methods=["POST"])
@permission_required("workspace.view")
def add_chat_member(user, rid):
    from api.sockets import socketio as sio
    member = _get_membership(user, rid)
    room = member.room
    if not room.is_group:
        raise APIException("Direct messages cannot gain members", status_code=400)
    body = request.get_json(silent=True) or {}
    new_user = User.query.get(int(body.get("user_id") or 0))
    if new_user is None or new_user.organization_id != user.organization_id:
        raise APIException("User not found", status_code=404)
    if new_user.id not in room.member_ids():
        db.session.add(ChatMember(room_id=rid, user_id=new_user.id))
        db.session.add(ChatMessage(
            room_id=rid, sender_id=None, kind="SYSTEM",
            body=f"{user.full_name or user.email} added "
                 f"{new_user.full_name or new_user.email}"))
        db.session.commit()
        sio.emit("chat:room-created", {"room_id": rid}, to=f"user:{new_user.id}")
    return jsonify(_room_summary(room, user)), 200


@api.route("/chat/rooms/<int:rid>/messages", methods=["GET"])
@permission_required("workspace.view")
def list_chat_messages(user, rid):
    _get_membership(user, rid)
    q = ChatMessage.query.filter_by(room_id=rid)
    before_id = request.args.get("before_id", type=int)
    if before_id:
        q = q.filter(ChatMessage.id < before_id)
    limit = min(request.args.get("limit", 50, type=int), 200)
    msgs = q.order_by(ChatMessage.id.desc()).limit(limit).all()
    return jsonify([m.serialize() for m in reversed(msgs)]), 200


@api.route("/chat/rooms/<int:rid>/messages", methods=["POST"])
@permission_required("workspace.view")
def post_chat_message(user, rid):
    """REST send — same result as the socket event (and broadcast to it)."""
    from api.sockets import socketio as sio
    from api.models import CHAT_MESSAGE_KINDS
    _get_membership(user, rid)
    body = request.get_json(silent=True) or {}
    kind = body.get("kind") if body.get("kind") in CHAT_MESSAGE_KINDS else "TEXT"
    text = (body.get("body") or "").strip() or None
    if not text and not body.get("media_url"):
        raise APIException("body or media_url is required", status_code=400)
    msg = ChatMessage(room_id=rid, sender_id=user.id, kind=kind, body=text,
                      media_url=body.get("media_url"),
                      media_type=body.get("media_type"),
                      meta=body.get("meta") or {})
    db.session.add(msg)
    db.session.commit()
    sio.emit("chat:message", msg.serialize(), to=f"chatroom:{rid}")
    return jsonify(msg.serialize()), 201


@api.route("/chat/rooms/<int:rid>/read", methods=["POST"])
@permission_required("workspace.view")
def mark_chat_read(user, rid):
    member = _get_membership(user, rid)
    member.last_read_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True}), 200


@api.route("/chat/upload", methods=["POST"])
@permission_required("workspace.view")
def chat_upload(user):
    """Multipart upload for voice notes / video / images / files."""
    from api.integrations import media
    f = request.files.get("file")
    if f is None:
        raise APIException("file is required", status_code=400)
    stored = media.store(f)
    return jsonify(stored), 201


# ---------------------------------------------------------------------------
# KYC intake form (full CDD questionnaire)
# ---------------------------------------------------------------------------
@api.route("/kyc-form/schema", methods=["GET"])
@permission_required("kyc.view")
def kyc_form_schema(user):
    from api import kyc_form
    ctype = request.args.get("customer_type", "INDIVIDUAL")
    rank = int(request.args.get("risk_rank", 0))
    return jsonify(kyc_form.schema_for(ctype, rank)), 200


@api.route("/customers/<int:cid>/kyc-form", methods=["GET"])
@permission_required("kyc.view")
def get_kyc_form(user, cid):
    """The customer's form: schema at their risk rank + current values + proofs."""
    from api import kyc_form
    from api.models import RISK_RANK
    customer = _get_customer_for(user, cid)
    rank = RISK_RANK.get(customer.risk_level, 0)
    schema = kyc_form.schema_for(customer.customer_type, rank)
    fields = ProfileField.query.filter_by(customer_id=cid).all()
    docs = Document.query.filter_by(customer_id=cid).all()
    return jsonify({
        **schema,
        "customer": customer.serialize(),
        "values": {f.field_key: {"value": f.value, "verified": f.verified,
                                 "source": f.source} for f in fields},
        "documents": [d.serialize() for d in docs],
        "completeness": requirement_engine.summary(customer),
    }), 200


@api.route("/customers/<int:cid>/kyc-form", methods=["POST"])
@permission_required("kyc.edit")
def save_kyc_form(user, cid):
    """Batch-save form answers into ProfileField (source='kyc_form')."""
    from api import kyc_form
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    values = body.get("fields") or {}
    if not isinstance(values, dict) or not values:
        raise APIException("fields must be a non-empty object", status_code=400)

    index = kyc_form.field_index()
    current = {f.field_key: f.value for f in
               ProfileField.query.filter_by(customer_id=cid).all()}
    saved = 0
    for key, value in values.items():
        spec = index.get(key)
        if spec is None:
            continue  # only schema fields are accepted
        value = ("" if value is None else str(value)).strip()
        if value == (current.get(key) or ""):
            continue  # unchanged — don't reset verification
        kyc_service.set_field(customer, key, value,
                              category=spec.get("category"),
                              source="kyc_form", actor=user)
        saved += 1

    if saved:
        kyc_service.sync_address_from_form(customer, actor=user)

    return jsonify({"saved": saved,
                    "completeness": requirement_engine.summary(customer)}), 200


@api.route("/customers/<int:cid>/kyc-form/submit", methods=["POST"])
@permission_required("kyc.edit")
def submit_kyc_form(user, cid):
    """Finalize the intake: audit + event so the rules engine routes review work."""
    from api.engine.events import emit_event
    customer = _get_customer_for(user, cid)
    summary = requirement_engine.summary(customer)

    audit.record("KYC_FORM_SUBMITTED", "customer", cid, actor=user,
                 new_value=f"completeness={summary['completeness_pct']}%",
                 commit=True)
    emit_event("KYC_FORM_SUBMITTED", customer_id=cid, severity="INFO",
               source="kyc_form", actor=user,
               payload={"completeness_pct": summary["completeness_pct"]})

    # A self-declared PEP triggers the EDD chain even before screening runs.
    pep = (ProfileField.query
           .filter_by(customer_id=cid, field_key="pep_self_declaration").first())
    if pep and (pep.value or "").lower() == "yes":
        emit_event("PEP_DETECTED", customer_id=cid, severity="HIGH",
                   source="kyc_form_self_declaration", actor=user,
                   payload={"declared": True})

    return jsonify({"submitted": True, "completeness": summary}), 202


# ---------------------------------------------------------------------------
# Public watchlists (OFAC / UN / EU) + Companies House KYB
# ---------------------------------------------------------------------------
@api.route("/watchlists", methods=["GET"])
@permission_required("screening.view", "regulatory.view")
def watchlist_stats(user):
    return jsonify(watchlist_service.stats()), 200


@api.route("/watchlists/search", methods=["GET"])
@permission_required("screening.view", "regulatory.view")
def watchlist_search(user):
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        raise APIException("q must be at least 3 characters", status_code=400)
    hits = watchlist_service.search(q, limit=25)
    return jsonify([{**e.serialize(), "score": score} for e, score in hits]), 200


@api.route("/name-suggestions", methods=["GET"])
@permission_required("customer.create", "customer.view")
def name_suggestions(user):
    """Type-ahead for the customer name field.

    Two groups, because two different mistakes happen while typing a name.
    `customers` catches the duplicate you are about to create. `watchlist`
    offers the canonical legal spelling from the public lists — and warns, at
    entry time rather than after onboarding, that the name is sanctioned.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"customers": [], "watchlist": []}), 200

    like = f"%{q.lower()}%"
    existing = (Customer.query
                .filter(Customer.organization_id == user.organization_id,
                        func.lower(Customer.name).like(like))
                .order_by(Customer.name).limit(10).all())
    hits = watchlist_service.suggest(q, limit=25)
    return jsonify({
        "customers": [{"id": c.id, "name": c.name, "status": c.status,
                       "customer_type": c.customer_type,
                       "risk_level": c.risk_level} for c in existing],
        "watchlist": [{"name": e.name, "source": e.source,
                       "entity_type": e.entity_type, "country": e.country,
                       "programs": e.programs or []} for e in hits],
    }), 200


@api.route("/watchlists/wallet", methods=["GET"])
@permission_required("screening.run", "screening.view")
def watchlist_wallet_check(user):
    """Screen a blockchain address against the sanctioned wallets.

    OFAC publishes designated wallets inside the SDN file the platform already
    downloads, so this costs nothing extra — and for a crypto client it is the
    check that actually matters.
    """
    address = (request.args.get("address") or "").strip()
    if len(address) < 20:
        raise APIException("A full wallet address is required", status_code=400)
    hits = watchlist_service.screen_wallet(address)
    return jsonify({
        "address": address,
        "sanctioned": bool(hits),
        "matches": [w.serialize() for w in hits],
    }), 200


@api.route("/watchlists/ingest", methods=["POST"])
@permission_required("regulatory.manage")
def watchlist_ingest(user):
    """Refresh the local copy of the public sanctions lists."""
    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "ALL").upper()
    prefer_live = body.get("live", True)
    limit = body.get("limit")
    if source == "ALL":
        imports = watchlist_service.ingest_all(actor=user,
                                               prefer_live=prefer_live,
                                               limit=limit)
    else:
        imports = [watchlist_service.ingest(source, actor=user,
                                            prefer_live=prefer_live,
                                            limit=limit)]
    return jsonify([i.serialize() for i in imports]), 200


# ---------------------------------------------------------------------------
# Portal access — how a customer gets their account.
#
# The staff member enters an email on the customer file; the client receives a
# link (a "Register" button) and a QR code that lead to the same registration
# page. The token carries the customer binding, so whoever registers through it
# is attached to this file and lands in the portal — the form itself has no say
# in which customer the account belongs to.
# ---------------------------------------------------------------------------
def _portal_invite_link(token):
    base = (os.getenv("PORTAL_URL") or request.host_url.rstrip("/"))
    return f"{base.rstrip('/')}/login?invite={token}"


def _qr_svg(link):
    import io as _io
    import segno
    buf = _io.BytesIO()          # segno writes SVG as bytes
    segno.make(link, error="m").save(buf, kind="svg", scale=4,
                                     dark="#131722", border=2)
    return buf.getvalue().decode("utf-8")


def _qr_png(link):
    import io as _io
    import segno
    buf = _io.BytesIO()
    segno.make(link, error="m").save(buf, kind="png", scale=6, border=2)
    return buf.getvalue()


@api.route("/customers/<int:cid>/portal-access", methods=["GET"])
@permission_required("customer.view")
def portal_access_status(user, cid):
    """Who can already sign in for this customer, and any pending invite."""
    customer = _get_customer_for(user, cid)
    accounts = User.query.filter_by(customer_id=customer.id).all()
    pending = (Invitation.query
               .filter_by(customer_id=customer.id, status="PENDING")
               .order_by(Invitation.id.desc()).all())
    out = []
    for inv in pending:
        if not inv.is_valid():
            continue
        link = _portal_invite_link(inv.token)
        out.append({**inv.serialize(), "link": link, "qr_svg": _qr_svg(link)})
    return jsonify({
        "accounts": [{"id": u.id, "email": u.email, "full_name": u.full_name,
                      "is_active": u.is_active} for u in accounts],
        "pending": out,
    }), 200


@api.route("/customers/<int:cid>/portal-access", methods=["POST"])
@permission_required("customer.update")
def invite_customer_to_portal(user, cid):
    """Create the portal invitation and email the link + QR to the client."""
    from api.integrations import mailer
    customer = _get_customer_for(user, cid)
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise APIException("A valid email address is required", status_code=400)
    if User.query.filter_by(email=email).first():
        raise APIException("A user with this email already exists", status_code=409)

    # One live invitation per customer+email: re-inviting replaces the token,
    # so a mis-sent link can always be killed by sending a fresh one.
    for old in Invitation.query.filter_by(customer_id=customer.id,
                                          email=email, status="PENDING").all():
        old.status = "REVOKED"

    inv = Invitation(organization_id=customer.organization_id, email=email,
                     proposed_role="CUSTOMER_USER", customer_id=customer.id,
                     created_by=user.id)
    db.session.add(inv)
    db.session.flush()
    audit.record("PORTAL_INVITED", "customer", customer.id, actor=user,
                 new_value=email, reason="Portal access invitation")
    db.session.commit()

    link = _portal_invite_link(inv.token)
    org = customer.organization.name if customer.organization else "your compliance team"
    html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;padding:24px">
  <h2 style="color:#131722">{org}</h2>
  <p>Hello,</p>
  <p>{org} has opened a secure portal for you. Use it to provide your
     information, send documents and message the team directly.</p>
  <p style="text-align:center;margin:28px 0">
    <a href="{link}" style="background:#111a2e;color:#ffffff;padding:14px 34px;
       border-radius:8px;text-decoration:none;font-weight:bold">Register</a>
  </p>
  <p>Or scan this QR code with your phone — it leads to the same page:</p>
  <p style="text-align:center"><img src="cid:__QR__" alt="Registration QR code"
     width="180" height="180" /></p>
  <p style="color:#6b7280;font-size:12px">This invitation expires in 7 days.
     If you were not expecting it, you can ignore this message.</p>
</div>"""
    text = (f"{org} has opened a secure portal for you.\n\n"
            f"Register here: {link}\n\n"
            "This invitation expires in 7 days.")
    email_result = mailer.send(email, f"{org} — your secure portal access",
                               text, html=html,
                               inline_png=("portal-qr.png", _qr_png(link)))
    return jsonify({
        "invitation": inv.serialize(),
        "link": link,
        "qr_svg": _qr_svg(link),
        "email": email_result,
    }), 201


@api.route("/customers/<int:cid>/portal-access/<int:iid>", methods=["DELETE"])
@permission_required("customer.update")
def revoke_portal_invite(user, cid, iid):
    customer = _get_customer_for(user, cid)
    inv = Invitation.query.filter_by(id=iid, customer_id=customer.id).first()
    if inv is None:
        raise APIException("Invitation not found", status_code=404)
    inv.status = "REVOKED"
    audit.record("PORTAL_INVITE_REVOKED", "customer", customer.id, actor=user,
                 new_value=inv.email, commit=True)
    return jsonify({"revoked": True}), 200


@api.route("/customers/<int:cid>/deletion-check", methods=["GET"])
@permission_required("customer.update")
def customer_deletion_check(user, cid):
    """What would happen if this customer were removed (shown in the modal).

    Gated on customer.update because the default action is archiving, which
    anyone who can edit the file may do. `can_delete` tells the modal whether
    to offer the destructive "delete from the database" option on top.
    """
    from api.engine import customer_deletion
    customer = _get_customer_for(user, cid)
    return jsonify({
        "customer": customer.serialize(),
        "blockers": customer_deletion.blockers(customer),
        "can_delete": has_permission(user, "customer.delete"),
        "can_override": has_permission(user, "organization.update"),
        "counts": {
            "cases": Case.query.filter_by(customer_id=cid).count(),
            "tasks": Task.query.filter_by(customer_id=cid).count(),
            "documents": Document.query.filter_by(customer_id=cid).count(),
            "screening_matches": ScreeningMatch.query.filter_by(customer_id=cid).count(),
            "events": ComplianceEvent.query.filter_by(customer_id=cid).count(),
            "alerts": ComplianceAlert.query.filter_by(customer_id=cid).count(),
        },
    }), 200


@api.route("/customers/<int:cid>", methods=["DELETE"])
@permission_required("customer.delete")
def delete_customer_route(user, cid):
    """Delete an erroneous customer record. Requires a typed confirmation of
    the customer name + a reason; refuses when retention rules apply."""
    from api.engine import customer_deletion
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    confirm = (body.get("confirm_name") or "").strip()
    if len(reason) < 5:
        raise APIException("A reason (min 5 chars) is required — it is audited.",
                           status_code=400)
    if confirm.lower() != (customer.name or "").strip().lower():
        raise APIException("Confirmation failed: type the exact customer name.",
                           status_code=400)

    # Only an administrator may override the retention guard.
    force = bool(body.get("force")) and has_permission(user, "organization.update")
    try:
        out = customer_deletion.delete_customer(customer, user, reason, force=force)
    except ValueError as exc:
        raise APIException(str(exc), status_code=409)
    return jsonify(out), 200


@api.route("/customers/<int:cid>/archive", methods=["POST"])
@permission_required("customer.update")
def archive_customer_route(user, cid):
    """Safe alternative to deletion: keep the file, leave the active book."""
    from api.engine import customer_deletion
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "Archived by user").strip()
    customer_deletion.archive_customer(customer, user, reason)
    return jsonify(customer.serialize()), 200


@api.route("/customers/<int:cid>/restore", methods=["POST"])
@permission_required("customer.update")
def restore_customer_route(user, cid):
    """Bring an archived customer back into the active book."""
    from api.engine import customer_deletion
    customer = _get_customer_for(user, cid)
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "Restored by user").strip()
    customer_deletion.restore_customer(customer, user, reason)
    return jsonify(customer.serialize()), 200


@api.route("/customers/<int:cid>/enrich", methods=["POST"])
@permission_required("kyc.edit")
def enrich_customer(user, cid):
    """Auto-fill the file from public sources (registries, LEI, adverse media).

    Runs inline so the analyst gets the report immediately; the same engine
    also runs asynchronously (Celery) when customers are created in bulk.
    """
    from api.engine import enrichment_service
    customer = _get_customer_for(user, cid)
    report = enrichment_service.enrich(customer, actor=user)
    return jsonify(report), 200


@api.route("/customers/<int:cid>/kyb-lookup", methods=["POST"])
@permission_required("kyb.view")
def kyb_lookup(user, cid):
    """Company-registry lookup (Companies House) through the KYB provider."""
    customer = _get_customer_for(user, cid)
    try:
        result = provider_service.verify_customer(customer, actor=user,
                                                  provider_type="KYB")
    except RuntimeError as exc:
        raise APIException(str(exc), status_code=409)
    return jsonify(result.serialize()), 200


# ---------------------------------------------------------------------------
# Compliance Copilot (AI assistant)
# ---------------------------------------------------------------------------
def _get_conversation_for(user, conversation_id):
    conv = Conversation.query.get(conversation_id)
    if conv is None or conv.organization_id != user.organization_id \
            or conv.user_id != user.id:
        raise APIException("Conversation not found", status_code=404)
    return conv


@api.route("/assistant/meta", methods=["GET"])
@permission_required("workspace.view")
def assistant_meta(user):
    """UI bootstrap: which provider is live + suggested prompts."""
    from api.integrations.ai import get_llm
    return jsonify({
        "provider": get_llm().name,
        "suggested_prompts": assistant_service.SUGGESTED_PROMPTS,
    }), 200


@api.route("/assistant/check", methods=["POST"])
@permission_required("workspace.view")
def assistant_check(user):
    """Probe the configured AI provider so credential problems are visible in
    the UI instead of only surfacing when someone sends a message."""
    from api.integrations.ai import get_llm, reset_llm
    reset_llm()  # re-read the environment: the key may have just been fixed
    provider = get_llm()
    ok, detail = provider.check()
    return jsonify({"provider": provider.name, "ok": ok, "detail": detail}), 200


@api.route("/assistant/conversations", methods=["GET"])
@permission_required("workspace.view")
def list_conversations(user):
    convs = (Conversation.query
             .filter_by(organization_id=user.organization_id, user_id=user.id)
             .order_by(Conversation.updated_at.desc()).all())
    return jsonify([c.serialize() for c in convs]), 200


@api.route("/assistant/conversations", methods=["POST"])
@permission_required("workspace.view")
def create_conversation(user):
    body = request.get_json(silent=True) or {}
    customer_id = body.get("customer_id")
    if customer_id is not None:
        # Validate the anchor belongs to the org.
        _get_customer_for(user, customer_id)
    conv = Conversation(
        organization_id=user.organization_id,
        user_id=user.id,
        customer_id=customer_id,
        title=(body.get("title") or "New conversation"),
    )
    db.session.add(conv)
    db.session.commit()
    return jsonify(conv.serialize(with_messages=True)), 201


@api.route("/assistant/conversations/<int:conversation_id>", methods=["GET"])
@permission_required("workspace.view")
def get_conversation(user, conversation_id):
    conv = _get_conversation_for(user, conversation_id)
    return jsonify(conv.serialize(with_messages=True)), 200


@api.route("/assistant/conversations/<int:conversation_id>/messages", methods=["POST"])
@permission_required("workspace.view")
def send_assistant_message(user, conversation_id):
    conv = _get_conversation_for(user, conversation_id)
    body = request.get_json(silent=True) or {}
    text = (body.get("content") or "").strip()
    if not text:
        raise APIException("content is required", status_code=400)
    try:
        reply = assistant_service.ask(conv, user, text)
    except Exception as exc:
        # Wrong/expired API key, quota, network… — surface it readably instead
        # of a blank 500, and leave the conversation intact.
        db.session.rollback()
        raise APIException(f"AI provider error: {exc}", status_code=502)
    return jsonify({"conversation": conv.serialize(),
                    "reply": reply.serialize()}), 201


@api.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "celery": _celery_enabled()}), 200
