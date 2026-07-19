"""Provider adapters: KYC / KYB / AML behind stable interfaces.

The application depends on these interfaces, never on a vendor SDK. Add a real
provider by implementing the interface and registering the adapter — nothing
else in the platform changes.
"""
from api.integrations.providers.base import (
    KYCProvider, KYBProvider, AMLScreeningProvider, NormalizedResult,
)
from api.integrations.providers.registry import get_adapter, ADAPTERS

__all__ = ["KYCProvider", "KYBProvider", "AMLScreeningProvider",
           "NormalizedResult", "get_adapter", "ADAPTERS"]
