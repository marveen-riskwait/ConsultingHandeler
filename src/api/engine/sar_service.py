"""SAR/STR lifecycle: draft -> approval (four-eyes) -> submitted, + goAML export.

Four-eyes is enforced structurally, not by convention: the account that drafts
a report is stored on it, and approve() refuses that same account. The goAML
export produces the UNODC report shape most FIUs ingest — a production
deployment pins it to the exact FIU XSD version, the way watchlists pin to a
live source; the structure here is faithful, not vendor-locked.
"""
from datetime import datetime
from xml.etree import ElementTree as ET
from xml.dom import minidom

from api.models import (db, SuspiciousActivityReport, Customer, Transaction,
                        Organization, User, utcnow)
from api.engine import audit


def _next_reference(organization_id):
    year = utcnow().year
    n = (SuspiciousActivityReport.query
         .filter_by(organization_id=organization_id).count()) + 1
    return f"SAR-{year}-{n:04d}"


def create_draft(customer, *, report_type="STR", reason="", indicators=None,
                 transaction_ids=None, case_id=None, actor=None):
    sar = SuspiciousActivityReport(
        organization_id=customer.organization_id,
        customer_id=customer.id,
        case_id=case_id,
        reference=_next_reference(customer.organization_id),
        report_type=report_type if report_type in ("SAR", "STR") else "STR",
        status="DRAFT",
        reason=reason,
        indicators=indicators or [],
        transaction_ids=transaction_ids or [],
        created_by=actor.id if actor else None,
    )
    db.session.add(sar)
    audit.record("SAR_DRAFTED", "sar", None, actor=actor,
                 new_value=f"{sar.reference} ({sar.report_type})", commit=True)
    return sar


def update_draft(sar, *, reason=None, indicators=None, transaction_ids=None,
                 report_type=None, actor=None):
    if sar.status not in ("DRAFT", "REJECTED"):
        raise ValueError("Only a draft or rejected report can be edited")
    if reason is not None:
        sar.reason = reason
    if indicators is not None:
        sar.indicators = indicators
    if transaction_ids is not None:
        sar.transaction_ids = transaction_ids
    if report_type in ("SAR", "STR"):
        sar.report_type = report_type
    if sar.status == "REJECTED":
        sar.status = "DRAFT"
        sar.rejection_reason = None
    db.session.commit()
    return sar


def submit_for_approval(sar, actor=None):
    if sar.status not in ("DRAFT", "REJECTED"):
        raise ValueError("Report is not a draft")
    if not (sar.reason or "").strip():
        raise ValueError("A reason (grounds for suspicion) is required")
    sar.status = "PENDING_APPROVAL"
    audit.record("SAR_SUBMITTED_FOR_APPROVAL", "sar", sar.id, actor=actor,
                 new_value=sar.reference, commit=True)
    return sar


def approve(sar, actor):
    """Four-eyes: the approver must differ from the drafter."""
    if sar.status != "PENDING_APPROVAL":
        raise ValueError("Report is not awaiting approval")
    if actor and sar.created_by == actor.id:
        raise PermissionError(
            "Four-eyes: the report's author cannot approve their own SAR")
    sar.status = "APPROVED"
    sar.approved_by = actor.id if actor else None
    audit.record("SAR_APPROVED", "sar", sar.id, actor=actor,
                 new_value=sar.reference, commit=True)
    return sar


def reject(sar, actor, reason=""):
    if sar.status != "PENDING_APPROVAL":
        raise ValueError("Report is not awaiting approval")
    sar.status = "REJECTED"
    sar.rejection_reason = reason
    audit.record("SAR_REJECTED", "sar", sar.id, actor=actor,
                 new_value=sar.reference, reason=reason, commit=True)
    return sar


def mark_submitted(sar, actor=None):
    """Record that the approved report was filed with the FIU."""
    if sar.status != "APPROVED":
        raise ValueError("Only an approved report can be marked submitted")
    sar.status = "SUBMITTED"
    sar.submitted_at = utcnow()
    audit.record("SAR_FILED", "sar", sar.id, actor=actor,
                 new_value=sar.reference, commit=True)
    return sar


# --------------------------------------------------------------------------- #
# goAML XML export
# --------------------------------------------------------------------------- #
def _txt(parent, tag, value):
    el = ET.SubElement(parent, tag)
    el.text = "" if value is None else str(value)
    return el


def build_goaml_xml(sar):
    """Produce a goAML-shaped report XML (UNODC schema family).

    Faithful to the goAML structure — report header, reporting entity, reason,
    indicators, the subject party and the linked transactions — without pinning
    to one FIU's exact XSD version (that mapping is a deployment concern)."""
    customer = Customer.query.get(sar.customer_id)
    org = Organization.query.get(sar.organization_id)
    drafter = User.query.get(sar.created_by) if sar.created_by else None

    report = ET.Element("report")
    _txt(report, "rentity_id", sar.organization_id)
    _txt(report, "submission_code", "E")            # E = electronic
    _txt(report, "report_code", sar.report_type)    # STR / SAR
    _txt(report, "entity_reference", sar.reference)
    _txt(report, "submission_date",
         (sar.submitted_at or utcnow()).strftime("%Y-%m-%dT%H:%M:%S"))
    _txt(report, "reason", sar.reason or "")

    rentity = ET.SubElement(report, "reporting_entity")
    _txt(rentity, "name", org.name if org else "")
    if drafter:
        _txt(rentity, "reporting_person", drafter.full_name or drafter.email)

    # The subject of the report.
    activity = ET.SubElement(report, "activity")
    ind = ET.SubElement(activity, "report_indicators")
    for code in (sar.indicators or []):
        _txt(ind, "indicator", code)

    party = ET.SubElement(activity, "report_party")
    account = ET.SubElement(party, "account")
    _txt(account, "institution_name", org.name if org else "")
    signatory = ET.SubElement(party, "signatory")
    is_company = (customer.customer_type in ("COMPANY", "TRUST")) if customer else False
    entity_or_person = ET.SubElement(signatory,
                                     "entity" if is_company else "person")
    if is_company:
        _txt(entity_or_person, "name", customer.name if customer else "")
        _txt(entity_or_person, "incorporation_country_code",
             (customer.country or "") if customer else "")
    else:
        _txt(entity_or_person, "full_name", customer.name if customer else "")
        _txt(entity_or_person, "residence", (customer.country or "") if customer else "")

    # The transactions being reported.
    txns = (Transaction.query
            .filter(Transaction.id.in_(sar.transaction_ids or [0])).all())
    for t in txns:
        tel = ET.SubElement(report, "transaction")
        _txt(tel, "transactionnumber", t.external_id or f"TX{t.id}")
        _txt(tel, "transaction_direction",
             "I" if t.direction == "INBOUND" else "O")
        _txt(tel, "date_transaction",
             t.booked_at.strftime("%Y-%m-%dT%H:%M:%S") if t.booked_at else "")
        _txt(tel, "amount_local", t.amount_base)
        _txt(tel, "transmode_code", t.method or "OTHER")
        if t.counterparty_name:
            cp = ET.SubElement(tel, "t_from_my_client")
            _txt(cp, "from_funds_code", t.currency)
            _txt(cp, "from_country", t.counterparty_country or "")

    raw = ET.tostring(report, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
