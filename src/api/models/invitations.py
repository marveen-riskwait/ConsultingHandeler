"""User invitations — how someone becomes a member of an organization.

Flow (per the instruction document):
    Admin/Manager -> Create Invitation -> token -> User accepts ->
    Account Creation -> Organization Membership -> Role -> Team -> Workspace
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

INVITATION_STATUSES = ("PENDING", "ACCEPTED", "EXPIRED", "REVOKED")


def new_invitation_token():
    return secrets.token_urlsafe(32)


class Invitation(db.Model):
    __tablename__ = "invitation"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(120), nullable=False)
    proposed_role: Mapped[str] = mapped_column(String(40), nullable=False, default="KYC_ANALYST")
    proposed_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False,
                                       default=new_invitation_token)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: utcnow() + timedelta(days=7))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def is_valid(self):
        return self.status == "PENDING" and (self.expires_at is None or
                                             self.expires_at > utcnow())

    def serialize(self, with_token=False):
        data = {
            "id": self.id,
            "organization_id": self.organization_id,
            "email": self.email,
            "proposed_role": self.proposed_role,
            "proposed_team_id": self.proposed_team_id,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
        }
        if with_token:
            # Only returned to the admin at creation time (no email channel yet).
            data["token"] = self.token
        return data
