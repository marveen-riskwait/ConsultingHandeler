"""Risk Engine — explainable, versioned risk scoring.

A score is never "just a number": every recompute stores the exact factors that
produced it and the actions the score requires, as a new RiskAssessment row so
the history stays auditable.
"""
from api.models import (
    db, Customer, RiskAssessment,
    HIGH_RISK_COUNTRIES, HIGH_RISK_ACTIVITIES,
)
from api.engine import audit

METHODOLOGY_VERSION = "v1"

# Each factor: (predicate(customer) -> bool, code, label, impact)
_FACTORS = [
    (lambda c: c.is_pep, "PEP", "Politically Exposed Person detected", 30),
    (lambda c: c.has_sanctions_match, "SANCTIONS", "Potential sanctions match", 40),
    (lambda c: c.has_adverse_media, "ADVERSE_MEDIA", "Relevant adverse media", 20),
    (lambda c: c.complex_ownership, "OWNERSHIP", "Complex ownership structure", 15),
    (lambda c: (c.country or "") in HIGH_RISK_COUNTRIES,
     "GEOGRAPHY", "High-risk jurisdiction", 20),
    (lambda c: (c.business_activity or "").lower() in HIGH_RISK_ACTIVITIES,
     "BUSINESS", "High-risk business activity", 25),
]


def _level_for(score):
    if score <= 30:
        return "LOW"
    if score <= 70:
        return "MEDIUM"
    if score <= 100:
        return "HIGH"
    return "CRITICAL"


def _required_actions(level, factors):
    actions = []
    codes = {f["code"] for f in factors}
    if level in ("HIGH", "CRITICAL"):
        actions += ["Enhanced Due Diligence", "Source of Wealth",
                    "Source of Funds", "Senior management approval"]
    if "PEP" in codes:
        actions.append("PEP periodic monitoring")
    if "SANCTIONS" in codes:
        actions.append("Sanctions match investigation")
    review = {"LOW": "Review every 36 months", "MEDIUM": "Review every 24 months",
              "HIGH": "Review every 12 months", "CRITICAL": "Review every 3-6 months"}
    actions.append(review[level])
    # de-duplicate, keep order
    seen, ordered = set(), []
    for a in actions:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered


def recompute(customer: Customer, *, actor=None, reason="Risk recomputed"):
    """Recompute risk for a customer, persist a new assessment, update the
    denormalised fields and leave an audit trail. Returns the RiskAssessment.

    Does NOT emit further compliance events — callers own that decision to keep
    processing loop-free.
    """
    factors = []
    score = 0
    for predicate, code, label, impact in _FACTORS:
        if predicate(customer):
            score += impact
            factors.append({"code": code, "label": label, "impact": impact})

    level = _level_for(score)
    actions = _required_actions(level, factors)

    old_score, old_level = customer.risk_score, customer.risk_level

    assessment = RiskAssessment(
        customer_id=customer.id,
        score=score,
        level=level,
        methodology_version=METHODOLOGY_VERSION,
        factors=factors,
        required_actions=actions,
        reason=reason,
    )
    db.session.add(assessment)

    customer.risk_score = score
    customer.risk_level = level

    if old_score != score or old_level != level:
        audit.record(
            "RISK_UPDATED", "customer", customer.id,
            actor=actor,
            old_value=f"{old_level} ({old_score})",
            new_value=f"{level} ({score})",
            reason=reason,
        )

    db.session.commit()
    return assessment
