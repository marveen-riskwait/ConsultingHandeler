"""Workflow objects: Case and Task."""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

CASE_STATUSES = ("OPEN", "IN_PROGRESS", "PENDING_APPROVAL", "ESCALATED", "CLOSED")


class Case(db.Model):
    __tablename__ = "case"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="cases")

    case_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    # The team that owns the case. Set by the assignment rules; it is what the
    # customer conversation is addressed to, so the client talks to a team
    # rather than to whoever happened to be assigned that day.
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=True)

    decision: Mapped[str] = mapped_column(String(60), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=True)
    decided_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    tasks: Mapped[list["Task"]] = relationship(back_populates="case")

    def serialize(self, with_tasks=False):
        data = {
            "id": self.id,
            "customer_id": self.customer_id,
            "case_type": self.case_type,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "team_id": self.team_id,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "decided_by": self.decided_by,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }
        if with_tasks:
            data["tasks"] = [t.serialize() for t in self.tasks]
        return data


class Task(db.Model):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case.id"), nullable=True)
    case: Mapped["Case"] = relationship(back_populates="tasks")

    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    # The requirement this task chases, when it chases one. Matching on the
    # title worked until a label contained another code; an explicit link is
    # what lets the task close by itself when the item arrives.
    requirement_code: Mapped[str] = mapped_column(String(60), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "case_id": self.case_id,
            "task_type": self.task_type,
            "title": self.title,
            "status": self.status,
            "requirement_code": self.requirement_code,
            "priority": self.priority,
            "assigned_to": self.assigned_to,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
