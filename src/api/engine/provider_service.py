"""Provider orchestration: verification, normalization, health, webhooks.

Webhook pipeline (per the document):
    Provider Webhook -> Verify Signature -> Check Idempotency -> Store Raw Event
      -> Normalize -> Create Compliance Event -> Run Rules Engine

Provider credentials never leave the backend; failed integrations are never
silently ignored (they raise / are logged on the WebhookEvent).
"""
import hashlib
import hmac
import secrets

from api.models import (
    db, Provider, ProviderCredential, ProviderHealthStatus,
    RawProviderResponse, NormalizedComplianceResult, WebhookEvent,
    Customer, utcnow,
)
from api.engine import audit
from api.engine.events import emit_event
from api.integrations.providers import get_adapter


# --------------------------------------------------------------------------- lookup
def find_provider(organization_id, *, name=None, provider_type=None, enabled=True):
    q = Provider.query.filter(
        (Provider.organization_id == organization_id) |
        (Provider.organization_id.is_(None)))
    if enabled:
        q = q.filter(Provider.enabled.is_(True))
    if name:
        q = q.filter(Provider.name == name)
    if provider_type:
        q = q.filter(Provider.provider_type == provider_type)
    return q.first()


def credentials_map(provider_row):
    return {c.key_name: c.secret_value for c in provider_row.credentials}


# --------------------------------------------------------------------------- results
def store_result(organization_id, customer_id, normalized):
    db.session.add(RawProviderResponse(
        organization_id=organization_id, provider=normalized.provider,
        provider_reference=normalized.provider_reference,
        verification_type=normalized.result_type, customer_id=customer_id,
        payload=normalized.raw or normalized.data))
    result = NormalizedComplianceResult(
        organization_id=organization_id, provider=normalized.provider,
        provider_reference=normalized.provider_reference,
        result_type=normalized.result_type, status=normalized.status,
        confidence=normalized.confidence, customer_id=customer_id,
        data=normalized.data)
    db.session.add(result)
    db.session.commit()
    return result


def verify_customer(customer, actor=None, provider_type="KYC"):
    """Run a provider verification for a customer, store raw + normalized
    results, and emit PROVIDER_STATUS_CHANGED so the rules engine reacts."""
    provider = find_provider(customer.organization_id, provider_type=provider_type)
    if provider is None:
        raise RuntimeError(f"No enabled {provider_type} provider configured")

    adapter = get_adapter(provider)
    normalized = adapter.create_verification(
        {"name": customer.name, "customer_id": customer.id,
         "type": customer.customer_type})
    result = store_result(customer.organization_id, customer.id, normalized)

    audit.record("PROVIDER_VERIFICATION", "customer", customer.id, actor=actor,
                 new_value=f"{provider.name}:{normalized.status}",
                 reason=normalized.result_type, commit=True)
    emit_event("PROVIDER_STATUS_CHANGED", customer_id=customer.id,
               severity="HIGH" if normalized.status == "FAILED" else "INFO",
               source=provider.name, actor=actor,
               payload={"status": normalized.status,
                        "result_type": normalized.result_type,
                        "provider": provider.name,
                        "provider_reference": normalized.provider_reference})
    return result


# --------------------------------------------------------------------------- health
def check_health(provider_row):
    adapter = get_adapter(provider_row)
    status, detail = adapter.health_check()
    hs = ProviderHealthStatus(provider_id=provider_row.id, status=status,
                              detail=detail)
    db.session.add(hs)
    db.session.commit()
    return hs


def latest_health(provider_row):
    return (ProviderHealthStatus.query
            .filter_by(provider_id=provider_row.id)
            .order_by(ProviderHealthStatus.checked_at.desc()).first())


# --------------------------------------------------------------------------- webhooks
def verify_signature(provider_row, raw_body: bytes, signature_header):
    """HMAC-SHA256 of the raw body with the provider's webhook_secret.
    Returns True/False. If no secret is configured, only the mock adapter is
    trusted (dev); real providers without a secret fail closed."""
    secret = credentials_map(provider_row).get("webhook_secret")
    if not secret:
        return provider_row.adapter.startswith("mock")
    if not signature_header:
        return False
    provided = signature_header.split("=", 1)[-1].strip()  # allow "sha256=..."
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def process_webhook(provider_name, raw_body: bytes, payload, signature_header,
                    event_id_header=None):
    """Idempotent, signature-checked webhook ingestion. Returns (status_code, dict)."""
    provider = Provider.query.filter_by(name=provider_name).first()
    if provider is None:
        return 404, {"message": "Unknown provider"}

    external_event_id = (payload.get("event_id") or event_id_header
                         or hashlib.sha256(raw_body).hexdigest()[:32])

    # Verify the signature FIRST — a rejected (unauthenticated) request must not
    # consume the idempotency key, or it could block the legitimate retry.
    valid = verify_signature(provider, raw_body, signature_header)
    if not valid:
        db.session.add(WebhookEvent(
            provider=provider_name,
            external_event_id=external_event_id + ":rejected:" + secrets.token_hex(4),
            signature_valid=False, status="ERROR", error="Invalid signature",
            payload=payload))
        db.session.commit()
        return 401, {"message": "Invalid signature"}

    # Idempotency: a replay of the same authenticated event is acknowledged.
    existing = WebhookEvent.query.filter_by(external_event_id=external_event_id).first()
    if existing:
        return 200, {"status": "duplicate", "webhook_event_id": existing.id}

    wh = WebhookEvent(provider=provider_name, external_event_id=external_event_id,
                      signature_valid=True, payload=payload)
    db.session.add(wh)

    try:
        adapter = get_adapter(provider)
        normalized = adapter.normalize_webhook(payload)
        customer_id = payload.get("customer_id")
        if customer_id and Customer.query.get(customer_id) is None:
            customer_id = None
        org_id = provider.organization_id or (
            Customer.query.get(customer_id).organization_id if customer_id else None)
        store_result(org_id, customer_id, normalized)

        emit_event("PROVIDER_STATUS_CHANGED", customer_id=customer_id,
                   severity="HIGH" if normalized.status in ("FAILED", "MATCH") else "INFO",
                   source=provider_name,
                   payload={"status": normalized.status,
                            "result_type": normalized.result_type,
                            "provider": provider_name,
                            "provider_reference": normalized.provider_reference})
        wh.status = "PROCESSED"
        wh.processed_at = utcnow()
        db.session.commit()
        return 200, {"status": "processed", "result": normalized.status}
    except Exception as exc:   # never silently ignore a failed integration
        wh.status = "ERROR"
        wh.error = str(exc)[:500]
        db.session.commit()
        return 500, {"message": "Webhook processing failed", "error": str(exc)[:200]}
