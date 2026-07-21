"""Assignment engine — WHO receives a new case.

    New case -> matching AssignmentRule (case_type / risk_level, by priority)
             -> candidate pool (team / required_role / risk gate)
             -> strategy (ROUND_ROBIN | LEAST_LOADED | SKILL_BASED | RISK_BASED | MANUAL)
             -> assignee (or None => stays in the unassigned queue)

Every automatic assignment is audited.
"""
from api.models import (
    db, User, Case, Customer, Team, TeamMembership, AssignmentRule,
)
from api.engine import audit
from api.engine.workload import active_case_count

# Roles senior enough for high-risk work (RISK_BASED strategy).
_SENIOR_ROLES = ("SENIOR_ANALYST", "COMPLIANCE_OFFICER", "MLRO",
                 "COMPLIANCE_MANAGER", "MANAGER")


def _candidates(rule, organization_id):
    """Active users eligible under the rule's pool constraints."""
    q = User.query.filter_by(organization_id=organization_id, is_active=True)
    users = q.all()

    if rule and rule.team_id:
        member_ids = {tm.user_id for tm in
                      TeamMembership.query.filter_by(team_id=rule.team_id).all()}
        users = [u for u in users if u.id in member_ids]

    role_filter = rule.required_role if rule else None
    if role_filter:
        users = [u for u in users if role_filter in u.role_names()]
    else:
        # Sensible default pool: operational roles only.
        users = [u for u in users if u.role not in
                 ("CUSTOMER_USER", "AUDITOR", "REGULATORY_MANAGER",
                  "ORGANIZATION_ADMIN", "PLATFORM_ADMIN", "ADMIN")]
    return users


def _pick(strategy, candidates, rule, case):
    if not candidates:
        return None
    if strategy == "MANUAL":
        return None

    if strategy == "RISK_BASED":
        senior = [u for u in candidates
                  if any(r in _SENIOR_ROLES for r in u.role_names())]
        pool = senior or candidates
        return min(pool, key=lambda u: active_case_count(u.id))

    if strategy == "SKILL_BASED":
        # Skills are not modeled yet; required_role already narrowed the pool.
        return min(candidates, key=lambda u: active_case_count(u.id))

    if strategy == "ROUND_ROBIN":
        ordered = sorted(candidates, key=lambda u: u.id)
        last = rule.last_assigned_user_id if rule else None
        nxt = ordered[0]
        if last is not None:
            ids = [u.id for u in ordered]
            for uid in ids:
                if uid > last:
                    nxt = next(u for u in ordered if u.id == uid)
                    break
        if rule:
            rule.last_assigned_user_id = nxt.id
        return nxt

    # LEAST_LOADED (default)
    return min(candidates, key=lambda u: active_case_count(u.id))


def match_rule(case, customer):
    """Highest-priority active rule matching the case type + customer risk."""
    rules = (AssignmentRule.query
             .filter_by(organization_id=customer.organization_id, active=True)
             .order_by(AssignmentRule.priority.asc()).all())
    for rule in rules:
        if rule.case_type and rule.case_type != case.case_type:
            continue
        if rule.risk_level and rule.risk_level != customer.risk_level:
            continue
        return rule
    return None


def auto_assign(case, *, strategy=None, actor=None):
    """Assign a case per rules (or a forced strategy). Returns the assignee or None."""
    if case.assigned_to:
        return User.query.get(case.assigned_to)
    customer = Customer.query.get(case.customer_id)
    if customer is None:
        return None

    rule = None if strategy else match_rule(case, customer)
    chosen_strategy = strategy or (rule.strategy if rule else None)
    if chosen_strategy is None:
        return None   # no rule => stays in the queue for manual triage

    assignee = _pick(chosen_strategy, _candidates(rule, customer.organization_id),
                     rule, case)
    if assignee is None:
        return None

    case.assigned_to = assignee.id
    if rule is not None and rule.team_id:
        case.team_id = rule.team_id
    audit.record("CASE_ASSIGNED", "case", case.id, actor=actor,
                 new_value=assignee.email,
                 reason=f"{chosen_strategy}" + (f" (rule: {rule.name})" if rule else ""))
    # The customer conversation is addressed to whoever handles the file, so
    # assigning a case is what opens it to the right people.
    from api.engine import customer_chat
    customer_chat.sync_for_case(case)
    db.session.commit()
    return assignee
