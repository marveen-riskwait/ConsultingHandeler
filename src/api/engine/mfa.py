"""Two-factor authentication.

Staff use TOTP (an authenticator app). Portal customers use a one-time code
emailed at sign-in — a real second factor, but gentler for the general public,
and its recovery is native (they already control the inbox).

The login is two steps: the password endpoint verifies the password and, when
2FA applies, returns a short-lived *pending* ticket instead of a session. The
ticket cannot access anything (current_user rejects mfa_pending tokens); only
presenting the second factor exchanges it for real cookies.
"""
import hashlib
import secrets

import pyotp

from api.models import db, User
from api.engine import audit

ISSUER = "Compliance OS"
_BACKUP_COUNT = 8


def default_method(user):
    """TOTP for staff, an emailed code for portal customers."""
    return "EMAIL_OTP" if user.is_portal_user() else "TOTP"


# --- TOTP enrollment ---------------------------------------------------------
def begin_totp_enrollment(user):
    """Generate a secret and the provisioning URI to show as a QR. Not enabled
    until a first valid code confirms the user scanned it correctly."""
    secret = pyotp.random_base32()
    user.mfa_secret = secret
    user.mfa_method = "TOTP"
    db.session.commit()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return {"secret": secret, "otpauth_uri": uri}


def confirm_totp(user, code):
    """Turn TOTP on once the user proves the app is set up. Returns backup
    codes (shown once) or None if the code was wrong."""
    if not user.mfa_secret or not _totp_ok(user.mfa_secret, code):
        return None
    user.mfa_enabled = True
    user.mfa_method = "TOTP"
    codes = _new_backup_codes(user)
    audit.record("MFA_ENABLED", "user", user.id, actor=user,
                 new_value="TOTP", commit=True)
    return codes


def _totp_ok(secret, code):
    return pyotp.TOTP(secret).verify((code or "").strip(), valid_window=1)


# --- email OTP ---------------------------------------------------------------
def send_email_otp(user):
    """Issue and email a one-time code (reuses the email-token plumbing)."""
    from api.models import email_tokens
    from api.integrations import mailer
    code = email_tokens.issue(user, "LOGIN_OTP")
    org = user.organization.name if user.organization else None
    mailer.send_login_otp(user, org, code)
    return True


def _email_otp_ok(user, code):
    from api.models import email_tokens
    return email_tokens.consume(user.id, "LOGIN_OTP", (code or "").strip())


# --- backup codes ------------------------------------------------------------
def _hash(code):
    return hashlib.sha256((code or "").encode()).hexdigest()


def _new_backup_codes(user):
    codes = [f"{secrets.randbelow(10**8):08d}" for _ in range(_BACKUP_COUNT)]
    user.mfa_backup_codes = [_hash(c) for c in codes]
    db.session.commit()
    return codes   # cleartext, shown once


def _consume_backup(user, code):
    h = _hash((code or "").strip())
    if h in (user.mfa_backup_codes or []):
        user.mfa_backup_codes = [c for c in user.mfa_backup_codes if c != h]
        db.session.commit()
        return True
    return False


# --- verification at login ---------------------------------------------------
def verify(user, code):
    """Check the second factor by the user's method, or a backup code. A
    successful backup code is single-use."""
    if user.mfa_method == "TOTP":
        if user.mfa_secret and _totp_ok(user.mfa_secret, code):
            return True
    elif user.mfa_method == "EMAIL_OTP":
        if _email_otp_ok(user, code):
            return True
    return _consume_backup(user, code)


def disable(user, actor=None):
    user.mfa_enabled = False
    user.mfa_method = None
    user.mfa_secret = None
    user.mfa_backup_codes = []
    audit.record("MFA_DISABLED", "user", user.id, actor=actor or user,
                 commit=True)
