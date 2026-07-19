"""Workflow engine — run a WorkflowDefinition against a case.

A case that matches a definition's applies_case_type auto-starts a workflow.
Steps advance one at a time; a step that requires approval cannot be completed
until an Approval has been granted by the required role.
"""
from api.models import (
    db, WorkflowDefinition, WorkflowInstance, WorkflowStepState, Approval,
    Case, utcnow,
)
from api.engine import audit


def start_for_case(case, organization_id, definition=None, actor=None):
    """Instantiate a workflow for a case (auto-called on case creation)."""
    if definition is None:
        definition = (WorkflowDefinition.query
                      .filter_by(applies_case_type=case.case_type, active=True)
                      .filter((WorkflowDefinition.organization_id == organization_id) |
                              (WorkflowDefinition.organization_id.is_(None)))
                      .order_by(WorkflowDefinition.organization_id.isnot(None).desc())
                      .first())
    if definition is None:
        return None
    if WorkflowInstance.query.filter_by(case_id=case.id,
                                        definition_id=definition.id).first():
        return None   # already running

    instance = WorkflowInstance(organization_id=organization_id,
                                definition_id=definition.id, case_id=case.id,
                                name=definition.name, status="IN_PROGRESS")
    db.session.add(instance)
    db.session.flush()

    steps = sorted(definition.steps, key=lambda s: s.order)
    for i, step in enumerate(steps):
        db.session.add(WorkflowStepState(
            instance_id=instance.id, step_id=step.id, order=step.order,
            code=step.code, name=step.name,
            requires_approval=step.requires_approval,
            approver_role=step.approver_role,
            status="ACTIVE" if i == 0 else "PENDING"))
    audit.record("WORKFLOW_STARTED", "workflow_instance", instance.id, actor=actor,
                 new_value=definition.name, reason=f"case {case.id}", commit=True)
    return instance


def _active_step(instance):
    return (WorkflowStepState.query
            .filter_by(instance_id=instance.id, status="ACTIVE").first())


def complete_current_step(instance, actor=None, note=None):
    step = _active_step(instance)
    if step is None:
        raise ValueError("No active step to complete")
    if step.requires_approval:
        approved = any(a.status == "APPROVED" for a in step.approvals)
        if not approved:
            raise PermissionError(
                f"Step '{step.name}' requires approval by {step.approver_role} "
                "before it can be completed")

    step.status = "DONE"
    step.completed_by = actor.id if actor else None
    step.completed_at = utcnow()
    step.note = note

    nxt = (WorkflowStepState.query
           .filter_by(instance_id=instance.id, status="PENDING")
           .order_by(WorkflowStepState.order).first())
    if nxt:
        nxt.status = "ACTIVE"
    else:
        instance.status = "COMPLETED"
        instance.completed_at = utcnow()

    audit.record("WORKFLOW_STEP_COMPLETED", "workflow_instance", instance.id,
                 actor=actor, new_value=step.name)
    db.session.commit()
    return instance


def decide_approval(instance, actor, decision, reason):
    """Record an approval decision on the current approval-gated step."""
    step = _active_step(instance)
    if step is None or not step.requires_approval:
        raise ValueError("No step awaiting approval")
    approval = Approval(instance_id=instance.id, step_state_id=step.id,
                        required_role=step.approver_role,
                        status="APPROVED" if decision == "APPROVE" else "REJECTED",
                        decided_by=actor.id if actor else None,
                        decided_at=utcnow(), reason=reason)
    db.session.add(approval)
    audit.record("WORKFLOW_APPROVAL", "workflow_instance", instance.id, actor=actor,
                 new_value=approval.status, reason=reason)
    if approval.status == "REJECTED":
        instance.status = "CANCELLED"
    db.session.commit()
    return approval
