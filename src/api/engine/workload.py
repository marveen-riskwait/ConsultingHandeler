"""Workload engine — how loaded is each analyst, explainably.

Per the instruction document, each user gets:
    active_cases, active_tasks, overdue_tasks, high_risk_cases,
    critical_alerts, average_resolution_time, workload_score

The score is a weighted sum capped at 100 so managers can compare at a glance:
    active_cases x8, active_tasks x3, overdue_tasks x10,
    high_risk_cases x5, critical_alerts x5
"""
from api.models import db, User, Case, Task, Customer, utcnow

_WEIGHTS = {"active_cases": 8, "active_tasks": 3, "overdue_tasks": 10,
            "high_risk_cases": 5, "critical_alerts": 5}


def compute_user_workload(user):
    now = utcnow()
    open_cases = (Case.query.filter_by(assigned_to=user.id)
                  .filter(Case.status != "CLOSED").all())
    open_tasks = (Task.query.filter_by(assigned_to=user.id)
                  .filter(Task.status != "DONE").all())
    overdue_tasks = [t for t in open_tasks if t.due_at and t.due_at < now]
    high_risk = [c for c in open_cases if c.priority in ("HIGH", "CRITICAL")]
    critical = [c for c in open_cases if c.priority == "CRITICAL"]

    closed = (Case.query.filter_by(assigned_to=user.id, status="CLOSED")
              .filter(Case.closed_at.isnot(None)).all())
    if closed:
        hours = [(c.closed_at - c.opened_at).total_seconds() / 3600
                 for c in closed if c.opened_at]
        avg_resolution_hours = round(sum(hours) / len(hours), 1) if hours else None
    else:
        avg_resolution_hours = None

    score = (len(open_cases) * _WEIGHTS["active_cases"]
             + len(open_tasks) * _WEIGHTS["active_tasks"]
             + len(overdue_tasks) * _WEIGHTS["overdue_tasks"]
             + len(high_risk) * _WEIGHTS["high_risk_cases"]
             + len(critical) * _WEIGHTS["critical_alerts"])

    return {
        "user_id": user.id,
        "name": user.full_name,
        "email": user.email,
        "role": user.role,
        "active_cases": len(open_cases),
        "active_tasks": len(open_tasks),
        "overdue_tasks": len(overdue_tasks),
        "high_risk_cases": len(high_risk),
        "critical_alerts": len(critical),
        "average_resolution_hours": avg_resolution_hours,
        "workload_score": min(100, score),
    }


def org_workload(organization_id):
    users = (User.query.filter_by(organization_id=organization_id, is_active=True)
             .all())
    # Only operational roles carry a caseload worth displaying.
    skip = {"CUSTOMER_USER"}
    return [compute_user_workload(u) for u in users if u.role not in skip]


def active_case_count(user_id):
    return (Case.query.filter_by(assigned_to=user_id)
            .filter(Case.status != "CLOSED").count())
