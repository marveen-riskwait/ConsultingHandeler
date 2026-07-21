"""Who may talk to whom in the chat.

The chat is used by BOTH sides of the relationship, so the directory is
asymmetric:

- Staff see their colleagues. Person-to-person.
- A portal user sees NOBODY here. A client does not write to an individual:
  they write to the organization, in the conversation attached to their file,
  which the assigned team reads (see engine/customer_chat.py). That is the
  whole point — no "their" officer to lose when someone leaves or is away.

So direct messages are strictly staff-to-staff. Every room creation and every
message goes through `can_message()`, so the boundary is enforced server-side,
not merely hidden in the UI.
"""
from api.models import User, Customer


def directory(user, query=None):
    """Users `user` may start a conversation with, optionally name-filtered."""
    base = User.query.filter_by(organization_id=user.organization_id,
                                is_active=True).filter(User.id != user.id)

    if user.is_portal_user():
        # Nothing to pick from: their conversation is the one on their file.
        return []
    # Colleagues only — a client is reached through the customer room.
    candidates = [u for u in base.all() if not u.is_portal_user()]

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
    # Either side being a client means this belongs in the customer room, where
    # the team can see it and the history survives a change of handler.
    if user.is_portal_user() or other.is_portal_user():
        return False
    return True


def can_create_group(user):
    """Portal users don't get to assemble arbitrary group rooms."""
    return not user.is_portal_user()
