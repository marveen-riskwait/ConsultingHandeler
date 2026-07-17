"""
Domain model for the Compliance OS vertical slice.

The whole platform is organised around a single spine:

    DATA  ->  EVENT  ->  RULE  ->  RISK  ->  WORKFLOW  ->  HUMAN DECISION  ->  AUDIT

Every model below plays one role in that spine. Keeping them in a single module
(for now) makes the flow easy to read; domains can be split into a package later
without touching the rest of the app.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

db = SQLAlchemy()


def utcnow():
    # Naive UTC: the DateTime columns are timezone-naive, and drivers (notably
    # sqlite) drop tzinfo on round-trip. Keeping everything naive-UTC avoids
    # "can't compare offset-naive and offset-aware datetimes" at read time.
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Tenancy & identity
# ---------------------------------------------------------------------------
class Organization(db.Model):
    __tablename__ = "organization"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    customers: Mapped[list["Customer"]] = relationship(back_populates="organization")

    def serialize(self):
        return {"id": self.id, "name": self.name}


# Roles drive both permissions (what you can do) and the workspace the frontend
# shows (what you see first). Kept as plain strings for the slice.
ROLES = ("ANALYST", "COMPLIANCE_OFFICER", "MANAGER", "AUDITOR", "ADMIN")


class User(db.Model):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="ANALYST")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)

    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="users")

    def serialize(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "organization_id": self.organization_id,
        }


# ---------------------------------------------------------------------------
# Customer (party) — individuals and companies share one table for the slice
# ---------------------------------------------------------------------------
CUSTOMER_TYPES = ("INDIVIDUAL", "COMPANY")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class Customer(db.Model):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="customers")

    customer_type: Mapped[str] = mapped_column(String(20), nullable=False, default="INDIVIDUAL")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=True)          # ISO-ish name
    business_activity: Mapped[str] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ONBOARDING")

    # Denormalised current risk (source of truth is the latest RiskAssessment).
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")

    # Compliance signals that feed the risk engine.
    is_pep: Mapped[bool] = mapped_column(Boolean, default=False)
    has_sanctions_match: Mapped[bool] = mapped_column(Boolean, default=False)
    has_adverse_media: Mapped[bool] = mapped_column(Boolean, default=False)
    complex_ownership: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_review_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    documents: Mapped[list["Document"]] = relationship(back_populates="customer")
    events: Mapped[list["ComplianceEvent"]] = relationship(back_populates="customer")
    cases: Mapped[list["Case"]] = relationship(back_populates="customer")
    assessments: Mapped[list["RiskAssessment"]] = relationship(back_populates="customer")

    def serialize(self):
        return {
            "id": self.id,
            "customer_type": self.customer_type,
            "name": self.name,
            "country": self.country,
            "business_activity": self.business_activity,
            "status": self.status,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "is_pep": self.is_pep,
            "has_sanctions_match": self.has_sanctions_match,
            "has_adverse_media": self.has_adverse_media,
            "complex_ownership": self.complex_ownership,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_review_at": self.last_review_at.isoformat() if self.last_review_at else None,
        }


HIGH_RISK_COUNTRIES = {"Iran", "North Korea", "Syria", "Myanmar", "Russia", "Panama"}
HIGH_RISK_ACTIVITIES = {"crypto exchange", "casino", "money service business", "arms trade"}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class Document(db.Model):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="documents")

    doc_type: Mapped[str] = mapped_column(String(60), nullable=False)   # PASSPORT, PROOF_OF_ADDRESS...
    status: Mapped[str] = mapped_column(String(30), default="PENDING")  # PENDING, VERIFIED, EXPIRED
    expiry_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "doc_type": self.doc_type,
            "status": self.status,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
        }


# ---------------------------------------------------------------------------
# Risk — versioned & explainable
# ---------------------------------------------------------------------------
class RiskAssessment(db.Model):
    __tablename__ = "risk_assessment"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="assessments")

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(20), default="v1")
    # factors: list of {code, label, impact}
    factors: Mapped[list] = mapped_column(JSON, default=list)
    required_actions: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "score": self.score,
            "level": self.level,
            "methodology_version": self.methodology_version,
            "factors": self.factors or [],
            "required_actions": self.required_actions or [],
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Compliance Event — the common language of the whole platform
# ---------------------------------------------------------------------------
EVENT_SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


class ComplianceEvent(db.Model):
    __tablename__ = "compliance_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    customer: Mapped["Customer"] = relationship(back_populates="events")

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)   # PEP_DETECTED...
    source: Mapped[str] = mapped_column(String(80), default="system")
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="NEW")        # NEW, PROCESSED, ERROR

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


# ---------------------------------------------------------------------------
# Rules — data-driven so a compliance admin can change behaviour without code
# ---------------------------------------------------------------------------
class ComplianceRule(db.Model):
    __tablename__ = "compliance_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # conditions: dict evaluated against the event payload / customer
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    # actions: list of {type, ...} — CREATE_CASE, CREATE_TASK, NOTIFY, RISK_IMPACT
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


# ---------------------------------------------------------------------------
# Workflow — Cases & Tasks
# ---------------------------------------------------------------------------
CASE_STATUSES = ("OPEN", "IN_PROGRESS", "PENDING_APPROVAL", "ESCALATED", "CLOSED")


class Case(db.Model):
    __tablename__ = "case"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="cases")

    case_type: Mapped[str] = mapped_column(String(80), nullable=False)   # SANCTIONS_MATCH, EDD...
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)

    decision: Mapped[str] = mapped_column(String(60), nullable=True)     # FALSE_POSITIVE...
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

    task_type: Mapped[str] = mapped_column(String(80), nullable=False)   # EDD_REVIEW...
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN")      # OPEN, DONE
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
            "priority": self.priority,
            "assigned_to": self.assigned_to,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Notifications — "requires_action" is the field that matters
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Audit — immutable event history: WHO / WHAT / WHEN / OLD / NEW / WHY
# ---------------------------------------------------------------------------
class AuditEvent(db.Model):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)  # null => system
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
