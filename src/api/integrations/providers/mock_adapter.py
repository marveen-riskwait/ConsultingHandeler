"""Deterministic mock KYC/KYB adapter — keeps local development fully working.

Verifications and webhook payloads are keyed on the subject name so demos are
reproducible without any external service or credentials.
"""
import secrets

from api.integrations.providers.base import (
    KYCProvider, KYBProvider, NormalizedResult,
)


def _ref():
    return "mock_" + secrets.token_hex(6)


class MockKYCProvider(KYCProvider):
    adapter_key = "mock_kyc"

    def create_verification(self, subject):
        name = (subject.get("name") or "").lower()
        # A "fail"/"pending" hint in the name lets demos exercise every branch.
        if "fail" in name:
            status, conf = "FAILED", 0.2
        elif "pending" in name:
            status, conf = "PENDING", None
        else:
            status, conf = "PASSED", 0.98
        return NormalizedResult(
            provider="mock", provider_reference=_ref(),
            result_type="IDENTITY", status=status, confidence=conf,
            data={"subject": subject.get("name"), "checks": ["document", "liveness"]},
            raw={"reviewResult": {"reviewAnswer": status}},
        )

    def get_verification_status(self, provider_reference):
        return NormalizedResult(
            provider="mock", provider_reference=provider_reference,
            result_type="IDENTITY", status="PASSED", confidence=0.98)

    def normalize_webhook(self, payload):
        answer = (payload.get("reviewResult", {}).get("reviewAnswer")
                  or payload.get("status") or "PENDING").upper()
        mapping = {"GREEN": "PASSED", "RED": "FAILED", "PASSED": "PASSED",
                   "FAILED": "FAILED", "PENDING": "PENDING"}
        return NormalizedResult(
            provider="mock",
            provider_reference=payload.get("applicantId") or payload.get("reference") or _ref(),
            result_type=payload.get("type", "IDENTITY"),
            status=mapping.get(answer, "PENDING"),
            confidence=payload.get("confidence"),
            data=payload, raw=payload)


class MockKYBProvider(KYBProvider):
    adapter_key = "mock_kyb"

    def verify_business(self, entity):
        return NormalizedResult(
            provider="mock", provider_reference=_ref(),
            result_type="KYB", status="PASSED", confidence=0.9,
            data={"legal_name": entity.get("name"), "registry": "verified"})

    def normalize_webhook(self, payload):
        return NormalizedResult(
            provider="mock",
            provider_reference=payload.get("reference") or _ref(),
            result_type="KYB", status=payload.get("status", "PENDING").upper(),
            data=payload, raw=payload)
