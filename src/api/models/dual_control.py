"""Dual control (maker-checker) — a generalised four-eyes gate.

Some acts are too consequential for one person: deleting a customer, forcing a
risk level, overriding a retention guard. When dual control is enabled for an
action type, the maker's request is held PENDING instead of executing; a
different person (the checker) approves it, and only then does the act run.

The SAR flow has its own hard-wired four-eyes; this is the configurable,
reusable version an organisation can switch on per action type.
"""
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

DC_STATUSES = ("PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED")

# Action types that can be placed under dual control. The executor registry in
# engine/dual_control.py must have a handler for each.
DC_ACTIONS = ("CUSTOMER_DELETE",)


class DualControlPolicy(db.Model):
    """Whether an action type requires a second approval, per organisation.
    Absent row = disabled (opt-in), so switching it on is a deliberate act."""
    __tablename__ = "dual_control_policy"
    __table_args__ = (UniqueConstraint("organization_id", "action_type",
                                       name="uq_dc_policy_org_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    def serialize(self):
        return {"action_type": self.action_type, "enabled": self.enabled}


class DualControlRequest(db.Model):
    __tablename__ = "dual_control_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # What the act targets, and the parameters needed to run it later.
    target_type: Mapped[str] = mapped_column(String(40), nullable=True)
    target_id: Mapped[int] = mapped_column(nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    requested_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=True)
    result_note: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "action_type": self.action_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason,
            "summary": self.summary,
            "status": self.status,
            "requested_by": self.requested_by,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "rejection_reason": self.rejection_reason,
            "result_note": self.result_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
