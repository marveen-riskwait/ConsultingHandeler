"""Multi-tenant org structure: Departments, Teams, memberships, access policy.

    Platform -> Organization -> Departments -> Teams -> Users

Membership is explicit (OrganizationMembership / TeamMembership). AccessPolicy
carries the ABAC data scope (which rows a user may see for a resource).
"""
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import db, utcnow

MEMBERSHIP_STATUSES = ("ACTIVE", "INVITED", "DISABLED")
TEAM_ROLES = ("MEMBER", "MANAGER")
SCOPE_TYPES = ("ORG", "DEPARTMENT", "TEAM", "ASSIGNED", "SELF")


class Department(db.Model):
    __tablename__ = "department"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    teams: Mapped[list["Team"]] = relationship(back_populates="department")

    def serialize(self):
        return {"id": self.id, "organization_id": self.organization_id,
                "name": self.name}


class Team(db.Model):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    manager_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    department: Mapped["Department"] = relationship(back_populates="teams")
    memberships: Mapped[list["TeamMembership"]] = relationship(back_populates="team")

    def serialize(self, with_members=False):
        data = {"id": self.id, "organization_id": self.organization_id,
                "department_id": self.department_id, "name": self.name,
                "manager_id": self.manager_id}
        if with_members:
            data["members"] = [m.user_id for m in self.memberships]
        return data


class OrganizationMembership(db.Model):
    __tablename__ = "organization_membership"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "organization_id": self.organization_id,
                "user_id": self.user_id, "status": self.status}


class TeamMembership(db.Model):
    __tablename__ = "team_membership"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    role_in_team: Mapped[str] = mapped_column(String(20), default="MEMBER")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    team: Mapped["Team"] = relationship(back_populates="memberships")

    def serialize(self):
        return {"id": self.id, "team_id": self.team_id, "user_id": self.user_id,
                "role_in_team": self.role_in_team}


class AccessPolicy(db.Model):
    """ABAC scope: for (role or user) + resource, what row scope applies.

    scope_type in SCOPE_TYPES. Absence of a policy => the role's default scope
    (see engine.data_scope). scope_value is optional (e.g. a team/department id).
    """
    __tablename__ = "access_policy"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=True)
    resource: Mapped[str] = mapped_column(String(40), nullable=False)   # customer / case / task
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ORG")
    scope_value: Mapped[str] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "organization_id": self.organization_id,
                "role_id": self.role_id, "user_id": self.user_id,
                "resource": self.resource, "scope_type": self.scope_type,
                "scope_value": self.scope_value, "active": self.active}
