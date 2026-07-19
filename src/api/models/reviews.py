"""Review — the recurring/triggered re-examination of a customer.

A review can be scheduled (periodic, frequency by risk) OR triggered by an event
(new PEP, sanctions, UBO change, document expiry). This is the document's
"event-driven periodic review".
"""
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

REVIEW_TYPES = ("INITIAL_KYC", "PERIODIC_REVIEW", "EVENT_DRIVEN_REVIEW",
                "EDD_REVIEW", "REMEDIATION_REVIEW")
REVIEW_STATUSES = ("SCHEDULED", "DUE", "IN_PROGRESS", "COMPLETED", "OVERDUE")

# Review frequency (months) by risk level.
REVIEW_FREQUENCY_MONTHS = {"LOW": 36, "MEDIUM": 24, "HIGH": 12, "CRITICAL": 6}


class Review(db.Model):
    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)

    review_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    trigger: Mapped[str] = mapped_column(String(120), nullable=True)   # why this review exists
    methodology_version: Mapped[str] = mapped_column(String(20), nullable=True)

    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    decision: Mapped[str] = mapped_column(String(60), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "review_type": self.review_type,
            "status": self.status,
            "trigger": self.trigger,
            "methodology_version": self.methodology_version,
            "assigned_to": self.assigned_to,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
