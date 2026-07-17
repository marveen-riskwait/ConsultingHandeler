"""RBAC: Role, Permission and their association.

The shift from the v1 static roles is: a Role is now a bag of Permissions, and
every protected action checks a *permission code* — not a role name. This lets
us add roles (SENIOR_ANALYST, MLRO, REGULATORY_MANAGER, ...) and tune what each
can do without touching route code.

The catalog and the default role -> permission mapping live here so both the
seed command and any future admin UI read from one place.
"""
from sqlalchemy import String, Boolean, Integer, ForeignKey, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db


# --- Catalog of every permission the platform understands -------------------
# (code, human label). Grouped by domain for readability.
PERMISSION_CATALOG = [
    ("workspace.view", "View own workspace"),
    ("customer.view", "View customers"),
    ("customer.create", "Create customers"),
    ("customer.update", "Update customers"),
    ("customer.delete", "Delete customers"),
    ("kyc.view", "View KYC data"),
    ("kyc.edit", "Edit KYC data"),
    ("kyc.approve", "Approve KYC"),
    ("kyb.view", "View KYB data"),
    ("kyb.edit", "Edit KYB data"),
    ("screening.run", "Run screening"),
    ("screening.review", "Review screening matches"),
    ("screening.confirm", "Confirm a screening match"),
    ("risk.view", "View risk"),
    ("risk.calculate", "Recalculate risk"),
    ("risk.override", "Override risk"),
    ("case.view", "View cases"),
    ("case.create", "Create cases"),
    ("case.assign", "Assign cases"),
    ("case.escalate", "Escalate cases"),
    ("case.close", "Close cases"),
    ("task.view", "View tasks"),
    ("task.create", "Create tasks"),
    ("task.assign", "Assign tasks"),
    ("task.complete", "Complete tasks"),
    ("document.view", "View documents"),
    ("document.upload", "Upload documents"),
    ("document.verify", "Verify documents"),
    ("document.delete", "Delete documents"),
    ("workflow.view", "View workflows"),
    ("workflow.execute", "Execute workflows"),
    ("workflow.configure", "Configure workflows"),
    ("rules.view", "View rules"),
    ("rules.create", "Create rules"),
    ("rules.edit", "Edit rules"),
    ("rules.activate", "Activate rules"),
    ("audit.view", "View audit trail"),
    ("regulatory.view", "View regulatory intelligence"),
    ("regulatory.manage", "Manage regulatory intelligence"),
    ("organization.manage", "Manage organization"),
    ("users.manage", "Manage users"),
    ("roles.manage", "Manage roles & permissions"),
]

ALL_CODES = [c for c, _ in PERMISSION_CATALOG]


def _codes(*groups):
    """Flatten helper for readable role definitions."""
    out = []
    for g in groups:
        out.extend(g)
    return out


_ANALYST_BASE = [
    "workspace.view",
    "customer.view", "customer.create", "customer.update",
    "kyc.view", "kyc.edit", "kyb.view",
    "screening.run", "screening.review",
    "risk.view", "risk.calculate",
    "case.view", "case.create",
    "task.view", "task.create", "task.complete",
    "document.view", "document.upload",
    "workflow.view", "rules.view",
]

# System roles shipped by default. name -> list of permission codes.
DEFAULT_ROLE_PERMISSIONS = {
    "CUSTOMER_USER": [
        "workspace.view", "document.upload", "kyc.view",
    ],
    "KYC_ANALYST": _ANALYST_BASE,
    # Back-compat alias for the v1 demo users.
    "ANALYST": _ANALYST_BASE,
    "SENIOR_ANALYST": _codes(_ANALYST_BASE, [
        "case.assign", "task.assign", "kyc.approve",
    ]),
    "COMPLIANCE_OFFICER": _codes(_ANALYST_BASE, [
        "screening.confirm", "kyc.approve", "kyb.edit",
        "risk.override", "case.assign", "case.escalate", "case.close",
        "document.verify", "audit.view",
    ]),
    "COMPLIANCE_MANAGER": _codes(_ANALYST_BASE, [
        "case.assign", "task.assign", "case.escalate",
        "workflow.configure", "rules.view", "audit.view",
    ]),
    "MANAGER": _codes(_ANALYST_BASE, [
        "case.assign", "task.assign", "workflow.configure", "audit.view",
    ]),
    "MLRO": _codes(_ANALYST_BASE, [
        "screening.confirm", "risk.override", "case.escalate", "case.close",
        "audit.view", "regulatory.view",
    ]),
    "AUDITOR": [
        "workspace.view", "customer.view", "kyc.view", "kyb.view",
        "screening.review", "risk.view", "case.view", "task.view",
        "document.view", "workflow.view", "rules.view", "audit.view",
        "regulatory.view",
    ],
    "REGULATORY_MANAGER": [
        "workspace.view", "regulatory.view", "regulatory.manage",
        "rules.view", "rules.create", "rules.edit", "rules.activate",
        "audit.view", "customer.view",
    ],
    # Technical admin != compliance decision maker: no screening.confirm,
    # risk.override or kyc.approve here on purpose.
    "PLATFORM_ADMIN": [
        "workspace.view",
        "customer.view", "customer.create", "customer.update", "customer.delete",
        "kyc.view", "kyb.view",
        "screening.run", "screening.review",
        "risk.view", "risk.calculate",
        "case.view", "case.create", "case.assign",
        "task.view", "task.create", "task.assign",
        "document.view", "document.upload", "document.delete",
        "workflow.view", "workflow.configure",
        "rules.view", "rules.create", "rules.edit", "rules.activate",
        "audit.view", "regulatory.view", "regulatory.manage",
        "organization.manage", "users.manage", "roles.manage",
    ],
    # Back-compat alias for the v1 demo admin user.
    "ADMIN": None,  # filled below = PLATFORM_ADMIN
}
DEFAULT_ROLE_PERMISSIONS["ADMIN"] = DEFAULT_ROLE_PERMISSIONS["PLATFORM_ADMIN"]


role_permissions = Table(
    "role_permissions",
    db.metadata,
    Column("role_id", ForeignKey("role.id"), primary_key=True),
    Column("permission_id", ForeignKey("permission.id"), primary_key=True),
)


class Permission(db.Model):
    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=True)

    def serialize(self):
        return {"id": self.id, "code": self.code, "label": self.label}


class Role(db.Model):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)

    permissions: Mapped[list["Permission"]] = relationship(
        secondary=role_permissions, lazy="selectin")

    def permission_codes(self):
        return {p.code for p in self.permissions}

    def serialize(self, with_permissions=False):
        data = {"id": self.id, "name": self.name, "label": self.label,
                "is_system": self.is_system}
        if with_permissions:
            data["permissions"] = sorted(self.permission_codes())
        return data
