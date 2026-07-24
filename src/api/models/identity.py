"""Tenancy & identity: Organization and User.

User keeps a `role` name string (for display / back-compat) but authorization
now flows through `role_obj` -> permissions (see api.models.authz).
"""
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow
from api.models.authz import user_roles, user_permissions

# Role names available in the platform (see authz.DEFAULT_ROLE_PERMISSIONS).
ROLES = (
    "CUSTOMER_USER", "KYC_ANALYST", "ANALYST", "SENIOR_ANALYST",
    "COMPLIANCE_OFFICER", "COMPLIANCE_MANAGER", "MANAGER", "MLRO",
    "AUDITOR", "REGULATORY_MANAGER",
    "ORGANIZATION_ADMIN", "PLATFORM_ADMIN", "ADMIN",
)


class Organization(db.Model):
    __tablename__ = "organization"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # AML record-keeping retention after a relationship ends (AMLD/FATF: 5y).
    data_retention_months: Mapped[int] = mapped_column(Integer, default=60,
                                                       nullable=False,
                                                       server_default="60")

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    customers: Mapped[list["Customer"]] = relationship(back_populates="organization")

    def serialize(self):
        return {"id": self.id, "name": self.name,
                "data_retention_months": self.data_retention_months}


def _sign_avatar(url):
    if not url:
        return url
    from api.integrations import media
    return media.sign_url(url)


class User(db.Model):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="KYC_ANALYST")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    # An address is unverified until the holder clicks the link. New self-service
    # signups start False; invited users are verified by accepting the invite
    # (receiving it proved they control the address). server_default '1'
    # grandfathers everyone who existed before this column.
    email_verified: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="1")
    # Two-factor authentication. Staff use TOTP (authenticator app); portal
    # customers use a one-time code emailed at sign-in (EMAIL_OTP), gentler for
    # the general public while still a real second factor.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0")
    mfa_method: Mapped[str] = mapped_column(String(12), nullable=True)  # TOTP / EMAIL_OTP
    mfa_secret: Mapped[str] = mapped_column(String(64), nullable=True)  # TOTP shared secret
    mfa_backup_codes: Mapped[list] = mapped_column(JSON, default=list)  # hashed one-time codes

    # Profile: identity a colleague sees, plus the photo that makes people
    # recognisable in chat before they read the name.
    avatar_url: Mapped[str] = mapped_column(String(500), nullable=True)
    job_title: Mapped[str] = mapped_column(String(120), nullable=True)
    phone: Mapped[str] = mapped_column(String(40), nullable=True)
    timezone: Mapped[str] = mapped_column(String(60), nullable=True)
    # Brute-force defence: consecutive failed logins, and a lock that lifts
    # itself after a cooldown so a locked-out real user is not stranded.
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="users")

    # Portal users (role CUSTOMER_USER) represent a customer: this is the file
    # they belong to. Staff users leave it NULL.
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"),
                                             nullable=True)

    def is_portal_user(self):
        """A customer-side user — must only ever reach their own contacts."""
        return self.customer_id is not None or self.role == "CUSTOMER_USER"

    # Legacy single role (kept for back-compat / display).
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=True)
    role_obj: Mapped["Role"] = relationship(lazy="selectin", foreign_keys=[role_id])

    # A user may hold several roles.
    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, lazy="selectin")

    # Special authorizations: individual permission grants on top of roles.
    extra_permissions: Mapped[list["Permission"]] = relationship(
        secondary=user_permissions, lazy="selectin")

    def permission_codes(self):
        codes = set()
        if self.role_obj:
            codes |= self.role_obj.permission_codes()
        for r in self.roles:
            codes |= r.permission_codes()
        codes |= {p.code for p in self.extra_permissions}
        return codes

    def role_permission_codes(self):
        """Permissions coming from roles only (to distinguish extra grants)."""
        codes = set()
        if self.role_obj:
            codes |= self.role_obj.permission_codes()
        for r in self.roles:
            codes |= r.permission_codes()
        return codes

    def role_names(self):
        names = set()
        if self.role_obj:
            names.add(self.role_obj.name)
        for r in self.roles:
            names.add(r.name)
        if not names and self.role:
            names.add(self.role)
        return sorted(names)

    def has_permission(self, code):
        return code in self.permission_codes()

    def serialize(self, with_permissions=True):
        data = {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "avatar_url": _sign_avatar(self.avatar_url),
            "job_title": self.job_title,
            "phone": self.phone,
            "timezone": self.timezone,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "role": self.role,
            "roles": self.role_names(),
            "organization_id": self.organization_id,
            "customer_id": self.customer_id,
            "is_portal_user": self.is_portal_user(),
            "email_verified": self.email_verified,
            "mfa_enabled": self.mfa_enabled,
            "mfa_method": self.mfa_method,
            "extra_permissions": sorted(p.code for p in self.extra_permissions),
        }
        if with_permissions:
            data["permissions"] = sorted(self.permission_codes())
        return data
