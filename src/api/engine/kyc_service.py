"""KYC data service — set/verify profile fields with provenance + audit.

Changing a field value re-opens verification (verified -> False) unless the
source is a trusted provider; every change is audited with old -> new.
"""
from api.models import db, ProfileField, utcnow
from api.engine import audit

TRUSTED_SOURCES = {"provider", "passport", "id_document"}


def set_field(customer, field_key, value, *, category=None, source="manual",
              confidence=None, actor=None):
    field = (ProfileField.query
             .filter_by(customer_id=customer.id, field_key=field_key).first())
    old_value = field.value if field else None
    if field is None:
        field = ProfileField(customer_id=customer.id, field_key=field_key)
        db.session.add(field)

    field.category = category or field.category
    field.value = value
    field.source = source
    field.confidence = confidence
    field.last_changed_at = utcnow()
    # A high-confidence trusted source is auto-verified; otherwise a value change
    # resets verification.
    if source in TRUSTED_SOURCES and (confidence or 0) >= 0.9:
        field.verified = True
        field.verified_at = utcnow()
    elif old_value != value:
        field.verified = False
        field.verified_at = None
        field.verified_by = None

    audit.record("PROFILE_FIELD_SET", "customer", customer.id, actor=actor,
                 old_value=f"{field_key}={old_value}",
                 new_value=f"{field_key}={value} (src={source})", commit=True)
    return field


def verify_field(field, actor):
    field.verified = True
    field.verified_by = actor.id if actor else None
    field.verified_at = utcnow()
    audit.record("PROFILE_FIELD_VERIFIED", "customer", field.customer_id,
                 actor=actor, new_value=field.field_key, commit=True)
    return field
