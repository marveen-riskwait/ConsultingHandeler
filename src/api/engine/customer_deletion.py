"""Customer deletion — for erroneous records only, never for tidying up.

AML record-keeping (FATF R.11, EU AMLR) requires CDD records and the analysis
behind decisions to be retained for years. So this is deliberately narrow:

- `blockers()` refuses to erase a customer that carries real compliance
  history — a confirmed screening match, a closed/decided case, a completed
  review or a filed provider result. Those files must be kept (archive them
  instead: status = ARCHIVED).
- Everything else — the duplicates and mistakes the user actually wants gone —
  is deleted with its dependent rows, under a required reason.
- The AuditEvent trail is NEVER deleted: audit rows reference the customer by
  id/type without a foreign key, so the record of "who deleted what and why"
  outlives the customer, which is exactly what an auditor needs.
"""
from api.models import (
    db, Customer, Document, RiskAssessment, ProfileField, RequirementInstance,
    ScreeningRun, ScreeningMatch, Case, Task, Notification, ComplianceEvent,
    ComplianceAlert, Review, Party, Address, OwnershipRelationship,
    NormalizedComplianceResult, RawProviderResponse, WorkflowInstance,
    Conversation, ACTIVE_MATCH_STATUSES, utcnow,
)
from api.engine import audit


def blockers(customer):
    """Reasons this customer must be archived rather than deleted."""
    out = []

    confirmed = (ScreeningMatch.query
                 .filter_by(customer_id=customer.id, status="CONFIRMED")
                 .count())
    if confirmed:
        out.append(f"{confirmed} confirmed screening match(es) — sanctions/PEP "
                   "findings must be retained")

    decided = (Case.query
               .filter(Case.customer_id == customer.id,
                       Case.status.in_(["CLOSED", "APPROVED", "REJECTED",
                                        "ESCALATED"]))
               .count())
    if decided:
        out.append(f"{decided} decided/escalated case(s) — the decision record "
                   "must be retained")

    completed_reviews = (Review.query
                         .filter_by(customer_id=customer.id, status="COMPLETED")
                         .count())
    if completed_reviews:
        out.append(f"{completed_reviews} completed KYC review(s) must be retained")

    filed = (NormalizedComplianceResult.query
             .filter_by(customer_id=customer.id).count())
    if filed:
        out.append(f"{filed} provider verification result(s) on file")

    return out


def _delete_parties(customer):
    """Ownership graph: edges, addresses, then the parties themselves."""
    parties = Party.query.filter_by(customer_id=customer.id).all()
    ids = [p.id for p in parties]
    if customer.root_party_id and customer.root_party_id not in ids:
        ids.append(customer.root_party_id)
    if not ids:
        return
    (OwnershipRelationship.query
     .filter((OwnershipRelationship.owner_party_id.in_(ids)) |
             (OwnershipRelationship.owned_party_id.in_(ids)))
     .delete(synchronize_session=False))
    Address.query.filter(Address.party_id.in_(ids)).delete(
        synchronize_session=False)
    # Break the customer -> party FK before removing the parties.
    customer.root_party_id = None
    db.session.flush()
    Party.query.filter(Party.id.in_(ids)).delete(synchronize_session=False)


def delete_customer(customer, actor, reason, force=False):
    """Delete a customer and its dependent records. Raises ValueError when
    retention rules block it (unless `force`, reserved for admins)."""
    found = blockers(customer)
    if found and not force:
        raise ValueError(
            "This customer has compliance history that must be retained: "
            + "; ".join(found)
            + ". Archive the customer instead of deleting it.")

    cid, name = customer.id, customer.name

    # Cases first — alerts, matches, tasks and workflow instances point at them.
    case_ids = [c.id for c in Case.query.filter_by(customer_id=cid).all()]
    if case_ids:
        WorkflowInstance.query.filter(
            WorkflowInstance.case_id.in_(case_ids)).delete(
                synchronize_session=False)
    for model in (ComplianceAlert, ScreeningMatch, Task, Notification,
                  ComplianceEvent, Review, RequirementInstance, ProfileField,
                  Document, RiskAssessment, ScreeningRun, Conversation,
                  RawProviderResponse, NormalizedComplianceResult):
        model.query.filter_by(customer_id=cid).delete(synchronize_session=False)
    Case.query.filter_by(customer_id=cid).delete(synchronize_session=False)

    _delete_parties(customer)

    db.session.delete(customer)
    # Audit survives the customer — that is the point of an audit trail.
    audit.record("CUSTOMER_DELETED", "customer", cid, actor=actor,
                 old_value=name, reason=reason,
                 metadata={"forced": bool(force),
                           "blockers_overridden": found if force else []},
                 commit=True)
    return {"deleted": True, "customer_id": cid, "name": name}


def archive_customer(customer, actor, reason):
    """The safe alternative: keep everything, take it out of the active book.
    Stamps archived_at — the start of the retention clock."""
    old = customer.status
    customer.status = "ARCHIVED"
    customer.archived_at = utcnow()
    audit.record("CUSTOMER_ARCHIVED", "customer", customer.id, actor=actor,
                 old_value=old, new_value="ARCHIVED", reason=reason,
                 commit=True)
    return customer


def restore_customer(customer, actor, reason):
    """Undo an archive — what makes "remove from the workspace" reversible.
    Clears archived_at: the relationship is active again, so is its clock."""
    old = customer.status
    customer.status = "ACTIVE"
    customer.archived_at = None
    audit.record("CUSTOMER_RESTORED", "customer", customer.id, actor=actor,
                 old_value=old, new_value="ACTIVE", reason=reason, commit=True)
    return customer
