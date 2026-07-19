# Compliance OS — Architecture Audit

Required analysis deliverable (per the instruction document). Describes the
current state of the codebase, what is reusable, technical debt, the migration
strategy, the target architecture, and the phased roadmap. **No functionality is
removed; the core spine is preserved and expanded.**

Core principle (unchanged, the backbone of everything):

```
DATA → EVENT → RULE → RISK → REQUIREMENT → WORKFLOW → HUMAN DECISION → AUDIT
```

---

## 1. Current architecture analysis

Modular Flask monolith on the 4Geeks template (React + Flask + SQLAlchemy +
Alembic + JWT + Celery/Redis), single-origin deploy on Render.

- **Backend** `src/api/`: `models/` (domain package), `engine/` (audit, events,
  risk, rules, ownership, screening_service, screening shim), `integrations/screening/`
  (provider abstraction), `auth.py` (JWT + permission decorators), `rbac.py`
  (role/permission provisioning), `routes.py`, `tasks.py` (Celery), `commands.py` (seed).
- **Frontend** `src/front/`: `pages/` (Login, Workspace, Customers, Customer360,
  CaseDetail), `services/api.js`, `permissions/can.js`, `store.js`, `hooks/`.
- **Async**: Celery + Redis with an **inline fallback** when no broker is set
  (events processed synchronously) — keeps local/demo runnable without Redis.

## 2. Existing feature inventory (shipped, increments 1–2)

| Area | Status |
|------|--------|
| JWT auth, org-scoped API | ✅ |
| **RBAC** (Role, Permission, RolePermission), permission-based decorators | ✅ |
| Event bus → rules engine → risk engine → case/task/notification | ✅ |
| Explainable, versioned RiskAssessment | ✅ (not yet configurable/data-driven) |
| **Parties**: Party (person/org), OwnershipRelationship, **UBO computation** | ✅ |
| **ScreeningRun / ScreeningMatch** (lifecycle + review, history preserved) | ✅ |
| Screening provider abstraction (`integrations/screening`, MockProvider) | ✅ (screening only) |
| Audit trail (immutable WHO/WHAT/WHEN/OLD→NEW/WHY) | ✅ |
| Document expiry monitoring (Celery beat) | ✅ |
| Role-based Analyst workspace, Customer 360, Case decision | ✅ |

## 3. Database model inventory

`Organization, User(role, role_id), Role, Permission, role_permissions,
Customer(root_party_id, derived screening flags), Document, RiskAssessment,
Party, OwnershipRelationship, ScreeningRun, ScreeningMatch, ComplianceEvent,
ComplianceRule, Case, Task, Notification, AuditEvent`.
Migrations: `0763…` (base), `b1af…` (compliance models), `8b3f…` (RBAC),
`1bd1…` (parties/ownership/screening).

## 4. API inventory (all under `/api`, org-scoped, audited)

`auth/register|login|me`, `customers` (+`/:id`, `/screen`, `/documents`,
`/timeline`, `/ownership`, `/screening`), `screening/matches/:id/review`,
`workspace`, `cases` (+`/:id`, `/:id/decision`), `tasks/my-work`,
`tasks/:id/complete`, `notifications` (+`/:id/read`), `rules`, `roles`,
`permissions`, `audit`, `health`.

## 5. Frontend page inventory

`Login`, `Workspace` (analyst inbox), `Customers`, `Customer360`
(risk/why + changes + cases + tasks + events + screening matches + ownership/UBO),
`CaseDetail` (compare + decision + audit). Nav generated from permissions.

## 6. Reusable code

Everything above is reused and extended — **nothing is thrown away**. The event
bus, rules/risk engines, audit helper, provider abstraction, ownership/UBO,
ScreeningMatch and the permission machinery are the foundation the remaining
phases build on.

## 7. Technical debt / reconciliation with the spec

Items to align with the instruction document (addressed in upcoming phases):

- **Permission naming**: adopt the document's canonical codes as the source of
  truth — `screening.review_match`, `screening.confirm_match`, `case.reassign`,
  `kyc.review`, `risk.approve`, `rule.*` (singular), plus `team.*`, `user.*`,
  `role.*`, `management.*`, `organization.*`. Earlier codes
  (`screening.review/confirm`, `rules.*`) are renamed/aliased.
- **UserRole many-to-many**: a user currently has one `role_id`; the spec wants a
  `UserRole` join (multiple roles). Add it; keep `role`/`role_id` for back-compat.
- **Missing org role** `ORGANIZATION_ADMIN` (distinct from `PLATFORM_ADMIN`).
- **No ABAC yet**: only `organization_id` isolation. Add `AccessPolicy` + data
  scopes (assigned / team / department / org / read-only-all).
- **No Departments / Teams / memberships / assignment / SLA / workload** yet.
- **Risk engine** is code-based `_FACTORS`; spec wants data-driven
  `RiskMethodology/RiskFactor/RiskRule/RiskThreshold` (later phase).
- **No Requirement / Review / Workflow engines, Alert Center, Regulatory
  Intelligence, real providers/webhooks** yet (later phases).

## 8. Migration strategy

Additive Alembic migrations only; each `up/down/re-up` tested on SQLite before
push. New FKs are explicitly named (SQLite batch requirement). No destructive
schema changes; derived/denormalized fields kept as caches so existing engines
keep working. `flask db upgrade` runs on deploy (Render release phase).

## 9. Target architecture

Multi-tenant Compliance Operations Platform, five workspaces sharing one backend:
**Compliance Operations** (analysts/officers), **Management** (managers),
**Platform Administration** (admins), **Customer Portal**, **Audit & Regulatory**.

```
Platform → Organization → Departments → Teams → Users
User → OrganizationMembership → Roles (UserRole) → Permissions → Teams → Data Scope → Workspace
```

Backend enforces tenant isolation + RBAC + ABAC; frontend only hides UI.
Provider Orchestration Layer (KYC/KYB/AML adapters + normalization + webhooks)
feeds the event spine. Continuous monitoring via Celery.

## 10. Implementation roadmap (aligned to the document's phases)

- **Phase A — Authorization Foundation** *(next)*: Department, Team,
  OrganizationMembership, TeamMembership, UserRole, AccessPolicy; reconcile the
  permission catalog; add `ORGANIZATION_ADMIN`; ABAC data-scope service applied
  to list queries. No new KYC/screening. *(document "Prompt 2")*
- **Phase B — Administration**: users, teams, departments, roles, permissions,
  org settings, invitation flow (screens + endpoints).
- **Phase C — Management**: manager dashboard, team view, workload engine,
  queues, assignment engine (round-robin/least-loaded/skill/risk), SLA, performance.
- **Phase D — Domain refactor**: explicit Person/LegalEntity + CustomerRelationship
  + Address (extends the current Party layer).
- **Phase E — KYC/KYB + Requirement engine**: profiles with field provenance,
  required-info/missing-info detection, document requirements.
- **Phase F — Provider Integration Layer**: registry, configuration, credentials,
  adapters (Sumsub/Trulioo/ComplyAdvantage-ready), normalization, webhooks
  (`/api/webhooks/providers/:provider`, signature + idempotency). MockProvider kept.
- **Phase G — Risk (data-driven)**, **Requirement/Review engines**,
  **Workflow engine**, **Alert Center**, **Continuous monitoring**,
  **Regulatory Intelligence**, **Audit hardening + tests**.

The project remains runnable after every phase.

---

### Progress so far
- Increment 1 (RBAC + models package) — shipped.
- Increment 2 (Parties/Ownership/UBO + ScreeningMatch + provider abstraction) — shipped.
- **Phase A (Authorization Foundation)** — shipped: Departments, Teams,
  OrganizationMembership, TeamMembership, UserRole (multi-role), AccessPolicy;
  permission catalog reconciled to the document's canonical codes;
  `ORGANIZATION_ADMIN` added; ABAC data-scope service applied to case/task/
  workspace queries; tenancy endpoints (`/organization`, `/departments`,
  `/teams`, `/users`) with permission enforcement.
- **Phase B (Administration)** — shipped: Invitation model + accept flow
  (`/invitations`, `/auth/accept-invitation` with token, auto org membership +
  team assignment), user management (`PATCH /users/:id` role change / disable,
  admin-role grants gated by `role.update`), org settings (`PATCH /organization`),
  disabled-account login rejection; frontend Administration area
  (Users / Teams & Departments / Roles permission matrix / Organization) gated
  by permissions, invite-link accept mode on the Login screen.
- **Phase C (Management)** — shipped: AssignmentRule + SLAConfiguration models;
  workload engine (per-user explainable workload_score); assignment engine
  (ROUND_ROBIN / LEAST_LOADED / SKILL_BASED / RISK_BASED / MANUAL) wired into
  the rules engine so new cases are auto-assigned event-driven (unmatched cases
  land in the queue); SLA engine (on-time / at-risk / breached from
  per-priority target hours); endpoints /management/dashboard, /workload,
  /queues (+bulk-assign), /sla, /cases/:id/assign, /assignment-rules; Manager
  workspace UI (Operations dashboard with team-workload bars + SLA, Queues with
  manual/auto/bulk assign, Workload table). Review fixes from the doc check:
  user.disable gating, user multi-role add/remove endpoints, PATCH /teams/:id
  (manager config), USER_CREATED audit on registration.
- **Phase D (Domain refactor)** — shipped: explicit **Person / LegalEntity**
  via single-table polymorphism on Party (`polymorphic_on kind`, no schema
  change — existing rows load as the right subclass); **Address** model with
  full history (is_current / valid_from / valid_to, old row kept on replace);
  `engine/party_service.py` turns KYB mutations into spine EVENTS
  (DIRECTOR_CHANGED / OWNERSHIP_CHANGED / UBO_CHANGED / ADDRESS_CHANGED) with
  UBO-diff detection; `complex_ownership` now **derived from the graph shape**
  instead of a manual boolean, and folded into risk; new rules (director ->
  screen, ownership -> review, UBO -> verify, address -> info); endpoints
  POST /customers/:id/ownership (now event-driven), GET/POST
  /customers/:id/addresses, GET /parties/:id; Customer 360 gains an Add-owner
  KYB form (relationship + kind + %), a Directors list, and an Addresses card
  with history — permission-gated (kyb.edit / kyc.edit).
- **Phase E (KYC/KYB + Requirement Engine)** — shipped: **ProfileField** with
  full provenance (value / source / verified / verified_by / confidence /
  last_changed_at; value change re-opens verification, trusted high-confidence
  source auto-verifies); data-driven **RequirementDefinition** (kind, customer
  type, min risk rank, jurisdiction, mapped data_field / doc_type) + per-customer
  **RequirementInstance**; `engine/requirement_engine.py` computes applicable
  requirements by profile, RECEIVED/VERIFIED/MISSING status, **completeness %**,
  and `request_missing_info()` (task per missing + notification +
  MISSING_INFORMATION_DETECTED event); `engine/kyc_service.py` (set/verify field,
  audited). Endpoints: GET/POST /customers/:id/fields,
  /fields/:fid/verify, GET /customers/:id/requirements,
  POST /customers/:id/request-info; overview now carries `completeness`.
  Customer 360 gains a Compliance-completeness bar with per-requirement chips +
  "Request missing info", and a KYC-data (provenance) card with Verify.
  12 system requirement definitions seeded (EDD pulled in at HIGH+ risk).
- **Next: Phase F — Provider Integration Layer** (registry, configuration,
  credentials, adapters ready for Sumsub/Trulioo/ComplyAdvantage, normalization,
  webhooks with signature + idempotency), keeping MockProvider.
