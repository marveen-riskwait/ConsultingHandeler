"""The customer conversation — carried by the organization, not by a person.

A client does not have "their" compliance officer. They have a file, that file
is handled by a team, and the people in that team change: holidays, turnover,
escalation to an MLRO. So the conversation belongs to the *customer*, and who
can read it is derived from who is working the file right now:

    audience = members of the teams on the customer's cases
             + whoever is individually assigned a case or task
             + the relationship manager, when one is recorded

Membership rows still exist — they are the access gate used by every chat route,
and they carry each person's read cursor — but they are *synced* from that
audience rather than hand-picked. Someone joining the team gains the history;
someone leaving stops receiving new messages without the thread being lost.

When nobody is assigned yet, the room is not orphaned: it stays an unassigned
conversation that any staff member allowed to see customers can pick up, and
answering it makes them a participant. A client message must never fall into a
void because no rule matched.
"""
from api.models import (
    db, ChatRoom, ChatMember, ChatMessage, Customer, User, Case, Task,
    TeamMembership,
)


def _team_ids(customer):
    return {c.team_id for c in Case.query.filter_by(customer_id=customer.id).all()
            if c.team_id}


def staff_audience(customer):
    """User ids of the staff currently responsible for this customer's file."""
    ids = set()
    for team_id in _team_ids(customer):
        ids |= {m.user_id for m in
                TeamMembership.query.filter_by(team_id=team_id).all()}
    for case in Case.query.filter_by(customer_id=customer.id).all():
        if case.assigned_to:
            ids.add(case.assigned_to)
    for task in Task.query.filter_by(customer_id=customer.id).all():
        if task.assigned_to:
            ids.add(task.assigned_to)
    if customer.relationship_manager_id:
        ids.add(customer.relationship_manager_id)
    return ids


def portal_user_ids(customer):
    return {u.id for u in User.query.filter_by(customer_id=customer.id).all()}


def room_for(customer, create=True):
    room = ChatRoom.query.filter_by(customer_id=customer.id).first()
    if room is None and create:
        room = ChatRoom(organization_id=customer.organization_id,
                        customer_id=customer.id, is_group=True,
                        name=customer.name)
        db.session.add(room)
        db.session.flush()
    return room


def sync_members(customer, extra_user_ids=()):
    """Align the room's members with who is on the file. Returns the room.

    `extra_user_ids` keeps someone who has already taken part: a colleague who
    answered an unassigned conversation should not be dropped the moment a rule
    assigns the case elsewhere — they are part of the thread's history.
    """
    room = room_for(customer)
    if room is None:
        return None
    if room.name != customer.name:          # the file was renamed
        room.name = customer.name

    wanted = staff_audience(customer) | portal_user_ids(customer) | set(extra_user_ids)
    # Anyone who has actually written in the room stays.
    wanted |= {m.sender_id for m in
               ChatMessage.query.filter_by(room_id=room.id).all() if m.sender_id}

    current = {m.user_id: m for m in room.members}
    for uid in wanted - set(current):
        db.session.add(ChatMember(room_id=room.id, user_id=uid))
    for uid in set(current) - wanted:
        db.session.delete(current[uid])
    db.session.flush()
    return room


def sync_for_case(case):
    """Called whenever a case is assigned or reassigned."""
    if not case or not case.customer_id:
        return None
    customer = Customer.query.get(case.customer_id)
    if customer is None:
        return None
    return sync_members(customer)


def is_unassigned(customer):
    return not staff_audience(customer)


def can_open(user, customer, has_permission):
    """May this user open the customer's conversation?

    Portal users: their own file, nothing else. Staff: they are on the file, or
    the conversation is unassigned and they are allowed to see customers — which
    is what stops an unclaimed client message from being unreadable.
    """
    if customer is None or customer.organization_id != user.organization_id:
        return False
    if user.is_portal_user():
        return user.customer_id == customer.id
    if user.id in staff_audience(customer):
        return True
    return is_unassigned(customer) and has_permission(user, "customer.view")
