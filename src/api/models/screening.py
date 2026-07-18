"""Screening runs and matches — first-class records, not boolean flags.

The v1 slice stored `customer.is_pep` / `has_sanctions_match`. That loses
history: clearing a false positive erased the fact it ever happened. Here every
screening produces a ScreeningRun, and every hit is a ScreeningMatch that keeps
its own lifecycle (POTENTIAL -> UNDER_REVIEW -> FALSE_POSITIVE / CONFIRMED /
ESCALATED) and the reviewer's decision. The customer flags become a derived
cache of "any non-false-positive match of this type".
"""
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

MATCH_TYPES = ("SANCTIONS", "PEP", "ADVERSE_MEDIA")
MATCH_STATUSES = ("POTENTIAL", "UNDER_REVIEW", "FALSE_POSITIVE", "CONFIRMED", "ESCALATED")
# Statuses that still count as a live compliance signal for risk.
ACTIVE_MATCH_STATUSES = ("POTENTIAL", "UNDER_REVIEW", "CONFIRMED", "ESCALATED")


class ScreeningRun(db.Model):
    __tablename__ = "screening_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(200), nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    requested_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    matches: Mapped[list["ScreeningMatch"]] = relationship(back_populates="run")

    def serialize(self, with_matches=False):
        data = {
            "id": self.id,
            "customer_id": self.customer_id,
            "subject_name": self.subject_name,
            "sources": self.sources or [],
            "status": self.status,
            "requested_by": self.requested_by,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
        if with_matches:
            data["matches"] = [m.serialize() for m in self.matches]
        return data


class ScreeningMatch(db.Model):
    __tablename__ = "screening_match"

    id: Mapped[int] = mapped_column(primary_key=True)
    screening_run_id: Mapped[int] = mapped_column(ForeignKey("screening_run.id"), nullable=True)
    run: Mapped["ScreeningRun"] = relationship(back_populates="matches")

    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    party_id: Mapped[int] = mapped_column(ForeignKey("party.id"), nullable=True)

    match_type: Mapped[str] = mapped_column(String(20), nullable=False)   # SANCTIONS / PEP / ADVERSE_MEDIA
    source: Mapped[str] = mapped_column(String(120), nullable=True)       # list / provider name
    matched_name: Mapped[str] = mapped_column(String(200), nullable=True)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_data: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="POTENTIAL")

    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reviewed_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=True)

    case_id: Mapped[int] = mapped_column(ForeignKey("case.id"), nullable=True)

    def serialize(self):
        return {
            "id": self.id,
            "screening_run_id": self.screening_run_id,
            "customer_id": self.customer_id,
            "party_id": self.party_id,
            "match_type": self.match_type,
            "source": self.source,
            "matched_name": self.matched_name,
            "match_score": self.match_score,
            "match_data": self.match_data or {},
            "status": self.status,
            "first_detected_at": self.first_detected_at.isoformat() if self.first_detected_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "decision_reason": self.decision_reason,
            "case_id": self.case_id,
        }
