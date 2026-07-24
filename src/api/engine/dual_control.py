"""Dual-control engine: policy check, request lifecycle, and the executor
registry that actually runs an act once a checker approves it.

Maker-checker is enforced structurally: approve() refuses when the checker is
the maker (requested_by), exactly as the SAR flow does — a held request is
never a rubber stamp for its own author.
"""
from api.models import (db, DualControlPolicy, DualControlRequest, Customer,
                        utcnow)
from api.engine import audit


def is_required(organization_id, action_type):
    policy = (DualControlPolicy.query
              .filter_by(organization_id=organization_id, action_type=action_type)
              .first())
    return bool(policy and policy.enabled)


def set_policy(organization_id, action_type, enabled, actor=None):
    policy = (DualControlPolicy.query
              .filter_by(organization_id=organization_id, action_type=action_type)
              .first())
    if policy is None:
        policy = DualControlPolicy(organization_id=organization_id,
                                   action_type=action_type)
        db.session.add(policy)
    policy.enabled = bool(enabled)
    audit.record("DUAL_CONTROL_POLICY_SET", "organization", organization_id,
                 actor=actor,
                 new_value=f"{action_type}={'on' if enabled else 'off'}",
                 commit=True)
    return policy


def open_request(organization_id, action_type, *, target_type=None,
                 target_id=None, params=None, reason=None, summary=None,
                 actor=None):
    req = DualControlRequest(
        organization_id=organization_id, action_type=action_type,
        target_type=target_type, target_id=target_id, params=params or {},
        reason=reason, summary=summary, status="PENDING",
        requested_by=actor.id if actor else None)
    db.session.add(req)
    audit.record("DUAL_CONTROL_REQUESTED", "dual_control", None, actor=actor,
                 new_value=f"{action_type} · {summary or ''}", commit=True)
    return req


# --------------------------------------------------------------------------- #
# Executor registry — approval runs the real act.
# --------------------------------------------------------------------------- #
def _execute_customer_delete(req, checker):
    from api.engine import customer_deletion
    customer = Customer.query.get(req.target_id)
    if customer is None:
        return "Customer already gone."
    maker_id = req.requested_by
    reason = (req.params or {}).get("reason") or req.reason or "Dual-control deletion"
    reason = f"{reason} (dual control — maker #{maker_id}, checker #{checker.id})"
    customer_deletion.delete_customer(
        customer, checker, reason, force=bool((req.params or {}).get("force")))
    return f"Customer {req.target_id} deleted under dual control."


_EXECUTORS = {
    "CUSTOMER_DELETE": _execute_customer_delete,
}


def approve(req, checker):
    """Checker approves; the act runs. Four-eyes: checker must differ from
    the maker. Returns the (updated) request."""
    if req.status != "PENDING":
        raise ValueError("Request is not pending")
    if checker and req.requested_by == checker.id:
        raise PermissionError(
            "Four-eyes: you cannot approve a request you made yourself")
    executor = _EXECUTORS.get(req.action_type)
    if executor is None:
        raise ValueError(f"No executor for {req.action_type}")
    try:
        note = executor(req, checker)
        req.status = "EXECUTED"
        req.result_note = note
    except Exception as exc:                       # execution failed downstream
        req.status = "FAILED"
        req.result_note = str(exc)
        req.decided_by = checker.id if checker else None
        req.decided_at = utcnow()
        audit.record("DUAL_CONTROL_FAILED", "dual_control", req.id, actor=checker,
                     new_value=req.action_type, reason=str(exc), commit=True)
        raise
    req.decided_by = checker.id if checker else None
    req.decided_at = utcnow()
    audit.record("DUAL_CONTROL_APPROVED", "dual_control", req.id, actor=checker,
                 new_value=f"{req.action_type} executed", commit=True)
    return req


def reject(req, checker, reason=""):
    if req.status != "PENDING":
        raise ValueError("Request is not pending")
    req.status = "REJECTED"
    req.rejection_reason = reason
    req.decided_by = checker.id if checker else None
    req.decided_at = utcnow()
    audit.record("DUAL_CONTROL_REJECTED", "dual_control", req.id, actor=checker,
                 new_value=req.action_type, reason=reason, commit=True)
    return req
