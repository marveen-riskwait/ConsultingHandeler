"""External provider integration models.

The platform is a Compliance Orchestration Layer: it does not build the sanctions
/ PEP / identity databases, it talks to providers (Sumsub, Trulioo,
ComplyAdvantage, ...) through adapters. These models persist the configuration,
credentials (never exposed to the frontend), health, the RAW provider payloads
and the NORMALIZED internal results, plus webhook events for idempotency.
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Float, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

PROVIDER_TYPES = ("KYC", "KYB", "AML", "SCREENING", "IDENTITY", "DOCUMENT", "FRAUD")
HEALTH_STATUSES = ("UP", "DEGRADED", "DOWN", "UNKNOWN")


class Provider(db.Model):
    """A configured integration (org-scoped; organization_id NULL = shared)."""
    __tablename__ = "provider"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(20), nullable=False)
    adapter: Mapped[str] = mapped_column(String(40), nullable=False, default="mock")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    credentials: Mapped[list["ProviderCredential"]] = relationship(back_populates="provider")

    def serialize(self, with_health=None):
        data = {
            "id": self.id, "name": self.name, "provider_type": self.provider_type,
            "adapter": self.adapter, "enabled": self.enabled,
            "config": self.config or {},
            # Never the secret values — only which credential keys exist.
            "credential_keys": [c.key_name for c in self.credentials],
        }
        if with_health is not None:
            data["health"] = with_health.serialize() if with_health else None
        return data


class ProviderCredential(db.Model):
    """A secret for a provider. Stored server-side, NEVER serialized to clients."""
    __tablename__ = "provider_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("provider.id"), nullable=False)
    provider: Mapped["Provider"] = relationship(back_populates="credentials")
    key_name: Mapped[str] = mapped_column(String(60), nullable=False)   # api_key, webhook_secret
    secret_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProviderHealthStatus(db.Model):
    __tablename__ = "provider_health_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("provider.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    detail: Mapped[str] = mapped_column(String(200), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"status": self.status, "detail": self.detail,
                "checked_at": self.checked_at.isoformat() if self.checked_at else None}


class RawProviderResponse(db.Model):
    """The untouched provider payload — kept for audit / reproducibility."""
    __tablename__ = "raw_provider_response"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(120), nullable=True)
    verification_type: Mapped[str] = mapped_column(String(40), nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "provider": self.provider,
                "provider_reference": self.provider_reference,
                "verification_type": self.verification_type,
                "received_at": self.received_at.isoformat() if self.received_at else None}


class NormalizedComplianceResult(db.Model):
    """Provider-agnostic internal result — the rest of the platform uses this."""
    __tablename__ = "normalized_compliance_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(120), nullable=True)
    result_type: Mapped[str] = mapped_column(String(40), nullable=False)   # IDENTITY / SCREENING / KYB
    status: Mapped[str] = mapped_column(String(30), nullable=False)        # PASSED / FAILED / PENDING / MATCH
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id, "provider": self.provider,
            "provider_reference": self.provider_reference,
            "result_type": self.result_type, "status": self.status,
            "confidence": self.confidence, "customer_id": self.customer_id,
            "data": self.data or {},
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class WebhookEvent(db.Model):
    """Received provider webhook — external_event_id enforces idempotency."""
    __tablename__ = "webhook_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="RECEIVED")     # RECEIVED / PROCESSED / ERROR
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def serialize(self):
        return {"id": self.id, "provider": self.provider,
                "external_event_id": self.external_event_id,
                "signature_valid": self.signature_valid, "status": self.status,
                "received_at": self.received_at.isoformat() if self.received_at else None}
