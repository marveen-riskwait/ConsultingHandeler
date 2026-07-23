"""Single-use, expiring tokens sent by email.

One table for three purposes that share the same shape — a secret the holder
proves by presenting it, once, before it expires:

    VERIFY_EMAIL   a link that confirms the address is real
    RESET_PASSWORD a link that authorises setting a new password
    LOGIN_OTP      a 6-digit code that is the customer's second factor

The secret is stored HASHED, never in the clear: a database leak must not hand
an attacker a live reset link or a working OTP. `used_at` makes it single-use;
`expires_at` bounds the window.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

EMAIL_TOKEN_PURPOSES = ("VERIFY_EMAIL", "RESET_PASSWORD", "LOGIN_OTP")

# Link tokens are long and random; OTPs are short and numeric.
_LINK_TTL = timedelta(hours=24)
_RESET_TTL = timedelta(hours=1)
_OTP_TTL = timedelta(minutes=10)
_TTL = {"VERIFY_EMAIL": _LINK_TTL, "RESET_PASSWORD": _RESET_TTL,
        "LOGIN_OTP": _OTP_TTL}


def _hash(secret):
    return hashlib.sha256((secret or "").encode()).hexdigest()


class EmailToken(db.Model):
    __tablename__ = "email_token"
    __table_args__ = (Index("ix_email_token_lookup", "purpose", "token_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def issue(user, purpose):
    """Create a token, returning the CLEAR secret (to email) — the DB keeps
    only its hash. Any earlier live token of the same purpose is invalidated,
    so only the most recent link/code works."""
    for old in EmailToken.query.filter_by(user_id=user.id, purpose=purpose,
                                           used_at=None).all():
        old.used_at = utcnow()
    if purpose == "LOGIN_OTP":
        secret = f"{secrets.randbelow(1_000_000):06d}"   # 6 digits
    else:
        secret = secrets.token_urlsafe(32)
    db.session.add(EmailToken(user_id=user.id, purpose=purpose,
                              token_hash=_hash(secret),
                              expires_at=utcnow() + _TTL[purpose]))
    db.session.commit()
    return secret


def consume(user_id, purpose, secret):
    """Validate and burn a token. Returns True on success (and marks it used),
    False if it is unknown, wrong, already used or expired."""
    row = (EmailToken.query
           .filter_by(user_id=user_id, purpose=purpose,
                      token_hash=_hash(secret), used_at=None)
           .first())
    if row is None or row.expires_at < utcnow():
        return False
    row.used_at = utcnow()
    db.session.commit()
    return True


def consume_by_secret(purpose, secret):
    """Same, when the caller has only the secret (link flows): find the row by
    its hash, return the user_id it belonged to, or None."""
    row = (EmailToken.query
           .filter_by(purpose=purpose, token_hash=_hash(secret), used_at=None)
           .first())
    if row is None or row.expires_at < utcnow():
        return None
    row.used_at = utcnow()
    db.session.commit()
    return row.user_id
