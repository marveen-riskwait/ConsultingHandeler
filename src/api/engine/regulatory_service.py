"""Regulatory Intelligence service.

A regulatory change becomes actionable: registering one emits a
REGULATORY_REQUIREMENT_CHANGED event (so rules/notifications fire) and an
ImpactAssessment maps it to the requirements, controls, workflows and customers
it touches.
"""
from api.models import (
    db, RegulatorySource, RegulatoryRequirement, ComplianceControl,
    RegulatoryChange, ImpactAssessment, WorkflowDefinition, Customer, utcnow,
)
from api.engine import audit
from api.engine.events import emit_event


def register_change(organization_id, source, title, summary, impact_level="MEDIUM",
                    actor=None):
    change = RegulatoryChange(organization_id=organization_id,
                              source_id=source.id if source else None,
                              title=title, summary=summary,
                              impact_level=impact_level, status="NEW")
    db.session.add(change)
    audit.record("REGULATORY_CHANGE_DETECTED", "regulatory_change", None,
                 actor=actor, new_value=title,
                 reason=source.name if source else None)
    db.session.commit()

    emit_event("REGULATORY_REQUIREMENT_CHANGED", customer_id=None,
               severity="HIGH" if impact_level == "HIGH" else "MEDIUM",
               source="regulatory_intelligence", actor=actor,
               payload={"change_id": change.id, "title": title,
                        "impact_level": impact_level,
                        "organization_id": organization_id})
    return change


def assess_impact(change, actor=None, notes=None):
    """Compute what the change touches: requirements & controls under its source,
    the workflows in place, and the customer population."""
    req_ids, control_ids = [], []
    if change.source_id:
        reqs = RegulatoryRequirement.query.filter_by(source_id=change.source_id).all()
        req_ids = [r.id for r in reqs]
        for r in reqs:
            control_ids += [c.id for c in r.controls]

    workflows = [w.code for w in WorkflowDefinition.query.filter(
        (WorkflowDefinition.organization_id == change.organization_id) |
        (WorkflowDefinition.organization_id.is_(None))).all()]
    customer_count = (Customer.query
                      .filter_by(organization_id=change.organization_id).count()
                      if change.organization_id else Customer.query.count())

    assessment = change.assessment or ImpactAssessment(change_id=change.id)
    assessment.affected_requirement_ids = req_ids
    assessment.affected_control_ids = list(set(control_ids))
    assessment.affected_workflow_codes = workflows
    assessment.affected_customer_count = customer_count
    assessment.notes = notes
    assessment.assessed_by = actor.id if actor else None
    if change.assessment is None:
        db.session.add(assessment)
    change.status = "ASSESSED"
    audit.record("REGULATORY_IMPACT_ASSESSED", "regulatory_change", change.id,
                 actor=actor,
                 new_value=f"{len(req_ids)} req, {len(set(control_ids))} controls, "
                           f"{customer_count} customers", commit=True)
    return assessment


def dashboard(organization_id):
    def scoped(model):
        return model.query.filter((model.organization_id == organization_id) |
                                  (model.organization_id.is_(None)))

    sources = scoped(RegulatorySource).all()
    changes = (scoped(RegulatoryChange)
               .order_by(RegulatoryChange.detected_at.desc()).all())
    controls = scoped(ComplianceControl).all()
    status_counts = {}
    for c in controls:
        status_counts[c.implementation_status] = status_counts.get(
            c.implementation_status, 0) + 1
    impact_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for ch in changes:
        impact_counts[ch.impact_level] = impact_counts.get(ch.impact_level, 0) + 1

    return {
        "sources": [s.serialize() for s in sources],
        "recent_changes": [ch.serialize(with_assessment=True) for ch in changes[:20]],
        "controls": [c.serialize() for c in controls],
        "control_status_counts": status_counts,
        "impact_counts": impact_counts,
        "requirement_count": RegulatoryRequirement.query.filter(
            RegulatoryRequirement.source_id.in_([s.id for s in sources] or [0])).count(),
    }
