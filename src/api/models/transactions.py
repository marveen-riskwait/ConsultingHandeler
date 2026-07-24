"""Transactions and their monitoring.

KYC/KYB answers *who* the customer is; transaction monitoring answers *what
they do*. A Transaction is booked activity on the relationship; the monitoring
engine runs detectors over it (large amount, structuring, high-risk
counterparty, velocity, rapid pass-through) and, when one fires, raises a
TRANSACTION_ALERT event onto the same spine that already turns events into
cases and alerts — so nothing about routing, dedup or close-out is reinvented.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import db, utcnow

DIRECTIONS = ("INBOUND", "OUTBOUND")
# Payment rails — mirror the KYC form's expected-methods vocabulary.
METHODS = ("SEPA", "SWIFT", "CARD", "DIRECT_DEBIT", "CASH", "CHEQUE",
           "CRYPTO", "EMONEY", "INTERNAL", "OTHER")

# Detector codes a transaction can be flagged with.
DETECTORS = ("LARGE_AMOUNT", "HIGH_RISK_COUNTRY", "STRUCTURING",
             "VELOCITY", "RAPID_PASSTHROUGH", "CASH_INTENSIVE")


class Transaction(db.Model):
    """One booked movement on a customer relationship.

    Amounts are held in their original currency plus a best-effort
    `amount_base` in the reporting currency (EUR) that the detectors compare
    against — real FX normalisation is a later refinement, flagged like the
    other dev-time simplifications (Celery inline, in-process call state)."""
    __tablename__ = "transaction"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False)

    # Idempotency: the same source reference is ingested once (per org).
    external_id: Mapped[str] = mapped_column(String(120), nullable=True)

    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="INBOUND")
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    amount_base: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    method: Mapped[str] = mapped_column(String(20), nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=True)
    counterparty_country: Mapped[str] = mapped_column(String(80), nullable=True)
    reference: Mapped[str] = mapped_column(String(255), nullable=True)

    booked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    # Monitoring outcome: which detectors fired (codes), and whether the
    # transaction is flagged at all. The evidence for each lives on the
    # TRANSACTION_ALERT event, not here.
    flags: Mapped[list] = mapped_column(JSON, default=list)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "external_id": self.external_id,
            "direction": self.direction,
            "amount": self.amount,
            "currency": self.currency,
            "amount_base": self.amount_base,
            "method": self.method,
            "counterparty_name": self.counterparty_name,
            "counterparty_country": self.counterparty_country,
            "reference": self.reference,
            "booked_at": self.booked_at.isoformat() if self.booked_at else None,
            "flags": self.flags or [],
            "flagged": self.flagged,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
