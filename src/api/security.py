"""Startup security posture and shared credential rules.

Kept in one place so the decisions are auditable rather than scattered: what
counts as a strong-enough secret, what a password must satisfy, and which
origins the browser may call from. A financial-sector product is expected to
refuse to run insecurely, not to run and hope.
"""
import os

# The value the template ships with. If this ever reaches production, anyone
# reading the public repo can forge valid tokens — so it is treated as "no
# secret at all".
_DEFAULT_SECRET = "change-me-in-production"
_MIN_SECRET_LEN = 32

# A small block-list of the passwords that dominate every breach corpus. Not a
# substitute for length, just a floor under it.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456", "12345678", "123456789",
    "qwerty", "azerty", "111111", "abc123", "letmein", "welcome", "admin",
    "iloveyou", "changeme", "compliance", "compliance1", "demo1234",
}
MIN_PASSWORD_LEN = 12

# Base words that make a password guessable even when padded with digits.
_COMMON_BASES = ("password", "azerty", "qwerty", "motdepasse",
                 "compliance", "welcome", "letmein")


def is_production():
    return os.getenv("FLASK_DEBUG") != "1"


def check_startup_secret():
    """Raise at boot if the JWT secret is missing, default or too short — but
    only in production, so local dev with a throwaway secret still runs."""
    secret = os.getenv("JWT_SECRET_KEY") or os.getenv("FLASK_APP_KEY") or ""
    if not is_production():
        return
    if not secret or secret == _DEFAULT_SECRET or len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            "JWT_SECRET_KEY is missing, default or shorter than "
            f"{_MIN_SECRET_LEN} characters. Set a strong random secret before "
            "starting in production (e.g. `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"`).")


def cors_origins():
    """Allowed browser origins.

    Production must name them explicitly (CORS_ORIGINS, comma-separated) — an
    open CORS on a financial app lets any site drive the API with a stolen
    cookie. Development falls back to '*' for the two-server Vite setup.
    """
    configured = os.getenv("CORS_ORIGINS", "").strip()
    if configured:
        return [o.strip() for o in configured.split(",") if o.strip()]
    return "*" if not is_production() else []


def password_problem(password):
    """Return a human reason the password is unacceptable, or None if it is
    fine. Shared by registration, invitation acceptance and password reset."""
    password = password or ""
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        return "This password is too common — choose a less predictable one."
    # "password1234" passes length and character-class checks but is obviously
    # weak: reject anything built around a common base word.
    if any(base in lowered for base in _COMMON_BASES):
        return "This password is too predictable — avoid words like 'password'."
    # A minimum of variety without being annoying: at least two character
    # classes among letters, digits and symbols.
    classes = sum([
        any(c.islower() or c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 2:
        return ("Password must mix at least two of: letters, digits, symbols.")
    return None


# --- brute-force lockout -----------------------------------------------------
# After this many consecutive failures the account is locked for the cooldown.
# Rate limiting (below) throttles the attempt volume; the lockout stops a slow
# drip against one account.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


def register_rate_limits(app):
    """Attach Flask-Limiter. In-memory by default (fine for one process);
    point RATELIMIT_STORAGE_URI at Redis for multi-worker production."""
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[os.getenv("RATELIMIT_DEFAULT", "300 per minute")],
        storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        headers_enabled=True,
        # Off in the test suite (RATELIMIT_ENABLED=false): the limiter binds at
        # import time, before app.config[TESTING] exists, and would otherwise
        # count every module's logins against one client address.
        enabled=os.getenv("RATELIMIT_ENABLED", "true").lower() != "false",
    )
    return limiter
