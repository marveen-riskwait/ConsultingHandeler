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


# --- cookie-based sessions ---------------------------------------------------
def apply_jwt_cookie_config(app):
    """Deliver JWTs as httpOnly cookies (browser) while still accepting the
    Authorization header (API clients, tests). CSRF is enforced on the cookie
    path only — flask-jwt-extended checks the X-CSRF-TOKEN header against a
    readable csrf cookie for every non-GET cookie request."""
    app.config["JWT_TOKEN_LOCATION"] = ["cookies", "headers"]
    app.config["JWT_COOKIE_CSRF_PROTECT"] = True
    app.config["JWT_CSRF_CHECK_FORM"] = False
    # Secure (HTTPS-only) in production; Lax is enough because the app is
    # same-origin (Vite proxy in dev, Flask-served bundle in prod).
    app.config["JWT_COOKIE_SECURE"] = is_production()
    app.config["JWT_COOKIE_SAMESITE"] = "Lax"
    # The refresh cookie is only ever sent to the refresh endpoint.
    app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth/refresh"
    app.config["JWT_ACCESS_COOKIE_PATH"] = "/"


# --- HTTP security headers ---------------------------------------------------
def apply_security_headers(app):
    """Content-Security-Policy, HSTS, clickjacking and sniffing protection via
    Talisman. The CSP names exactly the CDNs the front loads (Bootstrap, Font
    Awesome, Google Fonts) and allows same-origin scripts, styles, media and
    the Socket.IO connection. HTTPS is forced only in production so local dev
    over http still works.

    style-src keeps 'unsafe-inline': the UI uses React inline style props and
    Bootstrap, and inline *styles* are a negligible XSS vector next to scripts,
    which are locked to 'self' and the one CDN. That is where the real
    protection is.
    """
    try:
        from flask_talisman import Talisman
    except Exception:
        return None

    csp = {
        "default-src": "'self'",
        "script-src": ["'self'", "https://cdn.jsdelivr.net"],
        "style-src": ["'self'", "'unsafe-inline'",
                      "https://cdn.jsdelivr.net",
                      "https://cdnjs.cloudflare.com",
                      "https://use.fontawesome.com",
                      "https://fonts.googleapis.com"],
        "font-src": ["'self'", "data:",
                     "https://fonts.gstatic.com",
                     "https://cdnjs.cloudflare.com",
                     "https://use.fontawesome.com"],
        "img-src": ["'self'", "data:", "blob:"],
        "media-src": ["'self'", "blob:"],
        # Same-origin API + the Socket.IO websocket.
        "connect-src": ["'self'", "ws:", "wss:"],
        "frame-src": ["'self'", "blob:"],        # in-app PDF preview iframe
        "frame-ancestors": "'none'",             # nobody may frame us
        "object-src": "'none'",
        "base-uri": "'self'",
    }
    return Talisman(
        app,
        content_security_policy=csp,
        force_https=is_production(),
        strict_transport_security=is_production(),
        strict_transport_security_max_age=31536000,
        session_cookie_secure=is_production(),
        # We set our own CSRF (flask-jwt-extended); Talisman's is redundant.
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
    )


def mfa_enforced():
    """Whether staff must have 2FA. Off by default so a fresh/demo deployment
    is not locked out; set MFA_ENFORCED=true to require it in production."""
    return os.getenv("MFA_ENFORCED", "false").lower() == "true"
