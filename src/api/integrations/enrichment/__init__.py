"""Enrichment sources — external public data that fills customer files.

Each source implements `applies(customer)` and `run(customer, context)` and
returns a normalized result dict (see base.result). The engine
(api.engine.enrichment_service) orchestrates them, writes observations with
provenance, raises discrepancies and never overwrites human-declared data.

V1 is the FREE tier only: UK Companies House (profile/officers/PSC-UBO),
GLEIF LEI, French registry (recherche-entreprises), and GDELT adverse media.
Paid sources (OpenSanctions PEP, OpenCorporates…) plug in the same way later.
"""
from api.integrations.enrichment.companies_house import CompaniesHouseSource
from api.integrations.enrichment.gleif import GleifSource
from api.integrations.enrichment.sirene import FrenchRegistrySource
from api.integrations.enrichment.adverse_media import AdverseMediaSource

SOURCES = [
    CompaniesHouseSource(),
    FrenchRegistrySource(),
    GleifSource(),
    AdverseMediaSource(),
]


def sources_for(customer):
    return [s for s in SOURCES if s.applies(customer)]
