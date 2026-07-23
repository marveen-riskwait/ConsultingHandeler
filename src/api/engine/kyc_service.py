"""KYC data service — set/verify profile fields with provenance + audit.

Changing a field value re-opens verification (verified -> False) unless the
source is a trusted provider; every change is audited with old -> new.
"""
from api.models import db, ProfileField, utcnow
from api.engine import audit

TRUSTED_SOURCES = {"provider", "passport", "id_document"}
# Prefixes are trusted too: official registries queried by the enrichment
# engine (e.g. "registry:companies_house") count as independent verification.
TRUSTED_PREFIXES = ("registry:",)


def _is_trusted(source):
    return (source in TRUSTED_SOURCES
            or any(source.startswith(p) for p in TRUSTED_PREFIXES))


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
    if _is_trusted(source) and (confidence or 0) >= 0.9:
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


# The KYC form asks for the residential address in the same shape as the
# Addresses card (number / street / city / postal code / country). This keeps
# the two in sync: the form's answers become the current RESIDENTIAL address
# on the customer file, so nobody retypes what was already collected.
_ADDRESS_FORM_KEYS = ("residential_street_number", "residential_street_name",
                      "residential_city", "residential_postal_code",
                      "residential_country")


def _mirror_address(customer, *, line1, city=None, postal=None, country=None,
                    address_type="RESIDENTIAL", actor=None):
    """Add as the current address of that type unless identical — churning
    history on every save would bury the real changes it exists to show."""
    from api.engine import party_service
    from api.models import Address

    if customer.root_party_id:
        current = (Address.query
                   .filter_by(party_id=customer.root_party_id,
                              address_type=address_type, is_current=True)
                   .first())
        if current and (current.line1 or "") == line1 \
                and (current.city or "") == (city or "") \
                and (current.postal_code or "") == (postal or "") \
                and (current.country or "") == (country or ""):
            return current
    return party_service.add_address(customer, line1=line1, city=city,
                                     postal_code=postal, country=country,
                                     address_type=address_type, actor=actor)


def sync_address_from_form(customer, actor=None):
    """Mirror address-shaped ProfileFields into the Address history, so the
    same information never has to be typed twice.

    INDIVIDUAL: the form's structured residential block -> RESIDENTIAL.
    COMPANY: registered_office (declared on the form OR written by a registry
    enrichment) -> REGISTERED, as a single line. No-op while empty."""
    keys = _ADDRESS_FORM_KEYS + ("registered_office",)
    vals = {f.field_key: (f.value or "").strip()
            for f in ProfileField.query
                .filter(ProfileField.customer_id == customer.id,
                        ProfileField.field_key.in_(keys)).all()}

    if customer.customer_type == "COMPANY":
        office = vals.get("registered_office", "")
        if not office:
            return None
        return _mirror_address(customer, line1=office,
                               address_type="REGISTERED", actor=actor)

    street = vals.get("residential_street_name", "")
    if not street:
        return None
    line1 = f"{vals.get('residential_street_number', '')} {street}".strip()
    return _mirror_address(
        customer, line1=line1,
        city=vals.get("residential_city") or None,
        postal=vals.get("residential_postal_code") or None,
        country=vals.get("residential_country") or None,
        address_type="RESIDENTIAL", actor=actor)
