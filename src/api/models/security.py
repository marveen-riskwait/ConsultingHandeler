"""Revoked tokens — the blocklist behind real logout.

A JWT is otherwise valid until it expires, so "log out" on the client only
forgets the token; the token itself still works for hours. This table records
the tokens that must be refused, keyed by their jti (unique id). Rows carry the
token's own expiry so the list can be pruned once entries can no longer be
presented — a blocklist only needs to remember a token until it would expire
anyway.
"""
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow


class RevokedToken(db.Model):
    __tablename__ = "revoked_token"
    __table_args__ = (Index("ix_revoked_token_jti", "jti"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    # When the token would have expired on its own — the row is useless after.
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def revoke(jti, user_id=None, expires_at=None):
    if RevokedToken.query.filter_by(jti=jti).first():
        return
    db.session.add(RevokedToken(jti=jti, user_id=user_id, expires_at=expires_at))


def is_revoked(jti):
    return RevokedToken.query.filter_by(jti=jti).first() is not None


def purge_expired():
    """Drop rows whose token has expired — safe to run periodically."""
    RevokedToken.query.filter(RevokedToken.expires_at.isnot(None),
                              RevokedToken.expires_at < utcnow()).delete()
    db.session.commit()
