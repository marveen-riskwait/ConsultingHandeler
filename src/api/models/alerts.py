"""ComplianceAlert — a first-class compliance object, NOT a notification.

Per the document: a Notification is an information-delivery mechanism; a
ComplianceAlert is something that must be triaged, assigned, investigated and
resolved. High/critical compliance events raise an alert automatically.
"""
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

ALERT_STATUSES = ("OPEN", "ASSIGNED", "IN_REVIEW", "RESOLVED", "DISMISSED")


class ComplianceAlert(db.Model):
    __tablename__ = "compliance_alert"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("compliance_event.id"), nullable=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case.id"), nullable=True)

    alert_type: Mapped[str] = mapped_column(String(80), nullable=False)   # = source event type
    source: Mapped[str] = mapped_column(String(80), default="system")
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")

    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    resolution: Mapped[str] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "event_id": self.event_id,
            "case_id": self.case_id,
            "alert_type": self.alert_type,
            "source": self.source,
            "severity": self.severity,
            "title": self.title,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
