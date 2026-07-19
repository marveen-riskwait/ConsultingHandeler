"""Risk Engine — explainable, versioned, data-driven risk scoring.

The active RiskMethodology (its factors + thresholds) lives in the database and
is versioned; every recompute stores the exact factors that produced the score,
the required actions and the methodology version, as a new RiskAssessment row so
the history stays auditable and interpretable under the methodology used.

If no methodology is configured, a legacy hardcoded set is used as a fallback so
the platform always scores risk.
"""
from api.models import (
    db, Customer, RiskAssessment, RiskMethodology,
    HIGH_RISK_COUNTRIES, HIGH_RISK_ACTIVITIES,
)
from api.engine import audit

# --- Legacy fallback (used only when no methodology exists in the DB) --------
_LEGACY_FACTORS = [
    (lambda c: c.is_pep, "PEP", "Politically Exposed Person detected", 30),
    (lambda c: c.has_sanctions_match, "SANCTIONS", "Potential sanctions match", 40),
    (lambda c: c.has_adverse_media, "ADVERSE_MEDIA", "Relevant adverse media", 20),
    (lambda c: c.complex_ownership, "OWNERSHIP", "Complex ownership structure", 15),
    (lambda c: (c.country or "") in HIGH_RISK_COUNTRIES,
     "GEOGRAPHY", "High-risk jurisdiction", 20),
    (lambda c: (c.business_activity or "").lower() in HIGH_RISK_ACTIVITIES,
     "BUSINESS", "High-risk business activity", 25),
]
_LEGACY_THRESHOLDS = [("LOW", 0, 30), ("MEDIUM", 31, 70),
                      ("HIGH", 71, 100), ("CRITICAL", 101, None)]


def active_methodology(organization_id):
    """Prefer the organization's active methodology, else a shared/system one."""
    org = (RiskMethodology.query
           .filter_by(organization_id=organization_id, active=True).first())
    if org:
        return org
    return (RiskMethodology.query
            .filter_by(organization_id=None, active=True).first())


def _factor_matches(factor, customer):
    ct = factor.condition_type
    cv = factor.condition_value or {}
    if ct == "FLAG":
        return bool(getattr(customer, cv.get("field", ""), False))
    if ct == "COUNTRY_IN":
        return (customer.country or "") in set(cv.get("values", []))
    if ct == "ACTIVITY_IN":
        return (customer.business_activity or "").lower() in {
            v.lower() for v in cv.get("values", [])}
    return False


def _level_from(thresholds, score):
    for level, lo, hi in sorted(thresholds, key=lambda t: t[1]):
        if score >= lo and (hi is None or score <= hi):
            return level
    return "LOW"


def _evaluate(customer):
    """Return (factors, score, level, methodology_version) using the DB
    methodology, or the legacy fallback."""
    methodology = active_methodology(customer.organization_id)
    factors, score = [], 0

    if methodology and methodology.factors:
        for f in methodology.factors:
            if f.active and _factor_matches(f, customer):
                score += f.impact
                factors.append({"code": f.code, "label": f.label, "impact": f.impact})
        thresholds = [(t.level, t.min_score, t.max_score)
                      for t in methodology.thresholds] or _LEGACY_THRESHOLDS
        return factors, score, _level_from(thresholds, score), methodology.version

    for predicate, code, label, impact in _LEGACY_FACTORS:
        if predicate(customer):
            score += impact
            factors.append({"code": code, "label": label, "impact": impact})
    return factors, score, _level_from(_LEGACY_THRESHOLDS, score), "legacy-v1"


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
    seen, ordered = set(), []
    for a in actions:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered


def recompute(customer: Customer, *, actor=None, reason="Risk recomputed"):
    """Recompute risk for a customer, persist a new (versioned) assessment,
    update the denormalised fields and leave an audit trail.

    Does NOT emit further compliance events — callers own that decision to keep
    processing loop-free.
    """
    factors, score, level, version = _evaluate(customer)
    actions = _required_actions(level, factors)
    old_score, old_level = customer.risk_score, customer.risk_level

    assessment = RiskAssessment(
        customer_id=customer.id,
        score=score,
        level=level,
        methodology_version=version,
        factors=factors,
        required_actions=actions,
        reason=reason,
    )
    db.session.add(assessment)

    customer.risk_score = score
    customer.risk_level = level

    if old_score != score or old_level != level:
        audit.record(
            "RISK_UPDATED", "customer", customer.id, actor=actor,
            old_value=f"{old_level} ({old_score})",
            new_value=f"{level} ({score})", reason=reason)

    db.session.commit()
    return assessment
