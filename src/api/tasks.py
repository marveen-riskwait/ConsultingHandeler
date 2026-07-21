"""Celery tasks — the asynchronous side of the platform.

The API enqueues these; workers run them out of the request cycle so Flask never
blocks on an external screening call or a 50k-customer sweep.
"""
from datetime import timedelta

from api.celery_app import celery
from api.models import db, Customer, Document, User, utcnow
from api.engine.events import emit_event
from api.engine.rules_engine import process_event
from api.engine.screening_service import run_screening_for


@celery.task(name="api.tasks.process_compliance_event")
def process_compliance_event(event_id):
    """Run the rules engine + risk recompute for one event."""
    process_event(event_id)
    return {"event_id": event_id, "status": "processed"}


@celery.task(name="api.tasks.run_screening")
def run_screening(customer_id, requested_by_id=None):
    """Screen a customer against sanctions / PEP / adverse-media sources.

    Delegates to the screening service, which records a ScreeningRun + typed
    ScreeningMatch rows, keeps the derived flags in sync, and fires the event
    chain (rules -> cases/tasks/notifications -> risk).
    """
    customer = Customer.query.get(customer_id)
    if customer is None:
        return {"error": "customer not found"}

    requested_by = User.query.get(requested_by_id) if requested_by_id else None
    run, emitted = run_screening_for(customer, requested_by=requested_by)
    return {"customer_id": customer_id, "run_id": run.id, "matches": emitted}


@celery.task(name="api.tasks.run_enrichment")
def run_enrichment(customer_id, requested_by_id=None):
    """Fill a customer's file from public sources (registries, LEI, media)."""
    from api.engine import enrichment_service
    customer = Customer.query.get(customer_id)
    if customer is None:
        return {"error": "customer not found"}
    actor = User.query.get(requested_by_id) if requested_by_id else None
    report = enrichment_service.enrich(customer, actor=actor)
    return {"customer_id": customer_id, "summary": report["summary"]}


@celery.task(name="api.tasks.check_review_deadlines")
def check_review_deadlines():
    """Continuous monitoring: flip due/overdue reviews and emit REVIEW_DUE /
    REVIEW_OVERDUE events."""
    from api.engine.review_engine import run_monitoring
    return run_monitoring()


@celery.task(name="api.tasks.rescreen_high_risk")
def rescreen_high_risk():
    """Periodically re-screen HIGH/CRITICAL customers against the providers."""
    customers = (Customer.query
                 .filter(Customer.risk_level.in_(["HIGH", "CRITICAL"])).all())
    for c in customers:
        run_screening_for(c)
    return {"rescreened": len(customers)}


@celery.task(name="api.tasks.check_document_expiry")
def check_document_expiry(days=30):
    """Daily sweep: emit DOCUMENT_EXPIRING for docs expiring within `days`."""
    horizon = utcnow() + timedelta(days=days)
    docs = (Document.query
            .filter(Document.expiry_date.isnot(None))
            .filter(Document.expiry_date <= horizon)
            .filter(Document.status != "EXPIRED")
            .all())
    count = 0
    for doc in docs:
        remaining = (doc.expiry_date - utcnow()).days
        emit_event("DOCUMENT_EXPIRING", customer_id=doc.customer_id,
                   severity="MEDIUM" if remaining > 7 else "HIGH",
                   source="document_monitor",
                   payload={"document_type": doc.doc_type,
                            "document_id": doc.id,
                            "days_remaining": remaining})
        count += 1
    return {"expiring": count}
