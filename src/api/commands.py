import os
import click

from api.models import (
    db, Organization, User, Customer, ComplianceRule,
    Party, OwnershipRelationship,
    Department, Team, OrganizationMembership, TeamMembership,
    AssignmentRule, SLAConfiguration, RequirementDefinition,
    Provider, ProviderCredential,
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
        "name": "Unusual transaction -> investigation",
        "event_type": "TRANSACTION_ALERT",
        "conditions": {},
        "actions": [
            {"type": "CREATE_CASE", "case_type": "TRANSACTION_MONITORING",
             "title": "Unusual transaction activity",
             "priority": "HIGH", "due_days": 3},
            {"type": "CREATE_TASK", "task_type": "TRANSACTION_REVIEW",
             "title": "Review flagged transaction activity",
             "priority": "HIGH", "due_days": 3},
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST", "KYC_ANALYST"],
             "title": "Unusual transaction activity",
             "message": "A transaction was flagged by monitoring — review the activity."},
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
    {
        "name": "New director -> screen them",
        "event_type": "DIRECTOR_CHANGED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "DIRECTOR_SCREENING",
             "title": "Screen new director (sanctions / PEP / adverse media)",
             "priority": "HIGH", "due_days": 3},
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST", "KYC_ANALYST"],
             "title": "Director changed",
             "message": "A new director was detected — screening required."},
        ],
    },
    {
        "name": "Ownership changed -> review structure",
        "event_type": "OWNERSHIP_CHANGED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "OWNERSHIP_REVIEW",
             "title": "Review updated ownership structure",
             "priority": "MEDIUM", "due_days": 7},
            {"type": "NOTIFY", "severity": "MEDIUM", "requires_action": False,
             "roles": ["ANALYST", "KYC_ANALYST"],
             "title": "Ownership structure changed",
             "message": "The ownership structure changed — review the graph."},
        ],
    },
    {
        "name": "UBO changed -> verify",
        "event_type": "UBO_CHANGED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "UBO_VERIFICATION",
             "title": "Verify new Ultimate Beneficial Owner",
             "priority": "HIGH", "due_days": 5},
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST", "KYC_ANALYST"],
             "title": "UBO changed",
             "message": "The Ultimate Beneficial Owner set changed — verification required."},
        ],
    },
    {
        "name": "Address changed -> info",
        "event_type": "ADDRESS_CHANGED",
        "conditions": {},
        "actions": [
            {"type": "NOTIFY", "severity": "INFO", "requires_action": False,
             "roles": ["ANALYST", "KYC_ANALYST"],
             "title": "Address changed",
             "message": "A customer address changed."},
        ],
    },
    {
        "name": "Provider verification failed -> remediation",
        "event_type": "PROVIDER_STATUS_CHANGED",
        "conditions": {"payload.status": "FAILED"},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "IDV_REMEDIATION",
             "title": "Identity verification failed — remediate",
             "priority": "HIGH", "due_days": 3},
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["ANALYST", "KYC_ANALYST"],
             "title": "Identity verification failed",
             "message": "A provider returned a failed verification."},
        ],
    },
    {
        "name": "Regulatory change -> notify regulatory manager",
        "event_type": "REGULATORY_REQUIREMENT_CHANGED",
        "conditions": {},
        "actions": [
            {"type": "NOTIFY", "severity": "HIGH", "requires_action": True,
             "roles": ["REGULATORY_MANAGER", "COMPLIANCE_MANAGER"],
             "title": "Regulatory change detected",
             "message": "A regulatory change was detected — assess its impact."},
        ],
    },
    {
        "name": "Provider screening match -> case",
        "event_type": "PROVIDER_STATUS_CHANGED",
        "conditions": {"payload.status": "MATCH"},
        "actions": [
            {"type": "CREATE_CASE", "case_type": "SANCTIONS_MATCH",
             "title": "Provider screening match", "priority": "CRITICAL",
             "due_days": 1},
            {"type": "NOTIFY", "severity": "CRITICAL", "requires_action": True,
             "roles": ["COMPLIANCE_OFFICER", "ANALYST"],
             "title": "Provider screening match",
             "message": "A screening provider reported a potential match."},
        ],
    },
    {
        "name": "Enrichment discrepancy -> data verification",
        "event_type": "ENRICHMENT_DISCREPANCY",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "DATA_VERIFICATION",
             "title": "Registry data differs from declared data — verify",
             "priority": "MEDIUM", "due_days": 3},
            {"type": "NOTIFY", "severity": "MEDIUM", "requires_action": True,
             "roles": ["KYC_ANALYST", "ANALYST"],
             "title": "Enrichment found a discrepancy",
             "message": "An official registry disagrees with declared customer "
                        "data. Verify which value is correct."},
        ],
    },
    {
        "name": "KYC form submitted -> analyst review",
        "event_type": "KYC_FORM_SUBMITTED",
        "conditions": {},
        "actions": [
            {"type": "CREATE_TASK", "task_type": "KYC_REVIEW",
             "title": "Review submitted KYC form",
             "priority": "MEDIUM", "due_days": 3},
            {"type": "NOTIFY", "severity": "INFO", "requires_action": True,
             "roles": ["KYC_ANALYST", "ANALYST"],
             "title": "KYC form submitted",
             "message": "A customer KYC form was submitted and awaits review."},
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


def _seed_org_structure(org):
    """Departments, Teams, memberships for the demo org (idempotent)."""
    # Organization membership for every user.
    for u in User.query.filter_by(organization_id=org.id).all():
        if not OrganizationMembership.query.filter_by(
                organization_id=org.id, user_id=u.id).first():
            db.session.add(OrganizationMembership(
                organization_id=org.id, user_id=u.id, status="ACTIVE"))

    dept = Department.query.filter_by(name="Compliance Department",
                                      organization_id=org.id).first()
    if dept is None:
        dept = Department(organization_id=org.id, name="Compliance Department")
        db.session.add(dept)
        db.session.flush()

    manager = User.query.filter_by(email="manager@demo.io").first()
    team = Team.query.filter_by(name="KYC Team", organization_id=org.id).first()
    if team is None:
        team = Team(organization_id=org.id, department_id=dept.id, name="KYC Team",
                    manager_id=manager.id if manager else None)
        db.session.add(team)
        db.session.flush()

    members = [
        ("analyst@demo.io", "MEMBER"),
        ("officer@demo.io", "MEMBER"),
        ("manager@demo.io", "MANAGER"),
    ]
    for email, role_in_team in members:
        u = User.query.filter_by(email=email).first()
        if u and not TeamMembership.query.filter_by(team_id=team.id, user_id=u.id).first():
            db.session.add(TeamMembership(team_id=team.id, user_id=u.id,
                                          role_in_team=role_in_team))
    db.session.commit()


# System-level requirement definitions (organization_id = NULL).
# min_risk_rank: 0=always, 2=HIGH and above (EDD).
DEFAULT_REQUIREMENTS = [
    # Trusts / legal arrangements (FATF R.25: role-based beneficial ownership)
    ("TRUST_DEED", "Trust deed / declaration of trust", "DOCUMENT", "TRUST", 0, None, "TRUST_DEED"),
    ("TRUSTEE_ID", "Trustee identity document(s)", "DOCUMENT", "TRUST", 0, None, "TRUSTEE_ID"),
    ("TRUST_TYPE", "Type of trust", "DATA", "TRUST", 0, "trust_type", None),
    ("GOVERNING_LAW", "Governing law", "DATA", "TRUST", 0, "governing_law", None),
    ("PURPOSE_OF_TRUST", "Purpose of the trust", "DATA", "TRUST", 0, "purpose_of_trust", None),
    ("SETTLOR_NAMES", "Settlor(s) declared", "DATA", "TRUST", 0, "settlor_names", None),
    ("TRUSTEE_NAMES", "Trustee(s) declared", "DATA", "TRUST", 0, "trustee_names", None),
    ("BENEFICIARY_NAMES", "Beneficiaries / class declared", "DATA", "TRUST", 0, "beneficiary_names", None),
    # Individuals
    ("IDENTITY_DOCUMENT", "Identity document", "DOCUMENT", "INDIVIDUAL", 0, None, "PASSPORT"),
    ("PROOF_OF_ADDRESS", "Proof of address", "DOCUMENT", "INDIVIDUAL", 0, None, "PROOF_OF_ADDRESS"),
    ("DATE_OF_BIRTH", "Date of birth", "DATA", "INDIVIDUAL", 0, "date_of_birth", None),
    ("NATIONALITY", "Nationality", "DATA", "INDIVIDUAL", 0, "nationality", None),
    ("OCCUPATION", "Occupation", "DATA", "INDIVIDUAL", 0, "occupation", None),
    # Companies
    ("CERTIFICATE_OF_INCORPORATION", "Certificate of incorporation", "DOCUMENT", "COMPANY", 0, None, "CERTIFICATE_OF_INCORPORATION"),
    ("ARTICLES_OF_ASSOCIATION", "Articles of association", "DOCUMENT", "COMPANY", 0, None, "ARTICLES_OF_ASSOCIATION"),
    ("REGISTRATION_NUMBER", "Registration number", "DATA", "COMPANY", 0, "registration_number", None),
    ("BUSINESS_ACTIVITY", "Business activity", "DATA", "COMPANY", 0, "business_activity", None),
    ("TAX_RESIDENCE", "Country of tax residence", "DATA", "INDIVIDUAL", 0, "country_of_tax_residence", None),
    ("PROOF_OF_INCOME", "Proof of income / revenue", "DOCUMENT", "INDIVIDUAL", 1, None, "PROOF_OF_INCOME"),
    ("SHAREHOLDER_REGISTER", "Shareholder register", "DOCUMENT", "COMPANY", 1, None, "SHAREHOLDER_REGISTER"),
    ("FINANCIAL_STATEMENTS", "Financial statements", "DOCUMENT", "COMPANY", 1, None, "FINANCIAL_STATEMENTS"),
    # Any customer
    ("PURPOSE_OF_RELATIONSHIP", "Purpose of relationship", "DATA", "ANY", 0, "purpose_of_relationship", None),
    ("EXPECTED_ACTIVITY", "Expected account activity", "DATA", "ANY", 0, "expected_monthly_volume", None),
    ("PEP_SELF_DECLARATION", "PEP self-declaration", "DATA", "ANY", 0, "pep_self_declaration", None),
    # Enhanced Due Diligence (HIGH risk and above)
    ("SOURCE_OF_FUNDS", "Source of funds", "DATA", "ANY", 2, "source_of_funds", None),
    ("SOURCE_OF_WEALTH", "Source of wealth", "DATA", "ANY", 2, "source_of_wealth", None),
    ("SOURCE_OF_FUNDS_EVIDENCE", "Source of funds evidence", "DOCUMENT", "ANY", 2, None, "SOURCE_OF_FUNDS_EVIDENCE"),
]


def _seed_regulatory():
    """A starter regulatory catalog mapping obligations to software controls."""
    from api.models import (RegulatorySource, RegulatoryRequirement,
                            ComplianceControl, RegulatoryChange)
    if RegulatorySource.query.first():
        return

    sources = {}
    for key, name, authority, jur, stype, url in [
        ("FATF", "FATF Recommendations", "FATF", "International", "RECOMMENDATION",
         "https://www.fatf-gafi.org/en/publications/Fatfrecommendations/"),
        ("AMLR", "Regulation (EU) 2024/1624 (AML Regulation)", "EU", "European Union",
         "REGULATION", "https://eur-lex.europa.eu/eli/reg/2024/1624/oj"),
        ("AMLA", "AMLA regulatory instruments", "AMLA", "European Union", "RTS",
         "https://www.amla.europa.eu/"),
        ("CSSF", "CSSF AML/CFT framework", "CSSF", "Luxembourg", "CIRCULAR",
         "https://www.cssf.lu/en/anti-money-laundering-and-terrorist-financing/"),
    ]:
        s = RegulatorySource(organization_id=None, name=name, authority=authority,
                             jurisdiction=jur, source_type=stype, official_url=url)
        db.session.add(s)
        db.session.flush()
        sources[key] = s

    # requirement -> control (software module) mapping (the doc's obligation matrix).
    reqs = [
        ("AMLR", "Art. 20", "Identify and verify the customer", "CDD",
         "Identity verification workflow", "KYC module", "IMPLEMENTED"),
        ("AMLR", "Art. 51", "Identify the beneficial owner(s)", "UBO",
         "Ownership graph + UBO detection", "Ownership engine", "IMPLEMENTED"),
        ("AMLR", "Art. 26", "Keep customer data up to date (ongoing monitoring)", "MONITORING",
         "Automatic review triggers + monitoring", "Review/Monitoring engine", "IMPLEMENTED"),
        ("FATF", "Rec. 10", "Risk-based customer due diligence", "RISK",
         "Data-driven risk methodology", "Risk engine", "IMPLEMENTED"),
        ("FATF", "Rec. 12", "Enhanced due diligence for PEPs", "EDD",
         "EDD workflow with senior approval", "Workflow engine", "IMPLEMENTED"),
        ("CSSF", "Reg. 12-02", "Record keeping & audit trail", "RECORD_KEEPING",
         "Immutable audit trail", "Audit engine", "IMPLEMENTED"),
        ("AMLA", "Consultation", "Harmonised ongoing-monitoring reporting formats", "REPORTING",
         "Regulatory reporting", "Reporting module", "NEEDS_REVIEW"),
    ]
    for skey, art, title, obl, control_name, module, status in reqs:
        r = RegulatoryRequirement(source_id=sources[skey].id, article_reference=art,
                                  title=title, obligation_type=obl)
        db.session.add(r)
        db.session.flush()
        db.session.add(ComplianceControl(
            organization_id=None, requirement_id=r.id, name=control_name,
            control_type=obl, software_module=module, implementation_status=status))

    # A sample detected change so the dashboard shows real content.
    db.session.add(RegulatoryChange(
        organization_id=None, source_id=sources["AMLA"].id,
        title="New AMLA guidance on ongoing monitoring",
        summary="AMLA published guidance affecting Article 26(5) ongoing-monitoring "
                "expectations; review monitoring frequency methodology.",
        impact_level="HIGH", status="NEW"))
    db.session.commit()


def _seed_workflows():
    """System workflow definitions (organization_id = NULL)."""
    from api.models import WorkflowDefinition, WorkflowStep
    specs = [
        ("EDD", "Enhanced Due Diligence", "PEP", [
            ("GATHER", "Gather information", False, None),
            ("SOW", "Review source of wealth", False, None),
            ("SOF", "Review source of funds", False, None),
            ("SCREEN", "Complete screening", False, None),
            ("RISK", "Assess risk", False, None),
            ("SENIOR_APPROVAL", "Senior approval", True, "COMPLIANCE_OFFICER"),
            ("DECISION", "Final decision", False, None),
            ("CLOSE", "Close", False, None),
        ]),
        ("SANCTIONS_INVESTIGATION", "Sanctions investigation", "SANCTIONS_MATCH", [
            ("CONFIRM_IDENTITY", "Confirm identity", False, None),
            ("COMPARE", "Compare against sanctions record", False, None),
            ("ASSESS", "Assess the match", False, None),
            ("DECISION", "Compliance decision", True, "COMPLIANCE_OFFICER"),
        ]),
    ]
    for code, name, case_type, steps in specs:
        if WorkflowDefinition.query.filter_by(code=code, organization_id=None).first():
            continue
        wf = WorkflowDefinition(organization_id=None, code=code, name=name,
                                applies_case_type=case_type, active=True)
        db.session.add(wf)
        db.session.flush()
        for i, (scode, sname, req, role) in enumerate(steps, start=1):
            db.session.add(WorkflowStep(definition_id=wf.id, order=i, code=scode,
                                        name=sname, requires_approval=req,
                                        approver_role=role))
    db.session.commit()


def _seed_risk_methodology():
    """Default system risk methodology v1 — mirrors the legacy hardcoded model,
    now data-driven and editable."""
    from api.models import (RiskMethodology, RiskFactor, RiskThreshold,
                            HIGH_RISK_ACTIVITIES)
    if RiskMethodology.query.filter_by(organization_id=None, version="v1").first():
        return
    meth = RiskMethodology(organization_id=None, version="v1",
                           name="Standard Methodology v1", active=True)
    db.session.add(meth)
    db.session.flush()

    factors = [
        ("PEP", "Politically Exposed Person detected", 30, "FLAG", {"field": "is_pep"}),
        ("SANCTIONS", "Potential sanctions match", 40, "FLAG", {"field": "has_sanctions_match"}),
        ("ADVERSE_MEDIA", "Relevant adverse media", 20, "FLAG", {"field": "has_adverse_media"}),
        ("OWNERSHIP", "Complex ownership structure", 15, "FLAG", {"field": "complex_ownership"}),
        # Geography is not seeded here: country_risk.sync() installs the three
        # official-list factors (FATF Call for Action / EU high-risk / FATF
        # Increased Monitoring) so the score can be traced to a published list.
        ("BUSINESS", "High-risk business activity", 25, "ACTIVITY_IN",
         {"values": sorted(HIGH_RISK_ACTIVITIES)}),
    ]
    for code, label, impact, ctype, cval in factors:
        db.session.add(RiskFactor(methodology_id=meth.id, code=code, label=label,
                                  impact=impact, condition_type=ctype,
                                  condition_value=cval))
    for level, lo, hi in [("LOW", 0, 30), ("MEDIUM", 31, 70),
                          ("HIGH", 71, 100), ("CRITICAL", 101, None)]:
        db.session.add(RiskThreshold(methodology_id=meth.id, level=level,
                                     min_score=lo, max_score=hi))
    db.session.commit()


def _seed_requirement_definitions():
    for code, label, kind, ctype, rank, data_field, doc_type in DEFAULT_REQUIREMENTS:
        exists = (RequirementDefinition.query
                  .filter_by(code=code, organization_id=None).first())
        if not exists:
            db.session.add(RequirementDefinition(
                organization_id=None, code=code, label=label, kind=kind,
                applies_customer_type=ctype, min_risk_rank=rank,
                data_field=data_field, doc_type=doc_type))
    db.session.commit()


def _seed_chat(org):
    """A team group chat with the demo users + a couple of starter messages."""
    from api.models import ChatRoom, ChatMember, ChatMessage
    if ChatRoom.query.filter_by(organization_id=org.id,
                                name="Compliance Team").first():
        return
    users = User.query.filter_by(organization_id=org.id).all()
    if not users:
        return
    room = ChatRoom(organization_id=org.id, is_group=True,
                    name="Compliance Team", created_by=users[0].id)
    db.session.add(room)
    db.session.flush()
    for u in users:
        db.session.add(ChatMember(room_id=room.id, user_id=u.id))
    officer = next((u for u in users if "officer" in u.email), users[0])
    manager = next((u for u in users if "manager" in u.email), users[-1])
    db.session.add_all([
        ChatMessage(room_id=room.id, sender_id=manager.id, kind="TEXT",
                    body="Welcome to the team channel — cases, reviews and "
                         "escalations get discussed here."),
        ChatMessage(room_id=room.id, sender_id=officer.id, kind="TEXT",
                    body="The Sberbank sanctions cases are assigned; call me "
                         "here if you need a second pair of eyes."),
    ])
    db.session.commit()


def _seed_providers(org):
    """A working mock provider + prepared (disabled) real stubs."""
    specs = [
        ("Mock Identity", "KYC", "mock", True,
         [("webhook_secret", "demo-secret")]),
        ("Sumsub", "KYC", "sumsub", False, []),
        ("ComplyAdvantage", "AML", "comply_advantage", False, []),
        # Real UK registry lookups; enabled because the adapter reports a clear
        # error until an api_key credential (or COMPANIES_HOUSE_API_KEY) is set.
        ("Companies House", "KYB", "companies_house", True, []),
    ]
    for name, ptype, adapter, enabled, creds in specs:
        provider = Provider.query.filter_by(name=name, organization_id=org.id).first()
        if provider is None:
            provider = Provider(organization_id=org.id, name=name,
                                provider_type=ptype, adapter=adapter, enabled=enabled)
            db.session.add(provider)
            db.session.flush()
            for key_name, value in creds:
                db.session.add(ProviderCredential(
                    provider_id=provider.id, key_name=key_name, secret_value=value))
    db.session.commit()


def _seed_management(org):
    """Default SLA targets + assignment rules for the demo org (idempotent)."""
    for priority, hours in (("CRITICAL", 24), ("HIGH", 72),
                            ("MEDIUM", 120), ("LOW", 240)):
        if not SLAConfiguration.query.filter_by(
                organization_id=org.id, case_priority=priority).first():
            db.session.add(SLAConfiguration(
                organization_id=org.id, case_priority=priority,
                target_hours=hours))

    kyc_team = Team.query.filter_by(name="KYC Team", organization_id=org.id).first()
    rules = [
        {"name": "Sanctions -> least loaded (KYC Team)",
         "case_type": "SANCTIONS_MATCH", "risk_level": None,
         "team_id": kyc_team.id if kyc_team else None,
         "strategy": "LEAST_LOADED", "priority": 10},
        {"name": "High risk -> senior staff",
         "case_type": None, "risk_level": "HIGH",
         "team_id": None, "strategy": "RISK_BASED", "priority": 20},
        {"name": "Default -> round robin",
         "case_type": None, "risk_level": None,
         "team_id": kyc_team.id if kyc_team else None,
         "strategy": "ROUND_ROBIN", "priority": 100},
    ]
    for spec in rules:
        if not AssignmentRule.query.filter_by(
                organization_id=org.id, name=spec["name"]).first():
            db.session.add(AssignmentRule(organization_id=org.id, **spec))
    db.session.commit()


def _seed_ownership(org):
    """Build the ownership graph for Alpha Crypto Ltd:
        John Smith --80%--> Beta Holdings --60%--> Alpha Crypto Ltd
        Jane Doe   --40%--------------------------> Alpha Crypto Ltd
    UBOs => John 48% (indirect), Jane 40% (direct)."""
    alpha = Customer.query.filter_by(name="Alpha Crypto Ltd",
                                     organization_id=org.id).first()
    if alpha is None or alpha.root_party_id:
        return

    from api.models import Person, LegalEntity

    def party(cls, name, **kw):
        p = cls(organization_id=org.id, name=name, **kw)
        db.session.add(p)
        db.session.flush()
        return p

    root = party(LegalEntity, "Alpha Crypto Ltd", customer_id=alpha.id,
                 business_activity="crypto exchange", country_of_incorporation="Panama")
    alpha.root_party_id = root.id
    beta = party(LegalEntity, "Beta Holdings", country_of_incorporation="Luxembourg")
    john = party(Person, "John Smith", nationality="United Kingdom",
                 country_of_residence="United Kingdom")
    jane = party(Person, "Jane Doe", nationality="France",
                 country_of_residence="Luxembourg")

    def edge(owner, owned, pct, rtype="SHAREHOLDER"):
        db.session.add(OwnershipRelationship(
            organization_id=org.id, owner_party_id=owner.id,
            owned_party_id=owned.id, percentage=pct, relationship_type=rtype))

    edge(beta, root, 60)
    edge(john, beta, 80)
    edge(jane, root, 40)
    db.session.commit()


def setup_commands(app):

    @app.cli.command("mail-test")
    @click.argument("recipient")
    def mail_test(recipient):
        """Send the customer notification to an address, to check the setup."""
        from api.integrations import mailer
        how = mailer.transport()
        if how is None:
            click.echo("No transport configured. Set BREVO_API_KEY (or "
                       "SMTP_HOST) and MAIL_FROM in .env.")
            return
        click.echo(f"Transport: {how} · from: {os.getenv('MAIL_FROM')} · "
                   f"portal link: {mailer.portal_url() or '(none — set PORTAL_URL)'}")
        result = mailer.send(
            recipient, "Test — this is what a customer receives",
            "Hello,\n\nThis is the notification a customer gets when the firm "
            "is waiting on them. It deliberately says nothing about their "
            "file.\n\n" + mailer._sign_in_line() + "\n")
        click.echo(f"Result: {result}")

    @app.cli.command("sync-country-risk")
    def sync_country_risk():
        """Refresh geography risk factors from the FATF / EU official lists."""
        from api.engine import country_risk
        out = country_risk.sync()
        for row in out.get("synced", []):
            flag = " STALE" if row["stale"] else ""
            click.echo(f"{row['code']}: {row['countries']} countries "
                       f"(as of {row['as_of']}{flag})")

    @app.cli.command("sync-rbac")
    def sync_rbac_cmd():
        """Provision the permission catalog and default system roles."""
        from api.rbac import sync_roles
        sync_roles()
        click.echo("RBAC synced (permissions + system roles).")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create the demo organization, users, rules and sample customers."""
        from api.engine import risk_engine, review_engine
        from api.rbac import sync_roles, get_role

        # RBAC first: permissions + system roles.
        sync_roles()
        click.echo("RBAC ready (permissions + roles).")

        _seed_requirement_definitions()
        click.echo(f"Requirement definitions ready: "
                   f"{RequirementDefinition.query.count()}")

        _seed_risk_methodology()
        from api.engine import country_risk
        geo = country_risk.sync(prefer_live=False,
                                institution_countries=["Russia", "Panama"])
        click.echo("Risk methodology v1 seeded (5 factors + 4 thresholds), "
                   f"plus {len(geo.get('synced', []))} official geography lists "
                   "(FATF Call for Action / EU high-risk / FATF monitoring).")

        _seed_workflows()
        click.echo("Workflows seeded (EDD 8-step w/ senior approval, "
                   "sanctions investigation).")

        _seed_regulatory()
        click.echo("Regulatory catalog seeded (FATF / EU AMLR / AMLA / CSSF "
                   "+ requirements, controls, a sample change).")

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
            ("manager@demo.io", "Mia Manager", "COMPLIANCE_MANAGER"),
            ("admin@demo.io", "Sam Admin", "ADMIN"),
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
        _seed_org_structure(org)
        click.echo("Demo users: analyst@ / officer@ / manager@ / admin@demo.io "
                   "(password: demo1234)")

        for spec in SAMPLE_CUSTOMERS:
            if Customer.query.filter_by(name=spec["name"],
                                        organization_id=org.id).first():
                continue
            customer = Customer(organization_id=org.id, status="ONBOARDING", **spec)
            db.session.add(customer)
            db.session.flush()
            risk_engine.recompute(customer, reason="Seed baseline")
            review_engine.schedule_initial(customer)
        db.session.commit()
        click.echo(f"Sample customers: {Customer.query.count()}")

        _seed_ownership(org)
        click.echo("Ownership graph seeded for 'Alpha Crypto Ltd' "
                   "(UBOs: John Smith 48%, Jane Doe 40%).")

        _seed_management(org)
        click.echo("Management seeded: SLA targets + assignment rules "
                   "(sanctions -> LEAST_LOADED on KYC Team, default ROUND_ROBIN).")

        _seed_providers(org)
        click.echo("Providers seeded: Mock Identity (KYC, webhook secret 'demo-secret'), "
                   "Sumsub + ComplyAdvantage stubs (disabled, need credentials).")
        _seed_chat(org)
        click.echo("Team chat seeded: 'Compliance Team' group with the demo users.")
        click.echo("Done. Log in and run screening on 'John Smith' or "
                   "'Sergei Ivanov' to see the full chain fire.")

    @app.cli.command("ingest-watchlists")
    @click.option("--source", default="ALL",
                  help="OFAC, UN, EU or ALL (default).")
    @click.option("--sample", is_flag=True, default=False,
                  help="Skip the live fetch and load the bundled samples.")
    @click.option("--limit", default=None, type=int,
                  help="Cap records per source (handy for quick local runs).")
    def ingest_watchlists(source, sample, limit):
        """Download the public sanctions lists (OFAC SDN / UN / EU) into the
        local watchlist used for screening."""
        from api.engine import watchlist_service
        prefer_live = not sample
        if source.upper() == "ALL":
            imports = watchlist_service.ingest_all(prefer_live=prefer_live,
                                                   limit=limit)
        else:
            imports = [watchlist_service.ingest(source, prefer_live=prefer_live,
                                                limit=limit)]
        for imp in imports:
            click.echo(f"{imp.source}: {imp.status} — {imp.detail}")

    @app.cli.command("insert-test-data")
    def insert_test_data():
        pass
