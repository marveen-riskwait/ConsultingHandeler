"""Data retention & subject access (GDPR).

Two duties pull in opposite directions and the platform must serve both:
 - the right of access (Art. 15): hand the data subject everything held on
   them, on demand — data_export();
 - storage limitation vs AML record-keeping: keep files for the legal
   retention period after a relationship ends, then purge them — the
   purge_candidates / purge_due pair.

Retention is the organisation's data_retention_months (AMLD/FATF default 60 =
5 years), counted from archived_at. The audit trail is never purged: who
deleted what, when and why survives every erasure.
"""
from datetime import timedelta

from api.models import (db, Customer, Organization, Document, Address,
                        ProfileField, ScreeningMatch, Case, Task, Transaction,
                        ComplianceAlert, Review, SuspiciousActivityReport,
                        AuditEvent, utcnow)
from api.engine import audit, customer_deletion, ownership


def set_retention(organization_id, months, actor=None):
    org = Organization.query.get(organization_id)
    if org is None:
        raise ValueError("Organization not found")
    months = max(0, int(months))
    org.data_retention_months = months
    audit.record("RETENTION_POLICY_SET", "organization", organization_id,
                 actor=actor, new_value=f"{months} months", commit=True)
    return org


def _cutoff(organization_id):
    org = Organization.query.get(organization_id)
    months = (org.data_retention_months if org else 60) or 0
    # Rough month arithmetic is fine for a retention horizon.
    return utcnow() - timedelta(days=months * 30), months


def purge_candidates(organization_id):
    """Archived customers whose retention period has elapsed."""
    cutoff, months = _cutoff(organization_id)
    rows = (Customer.query
            .filter(Customer.organization_id == organization_id,
                    Customer.status == "ARCHIVED",
                    Customer.archived_at.isnot(None),
                    Customer.archived_at <= cutoff)
            .all())
    return rows, months


def purge_due(organization_id, actor=None, dry_run=False):
    """Erase every archived customer past retention (audit trail kept).

    Uses force: the retention guard exists to prevent PREMATURE deletion; the
    end of the legal retention period is exactly when erasure is due."""
    rows, months = purge_candidates(organization_id)
    purged = []
    for c in rows:
        entry = {"id": c.id, "name": c.name,
                 "archived_at": c.archived_at.isoformat() if c.archived_at else None}
        if not dry_run:
            customer_deletion.delete_customer(
                c, actor, f"Data retention period elapsed ({months} months)",
                force=True)
        purged.append(entry)
    if purged and not dry_run:
        audit.record("RETENTION_PURGE_RUN", "organization", organization_id,
                     actor=actor, new_value=f"{len(purged)} record(s) purged",
                     commit=True)
    return {"count": len(purged), "months": months, "dry_run": dry_run,
            "customers": purged}


# --------------------------------------------------------------------------- #
# Subject access export (right of access)
# --------------------------------------------------------------------------- #
def data_export(customer):
    """Everything the platform holds on this customer, as one JSON structure —
    the deliverable for a data-subject access request."""
    cid = customer.id

    def dump(rows):
        return [r.serialize() for r in rows]

    graph = ownership.build_graph(customer)
    export = {
        "exported_at": utcnow().isoformat(),
        "customer": customer.serialize(),
        "profile_fields": dump(ProfileField.query.filter_by(customer_id=cid).all()),
        "addresses": ([a.serialize() for a in
                       Address.query.filter_by(party_id=customer.root_party_id).all()]
                      if customer.root_party_id else []),
        "ownership": {"ubos": ownership.compute_ubos(customer),
                      "graph": graph},
        "documents": dump(Document.query.filter_by(customer_id=cid).all()),
        "screening_matches": dump(ScreeningMatch.query.filter_by(customer_id=cid).all()),
        "cases": dump(Case.query.filter_by(customer_id=cid).all()),
        "tasks": dump(Task.query.filter_by(customer_id=cid).all()),
        "transactions": dump(Transaction.query.filter_by(customer_id=cid).all()),
        "alerts": dump(ComplianceAlert.query.filter_by(customer_id=cid).all()),
        "reviews": dump(Review.query.filter_by(customer_id=cid).all()),
        "reports": dump(SuspiciousActivityReport.query.filter_by(customer_id=cid).all()),
        # The audit trail entries that reference this customer — the record of
        # who did what, part of the access right.
        "audit_trail": dump(AuditEvent.query
                            .filter_by(entity_type="customer", entity_id=cid)
                            .order_by(AuditEvent.created_at.asc()).all()),
    }
    audit.record("DATA_EXPORTED", "customer", cid,
                 new_value="subject access export", commit=True)
    return export
