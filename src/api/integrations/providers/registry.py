"""Adapter registry — map a Provider.adapter string to an adapter class."""
from api.integrations.providers.mock_adapter import MockKYCProvider, MockKYBProvider
from api.integrations.providers.real_adapters import (
    SumsubKYCProvider, TruliooKYCProvider, ComplyAdvantageAMLProvider,
)
from api.integrations.providers.companies_house import CompaniesHouseKYBProvider
from api.integrations.fraud.abuseipdb import AbuseIPDBProvider

ADAPTERS = {
    "mock": MockKYCProvider,          # default KYC
    "mock_kyc": MockKYCProvider,
    "mock_kyb": MockKYBProvider,
    "sumsub": SumsubKYCProvider,
    "trulioo": TruliooKYCProvider,
    "comply_advantage": ComplyAdvantageAMLProvider,
    "companies_house": CompaniesHouseKYBProvider,
    "abuseipdb": AbuseIPDBProvider,
}


def get_adapter(provider_row):
    """Instantiate the adapter for a Provider DB row, injecting its config +
    credentials (key_name -> secret)."""
    cls = ADAPTERS.get(provider_row.adapter, MockKYCProvider)
    creds = {c.key_name: c.secret_value for c in provider_row.credentials}
    return cls(config=provider_row.config or {}, credentials=creds)
