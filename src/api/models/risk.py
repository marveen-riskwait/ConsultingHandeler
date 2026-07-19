"""Data-driven, versioned risk methodology.

The v1 risk engine hardcoded its factors in Python. Here the methodology lives in
the database — factors, their impacts and the level thresholds are configurable
and versioned, so a compliance admin can change the model without a deploy and
old assessments remain interpretable under the methodology that produced them.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

# How a factor decides whether it applies to a customer.
FACTOR_CONDITIONS = ("FLAG", "COUNTRY_IN", "ACTIVITY_IN")


class RiskMethodology(db.Model):
    __tablename__ = "risk_methodology"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)   # e.g. v1, v2.1
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    factors: Mapped[list["RiskFactor"]] = relationship(back_populates="methodology")
    thresholds: Mapped[list["RiskThreshold"]] = relationship(back_populates="methodology")

    def serialize(self, deep=False):
        data = {"id": self.id, "version": self.version, "name": self.name,
                "active": self.active,
                "organization_id": self.organization_id}
        if deep:
            data["factors"] = [f.serialize() for f in self.factors]
            data["thresholds"] = sorted((t.serialize() for t in self.thresholds),
                                        key=lambda t: t["min_score"])
        return data


class RiskFactor(db.Model):
    __tablename__ = "risk_factor"

    id: Mapped[int] = mapped_column(primary_key=True)
    methodology_id: Mapped[int] = mapped_column(ForeignKey("risk_methodology.id"), nullable=False)
    methodology: Mapped["RiskMethodology"] = relationship(back_populates="factors")

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)

    condition_type: Mapped[str] = mapped_column(String(20), nullable=False)   # FLAG / COUNTRY_IN / ACTIVITY_IN
    condition_value: Mapped[dict] = mapped_column(JSON, default=dict)          # {"field": "is_pep"} or {"values": [...]}
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def serialize(self):
        return {"id": self.id, "code": self.code, "label": self.label,
                "impact": self.impact, "condition_type": self.condition_type,
                "condition_value": self.condition_value or {}, "active": self.active}


class RiskThreshold(db.Model):
    __tablename__ = "risk_threshold"

    id: Mapped[int] = mapped_column(primary_key=True)
    methodology_id: Mapped[int] = mapped_column(ForeignKey("risk_methodology.id"), nullable=False)
    methodology: Mapped["RiskMethodology"] = relationship(back_populates="thresholds")

    level: Mapped[str] = mapped_column(String(20), nullable=False)   # LOW / MEDIUM / HIGH / CRITICAL
    min_score: Mapped[int] = mapped_column(Integer, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, nullable=True)   # NULL = open-ended top band

    def serialize(self):
        return {"id": self.id, "level": self.level,
                "min_score": self.min_score, "max_score": self.max_score}
