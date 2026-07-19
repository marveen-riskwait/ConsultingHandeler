"""Regulatory Intelligence — the "which rule applies, to whom, and which software
feature satisfies it" layer.

The document's 5-level chain:
    Authority -> RegulatorySource -> RegulatoryRequirement -> ComplianceControl
              -> Software feature (+ implementation status)

A RegulatoryChange (a new publication / changed requirement) triggers an
ImpactAssessment: which requirements, controls, workflows and customers it
touches — so a regulatory change becomes actionable, not just a PDF.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

SOURCE_TYPES = ("RECOMMENDATION", "REGULATION", "DIRECTIVE", "GUIDELINE",
                "LAW", "CIRCULAR", "RTS", "ITS")
CONTROL_STATUSES = ("IMPLEMENTED", "PARTIAL", "MISSING", "NEEDS_REVIEW")
CHANGE_IMPACT = ("LOW", "MEDIUM", "HIGH")
CHANGE_STATUSES = ("NEW", "UNDER_REVIEW", "ASSESSED", "ACTIONED")


class RegulatorySource(db.Model):
    __tablename__ = "regulatory_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    authority: Mapped[str] = mapped_column(String(80), nullable=False)   # FATF / EU / AMLA / CSSF
    jurisdiction: Mapped[str] = mapped_column(String(80), nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=True)
    official_url: Mapped[str] = mapped_column(String(400), nullable=True)
    effective_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    requirements: Mapped[list["RegulatoryRequirement"]] = relationship(back_populates="source")

    def serialize(self, deep=False):
        data = {"id": self.id, "name": self.name, "authority": self.authority,
                "jurisdiction": self.jurisdiction, "source_type": self.source_type,
                "official_url": self.official_url, "active": self.active}
        if deep:
            data["requirements"] = [r.serialize() for r in self.requirements]
        return data


class RegulatoryRequirement(db.Model):
    __tablename__ = "regulatory_requirement"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("regulatory_source.id"), nullable=False)
    source: Mapped["RegulatorySource"] = relationship(back_populates="requirements")
    article_reference: Mapped[str] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    obligation_type: Mapped[str] = mapped_column(String(80), nullable=True)

    controls: Mapped[list["ComplianceControl"]] = relationship(back_populates="requirement")

    def serialize(self, with_controls=False):
        data = {"id": self.id, "source_id": self.source_id,
                "article_reference": self.article_reference, "title": self.title,
                "description": self.description, "obligation_type": self.obligation_type}
        if with_controls:
            data["controls"] = [c.serialize() for c in self.controls]
        return data


class ComplianceControl(db.Model):
    """A software feature/control that satisfies a regulatory requirement."""
    __tablename__ = "compliance_control"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("regulatory_requirement.id"), nullable=True)
    requirement: Mapped["RegulatoryRequirement"] = relationship(back_populates="controls")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    control_type: Mapped[str] = mapped_column(String(80), nullable=True)
    software_module: Mapped[str] = mapped_column(String(120), nullable=True)
    implementation_status: Mapped[str] = mapped_column(String(20), default="IMPLEMENTED")

    def serialize(self):
        return {"id": self.id, "requirement_id": self.requirement_id,
                "name": self.name, "control_type": self.control_type,
                "software_module": self.software_module,
                "implementation_status": self.implementation_status}


class RegulatoryChange(db.Model):
    __tablename__ = "regulatory_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("regulatory_source.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    impact_level: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), default="NEW")
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    effective_from: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    assessment: Mapped["ImpactAssessment"] = relationship(back_populates="change", uselist=False)

    def serialize(self, with_assessment=False):
        data = {"id": self.id, "source_id": self.source_id, "title": self.title,
                "summary": self.summary, "impact_level": self.impact_level,
                "status": self.status,
                "detected_at": self.detected_at.isoformat() if self.detected_at else None,
                "effective_from": self.effective_from.isoformat() if self.effective_from else None}
        if with_assessment:
            data["assessment"] = self.assessment.serialize() if self.assessment else None
        return data


class ImpactAssessment(db.Model):
    __tablename__ = "impact_assessment"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("regulatory_change.id"), nullable=False)
    change: Mapped["RegulatoryChange"] = relationship(back_populates="assessment")

    affected_requirement_ids: Mapped[list] = mapped_column(JSON, default=list)
    affected_control_ids: Mapped[list] = mapped_column(JSON, default=list)
    affected_workflow_codes: Mapped[list] = mapped_column(JSON, default=list)
    affected_customer_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    assessed_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "change_id": self.change_id,
                "affected_requirement_ids": self.affected_requirement_ids or [],
                "affected_control_ids": self.affected_control_ids or [],
                "affected_workflow_codes": self.affected_workflow_codes or [],
                "affected_customer_count": self.affected_customer_count,
                "notes": self.notes}
