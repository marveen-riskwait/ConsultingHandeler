"""Rules Engine — decides what an event MEANS.

Rules are data (ComplianceRule rows), not code, so a compliance admin can change
behaviour without a deploy. Each rule matches an event_type, checks optional
conditions, and runs a list of actions (create case / task / notification).
"""
from datetime import timedelta

from api.models import (
    db, ComplianceEvent, ComplianceRule, Customer, Case, Task,
    utcnow,
)
from api.engine import audit, risk_engine
from api.engine.events import notify_users, recipients_for


def _match_conditions(conditions, customer, payload):
    """Very small matcher. Supports keys of the form:
        "customer.<field>"  -> equality against the customer attribute
        "payload.<field>"   -> equality against the event payload
        "payload.<field>__gte" / "__lte" -> numeric comparison
    Empty conditions always match.
    """
    if not conditions:
        return True
    for key, expected in conditions.items():
        op = "eq"
        field = key
        for suffix in ("__gte", "__lte"):
            if key.endswith(suffix):
                op, field = suffix[2:], key[: -len(suffix)]
                break

        if field.startswith("customer."):
            actual = getattr(customer, field.split(".", 1)[1], None)
        elif field.startswith("payload."):
            actual = (payload or {}).get(field.split(".", 1)[1])
        else:
            actual = None

        if op == "eq" and actual != expected:
            return False
        if op == "gte" and not (actual is not None and actual >= expected):
            return False
        if op == "lte" and not (actual is not None and actual <= expected):
            return False
    return True


def _run_action(action, *, customer, event, created_case):
    atype = action.get("type")

    if atype == "CREATE_CASE":
        case = Case(
            customer_id=customer.id,
            case_type=action.get("case_type", event.event_type),
            title=action.get("title", event.event_type.replace("_", " ").title()),
            priority=action.get("priority", "MEDIUM"),
            status="OPEN",
            due_at=utcnow() + timedelta(days=action.get("due_days", 5)),
        )
        db.session.add(case)
        db.session.flush()  # get case.id
        audit.record("CASE_OPENED", "case", case.id, new_value=case.title,
                     reason=f"rule action on {event.event_type}")
        return case

    if atype == "CREATE_TASK":
        task = Task(
            customer_id=customer.id,
            case_id=created_case.id if created_case else None,
            task_type=action.get("task_type", "REVIEW"),
            title=action.get("title", "Compliance task"),
            priority=action.get("priority", "MEDIUM"),
            due_at=utcnow() + timedelta(days=action.get("due_days", 5)),
        )
        db.session.add(task)
        audit.record("TASK_CREATED", "task", None, new_value=task.title,
                     reason=f"rule action on {event.event_type}")
        return created_case

    if atype == "NOTIFY":
        users = recipients_for(customer, action.get("roles"))
        notify_users(
            users,
            severity=action.get("severity", event.severity),
            title=action.get("title", event.event_type.replace("_", " ").title()),
            message=action.get("message", ""),
            customer_id=customer.id if customer else None,
            event_id=event.id,
            requires_action=action.get("requires_action", False),
        )
        return created_case

    return created_case


def process_event(event_id):
    """Evaluate all active rules for an event, then recompute risk."""
    event = ComplianceEvent.query.get(event_id)
    if event is None or event.status == "PROCESSED":
        return
    customer = Customer.query.get(event.customer_id) if event.customer_id else None

    rules = ComplianceRule.query.filter_by(event_type=event.event_type, active=True).all()
    created_case = None
    for rule in rules:
        if not _match_conditions(rule.conditions, customer, event.payload):
            continue
        for action in (rule.actions or []):
            result = _run_action(action, customer=customer, event=event,
                                  created_case=created_case)
            if isinstance(result, Case):
                created_case = result

    # Risk is always kept fresh after an event is processed.
    if customer is not None:
        risk_engine.recompute(customer, reason=f"After {event.event_type}")

    event.status = "PROCESSED"
    event.processed_at = utcnow()
    db.session.commit()
    return event
