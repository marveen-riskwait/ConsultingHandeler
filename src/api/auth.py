"""Authentication & authorization helpers (JWT + role checks).

The frontend can hide buttons, but the backend is always the source of truth for
who may do what — every protected route goes through here.
"""
from functools import wraps

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
        return fn(user, *args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if roles and user.role not in roles and user.role != "ADMIN":
                raise APIException("Insufficient permissions", status_code=403)
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator
