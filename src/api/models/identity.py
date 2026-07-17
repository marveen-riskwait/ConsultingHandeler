"""Tenancy & identity: Organization and User.

User keeps a `role` name string (for display / back-compat) but authorization
now flows through `role_obj` -> permissions (see api.models.authz).
"""
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

# Role names available in the platform (see authz.DEFAULT_ROLE_PERMISSIONS).
ROLES = (
    "CUSTOMER_USER", "KYC_ANALYST", "ANALYST", "SENIOR_ANALYST",
    "COMPLIANCE_OFFICER", "COMPLIANCE_MANAGER", "MANAGER", "MLRO",
    "AUDITOR", "REGULATORY_MANAGER", "PLATFORM_ADMIN", "ADMIN",
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

    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=True)
    role_obj: Mapped["Role"] = relationship(lazy="selectin")

    def permission_codes(self):
        return self.role_obj.permission_codes() if self.role_obj else set()

    def has_permission(self, code):
        return code in self.permission_codes()

    def serialize(self, with_permissions=True):
        data = {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "organization_id": self.organization_id,
        }
        if with_permissions:
            data["permissions"] = sorted(self.permission_codes())
        return data
