"""Authentication & authorization helpers (JWT + role checks).

The frontend can hide buttons, but the backend is always the source of truth for
who may do what — every protected route goes through here.
"""
from functools import wraps

from flask import request
from flask_jwt_extended import (
    create_access_token, get_jwt_identity, verify_jwt_in_request,
)
from werkzeug.security import generate_password_hash, check_password_hash

from api.models import User
from api.utils import APIException


def hash_password(raw):
    return generate_password_hash(raw)


def verify_password(user, raw):
    return check_password_hash(user.password, raw)


def make_token(user):
    # Identity is a string; claims carry role + org for cheap checks.
    return create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "org": user.organization_id},
    )


def current_user():
    verify_jwt_in_request()
    uid = get_jwt_identity()
    user = User.query.get(int(uid)) if uid is not None else None
    if user is None or not user.is_active:
        raise APIException("Invalid or inactive user", status_code=401)
    return user


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        _enforce_portal_boundary(user)
        return fn(user, *args, **kwargs)
    return wrapper


def has_permission(user, code):
    """Single source of truth for authorization: role -> permissions."""
    return user.has_permission(code)


# ---------------------------------------------------------------------------
# Customer portal boundary
#
# A customer logging into the platform must never reach a staff endpoint. The
# old arrangement relied on portal accounts simply not holding the right
# permissions — which failed, because they legitimately need `kyc.view` to fill
# their own questionnaire and `workspace.view` to use chat, and those two open
# most of the customer file.
#
# So the boundary is an allowlist enforced at one choke point rather than a
# hope spread across every route: a portal account may call the /api/portal/*
# blueprint, plus the handful of shared endpoints below. Anything else is
# refused — including endpoints written next year by someone who never thought
# about portal users.
PORTAL_SHARED_ENDPOINTS = {
    "api.me", "api.logout",
    # Messaging: rooms are already gated by membership, and the customer room
    # is the intended channel to the firm.
    "api.list_chat_rooms", "api.list_chat_messages", "api.post_chat_message",
    "api.mark_chat_read", "api.chat_upload", "api.chat_users",
}


def _portal_may_call(user):
    """True when this request is inside the portal's allowed surface."""
    if not user.is_portal_user():
        return True
    if request.blueprint == "portal":
        return True
    return request.endpoint in PORTAL_SHARED_ENDPOINTS


def _enforce_portal_boundary(user):
    if not _portal_may_call(user):
        raise APIException(
            "This endpoint is not available to customer portal accounts.",
            status_code=403)


def permission_required(*codes, require_all=False):
    """Guard a route by permission code(s). By default holding ANY of the codes
    is enough; pass require_all=True to require every one.

    The decorated view receives the authenticated `user` as its first argument
    (same contract as login_required).
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            _enforce_portal_boundary(user)
            if codes:
                granted = user.permission_codes()
                ok = (granted.issuperset(codes) if require_all
                      else any(c in granted for c in codes))
                if not ok:
                    joiner = " & " if require_all else " or "
                    raise APIException(
                        "Missing permission: " + joiner.join(codes),
                        status_code=403)
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator


def role_required(*roles):
    """Deprecated in favour of permission_required; kept for any legacy callers."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if roles and user.role not in roles and user.role not in ("ADMIN", "PLATFORM_ADMIN"):
                raise APIException("Insufficient permissions", status_code=403)
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator
