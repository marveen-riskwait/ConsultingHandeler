"""Notifications — `requires_action` is the field that matters."""
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow


class Notification(db.Model):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("compliance_event.id"), nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)

    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    requires_action: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event_id": self.event_id,
            "customer_id": self.customer_id,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "requires_action": self.requires_action,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
