"""Domain models package.

Re-exports every model + shared helpers so the rest of the codebase can keep
importing `from api.models import db, User, Customer, ...` unchanged, while the
definitions live in per-domain modules.

The whole platform is organised around one spine:
    DATA -> EVENT -> RULE -> RISK -> WORKFLOW -> HUMAN DECISION -> AUDIT
"""
from api.models.base import db, utcnow

# Import order doesn't matter for relationships (string class refs resolve via
# the shared registry), but all modules must be imported before the first query
# so every mapper is registered. Importing them here guarantees that.
from api.models.authz import (
    Permission, Role, role_permissions, user_roles, user_permissions,
    PERMISSION_CATALOG, ALL_CODES, DEFAULT_ROLE_PERMISSIONS,
)
from api.models.identity import Organization, User, ROLES
from api.models.tenancy import (
    Department, Team, OrganizationMembership, TeamMembership, AccessPolicy,
    MEMBERSHIP_STATUSES, TEAM_ROLES, SCOPE_TYPES,
)
from api.models.invitations import Invitation, INVITATION_STATUSES
from api.models.management import (
    AssignmentRule, SLAConfiguration, ASSIGNMENT_STRATEGIES,
)
from api.models.kyc import (
    ProfileField, RequirementDefinition, RequirementInstance,
    FIELD_CATEGORIES, REQUIREMENT_KINDS, REQUIREMENT_STATUSES, RISK_RANK,
)
from api.models.providers import (
    Provider, ProviderCredential, ProviderHealthStatus,
    RawProviderResponse, NormalizedComplianceResult, WebhookEvent,
    PROVIDER_TYPES, HEALTH_STATUSES,
)
from api.models.risk import (
    RiskMethodology, RiskFactor, RiskThreshold, FACTOR_CONDITIONS,
)
from api.models.alerts import ComplianceAlert, ALERT_STATUSES
from api.models.reviews import (
    Review, REVIEW_TYPES, REVIEW_STATUSES, REVIEW_FREQUENCY_MONTHS,
)
from api.models.workflows import (
    WorkflowDefinition, WorkflowStep, WorkflowInstance, WorkflowStepState, Approval,
    WORKFLOW_INSTANCE_STATUSES, STEP_STATES, APPROVAL_STATUSES,
)
from api.models.regulatory import (
    RegulatorySource, RegulatoryRequirement, ComplianceControl,
    RegulatoryChange, ImpactAssessment,
    SOURCE_TYPES, CONTROL_STATUSES, CHANGE_IMPACT, CHANGE_STATUSES,
)
from api.models.customer import (
    Customer, Document, RiskAssessment,
    CUSTOMER_TYPES, RISK_LEVELS, HIGH_RISK_COUNTRIES, HIGH_RISK_ACTIVITIES,
)
from api.models.parties import (
    Party, Person, LegalEntity, Address, OwnershipRelationship,
    PARTY_KINDS, RELATIONSHIP_TYPES, CONTROL_TYPES, ADDRESS_TYPES, UBO_THRESHOLD,
)
from api.models.screening import (
    ScreeningRun, ScreeningMatch,
    MATCH_TYPES, MATCH_STATUSES, ACTIVE_MATCH_STATUSES,
)
from api.models.compliance import ComplianceEvent, ComplianceRule, EVENT_SEVERITIES
from api.models.workflow import Case, Task, CASE_STATUSES
from api.models.notifications import Notification
from api.models.audit import AuditEvent
from api.models.assistant import Conversation, Message, MESSAGE_ROLES
from api.models.sanctions import (
    SanctionedEntity, WatchlistImport, SanctionedWallet, SANCTION_SOURCES, SANCTION_ENTITY_TYPES,
)
from api.models.chat import ChatRoom, ChatMember, ChatMessage, CHAT_MESSAGE_KINDS

__all__ = [
    "db", "utcnow",
    "Permission", "Role", "role_permissions", "user_roles", "user_permissions",
    "PERMISSION_CATALOG", "ALL_CODES", "DEFAULT_ROLE_PERMISSIONS",
    "Organization", "User", "ROLES",
    "Department", "Team", "OrganizationMembership", "TeamMembership", "AccessPolicy",
    "MEMBERSHIP_STATUSES", "TEAM_ROLES", "SCOPE_TYPES",
    "Invitation", "INVITATION_STATUSES",
    "AssignmentRule", "SLAConfiguration", "ASSIGNMENT_STRATEGIES",
    "ProfileField", "RequirementDefinition", "RequirementInstance",
    "FIELD_CATEGORIES", "REQUIREMENT_KINDS", "REQUIREMENT_STATUSES", "RISK_RANK",
    "Provider", "ProviderCredential", "ProviderHealthStatus",
    "RawProviderResponse", "NormalizedComplianceResult", "WebhookEvent",
    "PROVIDER_TYPES", "HEALTH_STATUSES",
    "RiskMethodology", "RiskFactor", "RiskThreshold", "FACTOR_CONDITIONS",
    "ComplianceAlert", "ALERT_STATUSES",
    "Review", "REVIEW_TYPES", "REVIEW_STATUSES", "REVIEW_FREQUENCY_MONTHS",
    "WorkflowDefinition", "WorkflowStep", "WorkflowInstance", "WorkflowStepState",
    "Approval", "WORKFLOW_INSTANCE_STATUSES", "STEP_STATES", "APPROVAL_STATUSES",
    "RegulatorySource", "RegulatoryRequirement", "ComplianceControl",
    "RegulatoryChange", "ImpactAssessment",
    "SOURCE_TYPES", "CONTROL_STATUSES", "CHANGE_IMPACT", "CHANGE_STATUSES",
    "Customer", "Document", "RiskAssessment",
    "CUSTOMER_TYPES", "RISK_LEVELS", "HIGH_RISK_COUNTRIES", "HIGH_RISK_ACTIVITIES",
    "Party", "Person", "LegalEntity", "Address", "OwnershipRelationship",
    "PARTY_KINDS", "RELATIONSHIP_TYPES", "CONTROL_TYPES", "ADDRESS_TYPES",
    "UBO_THRESHOLD",
    "ScreeningRun", "ScreeningMatch",
    "MATCH_TYPES", "MATCH_STATUSES", "ACTIVE_MATCH_STATUSES",
    "ComplianceEvent", "ComplianceRule", "EVENT_SEVERITIES",
    "Case", "Task", "CASE_STATUSES",
    "Notification", "AuditEvent",
    "Conversation", "Message", "MESSAGE_ROLES",
    "SanctionedEntity", "WatchlistImport", "SanctionedWallet",
    "SANCTION_SOURCES", "SANCTION_ENTITY_TYPES",
    "ChatRoom", "ChatMember", "ChatMessage", "CHAT_MESSAGE_KINDS",
]
