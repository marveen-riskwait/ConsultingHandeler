"""Screening service — turns provider hits into ScreeningMatch records and drives
the event chain, keeping the customer's derived flags + risk in sync.

Match lifecycle: a hit that reappears in a later run updates last_seen_at but
keeps its earlier first_detected_at and any human review decision — history is
never lost (unlike the v1 boolean flags).
"""
from api.models import (
    db, Customer, ScreeningRun, ScreeningMatch, Case,
    ACTIVE_MATCH_STATUSES, utcnow,
)
from api.engine import audit, risk_engine
from api.engine.events import emit_event
from api.integrations.screening import get_provider

# match_type -> (event_type, severity, case_type or None)
_EVENT_MAP = {
    "SANCTIONS": ("SANCTIONS_MATCH_FOUND", "CRITICAL", "SANCTIONS_MATCH"),
    "PEP": ("PEP_DETECTED", "HIGH", "PEP"),
    "ADVERSE_MEDIA": ("ADVERSE_MEDIA_DETECTED", "MEDIUM", None),
}


def recompute_screening_flags(customer):
    """Derive the customer's cached flags from its live (non-false-positive)
    screening matches. This is the bridge that keeps the existing risk engine
    working while ScreeningMatch is the real source of truth."""
    active = (ScreeningMatch.query
              .filter_by(customer_id=customer.id)
              .filter(ScreeningMatch.status.in_(ACTIVE_MATCH_STATUSES))
              .all())
    types = {m.match_type for m in active}
    customer.is_pep = "PEP" in types
    customer.has_sanctions_match = "SANCTIONS" in types
    customer.has_adverse_media = "ADVERSE_MEDIA" in types


def _upsert_match(customer, run, m):
    """Return (match, is_new). Dedupe by (customer, type, source, matched_name)."""
    existing = (ScreeningMatch.query
                .filter_by(customer_id=customer.id, match_type=m.match_type,
                           source=m.source, matched_name=m.matched_name)
                .first())
    if existing:
        existing.last_seen_at = utcnow()
        existing.match_score = m.match_score
        existing.screening_run_id = run.id
        return existing, False

    match = ScreeningMatch(
        screening_run_id=run.id,
        customer_id=customer.id,
        match_type=m.match_type,
        source=m.source,
        matched_name=m.matched_name,
        match_score=m.match_score,
        match_data=m.data,
        status="POTENTIAL",
    )
    db.session.add(match)
    db.session.flush()
    return match, True


def run_screening_for(customer, requested_by=None):
    """Screen a customer, record a run + matches, fire events for new hits."""
    provider = get_provider()
    run = ScreeningRun(
        customer_id=customer.id,
        subject_name=customer.name,
        sources=[provider.name],
        status="RUNNING",
        requested_by=requested_by.id if requested_by else None,
    )
    db.session.add(run)
    db.session.flush()

    hits = provider.screen(customer.name, country=customer.country,
                           kind=("ORGANIZATION" if customer.customer_type == "COMPANY" else "PERSON"))
    audit.record("SCREENING_RUN", "customer", customer.id,
                 actor=requested_by,
                 new_value=f"{len(hits)} hit(s)", reason=f"run #{run.id}")

    emitted = []
    for hit in hits:
        match, is_new = _upsert_match(customer, run, hit)
        # Flags must reflect this match BEFORE the event is risk-scored.
        recompute_screening_flags(customer)
        db.session.commit()

        if not is_new:
            continue

        event_type, severity, case_type = _EVENT_MAP[hit.match_type]
        emit_event(event_type, customer_id=customer.id, severity=severity,
                   source=hit.source, actor=requested_by,
                   payload={"match_id": match.id, **hit.as_dict()})
        emitted.append(event_type)

        # Link the case the rules engine just opened back to this match.
        if case_type:
            case = (Case.query
                    .filter_by(customer_id=customer.id, case_type=case_type)
                    .filter(Case.status != "CLOSED")
                    .order_by(Case.id.desc()).first())
            if case:
                match.case_id = case.id
                db.session.commit()

    if not hits:
        emit_event("SCREENING_CLEARED", customer_id=customer.id, severity="INFO",
                   source=provider.name, actor=requested_by,
                   payload={"result": "no match"})

    run.status = "COMPLETED"
    run.finished_at = utcnow()
    db.session.commit()
    return run, emitted


_OPEN_ALERT_STATUSES = ("OPEN", "ASSIGNED", "IN_REVIEW")
_CLOSED_CASE_STATUSES = ("CLOSED", "APPROVED", "REJECTED", "ESCALATED")


def _close_out_cleared_match(match, user):
    """Close what a match opened, once it is cleared.

    Everything in the chain fires forwards — a match opens a case and raises an
    alert — and nothing fired backwards: clearing the match as a false positive
    left both standing. An analyst then sees an alert for a finding that no
    longer exists, and a case nobody will decide, and both keep counting
    towards queues and SLAs.

    Nothing is closed while something is still live: the case closes only when
    no active match remains on it, and an alert is resolved only when no active
    match of that kind remains on the customer.
    """
    from api.engine import alert_service
    from api.models import ComplianceAlert

    if match.case_id:
        still_active = (ScreeningMatch.query
                        .filter(ScreeningMatch.case_id == match.case_id,
                                ScreeningMatch.status.in_(ACTIVE_MATCH_STATUSES))
                        .count())
        case = Case.query.get(match.case_id)
        if still_active == 0 and case is not None \
                and case.status not in _CLOSED_CASE_STATUSES:
            case.status = "CLOSED"
            case.decision = case.decision or "NO_ACTION"
            case.decision_reason = (case.decision_reason
                                    or "All screening matches on this case were "
                                       "cleared as false positives.")
            case.decided_by = user.id if user else None
            audit.record("CASE_CLOSED", "case", case.id, actor=user,
                         new_value="CLOSED",
                         reason="Last active screening match cleared")

    # Alerts carry the event type that raised them, and _EVENT_MAP is the one
    # source of truth for which event each match kind emits — guessing the
    # names here is how the sanctions alert stayed open on the first attempt.
    event_type = _EVENT_MAP.get(match.match_type, (None,))[0]
    alert_types = (event_type,) if event_type else ()
    if alert_types:
        same_kind_active = (ScreeningMatch.query
                            .filter(ScreeningMatch.customer_id == match.customer_id,
                                    ScreeningMatch.match_type == match.match_type,
                                    ScreeningMatch.status.in_(ACTIVE_MATCH_STATUSES))
                            .count())
        if same_kind_active == 0:
            open_alerts = (ComplianceAlert.query
                           .filter(ComplianceAlert.customer_id == match.customer_id,
                                   ComplianceAlert.alert_type.in_(alert_types),
                                   ComplianceAlert.status.in_(_OPEN_ALERT_STATUSES))
                           .all())
            for alert in open_alerts:
                alert_service.resolve(
                    alert, user,
                    f"{match.match_type} match cleared as a false positive — "
                    "no active match of this kind remains.")


def review_match(match, decision, reason, user):
    """Record a human decision on a match and re-sync flags + risk.
    decision in FALSE_POSITIVE | CONFIRMED | ESCALATED."""
    old = match.status
    match.status = decision
    match.reviewed_by = user.id if user else None
    match.reviewed_at = utcnow()
    match.decision_reason = reason
    audit.record("MATCH_REVIEWED", "screening_match", match.id, actor=user,
                 old_value=old, new_value=decision, reason=reason)

    customer = Customer.query.get(match.customer_id)
    recompute_screening_flags(customer)
    if decision == "FALSE_POSITIVE":
        _close_out_cleared_match(match, user)
    db.session.commit()
    risk_engine.recompute(customer, actor=user, reason=f"Match {match.id}: {decision}")
    return match
