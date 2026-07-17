"""Celery tasks — the asynchronous side of the platform.

The API enqueues these; workers run them out of the request cycle so Flask never
blocks on an external screening call or a 50k-customer sweep.
"""
from datetime import timedelta

from api.celery_app import celery
from api.models import db, Customer, Document, utcnow
from api.engine import audit
from api.engine.events import emit_event
from api.engine.rules_engine import process_event
from api.engine.screening import get_provider


@celery.task(name="api.tasks.process_compliance_event")
def process_compliance_event(event_id):
    """Run the rules engine + risk recompute for one event."""
    process_event(event_id)
    return {"event_id": event_id, "status": "processed"}


@celery.task(name="api.tasks.run_screening")
def run_screening(customer_id):
    """Screen a customer against sanctions / PEP / adverse-media sources.

    Matches flip the corresponding customer flag and emit a compliance event,
    which the rules engine turns into cases/tasks/notifications and which the
    risk engine folds into the score.
    """
    customer = Customer.query.get(customer_id)
    if customer is None:
        return {"error": "customer not found"}

    matches = get_provider().screen(customer.name, customer.country)
    audit.record("SCREENING_RUN", "customer", customer.id,
                 new_value=f"{len(matches)} potential match(es)",
                 reason="screening job")
    db.session.commit()

    emitted = []
    for m in matches:
        mtype = m["match_type"]
        if mtype == "SANCTIONS":
            customer.has_sanctions_match = True
            db.session.commit()
            emit_event("SANCTIONS_MATCH_FOUND", customer_id=customer.id,
                       severity="CRITICAL", source=m["list"], payload=m)
            emitted.append("SANCTIONS_MATCH_FOUND")
        elif mtype == "PEP":
            customer.is_pep = True
            db.session.commit()
            emit_event("PEP_DETECTED", customer_id=customer.id,
                       severity="HIGH", source=m["list"], payload=m)
            emitted.append("PEP_DETECTED")
        elif mtype == "ADVERSE_MEDIA":
            customer.has_adverse_media = True
            db.session.commit()
            emit_event("ADVERSE_MEDIA_DETECTED", customer_id=customer.id,
                       severity="MEDIUM", source=m["list"], payload=m)
            emitted.append("ADVERSE_MEDIA_DETECTED")

    if not matches:
        # Clean screening is itself a fact worth recording on the timeline.
        emit_event("SCREENING_CLEARED", customer_id=customer.id,
                   severity="INFO", source="screening",
                   payload={"result": "no match"})

    return {"customer_id": customer_id, "matches": emitted}


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
