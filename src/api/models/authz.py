"""RBAC: Role, Permission, their association, and the UserRole join.

Permission codes are the canonical vocabulary from the instruction document.
Authorization checks a *permission code*, never a role name, so roles can be
added/tuned without touching route code. A user may hold MULTIPLE roles
(user_roles) in addition to the legacy single role_id; permissions are the union.
"""
from sqlalchemy import String, Boolean, ForeignKey, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db


# --- Canonical permission catalog (code, human label) -----------------------
PERMISSION_CATALOG = [
    ("workspace.view", "View own workspace"),

    ("organization.view", "View organization"),
    ("organization.update", "Update organization"),

    ("user.view", "View users"),
    ("user.create", "Create users / invite"),
    ("user.update", "Update users"),
    ("user.disable", "Disable users"),

    ("role.view", "View roles"),
    ("role.create", "Create roles"),
    ("role.update", "Update roles"),

    ("department.view", "View departments"),
    ("department.create", "Create departments"),
    ("department.update", "Update departments"),

    ("team.view", "View teams"),
    ("team.create", "Create teams"),
    ("team.update", "Update teams"),
    ("team.manage_members", "Manage team members"),

    ("customer.view", "View customers"),
    ("customer.create", "Create customers"),
    ("customer.update", "Update customers"),
    ("customer.delete", "Delete customers (erroneous records only)"),

    ("kyc.view", "View KYC"),
    ("kyc.edit", "Edit KYC"),
    ("kyc.review", "Review KYC"),
    ("kyc.approve", "Approve KYC"),

    ("kyb.view", "View KYB"),
    ("kyb.edit", "Edit KYB"),
    ("kyb.review", "Review KYB"),

    ("screening.run", "Run screening"),
    ("screening.view", "View screening"),
    ("screening.review_match", "Review a screening match"),
    ("screening.confirm_match", "Confirm a screening match"),

    ("risk.view", "View risk"),
    ("risk.calculate", "Recalculate risk"),
    ("risk.override", "Override risk"),
    ("risk.approve", "Approve risk"),

    ("case.view", "View cases"),
    ("case.create", "Create cases"),
    ("case.assign", "Assign cases"),
    ("case.reassign", "Reassign cases"),
    ("case.escalate", "Escalate cases"),
    ("case.update", "Update cases"),
    ("case.close", "Close cases"),
    ("case.approve", "Approve cases"),

    ("task.view", "View tasks"),
    ("task.create", "Create tasks"),
    ("task.assign", "Assign tasks"),
    ("task.complete", "Complete tasks"),

    ("transaction.view", "View transactions & monitoring"),
    ("transaction.ingest", "Ingest / import transactions"),

    ("document.view", "View documents"),
    ("document.upload", "Upload documents"),
    ("document.verify", "Verify documents"),
    ("document.delete", "Delete documents"),

    ("workflow.view", "View workflows"),
    ("workflow.execute", "Execute workflows"),
    ("workflow.configure", "Configure workflows"),

    ("rule.view", "View rules"),
    ("rule.create", "Create rules"),
    ("rule.update", "Update rules"),
    ("rule.activate", "Activate rules"),

    ("regulatory.view", "View regulatory intelligence"),
    ("regulatory.manage", "Manage regulatory intelligence"),

    ("audit.view", "View audit trail"),

    ("management.view", "View management dashboard"),
    ("management.team_view", "View teams / members"),
    ("management.performance_view", "View performance / SLA"),
    ("management.assign_work", "Assign / reassign work"),
]

ALL_CODES = [c for c, _ in PERMISSION_CATALOG]


def _codes(*groups):
    out = []
    for g in groups:
        out.extend(g)
    return out


_ANALYST_BASE = [
    "workspace.view",
    "customer.view", "customer.create", "customer.update",
    "kyc.view", "kyc.edit", "kyc.review",
    "kyb.view",
    "screening.run", "screening.view", "screening.review_match",
    "risk.view", "risk.calculate",
    "case.view", "case.create", "case.update",
    "task.view", "task.create", "task.complete",
    "transaction.view", "transaction.ingest",
    # Reading a passport scan and sending back a blurry one is first-line
    # work; approving the customer stays with the officer (kyc.approve).
    "document.view", "document.upload", "document.verify",
    "workflow.view", "workflow.execute", "rule.view",
]

_MANAGER_EXTRA = [
    "team.view", "management.view", "management.team_view",
    "management.performance_view", "management.assign_work",
    "case.assign", "case.reassign", "case.escalate",
    "task.assign", "workflow.configure", "audit.view", "user.view",
]

_ADMIN_CORE = [
    "workspace.view",
    "organization.view", "organization.update",
    "user.view", "user.create", "user.update", "user.disable",
    "role.view", "role.create", "role.update",
    "department.view", "department.create", "department.update",
    "team.view", "team.create", "team.update", "team.manage_members",
    "customer.view",
    "risk.view",
    "workflow.view", "workflow.configure",
    "rule.view", "rule.create", "rule.update", "rule.activate",
    "regulatory.view", "regulatory.manage",
    "audit.view",
    "management.view", "management.team_view",
    "management.performance_view", "management.assign_work",
]

# name -> permission codes. Technical admins deliberately lack compliance
# decision permissions (screening.confirm_match / risk.override / kyc.approve /
# risk.approve / case.approve): technical admin != compliance decision maker.
DEFAULT_ROLE_PERMISSIONS = {
    "CUSTOMER_USER": ["workspace.view", "document.upload", "kyc.view"],

    "KYC_ANALYST": _ANALYST_BASE,
    "ANALYST": _ANALYST_BASE,  # back-compat alias

    "SENIOR_ANALYST": _codes(_ANALYST_BASE, [
        "case.assign", "case.reassign", "task.assign", "kyc.approve",
        "kyb.review", "screening.confirm_match",
    ]),

    "COMPLIANCE_OFFICER": _codes(_ANALYST_BASE, [
        "screening.confirm_match", "kyc.approve", "kyb.edit", "kyb.review",
        "risk.override", "risk.approve",
        "case.assign", "case.escalate", "case.close", "case.approve",
        "document.verify", "audit.view", "customer.delete",
    ]),

    "COMPLIANCE_MANAGER": _codes(_ANALYST_BASE, _MANAGER_EXTRA),
    "MANAGER": _codes(_ANALYST_BASE, _MANAGER_EXTRA),  # back-compat alias

    "MLRO": _codes(_ANALYST_BASE, [
        "screening.confirm_match", "risk.override", "risk.approve",
        "case.escalate", "case.close", "case.approve",
        "audit.view", "regulatory.view", "customer.delete",
    ]),

    "AUDITOR": [
        "workspace.view", "customer.view", "kyc.view", "kyb.view",
        "screening.view", "risk.view", "case.view", "task.view",
        "transaction.view",
        "document.view", "workflow.view", "rule.view", "audit.view",
        "regulatory.view", "management.view", "management.team_view",
        "management.performance_view",
    ],

    "REGULATORY_MANAGER": [
        "workspace.view", "regulatory.view", "regulatory.manage",
        "rule.view", "rule.create", "rule.update", "rule.activate",
        "audit.view", "customer.view",
    ],

    # Single canonical administrator role. ORGANIZATION_ADMIN / PLATFORM_ADMIN
    # are kept only as back-compat aliases for pre-existing databases and are no
    # longer offered in the UI or the seed.
    "ADMIN": _ADMIN_CORE,
    "ORGANIZATION_ADMIN": _ADMIN_CORE,   # deprecated alias
    "PLATFORM_ADMIN": _ADMIN_CORE,       # deprecated alias
}


role_permissions = Table(
    "role_permissions",
    db.metadata,
    Column("role_id", ForeignKey("role.id"), primary_key=True),
    Column("permission_id", ForeignKey("permission.id"), primary_key=True),
)

# A user may hold several roles (in addition to the legacy single role_id).
user_roles = Table(
    "user_roles",
    db.metadata,
    Column("user_id", ForeignKey("user.id"), primary_key=True),
    Column("role_id", ForeignKey("role.id"), primary_key=True),
)

# Per-user special authorizations: individual permission GRANTS on top of the
# user's role(s). Effective permissions = union(roles) ∪ extra grants. There is
# deliberately no per-user deny — narrowing access is done via roles, so the
# model stays explainable ("where does this permission come from?").
user_permissions = Table(
    "user_permissions",
    db.metadata,
    Column("user_id", ForeignKey("user.id"), primary_key=True),
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
