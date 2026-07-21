"""Pytest fixtures: an isolated app on in-memory sqlite, seeded, with a client
and helpers to obtain JWTs for the demo roles.

Runs entirely offline — no Redis (events process inline) and no external
providers (MockProvider). Each test module gets a fresh database.
"""
import os
import sys

import pytest

# Make `import app` / `import api...` work and force inline event processing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.pop("CELERY_BROKER_URL", None)
os.environ.pop("REDIS_URL", None)
# Forced, not setdefault: `pipenv run pytest` loads .env, so a developer whose
# DATABASE_URL points at a real Postgres would otherwise have the suite try to
# connect to it (and drop_all it). Flask-SQLAlchemy binds the engine at
# init_app, before any fixture can override the config — so it has to be right
# here, at import time.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

# Same reason: a developer's real AI keys in .env would make the Copilot tests
# talk to Gemini/Claude instead of the deterministic mock, and fail offline.
for _key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
             "OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_PROVIDER"):
    os.environ.pop(_key, None)


@pytest.fixture(scope="module")
def app():
    import app as flask_module
    application = flask_module.app
    application.config.update(TESTING=True,
                              SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    from api.models import db
    with application.app_context():
        db.drop_all()
        db.create_all()
        _seed(db)
        yield application
        db.session.remove()
        db.drop_all()


def _seed(db):
    """Minimal but complete seed: RBAC, an org, the demo users, rules, a couple
    of customers, and the risk methodology."""
    from api.rbac import sync_roles, get_role
    from api.models import Organization, User, OrganizationMembership, Customer
    from api.auth import hash_password
    from api.engine import risk_engine
    from api.commands import (DEFAULT_RULES, _seed_requirement_definitions,
                             _seed_risk_methodology, _seed_workflows,
                             _seed_providers, _seed_management, _seed_org_structure)
    from api.models import ComplianceRule

    sync_roles()
    _seed_requirement_definitions()
    _seed_risk_methodology()
    _seed_workflows()
    for spec in DEFAULT_RULES:
        if not ComplianceRule.query.filter_by(name=spec["name"]).first():
            db.session.add(ComplianceRule(**spec))
    db.session.commit()

    org = Organization(name="Test Org")
    other = Organization(name="Other Org")
    db.session.add_all([org, other])
    db.session.flush()

    for email, name, role_name, o in [
        ("analyst@test.io", "A", "KYC_ANALYST", org),
        ("officer@test.io", "O", "COMPLIANCE_OFFICER", org),
        ("manager@test.io", "M", "COMPLIANCE_MANAGER", org),
        ("admin@test.io", "Admin", "ORGANIZATION_ADMIN", org),
        ("outsider@test.io", "X", "KYC_ANALYST", other),
    ]:
        role = get_role(role_name)
        u = User(email=email, full_name=name, role=role_name,
                 role_id=role.id, password=hash_password("pw"),
                 organization_id=o.id, is_active=True)
        db.session.add(u)
        db.session.flush()
        db.session.add(OrganizationMembership(organization_id=o.id, user_id=u.id))
    db.session.commit()

    for cname, country, ctype in [("Marie Dupont", "Luxembourg", "INDIVIDUAL"),
                                  ("John Smith", "United Kingdom", "INDIVIDUAL")]:
        c = Customer(organization_id=org.id, name=cname, country=country,
                     customer_type=ctype, status="ONBOARDING")
        db.session.add(c)
        db.session.flush()
        risk_engine.recompute(c, reason="seed")
    # a customer in the OTHER org, for tenant-isolation tests
    other_c = Customer(organization_id=other.id, name="Foreign Co",
                       country="Panama", customer_type="COMPANY")
    db.session.add(other_c)
    db.session.commit()

    # Teams/assignment + providers (Mock Identity w/ webhook secret) for the org.
    _seed_org_structure(org)
    _seed_management(org)
    _seed_providers(org)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def tokens(app):
    """email-role -> Bearer token for the seeded users."""
    from api.models import User
    from api.auth import make_token
    out = {}
    with app.app_context():
        for u in User.query.all():
            out[u.email] = make_token(u)
    return out


def auth(token):
    return {"Authorization": f"Bearer {token}"}
