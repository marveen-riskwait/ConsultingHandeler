"""Audit trail helper — the platform must always answer WHO/WHAT/WHEN/WHY.

Nothing important should mutate the database without leaving an AuditEvent.
"""
from api.models import db, AuditEvent


def record(action, entity_type, entity_id=None, *, actor=None, actor_label=None,
           old_value=None, new_value=None, reason=None, commit=False):
    """Append an immutable audit entry.

    `actor` is a User (or None for system-generated actions).
    """
    label = actor_label
    if label is None:
        label = actor.email if actor is not None else "system"

    entry = AuditEvent(
        actor_id=actor.id if actor is not None else None,
        actor_label=label,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        reason=reason,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
