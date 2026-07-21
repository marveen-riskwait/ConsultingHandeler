"""Who may talk to whom in the chat.

The chat is used by BOTH sides of the relationship, so the directory is
asymmetric:

- Staff see their colleagues AND the customer portal users, so a compliance
  officer can message a client directly.
- A portal user (a customer) sees ONLY their reference contacts — the
  customer's relationship manager, plus whoever is actually assigned to their
  open cases/tasks. Never the whole team, never other customers.

Every room creation and every message goes through `can_message()`, so the
boundary is enforced server-side, not merely hidden in the UI.
"""
from api.models import User, Customer, Case, Task


def _reference_ids(user):
    """Staff a portal user is allowed to reach, for their own customer file."""
    if not user.customer_id:
        return set()
    ids = set()
    customer = Customer.query.get(user.customer_id)
    if customer is not None and customer.relationship_manager_id:
        ids.add(customer.relationship_manager_id)
    # Fallback/extra: people actually working the file right now.
    for case in Case.query.filter_by(customer_id=user.customer_id).all():
        if case.assigned_to:
            ids.add(case.assigned_to)
    for task in Task.query.filter_by(customer_id=user.customer_id).all():
        if task.assigned_to:
            ids.add(task.assigned_to)
    ids.discard(user.id)
    return ids


def directory(user, query=None):
    """Users `user` may start a conversation with, optionally name-filtered."""
    base = User.query.filter_by(organization_id=user.organization_id,
                                is_active=True).filter(User.id != user.id)

    if user.is_portal_user():
        allowed = _reference_ids(user)
        if not allowed:
            return []
        candidates = base.filter(User.id.in_(allowed)).all()
    else:
        candidates = base.all()

    if query:
        q = query.strip().lower()
        candidates = [u for u in candidates
                      if q in (u.full_name or "").lower()
                      or q in (u.email or "").lower()]

    out = []
    for u in candidates:
        entry = {"id": u.id, "full_name": u.full_name, "email": u.email,
                 "role": u.role, "is_portal_user": u.is_portal_user()}
        if u.customer_id:
            customer = Customer.query.get(u.customer_id)
            entry["customer_name"] = customer.name if customer else None
        out.append(entry)
    # Colleagues first, then customers; alphabetical inside each group.
    out.sort(key=lambda e: (e["is_portal_user"],
                            (e["full_name"] or e["email"] or "").lower()))
    return out


def can_message(user, other):
    """May `user` open/participate in a conversation with `other`?"""
    if other is None or other.organization_id != user.organization_id:
        return False
    if user.id == other.id:
        return False
    if user.is_portal_user():
        return other.id in _reference_ids(user)
    if other.is_portal_user():
        # Staff may message a customer — any staff member of the org can.
        return True
    return True


def can_create_group(user):
    """Portal users don't get to assemble arbitrary group rooms."""
    return not user.is_portal_user()
