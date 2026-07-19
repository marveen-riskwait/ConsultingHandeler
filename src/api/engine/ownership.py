"""Ownership graph & Ultimate Beneficial Owner computation.

Edges are directed: owner -> owned (by percentage). Starting from a customer's
root party, we walk *incoming* edges (who owns this?) down to natural persons,
multiplying percentages along each path and summing across paths.

    John Smith --80%--> Beta Holdings --60%--> Alpha (root)
    => John's effective ownership of Alpha = 0.80 * 0.60 = 48%

A person at or above UBO_THRESHOLD (25%) is flagged as a UBO.
"""
from api.models import Party, OwnershipRelationship, UBO_THRESHOLD


def _incoming(owned_id):
    return (OwnershipRelationship.query
            .filter_by(owned_party_id=owned_id, active=True)
            .all())


def compute_ubos(customer):
    root = customer.root_party_id
    if not root:
        return []

    person_pct = {}       # person party_id -> total effective fraction
    control_persons = set()

    def dfs(node_id, factor, path):
        for e in _incoming(node_id):
            owner_id = e.owner_party_id
            if owner_id in path:           # cycle guard
                continue
            owner = Party.query.get(owner_id)
            if owner is None:
                continue
            contribution = factor * ((e.percentage or 0) / 100.0)
            if owner.kind == "PERSON":
                person_pct[owner_id] = person_pct.get(owner_id, 0.0) + contribution
                if e.relationship_type in ("UBO", "CONTROL"):
                    control_persons.add(owner_id)
            else:
                dfs(owner_id, contribution, path | {owner_id})

    dfs(root, 1.0, {root})

    result = []
    for pid, frac in person_pct.items():
        p = Party.query.get(pid)
        pct = round(frac * 100, 2)
        result.append({
            "party": p.serialize(),
            "effective_ownership": pct,
            "is_ubo": pct >= UBO_THRESHOLD or pid in control_persons,
            "via_control": pid in control_persons,
        })
    result.sort(key=lambda x: -x["effective_ownership"])
    return result


def is_complex(customer):
    """Complex ownership = at least one intermediate holding layer (an edge
    whose target is not the root, or an organization among the owners), or an
    unusually wide structure. Replaces the manual boolean with graph-derived truth."""
    graph = build_graph(customer)
    if not graph["root_id"]:
        return False
    root = graph["root_id"]
    multi_level = any(e["owned_party_id"] != root for e in graph["edges"])
    org_owners = any(n["kind"] == "ORGANIZATION" and n["id"] != root
                     for n in graph["nodes"])
    return multi_level or org_owners or len(graph["edges"]) > 3


def directors_of(customer):
    """Parties holding a DIRECTOR relationship to the customer's root party."""
    if not customer.root_party_id:
        return []
    edges = (OwnershipRelationship.query
             .filter_by(owned_party_id=customer.root_party_id,
                        relationship_type="DIRECTOR", active=True).all())
    return [Party.query.get(e.owner_party_id).serialize() for e in edges]


def build_graph(customer):
    """Return {root_id, nodes, edges} for the customer's ownership structure."""
    root = customer.root_party_id
    if not root:
        return {"root_id": None, "nodes": [], "edges": []}

    node_ids = set()
    edges = []

    def walk(node_id, path):
        node_ids.add(node_id)
        for e in _incoming(node_id):
            edges.append(e.serialize())
            if e.owner_party_id not in path:
                walk(e.owner_party_id, path | {e.owner_party_id})

    walk(root, {root})
    nodes = [Party.query.get(nid).serialize() for nid in node_ids]
    return {"root_id": root, "nodes": nodes, "edges": edges}
