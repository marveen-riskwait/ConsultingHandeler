"""Parties (people & organizations) and the ownership graph.

A single `Party` table is used as the graph node so ownership edges can point at
persons and companies uniformly. A Customer links to its *root* Party (the
onboarded entity); ownership is then discovered by walking the edges.

This is what lets the platform answer:
    "Who ULTIMATELY owns or controls this customer?"  (UBO)
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

PARTY_KINDS = ("PERSON", "ORGANIZATION")
# How one party relates to the entity it points at.
RELATIONSHIP_TYPES = ("SHAREHOLDER", "DIRECTOR", "UBO", "CONTROL", "AUTHORIZED_REP")
# For control that is not (only) about share percentage (per the document:
# voting / management / contractual control on top of direct/indirect ownership).
CONTROL_TYPES = ("VOTING_CONTROL", "MANAGEMENT_CONTROL", "CONTRACTUAL_CONTROL", "OTHER")

ADDRESS_TYPES = ("RESIDENTIAL", "REGISTERED", "BUSINESS", "MAILING")

# A person is a Ultimate Beneficial Owner at/above this effective ownership.
UBO_THRESHOLD = 25.0


class Party(db.Model):
    __tablename__ = "party"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)

    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="PERSON")
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Person attributes
    first_name: Mapped[str] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str] = mapped_column(String(120), nullable=True)
    date_of_birth: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    nationality: Mapped[str] = mapped_column(String(80), nullable=True)
    country_of_residence: Mapped[str] = mapped_column(String(80), nullable=True)

    # Organization attributes
    registration_number: Mapped[str] = mapped_column(String(80), nullable=True)
    country_of_incorporation: Mapped[str] = mapped_column(String(80), nullable=True)
    legal_form: Mapped[str] = mapped_column(String(80), nullable=True)
    business_activity: Mapped[str] = mapped_column(String(200), nullable=True)

    # Classification (source of truth for PEP is a ScreeningMatch; this is the
    # manually-asserted / known status).
    is_pep: Mapped[bool] = mapped_column(Boolean, default=False)
    pep_type: Mapped[str] = mapped_column(String(60), nullable=True)  # CURRENT / FORMER / FAMILY / ASSOCIATE

    # If this party is itself an onboarded customer, link it.
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Single-table polymorphism: `kind` discriminates Person / LegalEntity.
    # No schema change — existing rows load as the right subclass.
    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "PARTY"}

    def serialize(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "nationality": self.nationality,
            "country_of_residence": self.country_of_residence,
            "registration_number": self.registration_number,
            "country_of_incorporation": self.country_of_incorporation,
            "legal_form": self.legal_form,
            "business_activity": self.business_activity,
            "is_pep": self.is_pep,
            "pep_type": self.pep_type,
            "customer_id": self.customer_id,
        }


class Person(Party):
    """A natural person — KYC subject, director, shareholder or UBO."""
    __mapper_args__ = {"polymorphic_identity": "PERSON"}


class LegalEntity(Party):
    """A legal entity (company, trust, fund, ...) — KYB subject or owner."""
    __mapper_args__ = {"polymorphic_identity": "ORGANIZATION"}


class Address(db.Model):
    """Party address with full history — the platform must always answer
    "what was the address before, when did it change, who changed it?"."""
    __tablename__ = "address"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    party_id: Mapped[int] = mapped_column(ForeignKey("party.id"), nullable=False)

    address_type: Mapped[str] = mapped_column(String(20), default="RESIDENTIAL")
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[str] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(80), nullable=True)

    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    valid_to: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "party_id": self.party_id,
            "address_type": self.address_type,
            "line1": self.line1,
            "line2": self.line2,
            "city": self.city,
            "postal_code": self.postal_code,
            "country": self.country,
            "is_current": self.is_current,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


class OwnershipRelationship(db.Model):
    """A directed edge: `owner` owns/controls `owned` (by `percentage`)."""
    __tablename__ = "ownership_relationship"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)

    owner_party_id: Mapped[int] = mapped_column(ForeignKey("party.id"), nullable=False)
    owned_party_id: Mapped[int] = mapped_column(ForeignKey("party.id"), nullable=False)

    relationship_type: Mapped[str] = mapped_column(String(30), default="SHAREHOLDER")
    percentage: Mapped[float] = mapped_column(Float, default=0.0)
    control_type: Mapped[str] = mapped_column(String(30), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner: Mapped["Party"] = relationship(foreign_keys=[owner_party_id])
    owned: Mapped["Party"] = relationship(foreign_keys=[owned_party_id])

    def serialize(self):
        return {
            "id": self.id,
            "owner_party_id": self.owner_party_id,
            "owned_party_id": self.owned_party_id,
            "relationship_type": self.relationship_type,
            "percentage": self.percentage,
            "control_type": self.control_type,
            "active": self.active,
        }
