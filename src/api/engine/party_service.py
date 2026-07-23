"""Party service — KYB mutations that feed the compliance spine.

Adding an owner/director or changing an address is DATA; this service turns it
into EVENTS (OWNERSHIP_CHANGED / DIRECTOR_CHANGED / UBO_CHANGED /
ADDRESS_CHANGED) so the rules engine reacts, risk is recomputed, and the
consultant is notified — the document's continuous-compliance philosophy.
"""
from api.models import (
    db, Person, LegalEntity, Trust, Party, Address, OwnershipRelationship,
    utcnow,
)
from api.engine import audit, ownership
from api.engine.events import emit_event


def _party_class(kind):
    if kind == "ORGANIZATION":
        return LegalEntity
    if kind == "TRUST":
        return Trust
    return Person


def ensure_root_party(customer):
    if customer.root_party_id:
        return Party.query.get(customer.root_party_id)
    kind = {"COMPANY": "ORGANIZATION", "TRUST": "TRUST"}.get(
        customer.customer_type, "PERSON")
    cls = _party_class(kind)
    party = cls(
        organization_id=customer.organization_id,
        name=customer.name, customer_id=customer.id,
        business_activity=customer.business_activity,
        country_of_incorporation=customer.country if cls is not Person else None,
        country_of_residence=customer.country if cls is Person else None,
    )
    db.session.add(party)
    db.session.flush()
    customer.root_party_id = party.id
    db.session.commit()
    return party


def _ubo_snapshot(customer):
    return {u["party"]["name"] for u in ownership.compute_ubos(customer) if u["is_ubo"]}


def add_related_party(customer, *, owner_name, owner_kind="PERSON",
                      relationship_type="SHAREHOLDER", percentage=0.0,
                      control_type=None, country=None, nationality=None,
                      owned_party_id=None, actor=None):
    """Create a party + relationship edge and emit the right change events.

    Returns (owner_party, edge, emitted_event_types).
    """
    root = ensure_root_party(customer)
    ubos_before = _ubo_snapshot(customer)

    cls = _party_class(owner_kind)
    owner = cls(
        organization_id=customer.organization_id,
        name=owner_name,
        nationality=nationality,
        country_of_residence=country if cls is Person else None,
        country_of_incorporation=country if cls is LegalEntity else None,
    )
    db.session.add(owner)
    db.session.flush()

    edge = OwnershipRelationship(
        organization_id=customer.organization_id,
        owner_party_id=owner.id,
        owned_party_id=owned_party_id or root.id,
        relationship_type=relationship_type,
        percentage=float(percentage or 0),
        control_type=control_type,
    )
    db.session.add(edge)
    audit.record("OWNERSHIP_ADDED", "customer", customer.id, actor=actor,
                 new_value=f"{owner_name} ({relationship_type} {edge.percentage}%)",
                 reason="KYB")

    # Graph shape may have changed: derive complex_ownership BEFORE the event
    # is processed so the risk recompute sees the fresh value.
    customer.complex_ownership = ownership.is_complex(customer)
    db.session.commit()

    emitted = []
    if relationship_type == "DIRECTOR":
        emit_event("DIRECTOR_CHANGED", customer_id=customer.id, severity="MEDIUM",
                   source="kyb", actor=actor,
                   payload={"director": owner_name, "party_id": owner.id})
        emitted.append("DIRECTOR_CHANGED")
    else:
        emit_event("OWNERSHIP_CHANGED", customer_id=customer.id, severity="MEDIUM",
                   source="kyb", actor=actor,
                   payload={"owner": owner_name,
                            "relationship_type": relationship_type,
                            "percentage": edge.percentage})
        emitted.append("OWNERSHIP_CHANGED")

    ubos_after = _ubo_snapshot(customer)
    if ubos_after != ubos_before:
        emit_event("UBO_CHANGED", customer_id=customer.id, severity="HIGH",
                   source="kyb", actor=actor,
                   payload={"added": sorted(ubos_after - ubos_before),
                            "removed": sorted(ubos_before - ubos_after)})
        emitted.append("UBO_CHANGED")

    return owner, edge, emitted


def remove_related_party(customer, edge_id, actor=None, reason=None):
    """Deactivate an ownership/director edge (history kept, never deleted) and
    emit the same change events as an addition — removing a bogus owner IS an
    ownership change the monitoring chain must see.

    Returns (edge, emitted_event_types)."""
    edge = OwnershipRelationship.query.get(edge_id)
    if (edge is None or not edge.active
            or edge.organization_id != customer.organization_id):
        return None, []
    # The edge must belong to THIS customer's graph, not merely the same org.
    graph_nodes = {n["id"] for n in ownership.build_graph(customer)["nodes"]}
    if edge.owned_party_id not in graph_nodes:
        return None, []

    ubos_before = _ubo_snapshot(customer)
    owner = Party.query.get(edge.owner_party_id)
    owner_name = owner.name if owner else f"party {edge.owner_party_id}"

    edge.active = False
    audit.record("OWNERSHIP_REMOVED", "customer", customer.id, actor=actor,
                 old_value=f"{owner_name} ({edge.relationship_type} "
                           f"{edge.percentage}%)",
                 reason=reason or "KYB correction")
    customer.complex_ownership = ownership.is_complex(customer)
    db.session.commit()

    emitted = []
    if edge.relationship_type == "DIRECTOR":
        emit_event("DIRECTOR_CHANGED", customer_id=customer.id, severity="MEDIUM",
                   source="kyb", actor=actor,
                   payload={"director": owner_name, "removed": True})
        emitted.append("DIRECTOR_CHANGED")
    else:
        emit_event("OWNERSHIP_CHANGED", customer_id=customer.id, severity="MEDIUM",
                   source="kyb", actor=actor,
                   payload={"owner": owner_name, "removed": True,
                            "relationship_type": edge.relationship_type,
                            "percentage": edge.percentage})
        emitted.append("OWNERSHIP_CHANGED")

    ubos_after = _ubo_snapshot(customer)
    if ubos_after != ubos_before:
        emit_event("UBO_CHANGED", customer_id=customer.id, severity="HIGH",
                   source="kyb", actor=actor,
                   payload={"added": sorted(ubos_after - ubos_before),
                            "removed": sorted(ubos_before - ubos_after)})
        emitted.append("UBO_CHANGED")

    return edge, emitted


def add_address(customer, *, line1, line2=None, city=None, postal_code=None,
                country=None, address_type="RESIDENTIAL", actor=None):
    """Add an address; a replacement of a current address of the same type
    closes the old one (history kept) and emits ADDRESS_CHANGED."""
    party = ensure_root_party(customer)
    previous = (Address.query
                .filter_by(party_id=party.id, address_type=address_type,
                           is_current=True).first())

    addr = Address(
        organization_id=customer.organization_id,
        party_id=party.id,
        address_type=address_type,
        line1=line1, line2=line2, city=city,
        postal_code=postal_code, country=country,
    )
    db.session.add(addr)

    if previous:
        previous.is_current = False
        previous.valid_to = utcnow()
        audit.record("ADDRESS_CHANGED", "party", party.id, actor=actor,
                     old_value=f"{previous.line1}, {previous.country or ''}",
                     new_value=f"{line1}, {country or ''}")
        db.session.commit()
        emit_event("ADDRESS_CHANGED", customer_id=customer.id, severity="LOW",
                   source="kyc", actor=actor,
                   payload={"address_type": address_type,
                            "old_country": previous.country,
                            "new_country": country})
    else:
        audit.record("ADDRESS_ADDED", "party", party.id, actor=actor,
                     new_value=f"{line1}, {country or ''}", commit=True)

    return addr
