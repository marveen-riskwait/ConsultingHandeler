"""ABAC data-scoping — WHICH rows a user may see for a resource.

RBAC answers "can this user do X?"; data scope answers "on which records?".
Scope is resolved from AccessPolicy rows if present, else a sensible role
default. The backend applies the resulting filter to queries — the frontend is
never trusted for this.

Default behaviour is intentionally ORG-wide (tenant isolation only) so the
platform stays fully usable before the assignment engine (Phase C) exists;
tighter scopes (TEAM / ASSIGNED / SELF) are enforced whenever a policy or role
default asks for them.
"""
from sqlalchemy import or_

from api.models import (
    Customer, Case, Task, TeamMembership, AccessPolicy,
)

# Most permissive first.
_SCOPE_RANK = {"ORG": 0, "DEPARTMENT": 1, "TEAM": 2, "ASSIGNED": 3, "SELF": 4}


def user_team_ids(user):
    return [tm.team_id for tm in
            TeamMembership.query.filter_by(user_id=user.id).all()]


def team_member_ids(team_ids):
    if not team_ids:
        return []
    return [tm.user_id for tm in
            TeamMembership.query.filter(TeamMembership.team_id.in_(team_ids)).all()]


def _policy_scope(user, resource):
    role_ids = [r.id for r in user.roles]
    if user.role_id:
        role_ids.append(user.role_id)
    q = AccessPolicy.query.filter_by(organization_id=user.organization_id,
                                     resource=resource, active=True)
    q = q.filter(or_(AccessPolicy.user_id == user.id,
                     AccessPolicy.role_id.in_(role_ids or [0])))
    scopes = [p.scope_type for p in q.all()]
    if not scopes:
        return None
    # Grant the most permissive applicable scope.
    return min(scopes, key=lambda s: _SCOPE_RANK.get(s, 99))


def resolve_scope(user, resource):
    explicit = _policy_scope(user, resource)
    if explicit:
        return explicit
    # Role defaults.
    if user.has_permission("management.view") or user.has_permission("audit.view") \
            or user.has_permission("case.approve") or user.has_permission("organization.view"):
        return "ORG"
    if user.has_permission("case.assign") or user.has_permission("case.reassign"):
        return "TEAM"
    # Customer-portal users only see their own record.
    if user.role_names() == ["CUSTOMER_USER"]:
        return "SELF"
    return "ORG"


def _org_customer_ids(user):
    return [c.id for c in Customer.query
            .filter_by(organization_id=user.organization_id).all()]


def visible_customers(user):
    q = Customer.query.filter_by(organization_id=user.organization_id)
    if resolve_scope(user, "customer") == "SELF":
        # Customers linked to a party owned by this user would go here; for now
        # customer-portal users see nothing internal.
        q = q.filter(Customer.id == -1)
    return q


def visible_cases(user):
    ids = _org_customer_ids(user) or [0]
    q = Case.query.filter(Case.customer_id.in_(ids))
    scope = resolve_scope(user, "case")
    if scope == "ASSIGNED":
        q = q.filter(Case.assigned_to == user.id)
    elif scope == "TEAM":
        members = team_member_ids(user_team_ids(user)) or [user.id]
        if user.id not in members:
            members.append(user.id)
        q = q.filter(or_(Case.assigned_to.in_(members),
                         Case.assigned_to.is_(None)))
    return q


def visible_tasks(user):
    ids = _org_customer_ids(user) or [0]
    q = Task.query.filter(Task.customer_id.in_(ids))
    scope = resolve_scope(user, "case")
    if scope == "ASSIGNED":
        q = q.filter(or_(Task.assigned_to == user.id, Task.assigned_to.is_(None)))
    return q
