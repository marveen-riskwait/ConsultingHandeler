"""The KYC intake form — a data-driven definition of the full CDD questionnaire.

One place describes every section, field and proof the platform asks a customer
for. The frontend renders this schema; submissions land in ProfileField (with
provenance, source="kyc_form") and Document, which the Requirement Engine
already evaluates — so filling the form directly advances compliance
completeness, and EDD sections appear automatically at higher risk.

Content is consolidated from the standard corpus: FATF R.10 (CDD), R.12/22
(PEPs), R.24 (beneficial ownership), EU Regulation 2024/1624 (AMLR), the EBA
ML/TF Risk Factors Guidelines, the Wolfsberg CBDDQ/PBDDQ questionnaires and
CRS/FATCA-style tax self-certification.

Field `type`s the frontend understands:
    text | textarea | date | number | select | multiselect | boolean | country
`options` applies to select/multiselect. `required` is informational for the
UI — hard obligations live in RequirementDefinition, not here.
"""

# Reusable option lists ------------------------------------------------------
SOURCE_OF_FUNDS_OPTIONS = [
    "Salary / employment income", "Business revenue", "Sale of property",
    "Sale of shares / business", "Investment income", "Dividends", "Loan",
    "Inheritance", "Gift", "Cryptocurrency sale", "Pension",
    "Government benefits", "Savings", "Other",
]

SOURCE_OF_WEALTH_OPTIONS = [
    "Employment history", "Business ownership", "Investments", "Real estate",
    "Inheritance", "Family wealth", "Sale of business", "Dividends",
    "Capital contributions", "Group financing", "Other",
]

EXPECTED_PRODUCTS_OPTIONS = [
    "Payments account", "Cross-border transfers", "FX", "Cards",
    "Merchant acquiring", "Lending", "Custody", "Crypto services",
    "Trade finance", "Treasury", "Other",
]

PAYMENT_METHOD_OPTIONS = [
    "SEPA transfers", "SWIFT wires", "Card payments", "Direct debit",
    "Cash", "Cheques", "Crypto-assets", "E-money",
]

EMPLOYMENT_STATUS_OPTIONS = [
    "Employed", "Self-employed", "Business owner", "Retired", "Student",
    "Unemployed", "Other",
]

LEGAL_FORM_OPTIONS = [
    "Private limited company", "Public limited company", "Partnership",
    "Sole proprietorship", "Trust", "Foundation", "Non-profit / association",
    "Government entity", "Financial institution", "Other legal arrangement",
]

VOLUME_BANDS = ["< €10,000", "€10,000 – €50,000", "€50,000 – €250,000",
                "€250,000 – €1,000,000", "> €1,000,000"]

YES_NO = ["No", "Yes"]


def _f(key, label, type="text", *, category=None, options=None, required=False,
       help=None):
    out = {"key": key, "label": label, "type": type, "required": required}
    if category:
        out["category"] = category
    if options:
        out["options"] = options
    if help:
        out["help"] = help
    return out


# --------------------------------------------------------------------------- #
# Sections. `applies`: INDIVIDUAL | COMPANY | ANY. `min_risk_rank` mirrors the
# requirement engine (0=always, 2=HIGH+ → the EDD deep-dive sections).
# --------------------------------------------------------------------------- #
FORM_SECTIONS = [
    # ============================== INDIVIDUAL ==============================
    {
        "key": "identity", "title": "Personal identity", "icon": "fa-id-card",
        "applies": "INDIVIDUAL", "min_risk_rank": 0,
        "description": "Who the customer is, exactly as on their identity document.",
        "fields": [
            _f("first_name", "First name", required=True, category="IDENTITY"),
            _f("middle_names", "Middle name(s)", category="IDENTITY"),
            _f("last_name", "Last name", required=True, category="IDENTITY"),
            _f("previous_names", "Previous names / aliases", category="IDENTITY",
               help="Maiden names, legal name changes, known aliases."),
            _f("date_of_birth", "Date of birth", "date", required=True, category="PERSONAL"),
            _f("place_of_birth", "Place of birth", category="PERSONAL"),
            _f("country_of_birth", "Country of birth", "country", category="PERSONAL"),
            _f("nationality", "Nationality", "country", required=True, category="NATIONALITY"),
            _f("other_nationalities", "Other nationalities", category="NATIONALITY"),
            _f("gender", "Gender (where legally relevant)", "select",
               options=["—", "Female", "Male", "Other"], category="PERSONAL"),
            _f("national_id_number", "National identification number", category="IDENTITY"),
        ],
    },
    {
        "key": "tax", "title": "Tax residence", "icon": "fa-file-invoice-dollar",
        "applies": "INDIVIDUAL", "min_risk_rank": 0,
        "description": "CRS/FATCA-style self-certification.",
        "fields": [
            _f("country_of_tax_residence", "Country of tax residence", "country",
               required=True, category="TAX"),
            _f("other_tax_residencies", "Other tax residencies", category="TAX"),
            _f("tax_identification_number", "Tax identification number (TIN)",
               category="TAX"),
            _f("us_person", "US person (FATCA)?", "select", options=YES_NO,
               category="TAX"),
        ],
    },
    {
        "key": "contact", "title": "Contact & address", "icon": "fa-location-dot",
        "applies": "INDIVIDUAL", "min_risk_rank": 0,
        "description": "Residential address and verified contact points.",
        "fields": [
            # Structured, in the same shape as the Addresses card on the
            # customer file — saving here syncs the current RESIDENTIAL
            # address there (kyc_service.sync_address_from_form), so nobody
            # retypes what the form already collected.
            _f("residential_street_number", "Street number", category="ADDRESS"),
            _f("residential_street_name", "Street name", required=True,
               category="ADDRESS"),
            _f("residential_city", "City", required=True, category="ADDRESS"),
            _f("residential_postal_code", "Postal code", category="ADDRESS"),
            _f("residential_country", "Country", "country", required=True,
               category="ADDRESS"),
            _f("previous_address", "Previous address (if moved < 2 years ago)",
               "textarea", category="ADDRESS"),
            _f("phone_number", "Phone number", required=True, category="ADDRESS"),
            _f("email_address", "Email address", required=True, category="ADDRESS"),
        ],
    },
    {
        "key": "occupation", "title": "Occupation & financial profile",
        "icon": "fa-briefcase", "applies": "INDIVIDUAL", "min_risk_rank": 0,
        "description": "What the customer does and their financial standing.",
        "fields": [
            _f("employment_status", "Employment status", "select",
               options=EMPLOYMENT_STATUS_OPTIONS, category="OCCUPATION"),
            _f("occupation", "Occupation / profession", required=True,
               category="OCCUPATION"),
            _f("employer_name", "Employer / own business name", category="OCCUPATION"),
            _f("employer_industry", "Employer industry / sector", category="OCCUPATION"),
            _f("annual_income", "Annual income (EUR)", "select",
               options=VOLUME_BANDS, category="OCCUPATION"),
            _f("estimated_net_worth", "Estimated net worth (EUR)", "select",
               options=VOLUME_BANDS, category="OCCUPATION"),
        ],
    },

    # ================================ COMPANY ===============================
    {
        "key": "company", "title": "Company identification", "icon": "fa-building",
        "applies": "COMPANY", "min_risk_rank": 0,
        "description": "The legal entity, as registered.",
        "fields": [
            _f("legal_name", "Legal name", required=True, category="REGISTRATION"),
            _f("trading_name", "Trading name (if different)", category="REGISTRATION"),
            _f("previous_names", "Previous names", category="REGISTRATION"),
            _f("legal_form", "Legal form", "select", options=LEGAL_FORM_OPTIONS,
               required=True, category="REGISTRATION"),
            _f("registration_number", "Registration number", required=True,
               category="REGISTRATION",
               help="Companies House / RCS / commercial register number."),
            _f("lei", "LEI (if any)", category="REGISTRATION"),
            _f("date_of_incorporation", "Date of incorporation", "date",
               category="REGISTRATION"),
            _f("country_of_incorporation", "Country of incorporation", "country",
               required=True, category="REGISTRATION"),
            _f("tax_identification_number", "Tax identification number",
               category="TAX"),
            _f("vat_number", "VAT number", category="TAX"),
            _f("website", "Website", category="REGISTRATION"),
        ],
    },
    {
        "key": "company_contact", "title": "Addresses & contact",
        "icon": "fa-location-dot", "applies": "COMPANY", "min_risk_rank": 0,
        "description": "Registered office and where business actually happens.",
        "fields": [
            _f("registered_office", "Registered office address", "textarea",
               required=True, category="ADDRESS"),
            _f("principal_place_of_business", "Principal place of business",
               "textarea", category="ADDRESS",
               help="If different from the registered office."),
            _f("phone_number", "Phone number", category="ADDRESS"),
            _f("email_address", "Email address", required=True, category="ADDRESS"),
        ],
    },
    {
        "key": "business", "title": "Business activity", "icon": "fa-industry",
        "applies": "COMPANY", "min_risk_rank": 0,
        "description": "What the company does, where, and at what scale.",
        "fields": [
            _f("business_activity", "Main business activity", "textarea",
               required=True, category="BUSINESS"),
            _f("industry_sector", "Industry / sector", category="BUSINESS"),
            _f("nace_code", "NACE / SIC code", category="BUSINESS"),
            _f("products_services", "Products sold / services provided",
               "textarea", category="BUSINESS"),
            _f("target_customers", "Target customers", category="BUSINESS",
               help="Retail, corporate, financial institutions…"),
            _f("number_of_employees", "Number of employees", "number",
               category="BUSINESS"),
            _f("annual_turnover", "Annual turnover (EUR)", "select",
               options=VOLUME_BANDS, category="BUSINESS"),
            _f("countries_of_operation", "Countries of operation",
               category="BUSINESS"),
            _f("customer_countries", "Main customer countries", category="BUSINESS"),
            _f("supplier_countries", "Main supplier countries", category="BUSINESS"),
        ],
    },
    {
        "key": "ownership", "title": "Ownership & control", "icon": "fa-sitemap",
        "applies": "COMPANY", "min_risk_rank": 0,
        "description": ("Beneficial owners are declared here and recorded in the "
                        "ownership graph (25% threshold, direct or indirect)."),
        "fields": [
            _f("has_ubo_over_25", "Any natural person owning/controlling ≥ 25%?",
               "select", options=YES_NO, required=True, category="BUSINESS"),
            _f("ownership_structure_notes", "Ownership structure explanation",
               "textarea", category="BUSINESS",
               help="Describe layers, holding companies, trusts or control by "
                    "other means. If no ownership-based UBO exists, explain who "
                    "exercises control (senior managing official)."),
            _f("control_by_other_means", "Control exercised by other means?",
               "select", options=YES_NO, category="BUSINESS",
               help="Voting arrangements, agreements, golden shares…"),
        ],
    },

    # ================================= ANY ==================================
    {
        "key": "purpose", "title": "Purpose & expected activity",
        "icon": "fa-bullseye", "applies": "ANY", "min_risk_rank": 0,
        "description": ("Why the relationship exists and what normal activity "
                        "will look like — monitoring compares reality to this."),
        "fields": [
            _f("purpose_of_relationship", "Purpose of the relationship",
               "textarea", required=True, category="PURPOSE"),
            _f("products_requested", "Products / services requested",
               "multiselect", options=EXPECTED_PRODUCTS_OPTIONS, category="PURPOSE"),
            _f("expected_monthly_volume", "Expected monthly volume (EUR)",
               "select", options=VOLUME_BANDS, required=True, category="PURPOSE"),
            _f("expected_transaction_count", "Expected transactions per month",
               "number", category="PURPOSE"),
            _f("expected_payment_methods", "Expected payment methods",
               "multiselect", options=PAYMENT_METHOD_OPTIONS, category="PURPOSE"),
            _f("expected_currencies", "Expected currencies", category="PURPOSE"),
            _f("expected_countries", "Expected transaction countries",
               category="PURPOSE",
               help="Where money will be sent to / received from."),
            _f("expected_counterparties", "Main expected counterparties",
               "textarea", category="PURPOSE"),
        ],
    },
    {
        "key": "funds", "title": "Source of funds & wealth", "icon": "fa-coins",
        "applies": "ANY", "min_risk_rank": 0,
        "description": ("Source of Funds = where this money comes from. "
                        "Source of Wealth = how the overall wealth was built."),
        "fields": [
            _f("source_of_funds", "Source of funds", "multiselect",
               options=SOURCE_OF_FUNDS_OPTIONS, required=True,
               category="SOURCE_OF_FUNDS"),
            _f("source_of_funds_details", "Source of funds — details",
               "textarea", category="SOURCE_OF_FUNDS",
               help="Amounts, dates, origin accounts. Attach evidence in Proofs."),
            _f("source_of_wealth", "Source of wealth", "multiselect",
               options=SOURCE_OF_WEALTH_OPTIONS, category="SOURCE_OF_WEALTH"),
            _f("source_of_wealth_details", "Source of wealth — details",
               "textarea", category="SOURCE_OF_WEALTH"),
        ],
    },
    {
        "key": "pep", "title": "PEP declaration", "icon": "fa-landmark",
        "applies": "ANY", "min_risk_rank": 0,
        "description": ("Politically exposed persons: the customer, beneficial "
                        "owners, directors, their family members and close "
                        "associates. Screening runs independently — this is the "
                        "customer's own declaration."),
        "fields": [
            _f("pep_self_declaration",
               "Is the customer (or any UBO / director) a PEP?", "select",
               options=YES_NO, required=True, category="PURPOSE"),
            _f("pep_position", "Public function / position held", category="PURPOSE"),
            _f("pep_country", "Country of the public function", "country",
               category="PURPOSE"),
            _f("pep_relationship", "If family member / close associate: relationship",
               category="PURPOSE"),
        ],
    },

    # ============================== EDD (HIGH+) =============================
    {
        "key": "edd", "title": "Enhanced Due Diligence", "icon": "fa-magnifying-glass-plus",
        "applies": "ANY", "min_risk_rank": 2,
        "description": ("This customer is rated HIGH risk or above — enhanced "
                        "measures apply (EU AMLR / FATF R.19)."),
        "fields": [
            _f("sow_evidence_summary", "Documented source of wealth (detail)",
               "textarea", category="SOURCE_OF_WEALTH",
               help="Employment history, business sales, inheritances — with "
                    "amounts and supporting documents."),
            _f("sof_evidence_summary", "Documented source of funds (detail)",
               "textarea", category="SOURCE_OF_FUNDS"),
            _f("expected_high_risk_exposure",
               "Exposure to high-risk jurisdictions", "textarea", category="PURPOSE"),
            _f("edd_counterparty_detail", "Key counterparties — detail",
               "textarea", category="PURPOSE"),
        ],
    },
]


# ------------------------------ Proof checklist -----------------------------
# doc_type values feed Document rows; the requirement engine matches on them.
PROOF_CHECKLIST = [
    {"doc_type": "PASSPORT", "label": "Identity document",
     "applies": "INDIVIDUAL",
     "examples": "Passport, national ID card, residence permit."},
    {"doc_type": "PROOF_OF_ADDRESS", "label": "Proof of address",
     "applies": "ANY",
     "examples": "Utility bill or bank statement < 3 months old."},
    {"doc_type": "PROOF_OF_INCOME", "label": "Proof of income / revenue",
     "applies": "INDIVIDUAL",
     "examples": "Payslips, tax return, bank statements, pension statement."},
    {"doc_type": "CERTIFICATE_OF_INCORPORATION", "label": "Certificate of incorporation",
     "applies": "COMPANY", "examples": "Or an equivalent registry certificate."},
    {"doc_type": "ARTICLES_OF_ASSOCIATION", "label": "Articles of association",
     "applies": "COMPANY", "examples": "Statutes / memorandum of association."},
    {"doc_type": "REGISTER_EXTRACT", "label": "Commercial register extract",
     "applies": "COMPANY", "examples": "Recent extract (< 3 months)."},
    {"doc_type": "SHAREHOLDER_REGISTER", "label": "Shareholder register",
     "applies": "COMPANY", "examples": "Or an ownership chart signed by a director."},
    {"doc_type": "DIRECTORS_REGISTER", "label": "Directors register",
     "applies": "COMPANY", "examples": "Current directors and officers."},
    {"doc_type": "FINANCIAL_STATEMENTS", "label": "Financial statements",
     "applies": "COMPANY", "examples": "Latest annual accounts (audited if available)."},
    {"doc_type": "SOURCE_OF_FUNDS_EVIDENCE", "label": "Source of funds evidence",
     "applies": "ANY", "min_risk_rank": 2,
     "examples": "Sale agreements, loan agreements, inheritance documents, "
                 "investment statements."},
]


def schema_for(customer_type, risk_rank=0):
    """Sections + proof checklist applicable to a customer type at a risk rank."""
    sections = [s for s in FORM_SECTIONS
                if s["applies"] in ("ANY", customer_type)
                and risk_rank >= s.get("min_risk_rank", 0)]
    proofs = [p for p in PROOF_CHECKLIST
              if p["applies"] in ("ANY", customer_type)
              and risk_rank >= p.get("min_risk_rank", 0)]
    return {"sections": sections, "proofs": proofs}


def field_index():
    """key -> field def (for category lookup on submission)."""
    out = {}
    for s in FORM_SECTIONS:
        for f in s["fields"]:
            out[f["key"]] = f
    return out
