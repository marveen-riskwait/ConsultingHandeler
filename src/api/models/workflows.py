"""Configurable workflow engine models.

A WorkflowDefinition is an ordered list of WorkflowSteps (some requiring an
approval by a given role). Running one against a case creates a WorkflowInstance
with a WorkflowStepState per step; the case moves through the steps and can be
gated by Approvals — e.g. the EDD workflow's "Senior approval" step.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

WORKFLOW_INSTANCE_STATUSES = ("IN_PROGRESS", "COMPLETED", "CANCELLED")
STEP_STATES = ("PENDING", "ACTIVE", "DONE", "SKIPPED")
APPROVAL_STATUSES = ("PENDING", "APPROVED", "REJECTED")


class WorkflowDefinition(db.Model):
    __tablename__ = "workflow_definition"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    applies_case_type: Mapped[str] = mapped_column(String(80), nullable=True)  # auto-start match
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    steps: Mapped[list["WorkflowStep"]] = relationship(back_populates="definition")

    def serialize(self, deep=False):
        data = {"id": self.id, "code": self.code, "name": self.name,
                "applies_case_type": self.applies_case_type, "active": self.active,
                "organization_id": self.organization_id}
        if deep:
            data["steps"] = [s.serialize() for s in
                             sorted(self.steps, key=lambda s: s.order)]
        return data


class WorkflowStep(db.Model):
    __tablename__ = "workflow_step"

    id: Mapped[int] = mapped_column(primary_key=True)
    definition_id: Mapped[int] = mapped_column(ForeignKey("workflow_definition.id"), nullable=False)
    definition: Mapped["WorkflowDefinition"] = relationship(back_populates="steps")

    order: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approver_role: Mapped[str] = mapped_column(String(40), nullable=True)

    def serialize(self):
        return {"id": self.id, "order": self.order, "code": self.code,
                "name": self.name, "requires_approval": self.requires_approval,
                "approver_role": self.approver_role}


class WorkflowInstance(db.Model):
    __tablename__ = "workflow_instance"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    definition_id: Mapped[int] = mapped_column(ForeignKey("workflow_definition.id"), nullable=False)
    case_id: Mapped[int] = mapped_column(ForeignKey("case.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="IN_PROGRESS")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    step_states: Mapped[list["WorkflowStepState"]] = relationship(back_populates="instance")

    def serialize(self, deep=False):
        data = {"id": self.id, "definition_id": self.definition_id,
                "case_id": self.case_id, "name": self.name,
                "status": self.status,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None}
        if deep:
            data["steps"] = [s.serialize() for s in
                             sorted(self.step_states, key=lambda s: s.order)]
        return data


class WorkflowStepState(db.Model):
    __tablename__ = "workflow_step_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("workflow_instance.id"), nullable=False)
    instance: Mapped["WorkflowInstance"] = relationship(back_populates="step_states")
    step_id: Mapped[int] = mapped_column(ForeignKey("workflow_step.id"), nullable=True)

    order: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approver_role: Mapped[str] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")

    completed_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=True)

    approvals: Mapped[list["Approval"]] = relationship(back_populates="step_state")

    def serialize(self):
        return {"id": self.id, "order": self.order, "code": self.code,
                "name": self.name, "status": self.status,
                "requires_approval": self.requires_approval,
                "approver_role": self.approver_role,
                "completed_by": self.completed_by,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
                "note": self.note,
                "approvals": [a.serialize() for a in self.approvals]}


class Approval(db.Model):
    __tablename__ = "approval"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("workflow_instance.id"), nullable=False)
    step_state_id: Mapped[int] = mapped_column(ForeignKey("workflow_step_state.id"), nullable=False)
    step_state: Mapped["WorkflowStepState"] = relationship(back_populates="approvals")

    required_role: Mapped[str] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    decided_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "required_role": self.required_role,
                "status": self.status, "decided_by": self.decided_by,
                "decided_at": self.decided_at.isoformat() if self.decided_at else None,
                "reason": self.reason}
