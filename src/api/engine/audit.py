"""Audit trail helper — the platform must always answer WHO/WHAT/WHEN/WHERE/WHY.

Nothing important should mutate the database without leaving an AuditEvent.
Organization and IP address are captured automatically (from the actor and the
current request) so every entry is attributable and tenant-scoped.
"""
from api.models import db, AuditEvent


def _current_ip():
    try:
        from flask import request, has_request_context
        if has_request_context():
            # Honour a proxy's forwarded-for, else the direct peer.
            fwd = request.headers.get("X-Forwarded-For")
            return (fwd.split(",")[0].strip() if fwd else request.remote_addr)
    except Exception:
        pass
    return None


def record(action, entity_type, entity_id=None, *, actor=None, actor_label=None,
           old_value=None, new_value=None, reason=None, metadata=None,
           organization_id=None, commit=False):
    """Append an immutable audit entry.

    `actor` is a User (or None for system-generated actions). `organization_id`
    and the IP are derived automatically when not given.
    """
    label = actor_label
    if label is None:
        label = actor.email if actor is not None else "system"
    if organization_id is None and actor is not None:
        organization_id = actor.organization_id

    entry = AuditEvent(
        organization_id=organization_id,
        actor_id=actor.id if actor is not None else None,
        actor_label=label,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        reason=reason,
        context=metadata,
        ip_address=_current_ip(),
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
