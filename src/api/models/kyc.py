"""KYC/KYB profile fields (with provenance) and the requirement model.

ProfileField gives every important piece of customer data a provenance trail:
    value, source, verified, verified_by, confidence, last_changed_at
so the platform can answer "where did this come from and did we verify it?".

RequirementDefinition + RequirementInstance drive the "missing information"
feature: what a customer of a given type/risk/jurisdiction must provide, and
what is still outstanding — computed before the consultant opens the review.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

# KYC field categories (per the document's KYC module).
FIELD_CATEGORIES = (
    "IDENTITY", "PERSONAL", "ADDRESS", "TAX", "NATIONALITY", "RESIDENCY",
    "OCCUPATION", "SOURCE_OF_FUNDS", "SOURCE_OF_WEALTH", "PURPOSE",
    "BUSINESS", "REGISTRATION",
)

REQUIREMENT_KINDS = ("DATA", "DOCUMENT")
REQUIREMENT_STATUSES = ("MISSING", "RECEIVED", "VERIFIED", "WAIVED")

# Risk ordering so a requirement can apply "at HIGH risk and above".
RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class ProfileField(db.Model):
    __tablename__ = "profile_field"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)

    field_key: Mapped[str] = mapped_column(String(60), nullable=False)   # e.g. date_of_birth
    category: Mapped[str] = mapped_column(String(40), nullable=True)
    value: Mapped[str] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(60), default="manual")     # passport / provider / manual
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)

    last_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "field_key": self.field_key,
            "category": self.category,
            "value": self.value,
            "source": self.source,
            "verified": self.verified,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "confidence": self.confidence,
            "last_changed_at": self.last_changed_at.isoformat() if self.last_changed_at else None,
        }


class RequirementDefinition(db.Model):
    """What is required, for whom. Data-driven (seeded, org-overridable)."""
    __tablename__ = "requirement_definition"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)          # DATA / DOCUMENT

    applies_customer_type: Mapped[str] = mapped_column(String(20), default="ANY")  # INDIVIDUAL / COMPANY / ANY
    min_risk_rank: Mapped[int] = mapped_column(Integer, default=0)         # required at this risk and above
    jurisdiction: Mapped[str] = mapped_column(String(80), nullable=True)   # NULL = any

    data_field: Mapped[str] = mapped_column(String(60), nullable=True)     # ProfileField.field_key for DATA
    doc_type: Mapped[str] = mapped_column(String(60), nullable=True)       # Document.doc_type for DOCUMENT
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def serialize(self):
        return {
            "id": self.id, "code": self.code, "label": self.label,
            "kind": self.kind, "applies_customer_type": self.applies_customer_type,
            "min_risk_rank": self.min_risk_rank, "jurisdiction": self.jurisdiction,
            "data_field": self.data_field, "doc_type": self.doc_type,
            "active": self.active,
        }


class RequirementInstance(db.Model):
    """A requirement as it applies to one customer, with its current status."""
    __tablename__ = "requirement_instance"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    definition_id: Mapped[int] = mapped_column(ForeignKey("requirement_definition.id"), nullable=True)

    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="MISSING")

    waived_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    waived_reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def serialize(self):
        return {
            "id": self.id, "customer_id": self.customer_id,
            "definition_id": self.definition_id,
            "code": self.code, "label": self.label, "kind": self.kind,
            "status": self.status, "waived_reason": self.waived_reason,
        }
