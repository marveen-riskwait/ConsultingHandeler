"""Audit — immutable event history: WHO / WHAT / WHEN / OLD / NEW / WHY."""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow


class AuditEvent(db.Model):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(120), default="system")

    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)

    old_value: Mapped[str] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_label": self.actor_label,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
