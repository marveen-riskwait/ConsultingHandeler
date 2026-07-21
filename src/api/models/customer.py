"""Customer (party), Documents and versioned RiskAssessment.

NOTE: this still carries the v1 screening flags (is_pep, has_sanctions_match,
has_adverse_media). Increment 2 will move those to first-class ScreeningMatch
records; they stay here for now so the existing risk engine keeps working.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

CUSTOMER_TYPES = ("INDIVIDUAL", "COMPANY")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

HIGH_RISK_COUNTRIES = {"Iran", "North Korea", "Syria", "Myanmar", "Russia", "Panama"}
HIGH_RISK_ACTIVITIES = {"crypto exchange", "casino", "money service business", "arms trade"}


class Customer(db.Model):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="customers")

    customer_type: Mapped[str] = mapped_column(String(20), nullable=False, default="INDIVIDUAL")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=True)
    business_activity: Mapped[str] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ONBOARDING")

    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")

    # These three are now a DERIVED CACHE of ScreeningMatch (see engine.screening_service).
    # They stay so the risk engine keeps reading simple booleans.
    is_pep: Mapped[bool] = mapped_column(Boolean, default=False)
    has_sanctions_match: Mapped[bool] = mapped_column(Boolean, default=False)
    has_adverse_media: Mapped[bool] = mapped_column(Boolean, default=False)
    complex_ownership: Mapped[bool] = mapped_column(Boolean, default=False)

    # Root node of this customer's ownership graph (a Party of kind ORGANIZATION
    # for companies, PERSON for individuals). Plain FK, no relationship, to keep
    # the two Customer<->Party foreign keys unambiguous.
    # use_alter breaks the customer<->party DDL cycle (Party.customer_id points
    # back here): the constraint is added AFTER both tables exist, which is
    # required on PostgreSQL and for fresh-schema generation. The explicit name
    # matches migration 1bd1523c74a8.
    root_party_id: Mapped[int] = mapped_column(
        ForeignKey("party.id", use_alter=True, name="fk_customer_root_party"),
        nullable=True)

    # The staff member this customer deals with ("reference"): the person a
    # portal user is allowed to message. Falls back to case/task assignees.
    # use_alter breaks the customer<->user DDL cycle (User.customer_id points
    # back here) — same treatment as root_party_id; the explicit name matches
    # migration 212f1a147cb7.
    relationship_manager_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", use_alter=True,
                   name="fk_customer_relationship_manager_id_user"),
        nullable=True)

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
            "root_party_id": self.root_party_id,
            "relationship_manager_id": self.relationship_manager_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_review_at": self.last_review_at.isoformat() if self.last_review_at else None,
        }


class Document(db.Model):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="documents")

    doc_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
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


class RiskAssessment(db.Model):
    __tablename__ = "risk_assessment"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    customer: Mapped["Customer"] = relationship(back_populates="assessments")

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(20), default="v1")
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
