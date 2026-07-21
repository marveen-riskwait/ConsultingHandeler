"""Enrichment sources — external public data that fills customer files.

Each source implements `applies(customer)` and `run(customer, context)` and
returns a normalized result dict (see base.result). The engine
(api.engine.enrichment_service) orchestrates them, writes observations with
provenance, raises discrepancies and never overwrites human-declared data.

Everything here is FREE and keyless (Companies House wants a free key):
UK Companies House (profile/officers/PSC-UBO), French registry
(recherche-entreprises), GLEIF LEI (global), SEC EDGAR (US filers), EU VAT
validation via VIES (all member states), GDELT adverse media, and a Wikidata PEP *lead* (a lead, not a
determination — see the module docstring). Paid sources
(OpenSanctions PEP, OpenCorporates…) plug in the same way later.

Coverage is deliberately layered: GLEIF answers anywhere but only for entities
holding an LEI, national registries answer in depth but only at home, and VIES
covers the 25 EU countries where no free registry exists — proving the entity
is VAT-registered under that name and address.
"""
from api.integrations.enrichment.companies_house import CompaniesHouseSource
from api.integrations.enrichment.gleif import GleifSource
from api.integrations.enrichment.sirene import FrenchRegistrySource
from api.integrations.enrichment.sec_edgar import SecEdgarSource
from api.integrations.enrichment.vies import ViesSource
from api.integrations.enrichment.wikidata_pep import WikidataPepSource
from api.integrations.enrichment.adverse_media import AdverseMediaSource

SOURCES = [
    CompaniesHouseSource(),
    FrenchRegistrySource(),
    SecEdgarSource(),
    ViesSource(),
    GleifSource(),
    WikidataPepSource(),
    AdverseMediaSource(),
]


def sources_for(customer):
    return [s for s in SOURCES if s.applies(customer)]
