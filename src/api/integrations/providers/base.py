"""Provider adapter interfaces + the normalized result shape.

Every provider returns a different payload; adapters translate it into a single
internal NormalizedResult so the rest of the platform is provider-agnostic.
"""
from dataclasses import dataclass, field


@dataclass
class NormalizedResult:
    provider: str
    provider_reference: str
    result_type: str        # IDENTITY | SCREENING | KYB
    status: str             # PASSED | FAILED | PENDING | MATCH
    confidence: float = None
    data: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class BaseProvider:
    #: adapter registry key
    adapter_key = "base"
    provider_type = None

    def __init__(self, config=None, credentials=None):
        self.config = config or {}
        self.credentials = credentials or {}   # key_name -> secret

    def health_check(self):
        """Return (status, detail). Real adapters ping the vendor; the mock is
        always UP; adapters missing credentials report DEGRADED."""
        return ("UP", "mock adapter")

    # Providers normalize their own webhook payloads into a NormalizedResult.
    def normalize_webhook(self, payload):
        raise NotImplementedError


class KYCProvider(BaseProvider):
    provider_type = "KYC"

    def create_verification(self, subject):
        """Start an identity verification; return a NormalizedResult."""
        raise NotImplementedError

    def get_verification_status(self, provider_reference):
        raise NotImplementedError


class KYBProvider(BaseProvider):
    provider_type = "KYB"

    def verify_business(self, entity):
        raise NotImplementedError


class AMLScreeningProvider(BaseProvider):
    provider_type = "AML"

    def screen_subject(self, subject):
        raise NotImplementedError

    def get_matches(self, provider_reference):
        raise NotImplementedError
