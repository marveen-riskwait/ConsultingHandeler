"""Customer lifecycle operations — currently: hard deletion.

Deleting a customer is a compliance-sensitive act (record-keeping rules
normally require retaining CDD files for years), so it is:
- gated behind its own permission (customer.delete — ADMIN/managers only by
  default, grantable per-user via the permissions UI);
- audited BEFORE the rows disappear, with the operator's mandatory reason —
  the audit trail itself is never deleted (AuditEvent stores plain entity
  ids, not FKs), so WHO deleted WHAT, WHEN and WHY remains answerable;
- a deep purge: every dependent compliance record goes with the file, in
  FK-safe order. Copilot conversations are detached, not deleted — they
  belong to the user who had them.
"""
from api.models import (
    db, Customer, Document, RiskAssessment, ComplianceEvent, Case, Task,
    Notification, ScreeningRun, ScreeningMatch, ComplianceAlert, Review,
    ProfileField, RequirementInstance, Party, Address, OwnershipRelationship,
    RawProviderResponse, NormalizedComplianceResult, Conversation,
    WorkflowInstance, WorkflowStepState, Approval,
)
from api.engine import audit


def delete_customer(customer, actor, reason):
    """Remove a customer and every dependent compliance record. Audited."""
    cid = customer.id
    name = customer.name

    # The audit entry is written first (committed with the same transaction);
    # it references the id/name as plain values, so it survives the purge.
    audit.record("CUSTOMER_DELETED", "customer", cid, actor=actor,
                 old_value=name, reason=reason,
                 metadata={"customer_type": customer.customer_type,
                           "risk_level": customer.risk_level,
                           "status": customer.status})

    # Break the customer -> root party circular FK before touching parties.
    customer.root_party_id = None
    db.session.flush()

    def _purge(query):
        query.delete(synchronize_session=False)

    # Screening (matches reference runs + cases: matches first).
    _purge(ScreeningMatch.query.filter_by(customer_id=cid))
    _purge(ScreeningRun.query.filter_by(customer_id=cid))

    # Alerts before their triggering events; reviews; KYC data.
    _purge(ComplianceAlert.query.filter_by(customer_id=cid))
    _purge(Review.query.filter_by(customer_id=cid))
    _purge(RequirementInstance.query.filter_by(customer_id=cid))
    _purge(ProfileField.query.filter_by(customer_id=cid))

    # Workflow state h