"""Alert Center — raise, assign and resolve first-class compliance alerts.

An alert is created automatically from a high/critical compliance event (in
addition to any notification). Notifications inform; alerts must be worked.
"""
from api.models import db, ComplianceAlert, Customer, utcnow
from api.engine import audit

# Events that always deserve an alert regardless of severity.
_ALWAYS_ALERT = {"SANCTIONS_MATCH_FOUND", "PEP_DETECTED", "UBO_CHANGED",
                 "PROVIDER_STATUS_CHANGED"}


def maybe_create_from_event(event):
    """Raise a ComplianceAlert for a high/critical event (deduped per event)."""
    if event.customer_id is None:
        return None
    if event.severity not in ("HIGH", "CRITICAL") and event.event_type not in _ALWAYS_ALERT:
        return None
    if ComplianceAlert.query.filter_by(event_id=event.id).first():
        return None
    customer = Customer.query.get(event.customer_id)
    if customer is None:
        return None

    alert = ComplianceAlert(
        organization_id=customer.organization_id,
        customer_id=customer.id,
        event_id=event.id,
        alert_type=event.event_type,
        source=event.source,
        severity=event.severity,
        title=event.event_type.replace("_", " ").title(),
        status="OPEN",
    )
    db.session.add(alert)
    audit.record("ALERT_RAISED", "compliance_alert", None,
                 new_value=event.event_type, reason=f"event {event.id}")
    db.session.commit()
    return alert


def assign(alert, user, assignee):
    alert.assigned_to = assignee.id
    alert.status = "ASSIGNED"
    audit.record("ALERT_ASSIGNED", "compliance_alert", alert.id, actor=user,
                 new_value=assignee.email, commit=True)
    return alert


def resolve(alert, user, resolution, dismiss=False):
    alert.status = "DISMISSED" if dismiss else "RESOLVED"
    alert.resolution = resolution
    alert.resolved_by = user.id if user else None
    alert.resolved_at = utcnow()
    audit.record("ALERT_RESOLVED", "compliance_alert", alert.id, actor=user,
                 new_value=alert.status, reason=resolution, commit=True)
    return alert
