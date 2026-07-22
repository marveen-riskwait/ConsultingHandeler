"""Requirement engine — what a customer must provide, and what's still missing.

    Customer profile (type / risk / jurisdiction)
        -> applicable RequirementDefinitions
        -> compare with received data (ProfileField) + documents (Document)
        -> RequirementInstance status + completeness %
        -> (on request) Task / Notification + MISSING_INFORMATION_DETECTED

Computed BEFORE the consultant opens the review — the document's key
time-saving feature.
"""
from datetime import timedelta

from api.models import (
    db, Customer, Document, ProfileField, RequirementDefinition,
    RequirementInstance, Task, RISK_RANK, utcnow,
)
from api.engine import audit
from api.engine.events import emit_event, recipients_for, notify_users


def applicable_definitions(customer):
    """System (org-null) + this org's definitions that apply to the customer."""
    rank = RISK_RANK.get(customer.risk_level, 0)
    defs = (RequirementDefinition.query
            .filter(RequirementDefinition.active.is_(True))
            .filter((RequirementDefinition.organization_id == customer.organization_id) |
                    (RequirementDefinition.organization_id.is_(None)))
            .all())
    out = []
    for d in defs:
        if d.applies_customer_type != "ANY" and d.applies_customer_type != customer.customer_type:
            continue
        if rank < (d.min_risk_rank or 0):
            continue
        if d.jurisdiction and d.jurisdiction != (customer.country or ""):
            continue
        out.append(d)
    return out


def _status_for(customer, d):
    if d.kind == "DATA":
        f = (ProfileField.query
             .filter_by(customer_id=customer.id, field_key=d.data_field).first())
        if f is None or f.value in (None, ""):
            return "MISSING"
        return "VERIFIED" if f.verified else "RECEIVED"
    # DOCUMENT — a row without a file is a document we are still waiting for,
    # not evidence. Counting it would inflate completeness against nothing.
    docs = (Document.query
            .filter_by(customer_id=customer.id, doc_type=d.doc_type).all())
    with_file = [doc for doc in docs if doc.file_url]
    if not with_file:
        return "MISSING"
    if any(doc.status == "VERIFIED" for doc in with_file):
        return "VERIFIED"
    return "RECEIVED"


def evaluate(customer):
    """Recompute RequirementInstances for the customer; returns the instances."""
    applicable = applicable_definitions(customer)
    applicable_codes = {d.code for d in applicable}

    existing = {ri.code: ri for ri in
                RequirementInstance.query.filter_by(customer_id=customer.id).all()}

    for d in applicable:
        status = _status_for(customer, d)
        ri = existing.get(d.code)
        if ri is None:
            ri = RequirementInstance(customer_id=customer.id, definition_id=d.id,
                                     code=d.code, label=d.label, kind=d.kind)
            db.session.add(ri)
        elif ri.status == "WAIVED":
            continue  # a human waiver stands
        ri.definition_id = d.id
        ri.label = d.label
        ri.kind = d.kind
        ri.status = status

    # Drop instances that no longer apply (unless explicitly waived).
    for code, ri in existing.items():
        if code not in applicable_codes and ri.status != "WAIVED":
            db.session.delete(ri)

    db.session.commit()
    _close_satisfied_requests(customer)
    return (RequirementInstance.query.filter_by(customer_id=customer.id)
            .order_by(RequirementInstance.kind, RequirementInstance.code).all())


def _close_satisfied_requests(customer):
    """Close the information-request tasks whose item has arrived.

    The chain only ever fired forwards: something missing opened a task, and
    nothing closed it when the customer sent it in. The visible cost is not the
    stale row — it is an analyst chasing a client who already complied, which
    is the one mistake a compliance team cannot afford to make twice.
    """
    satisfied = {ri.code for ri in
                 RequirementInstance.query.filter_by(customer_id=customer.id).all()
                 if ri.status != "MISSING"}
    if not satisfied:
        return 0

    open_tasks = (Task.query
                  .filter_by(customer_id=customer.id,
                             task_type="INFORMATION_REQUEST")
                  .filter(Task.status != "DONE").all())
    closed = 0
    for task in open_tasks:
        code = task.requirement_code
        if code is None:                       # tasks created before the link
            code = next((c for c in satisfied if f"({c})" in (task.title or "")), None)
        if code and code in satisfied:
            task.status = "DONE"
            audit.record("TASK_COMPLETED", "task", task.id,
                         new_value="DONE",
                         reason=f"{code} was provided by the customer")
            closed += 1
    if closed:
        db.session.commit()
    return closed


def summary(customer):
    instances = evaluate(customer)
    total = len(instances) or 1
    satisfied = sum(1 for ri in instances if ri.status in ("VERIFIED", "RECEIVED", "WAIVED"))
    missing = [ri for ri in instances if ri.status == "MISSING"]
    return {
        "completeness_pct": round(100 * satisfied / total),
        "total": len(instances),
        "satisfied": satisfied,
        "missing_count": len(missing),
        "missing": [ri.serialize() for ri in missing],
        "requirements": [ri.serialize() for ri in instances],
    }


def _notify_customer_portal(customer):
    """Best effort: tell the customer something is waiting, nothing more."""
    try:
        from api.portal import notify_customer
        notify_customer(customer, what="some information")
    except Exception:
        pass          # a mail problem must never fail a compliance action


def request_missing_info(customer, actor=None):
    """Create one information-request task per missing requirement, notify the
    responsible team, and emit MISSING_INFORMATION_DETECTED once."""
    instances = evaluate(customer)
    missing = [ri for ri in instances if ri.status == "MISSING"]
    if not missing:
        return {"created": 0, "missing": 0}

    created = 0
    for ri in missing:
        exists = (Task.query.filter_by(customer_id=customer.id,
                                       task_type="INFORMATION_REQUEST")
                  .filter(db.or_(Task.requirement_code == ri.code,
                                 Task.title.like(f"%{ri.code}%")))
                  .filter(Task.status != "DONE").first())
        if exists:
            continue
        db.session.add(Task(
            customer_id=customer.id,
            task_type="INFORMATION_REQUEST",
            title=f"Request missing: {ri.label} ({ri.code})",
            requirement_code=ri.code,
            priority="MEDIUM",
            due_at=utcnow() + timedelta(days=10),
        ))
        created += 1

    users = recipients_for(customer, ["ANALYST", "KYC_ANALYST"])
    notify_users(users, severity="MEDIUM",
                 title="Missing information",
                 message=f"{len(missing)} requirement(s) missing for {customer.name}.",
                 customer_id=customer.id, requires_action=True)
    audit.record("INFORMATION_REQUESTED", "customer", customer.id, actor=actor,
                 new_value=", ".join(ri.code for ri in missing))
    db.session.commit()

    emit_event("MISSING_INFORMATION_DETECTED", customer_id=customer.id,
               severity="MEDIUM", source="requirement_engine", actor=actor,
               payload={"missing": [ri.code for ri in missing]})
    # The team now has tasks; the customer needs to know something is waiting.
    _notify_customer_portal(customer)
    return {"created": created, "missing": len(missing)}
