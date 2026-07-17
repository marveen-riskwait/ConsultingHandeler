"""RBAC provisioning service.

Keeps the Permission catalog and the default system Roles in sync with the
definitions in api.models.authz. Idempotent — safe to call on every boot, from
the seed command, or lazily when a new organization registers.
"""
from api.models import db, Permission, Role, PERMISSION_CATALOG, DEFAULT_ROLE_PERMISSIONS


def sync_permissions():
    existing = {p.code: p for p in Permission.query.all()}
    for code, label in PERMISSION_CATALOG:
        p = existing.get(code)
        if p is None:
            db.session.add(Permission(code=code, label=label))
        elif p.label != label:
            p.label = label
    db.session.commit()
    return {p.code: p for p in Permission.query.all()}


def sync_roles():
    perms = sync_permissions()
    for name, codes in DEFAULT_ROLE_PERMISSIONS.items():
        role = Role.query.filter_by(name=name).first()
        if role is None:
            role = Role(name=name, label=name.replace("_", " ").title(), is_system=True)
            db.session.add(role)
        # De-duplicate codes: a role's extra list may repeat a base permission.
        seen = set()
        wanted = []
        for c in codes:
            if c in perms and c not in seen:
                seen.add(c)
                wanted.append(perms[c])
        role.permissions = wanted
    db.session.commit()


def get_role(name):
    """Return the Role for `name`, provisioning the default RBAC set if missing."""
    role = Role.query.filter_by(name=name).first()
    if role is None:
        sync_roles()
        role = Role.query.filter_by(name=name).first()
    return role
