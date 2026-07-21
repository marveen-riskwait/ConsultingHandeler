"""Tenancy & identity: Organization and User.

User keeps a `role` name string (for display / back-compat) but authorization
now flows through `role_obj` -> permissions (see api.models.authz).
"""
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey
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

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    customers: Mapped[list["Customer"]] = relationship(back_populates="organization")

    def serialize(self):
        return {"id": self.id, "name": self.name}


class User(db.Model):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="KYC_ANALYST")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)

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
            "role": self.role,
            "roles": self.role_names(),
            "organization_id": self.organization_id,
            "customer_id": self.customer_id,
            "is_portal_user": self.is_portal_user(),
            "extra_permissions": sorted(p.code for p in self.extra_permissions),
        }
        if with_permissions:
            data["permissions"] = sorted(self.permission_codes())
        return data
