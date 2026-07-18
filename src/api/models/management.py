"""Management operations models: assignment rules and SLA configuration.

AssignmentRule decides WHO receives a new case (per the instruction document):
    event/case type + risk level -> team / role -> strategy
Strategies: ROUND_ROBIN, LEAST_LOADED, SKILL_BASED, RISK_BASED, MANUAL.

SLAConfiguration decides WHEN work must be done (target hours per priority);
the SLA engine derives on-time / at-risk / breached from it.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

ASSIGNMENT_STRATEGIES = ("ROUND_ROBIN", "LEAST_LOADED", "SKILL_BASED",
                         "RISK_BASED", "MANUAL")


class AssignmentRule(db.Model):
    __tablename__ = "assignment_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Matching criteria (NULL = matches anything).
    case_type: Mapped[str] = mapped_column(String(80), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=True)

    # Candidate pool.
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=True)
    required_role: Mapped[str] = mapped_column(String(40), nullable=True)
    required_skill: Mapped[str] = mapped_column(String(80), nullable=True)  # future use

    strategy: Mapped[str] = mapped_column(String(20), nullable=False, default="LEAST_LOADED")
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower = evaluated first
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Round-robin cursor.
    last_assigned_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id, "name": self.name,
            "case_type": self.case_type, "risk_level": self.risk_level,
            "team_id": self.team_id, "required_role": self.required_role,
            "required_skill": self.required_skill,
            "strategy": self.strategy, "priority": self.priority,
            "active": self.active,
        }


class SLAConfiguration(db.Model):
    __tablename__ = "sla_configuration"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    case_priority: Mapped[str] = mapped_column(String(20), nullable=False)  # CRITICAL...
    target_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def serialize(self):
        return {"id": self.id, "case_priority": self.case_priority,
                "target_hours": self.target_hours, "active": self.active}
