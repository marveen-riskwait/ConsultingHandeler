"""Review engine — scheduled + event-driven customer reviews.

Review frequency follows risk (LOW 36m ... CRITICAL 6m), but a review is also
triggered immediately by material events (PEP, sanctions, UBO change, adverse
media). Continuous monitoring flips SCHEDULED -> DUE and DUE -> OVERDUE.
"""
from datetime import timedelta

from api.models import (
    db, Customer, Review, REVIEW_FREQUENCY_MONTHS, utcnow,
)
from api.engine import audit
from api.engine.events import emit_event

# Events that trigger an immediate event-driven review.
REVIEW_TRIGGERS = {"PEP_DETECTED", "SANCTIONS_MATCH_FOUND", "UBO_CHANGED",
                   "ADVERSE_MEDIA_DETECTED"}


def frequency_months(risk_level):
    return REVIEW_FREQUENCY_MONTHS.get(risk_level, 24)


def _open_reviews(customer_id):
    return Review.query.filter_by(customer_id=customer_id).filter(
        Review.status.in_(["SCHEDULED", "DUE", "IN_PROGRESS", "OVERDUE"]))


def schedule_initial(customer, actor=None):
    """On onboarding: an INITIAL_KYC review is due now."""
    review = Review(organization_id=customer.organization_id,
                    customer_id=customer.id, review_type="INITIAL_KYC",
                    status="DUE", trigger="Onboarding",
                    due_at=utcnow() + timedelta(days=30))
    db.session.add(review)
    audit.record("REVIEW_SCHEDULED", "review", None, actor=actor,
                 new_value="INITIAL_KYC", reason="onboarding", commit=True)
    return review


def create_event_driven(customer, trigger, actor=None):
    """Immediate review triggered by an event (deduped by trigger)."""
    exists = _open_reviews(customer.id).filter(
        Review.review_type == "EVENT_DRIVEN_REVIEW",
        Review.trigger == trigger).first()
    if exists:
        return exists
    review = Review(organization_id=customer.organization_id,
                    customer_id=customer.id, review_type="EVENT_DRIVEN_REVIEW",
                    status="DUE", trigger=trigger,
                    due_at=utcnow() + timedelta(days=5))
    db.session.add(review)
    audit.record("REVIEW_SCHEDULED", "review", None, actor=actor,
                 new_value="EVENT_DRIVEN_REVIEW", reason=trigger, commit=True)
    return review


def maybe_event_review(event):
    if event.event_type in REVIEW_TRIGGERS and event.customer_id:
        customer = Customer.query.get(event.customer_id)
        if customer:
            create_event_driven(customer, trigger=event.event_type)


def complete_review(review, decision, reason, actor=None):
    review.status = "COMPLETED"
    review.completed_at = utcnow()
    review.decision = decision
    review.decision_reason = reason
    customer = Customer.query.get(review.customer_id)
    customer.last_review_at = utcnow()
    audit.record("REVIEW_COMPLETED", "review", review.id, actor=actor,
                 new_value=decision, reason=reason)

    # Schedule the next periodic review by current risk.
    months = frequency_months(customer.risk_level)
    scheduled_for = utcnow() + timedelta(days=months * 30)
    nxt = Review(organization_id=customer.organization_id,
                 customer_id=customer.id, review_type="PERIODIC_REVIEW",
                 status="SCHEDULED", trigger=f"Every {months} months ({customer.risk_level})",
                 scheduled_for=scheduled_for,
                 due_at=scheduled_for + timedelta(days=30))
    db.session.add(nxt)
    db.session.commit()
    return review, nxt


def start_review(review, actor=None):
    review.status = "IN_PROGRESS"
    review.started_at = utcnow()
    review.assigned_to = actor.id if actor else review.assigned_to
    db.session.commit()
    return review


def run_monitoring():
    """Flip SCHEDULED->DUE and DUE/IN_PROGRESS->OVERDUE; emit events. Returns counts."""
    now = utcnow()
    became_due = 0
    for r in Review.query.filter_by(status="SCHEDULED").filter(
            Review.scheduled_for.isnot(None)).filter(Review.scheduled_for <= now).all():
        r.status = "DUE"
        db.session.commit()
        emit_event("REVIEW_DUE", customer_id=r.customer_id, severity="MEDIUM",
                   source="monitoring", payload={"review_id": r.id, "type": r.review_type})
        became_due += 1

    became_overdue = 0
    for r in Review.query.filter(Review.status.in_(["DUE", "IN_PROGRESS"])).filter(
            Review.due_at.isnot(None)).filter(Review.due_at < now).all():
        r.status = "OVERDUE"
        db.session.commit()
        emit_event("REVIEW_OVERDUE", customer_id=r.customer_id, severity="HIGH",
                   source="monitoring", payload={"review_id": r.id, "type": r.review_type})
        became_overdue += 1

    return {"became_due": became_due, "became_overdue": became_overdue}
