"""Suspicious Activity / Transaction Reports (SAR / STR).

When an investigation concludes that activity is suspicious, the compliance
function files a report with the national Financial Intelligence Unit (FIU).
This models that report and its lifecycle, with a mandatory four-eyes gate
(the person who drafts a report can never be the one who approves it) and a
goAML-shaped XML export — goAML being the UNODC standard most FIUs ingest.
"""
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

REPORT_TYPES = ("SAR", "STR")           # activity vs transaction report
# DRAFT -> PENDING_APPROVAL -> APPROVED -> SUBMITTED, or REJECTED back to DRAFT.
SAR_STATUSES = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "SUBMITTED", "REJECTED")


class SuspiciousActivityReport(db.Model):
    __tablename__ = "suspicious_activity_report"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)
    case_id: Mapped[int] = mapped_column(ForeignKey("case.id"), nullable=True)

    reference: Mapped[str] = mapped_column(String(40), nullable=False)   # SAR-2026-0001
    report_type: Mapped[str] = mapped_column(String(10), nullable=False, default="STR")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")

    # The grounds for suspicion — the analyst's narrative, filed to the FIU.
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    indicators: Mapped[list] = mapped_column(JSON, default=list)         # typology codes
    transaction_ids: Mapped[list] = mapped_column(JSON, default=list)    # linked flagged txns

    # Four-eyes: drafter and approver are recorded and MUST differ.
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    approved_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "case_id": self.case_id,
            "reference": self.reference,
            "report_type": self.report_type,
            "status": self.status,
            "reason": self.reason,
            "indicators": self.indicators or [],
            "transaction_ids": self.transaction_ids or [],
            "created_by": self.created_by,
            "approved_by": self.approved_by,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
        }
