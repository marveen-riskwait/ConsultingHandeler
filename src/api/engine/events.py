"""Event bus — the single entry point for "something happened".

`emit_event` persists a ComplianceEvent then hands it to processing. Processing
runs asynchronously through Celery when a broker is configured, and inline
(synchronously) otherwise, so the vertical slice is fully demonstrable even
without Redis running.
"""
import os

from api.models import db, ComplianceEvent, User, Notification, Customer
from api.engine import audit


def notify_users(users, *, severity, title, message, customer_id=None,
                 event_id=None, requires_action=False):
    for user in users:
        db.session.add(Notification(
            user_id=user.id,
            event_id=event_id,
            customer_id=customer_id,
            severity=severity,
            title=title,
            message=message,
            requires_action=requires_action,
        ))


def recipients_for_org(organization_id, roles):
    """Active users in an organization holding one of `roles` (by primary or
    additional role)."""
    if not organization_id:
        return []
    users = User.query.filter_by(organization_id=organization_id, is_active=True).all()
    if not roles:
        return users
    roleset = set(roles)
    return [u for u in users if roleset & set(u.role_names())]


def recipients_for(customer, roles):
    """Users in the customer's organization holding one of `roles`."""
    if customer is None:
        return []
    return recipients_for_org(customer.organization_id, roles)


def _celery_enabled():
    # A broker URL means we can dispatch asynchronously.
    return bool(os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL"))


def emit_event(event_type, *, customer_id=None, severity="MEDIUM",
               source="system", payload=None, actor=None):
    """Create a compliance event and trigger its processing."""
    event = ComplianceEvent(
        event_type=event_type,
        customer_id=customer_id,
        severity=severity,
        source=source,
        payload=payload or {},
        status="NEW",
    )
    db.session.add(event)
    audit.record("EVENT_DETECTED", "compliance_event", None,
                 actor=actor, new_value=event_type,
                 reason=f"source={source}")
    db.session.commit()

    if _celery_enabled():
        # Imported lazily to avoid an import cycle (tasks import this module).
        try:
            from api.tasks import process_compliance_event
            process_compliance_event.delay(event.id)
            return event
        except Exception:
            # Broker unreachable — fall back to inline so nothing is lost.
            pass

    from api.engine.rules_engine import process_event
    process_event(event.id)
    return event
