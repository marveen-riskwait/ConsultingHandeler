import click

from api.models import (
    db, Organization, User, Customer, ComplianceRule,
)
from api.auth import hash_password

"""
Flask CLI commands. The important one here is `seed-demo`, which creates a
ready-to-explore compliance workspace: an organization, demo users, the default
rule set (PEP / sanctions / adverse media / document expiry) and a few sample
customers — some of which will produce screening hits.
"""


DEFAULT_RULES = [
    {
        "name": "PEP detected -> EDD",
        "event_type": "PEP_DETECTED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_CASE", "case_type": "PEP",
             "title": "PEP detected — Enhanced Due Diligence",
             "priority": "HIGH", "due_days": 5},
            {"type": "CREATE_TASK", "task_type": "EDD_REVIEW",
             "title": "Perform Enhanced Due Diligence",
             "priority": "HIGH", "due_days": 5},
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST"],
             "title": "PEP detected",
             "message": "A politically exposed person was detected. EDD required."},
        ],
    },
    {
        "name": "Sanctions match -> investigation",
        "event_type": "SANCTIONS_MATCH_FOUND",
        "conditions": {},
        "actions": [
            {"type": "CREATE_CASE", "case_type": "SANCTIONS_MATCH",
             "title": "Potential sanctions match",
             "priority": "CRITICAL", "due_days": 1},
            {"type": "CREATE_TASK", "task_type": "SANCTIONS_REVIEW",
             "title": "Compare customer against sanctions record",
             "priority": "CRITICAL", "due_days": 1},
            {"type": "NOTIFY", "severity": "CRITICAL", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST"],
             "title": "Potential sanctions match",
             "message": "Investigate immediately — a potential match was found."},
        ],
    },
    {
        "name": "Adverse media -> review",
        "event_type": "ADVERSE_MEDIA_DETECTED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "ADVERSE_MEDIA_REVIEW",
             "title": "Review adverse media relevance",
             "priority": "MEDIUM", "due_days": 7},
            {"type": "NOTIFY", "severity": "MEDIUM", "requires_action": False,
             "roles": ["ANALYST"],
             "title": "Adverse media detected",
             "message": "Adverse media found — assess relevance."},
        ],
    },
    {
        "name": "Document expiring -> renewal",
        "event_type": "DOCUMENT_EXPIRING",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "DOCUMENT_RENEWAL",
             "title": "Request renewed document",
             "priority": "MEDIUM", "due_days": 14},
            {"type": "NOTIFY", "severity": "MEDIUM", "requires_action": False,
             "roles": ["ANALYST"],
             "title": "Document expiring",
             "message": "A customer document is about to expire."},
        ],
    },
]


SAMPLE_CUSTOMERS = [
    {"name": "Marie Dupont", "customer_type": "INDIVIDUAL",
     "country": "Luxembourg", "business_activity": None},
    {"name": "John Smith", "customer_type": "INDIVIDUAL",
     "country": "United Kingdom", "business_activity": None},  # sanctions hit
    {"name": "Alpha Crypto Ltd", "customer_type": "COMPANY",
     "country": "Panama", "business_activity": "crypto exchange",
     "complex_ownership": True},
    {"name": "Sergei Ivanov", "customer_type": "INDIVIDUAL",
     "country": "Russia", "business_activity": None},  # sanctions + PEP hit
]


def setup_commands(app):

    @app.cli.command("sync-rbac")
    def sync_rbac_cmd():
        """Provision the permission catalog and default system roles."""
        from api.rbac import sync_roles
        sync_roles()
        click.echo("RBAC synced (permissions + system roles).")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create the demo organization, users, rules and sample customers."""
        from api.engine import risk_engine
        from api.rbac import sync_roles, get_role

        # RBAC first: permissions + system roles.
        sync_roles()
        click.echo("RBAC ready (permissions + roles).")

        # Rules (global, idempotent by name).
        for spec in DEFAULT_RULES:
            if not ComplianceRule.query.filter_by(name=spec["name"]).first():
                db.session.add(ComplianceRule(**spec))
        db.session.commit()
        click.echo(f"Rules ready: {ComplianceRule.query.count()}")

        org = Organization.query.filter_by(name="Acme Compliance").first()
        if org is None:
            org = Organization(name="Acme Compliance")
            db.session.add(org)
            db.session.flush()

        demo_users = [
            ("analyst@demo.io", "Alex Analyst", "KYC_ANALYST"),
            ("officer@demo.io", "Olivia Officer", "COMPLIANCE_OFFICER"),
            ("admin@demo.io", "Sam Admin", "PLATFORM_ADMIN"),
        ]
        for email, name, role_name in demo_users:
            role = get_role(role_name)
            existing = User.query.filter_by(email=email).first()
            if existing:
                # keep demo users in sync with the current RBAC definitions
                existing.role = role_name
                existing.role_id = role.id if role else None
            else:
                db.session.add(User(
                    email=email, full_name=name, role=role_name,
                    role_id=role.id if role else None,
                    password=hash_password("demo1234"),
                    organization_id=org.id, is_active=True,
                ))
        db.session.commit()
        click.echo("Demo users: analyst@demo.io / officer@demo.io / admin@demo.io "
                   "(password: demo1234)")

        for spec in SAMPLE_CUSTOMERS:
            if Customer.query.filter_by(name=spec["name"],
                                        organization_id=org.id).first():
                continue
            customer = Customer(organization_id=org.id, status="ONBOARDING", **spec)
            db.session.add(customer)
            db.session.flush()
            risk_engine.recompute(customer, reason="Seed baseline")
        db.session.commit()
        click.echo(f"Sample customers: {Customer.query.count()}")
        click.echo("Done. Log in and run screening on 'John Smith' or "
                   "'Sergei Ivanov' to see the full chain fire.")

    @app.cli.command("insert-test-data")
    def insert_test_data():
        pass
