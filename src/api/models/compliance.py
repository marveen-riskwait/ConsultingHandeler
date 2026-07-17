"""Compliance events (the platform's common language) and data-driven rules."""
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

EVENT_SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


class ComplianceEvent(db.Model):
    __tablename__ = "compliance_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    customer: Mapped["Customer"] = relationship(back_populates="events")

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="system")
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="NEW")

    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "event_type": self.event_type,
            "source": self.source,
            "severity": self.severity,
            "payload": self.payload or {},
            "status": self.status,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }


class ComplianceRule(db.Model):
    __tablename__ = "compliance_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def serialize(self):
        return {
            "id": self.id,
            "name": self.name,
            "event_type": self.event_type,
            "conditions": self.conditions or {},
            "actions": self.actions or [],
            "active": self.active,
        }
