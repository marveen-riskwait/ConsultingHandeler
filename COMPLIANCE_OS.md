# Compliance OS — vertical slice

A modular AML/KYC "Compliance Operating System" built on the 4Geeks
React + Flask template. This first slice implements the full spine end‑to‑end:

```
DATA  ->  EVENT  ->  RULE  ->  RISK  ->  WORKFLOW  ->  HUMAN DECISION  ->  AUDIT
```

Screening a customer emits a **ComplianceEvent**, the **rules engine** turns it
into a **case + task + notification**, the **risk engine** recomputes an
explainable score, and every step is recorded in an immutable **audit trail**.
An analyst reviews the case and records a decision (with a mandatory reason).

## Architecture

| Layer | Where | Role |
|-------|-------|------|
| Domain model | `src/api/models.py` | Organization, User(+role), Customer, Document, RiskAssessment, ComplianceEvent, ComplianceRule, Case, Task, Notification, AuditEvent |
| Event bus | `src/api/engine/events.py` | `emit_event()` — async via Celery when a broker is set, else inline |
| Rules engine | `src/api/engine/rules_engine.py` | Data‑driven `ComplianceRule` → CREATE_CASE / CREATE_TASK / NOTIFY |
| Risk engine | `src/api/engine/risk_engine.py` | Explainable, versioned scoring (factors + required actions) |
| Screening | `src/api/engine/screening.py` | Provider abstraction (mock provider ships by default) |
| Audit | `src/api/engine/audit.py` | WHO / WHAT / WHEN / OLD → NEW / WHY |
| Async workers | `src/api/tasks.py`, `src/api/celery_app.py` | screening, event processing, daily document‑expiry sweep (Celery beat) |
| Auth | `src/api/auth.py` | JWT + role decorators (backend is the source of truth) |
| API | `src/api/routes.py` | organization‑scoped, audited REST endpoints |
| Frontend | `src/front/pages/*` | Login, role‑based Workspace (inbox), Customer 360, Case investigation |

## Run it locally

**Backend (inline mode — no Redis needed):**
```bash
pipenv install
pipenv run upgrade          # apply migrations
pipenv run start            # flask run -p 3001
flask seed-demo             # demo org, users, rules, sample customers
```

**Async mode (Celery + Redis):** set `REDIS_URL` / `CELERY_BROKER_URL`, then also run:
```bash
cd src && celery -A api.celery_app.celery worker --loglevel=info
cd src && celery -A api.celery_app.celery beat   --loglevel=info   # scheduled sweeps
```
> With no broker configured, events are processed synchronously inline so the
> whole flow still works for development and demos.

**Frontend:**
```bash
npm install
VITE_BACKEND_URL=http://localhost:3001 npm run build   # or: npm run dev
```

## Demo walkthrough

1. Log in as `analyst@demo.io` / `demo1234`.
2. Open **Customers → John Smith** and click **Run screening**.
3. A **CRITICAL** "Potential sanctions match" case appears in **My Work**.
4. Open it: compare the identity attributes (87% name match, DOB/nationality
   differ), then record **False positive** with a reason.
5. The case closes, the risk score drops, and both the system and your decision
   are visible in the **audit trail**.

Demo users: `analyst@demo.io` (ANALYST), `officer@demo.io` (COMPLIANCE_OFFICER),
`admin@demo.io` (ADMIN) — all `demo1234`. Only a Compliance Officer can
**confirm** a match.
