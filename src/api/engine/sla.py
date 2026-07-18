"""SLA engine — on-time / at-risk / breached, from SLAConfiguration.

A case's SLA window comes from its priority's target_hours (fallback defaults).
    closed:  ON_TIME if closed before the deadline, else BREACHED
    open:    BREACHED past the deadline, AT_RISK in the last 25% of the window,
             ON_TIME otherwise
"""
from datetime import timedelta

from api.models import SLAConfiguration, utcnow

DEFAULT_TARGET_HOURS = {"CRITICAL": 24, "HIGH": 72, "MEDIUM": 120, "LOW": 240}


def target_hours_map(organization_id):
    m = dict(DEFAULT_TARGET_HOURS)
    for cfg in SLAConfiguration.query.filter_by(
            organization_id=organization_id, active=True).all():
        m[cfg.case_priority] = cfg.target_hours
    return m


def case_sla(case, hours_map):
    hours = hours_map.get(case.priority, 120)
    if not case.opened_at:
        return {"status": "ON_TIME", "deadline": None}
    deadline = case.opened_at + timedelta(hours=hours)
    if case.status == "CLOSED" and case.closed_at:
        status = "ON_TIME" if case.closed_at <= deadline else "BREACHED"
    else:
        now = utcnow()
        if now > deadline:
            status = "BREACHED"
        elif now > deadline - timedelta(hours=hours * 0.25):
            status = "AT_RISK"
        else:
            status = "ON_TIME"
    return {"status": status, "deadline": deadline.isoformat()}


def sla_summary(cases, organization_id):
    hours_map = target_hours_map(organization_id)
    counts = {"ON_TIME": 0, "AT_RISK": 0, "BREACHED": 0}
    for c in cases:
        counts[case_sla(c, hours_map)["status"]] += 1
    total = sum(counts.values()) or 1
    return {
        "on_time": counts["ON_TIME"],
        "at_risk": counts["AT_RISK"],
        "breached": counts["BREACHED"],
        "on_time_pct": round(100 * counts["ON_TIME"] / total),
    }
